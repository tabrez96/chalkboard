import gzip
import re
from pathlib import Path

import httpx
import pytest

from chalkboard import net
from chalkboard.ai import OllamaConfig
from chalkboard.ai.ollama import OllamaClient
from chalkboard.ai.websearch import searxng_client
from chalkboard.net import (
    OutboundBlocked,
    PageBlocked,
    PageError,
    fetch_page,
    guarded_client,
)
from chalkboard.sites import SiteAccess
from tests.test_assistant import (
    UNSUPPORTED,
    WEB_ANSWER,
    lesson,
    post_scoped,
    run_all,
    searx,
    sentences,
    server,
    web_on,
)
from tests.test_today_page import signed_in, sourced, subject, timetable

# Reuse the assistant tests' fixtures.
fixtures = (lesson, searx, server, signed_in, sourced, subject, timetable, web_on)

OLLAMA = "http://ollama.test:11434"
SEARXNG = "http://searxng.test:8080"
FORBIDDEN = re.compile(
    r"\bhttpx\.(Client|AsyncClient|HTTPTransport|AsyncHTTPTransport|get|post|put|patch|delete|request|stream)\b"
    r"|\burllib\.request\b|\bimport requests\b|\bhttp\.client\b"
)


class Recording(httpx.MockTransport):
    def __init__(self):
        self.seen = []
        super().__init__(self.answer)

    def answer(self, request):
        self.seen.append(request)
        return httpx.Response(200, json={})


def test_other_host_refused_before_connecting():
    inner = Recording()
    with pytest.raises(OutboundBlocked):
        guarded_client([OLLAMA], transport=inner).get("http://example.com/")
    assert inner.seen == []


@pytest.mark.parametrize(
    "url", ["http://ollama.test:11435/", "https://ollama.test:11434/"]
)
def test_other_port_and_scheme_refused(url):
    inner = Recording()
    with pytest.raises(OutboundBlocked):
        guarded_client([OLLAMA], transport=inner).get(url)
    assert inner.seen == []


def test_configured_host_allowed():
    inner = Recording()
    guarded_client([OLLAMA], transport=inner).get("http://OLLAMA.test:11434/api/tags")
    assert len(inner.seen) == 1


def test_ollama_client_cannot_reach_other_hosts():
    inner = Recording()
    client = OllamaClient(OllamaConfig(OLLAMA, "e", "w"), transport=inner)
    with pytest.raises(OutboundBlocked):
        client.http.get("http://example.com/")
    assert inner.seen == []


def test_searxng_client_cannot_reach_other_hosts():
    inner = Recording()
    with pytest.raises(OutboundBlocked):
        searxng_client(SEARXNG, transport=inner).get("http://example.com/")
    assert inner.seen == []


def test_no_http_outside_guard():
    root = Path(__file__).resolve().parents[2] / "chalkboard"
    offenders = [
        str(path.relative_to(root.parent))
        for path in root.rglob("*.py")
        if path.name != "net.py" and FORBIDDEN.search(path.read_text())
    ]
    assert offenders == []


PUBLIC = lambda host, port: ["93.184.216.34"]
TICKED = SiteAccess(False, frozenset({"openstax.org"}))
HTML = {"content-type": "text/html; charset=utf-8"}


class Scripted(httpx.MockTransport):
    """Answers each request with the next response from `replies` (the last one repeats)."""

    def __init__(self, *replies: httpx.Response):
        self.seen = []
        self.replies = list(replies)
        super().__init__(self.answer)

    def answer(self, request):
        self.seen.append(request)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def page(body=b"<p>hi</p>", **kw):
    return httpx.Response(200, content=body, headers=kw.get("headers", HTML))


def redirect(location):
    return httpx.Response(302, headers={"location": location})


def test_page_from_unticked_domain_never_connects():
    inner = Scripted(page())
    with pytest.raises(PageBlocked):
        fetch_page("https://evil.example/x", TICKED, transport=inner, resolve=PUBLIC)
    assert inner.seen == []


def test_redirect_to_unticked_domain_never_connects():
    inner = Scripted(redirect("https://evil.example/"), page())
    with pytest.raises(PageBlocked):
        fetch_page(
            "https://openstax.org/books", TICKED, transport=inner, resolve=PUBLIC
        )
    assert [r.url.host for r in inner.seen] == ["openstax.org"]


def test_relative_redirect_is_followed_and_rechecked():
    inner = Scripted(redirect("/books/2"), page(b"<p>two</p>"))
    result = fetch_page(
        "https://openstax.org/books/1", TICKED, transport=inner, resolve=PUBLIC
    )
    assert result.body == b"<p>two</p>"
    assert result.url == "https://openstax.org/books/2"
    assert [r.url.host for r in inner.seen] == ["openstax.org"] * 2


@pytest.mark.parametrize("open_mode", [False, True])
@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["192.168.1.40"],
        ["::1"],
        ["169.254.169.254"],
        ["93.184.216.34", "10.0.0.1"],
    ],
)
def test_private_address_refused(addresses, open_mode):
    inner = Scripted(page())
    access = SiteAccess(open_mode, frozenset({"openstax.org"}))
    with pytest.raises(PageBlocked):
        fetch_page(
            "https://openstax.org/",
            access,
            transport=inner,
            resolve=lambda host, port: addresses,
        )
    assert inner.seen == []


def test_open_mode_allows_any_public_host():
    inner = Scripted(page())
    result = fetch_page(
        "https://example.org/a",
        SiteAccess(True, frozenset()),
        transport=inner,
        resolve=PUBLIC,
    )
    assert result.content_type == "text/html"
    assert len(inner.seen) == 1


def test_too_many_redirects():
    inner = Scripted(redirect("https://openstax.org/loop"))
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/loop", TICKED, transport=inner, resolve=PUBLIC)
    assert len(inner.seen) == net.MAX_REDIRECTS + 1


def test_rejects_non_page_content_and_oversize(monkeypatch):
    pdf = Scripted(page(b"%PDF", headers={"content-type": "application/pdf"}))
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/a", TICKED, transport=pdf, resolve=PUBLIC)
    monkeypatch.setattr(net, "MAX_PAGE_BYTES", 5)
    big = Scripted(page(b"x" * 6))
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/a", TICKED, transport=big, resolve=PUBLIC)


def test_slow_download_hits_the_deadline(monkeypatch):
    monkeypatch.setattr(net, "PAGE_DEADLINE", -1)
    inner = Scripted(page())
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/a", TICKED, transport=inner, resolve=PUBLIC)
    assert inner.seen == []


def test_compressed_response_refused_and_identity_requested():
    inner = Scripted(
        page(gzip.compress(b"<p>hi</p>"), headers={**HTML, "content-encoding": "gzip"})
    )
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/a", TICKED, transport=inner, resolve=PUBLIC)
    assert inner.seen[0].headers["accept-encoding"] == "identity"


def test_declared_charset_is_kept():
    inner = Scripted(page(headers={"content-type": "text/plain; charset=ISO-8859-7"}))
    result = fetch_page(
        "https://openstax.org/a", TICKED, transport=inner, resolve=PUBLIC
    )
    assert (result.content_type, result.charset) == ("text/plain", "iso-8859-7")


def test_bad_status_is_an_error():
    inner = Scripted(httpx.Response(404, headers=HTML))
    with pytest.raises(PageError):
        fetch_page("https://openstax.org/a", TICKED, transport=inner, resolve=PUBLIC)


def test_is_public():
    assert net.is_public(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    assert not net.is_public([])
    assert not net.is_public(["fe80::1%eth0"])


def test_ai_code_contacts_only_ollama_and_searxng(
    app, signed_in, timetable, lesson, server, searx, web_on
):
    server.generated = [sentences("Force is cheese."), UNSUPPORTED, WEB_ANSWER]
    post_scoped(signed_in, timetable, lesson[0], "Who ran fastest?", "web")
    run_all(app)
    assert len(searx.seen) == 1  # the web-scope answer job really searched
    settings = app.extensions["chalkboard.settings"]
    OllamaClient(OllamaConfig(settings.ollama_url, "e", "w"), transport=searx).health()
    allowed = {origin(settings.ollama_url), origin(settings.searxng_url)}
    assert {origin(str(r.url)) for r in searx.seen} <= allowed
    assert len(searx.seen) == 2  # the search and the health check


def origin(url: str):
    parts = httpx.URL(url)
    return (parts.scheme, parts.host, parts.port or net.DEFAULT_PORTS[parts.scheme])
