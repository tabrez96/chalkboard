from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from chalkboard import jobs
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.extract import page_text
from chalkboard.models import AllowedSite, Chunk, Source, Subject
from chalkboard.sources import PAGE_RESOLVER_KEY, PAGE_TRANSPORT_KEY, status_label
from chalkboard.sources_page import LINK_MESSAGE
from tests.conftest import csrf_token, sign_in

BODY = " ".join(f"fact{i}" for i in range(300))
ARTICLE = (
    "<html><head><title>Physics Basics | OpenStax</title></head><body><nav>Menu</nav>"
    f"<article><h1>  Physics   Basics </h1><p>{BODY}</p><p>{BODY}</p></article></body></html>"
)
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
HTML = {"content-type": "text/html"}


class Pages(httpx.MockTransport):
    def __init__(self, *replies: httpx.Response):
        self.seen = []
        self.replies = list(replies)
        super().__init__(self.answer)

    def answer(self, request):
        self.seen.append(request)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def article(body=ARTICLE):
    return httpx.Response(200, content=body.encode(), headers=HTML)


@pytest.fixture
def subject(db_session, teacher):
    db_session.add(AllowedSite(teacher_id=teacher.id, domain="openstax.org"))
    db_session.flush()
    row = Subject(
        teacher_id=teacher.id,
        name="Physics",
        class_minutes=60,
        colour="#0E7A64",
        web_access="web",
    )
    row.sites = list(db_session.scalars(select(AllowedSite)))
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def data(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def server(app):
    fake = FakeOllama()
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    return fake


@pytest.fixture
def web(app):
    transport = Pages(article())
    app.extensions[PAGE_TRANSPORT_KEY] = transport
    app.extensions[PAGE_RESOLVER_KEY] = lambda host, port: ["93.184.216.34"]
    return transport


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def url(subject, tail=""):
    return f"/subjects/{subject.id}/sources{tail}"


def text(response):
    return response.get_data(as_text=True)


def save(client, subject, link):
    token = csrf_token(client, url(subject))
    return client.post(
        url(subject, "/save-page"), data={"csrf_token": token, "url": link}
    )


def count(db_session):
    return db_session.scalar(select(func.count()).select_from(Source))


def only_source(db_session):
    source = db_session.scalar(select(Source))
    db_session.refresh(source)
    db_session.refresh(source.job)
    return source


def test_page_from_ticked_site_ends_ready_with_domain(
    app, signed_in, subject, data, server, web, db_session
):
    response = save(signed_in, subject, "https://www.openstax.org/books/physics")
    assert response.status_code == 303
    assert jobs.run_once(app)  # save_page
    assert jobs.run_once(app)  # ingest_source
    source = only_source(db_session)
    assert (source.kind, source.origin, source.name) == (
        "web",
        "openstax.org",
        "Physics Basics",
    )
    assert status_label(source).startswith("Ready ·")
    assert db_session.scalar(select(func.count()).select_from(Chunk)) > 0
    assert "openstax.org" in text(signed_in.get(url(subject)))
    assert len(web.seen) == 1


def test_link_without_scheme_is_https(app, signed_in, subject, data, web, db_session):
    save(signed_in, subject, "openstax.org/books/x")
    jobs.run_once(app)
    assert str(web.seen[0].url) == "https://openstax.org/books/x"


def test_unticked_site_on_allowed_list_shows_change_link(
    signed_in, subject, web, db_session, teacher
):
    db_session.add(AllowedSite(teacher_id=teacher.id, domain="khanacademy.org"))
    db_session.commit()
    response = save(signed_in, subject, "https://khanacademy.org/x")
    html = text(response)
    assert response.status_code == 422
    assert "This site isn&#39;t chosen for Physics" in html
    assert (
        f'<a href="/subjects/{subject.id}/settings">Change in subject settings</a>'
        in html
    )
    assert count(db_session) == 0
    assert web.seen == []


def test_site_not_on_allowed_list_links_to_settings(
    signed_in, subject, web, db_session
):
    response = save(signed_in, subject, "https://evil.example/x")
    html = text(response)
    assert response.status_code == 422
    assert "This site isn&#39;t chosen for Physics" in html
    assert 'href="/settings"' in html
    assert count(db_session) == 0
    assert web.seen == []


@pytest.mark.parametrize("link", ["ftp://x.org/a", "http://127.0.0.1/", ""])
def test_bad_link_message(signed_in, subject, web, db_session, link):
    response = save(signed_in, subject, link)
    assert response.status_code == 422
    assert LINK_MESSAGE in text(response)
    assert count(db_session) == 0
    assert web.seen == []


def test_uploads_only_hides_link_field_and_refuses_post(
    signed_in, subject, web, db_session
):
    subject.web_access = "uploads"
    db_session.commit()
    assert 'id="page-form"' not in text(signed_in.get(url(subject)))
    assert save(signed_in, subject, "https://openstax.org/a").status_code == 400
    assert count(db_session) == 0


def test_open_mode_line_and_any_host(
    app, signed_in, subject, data, server, web, db_session, teacher
):
    subject.sites = []
    teacher.allow_any_site = True
    db_session.commit()
    html = text(signed_in.get(url(subject)))
    assert "Any website is allowed" in html
    assert 'id="page-form"' in html
    assert save(signed_in, subject, "https://example.org/a").status_code == 303
    assert only_source(db_session).origin == "example.org"


def test_redirect_to_unticked_site_shows_blocked(
    app, signed_in, subject, data, db_session
):
    transport = Pages(
        httpx.Response(302, headers={"location": "https://evil.example/"}), article()
    )
    app.extensions[PAGE_TRANSPORT_KEY] = transport
    app.extensions[PAGE_RESOLVER_KEY] = lambda host, port: ["93.184.216.34"]
    save(signed_in, subject, "https://openstax.org/a")
    jobs.run_once(app)
    source = only_source(db_session)
    assert status_label(source) == "Blocked: site not chosen"
    assert len(transport.seen) == 1
    assert "Blocked: site not chosen" in text(signed_in.get(url(subject)))


def test_site_unticked_before_job_runs_is_blocked(
    app, signed_in, subject, data, web, db_session
):
    save(signed_in, subject, "https://openstax.org/a")
    subject.sites = []
    db_session.commit()
    jobs.run_once(app)
    assert status_label(only_source(db_session)) == "Blocked: site not chosen"
    assert web.seen == []


def test_page_downloaded_once(app, signed_in, subject, data, server, web, db_session):
    server.up = False
    save(signed_in, subject, "https://openstax.org/a")
    jobs.run_once(app)  # save_page
    jobs.run_once(app)  # ingest waits for Ollama
    source = only_source(db_session)
    assert status_label(source) == "Saved · waiting for Ollama"
    server.up = True
    source.job.run_after = LONG_AGO
    db_session.commit()
    jobs.run_once(app)
    assert status_label(only_source(db_session)).startswith("Ready ·")
    assert len(web.seen) == 1


def test_failed_publish_leaves_no_part_file(
    app, signed_in, subject, data, web, db_session, monkeypatch
):
    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("chalkboard.sources.os.replace", broken)
    save(signed_in, subject, "https://openstax.org/a")
    jobs.run_once(app)
    assert list(data.rglob("*.part")) == []
    assert list(data.rglob("*.txt")) == []


def test_page_without_text_is_unreadable(app, signed_in, subject, data, db_session):
    transport = Pages(article("<html><body></body></html>"))
    app.extensions[PAGE_TRANSPORT_KEY] = transport
    app.extensions[PAGE_RESOLVER_KEY] = lambda host, port: ["93.184.216.34"]
    save(signed_in, subject, "https://openstax.org/a")
    jobs.run_once(app)
    assert status_label(only_source(db_session)) == "Couldn't read the file"


def test_page_text():
    title, body = page_text(ARTICLE.encode(), "text/html", "https://openstax.org/a")
    assert title == "Physics Basics"
    assert "fact10" in body
    assert page_text("héllo".encode("cp1252"), "text/plain", "https://x.org/") == (
        "",
        "héllo",
    )


def test_page_text_uses_declared_or_detected_charset():
    url = "https://x.org/"
    html = f"<html><body><article><p>{BODY} café</p></article></body></html>"
    _, text = page_text(html.encode("cp1252"), "text/html", url)
    assert "café" in text
    _, text = page_text(html.encode("cp1252"), "text/html", url, "windows-1252")
    assert "café" in text
    assert page_text(b"\xe2", "text/plain", url, "iso-8859-7") == ("", "β")
    assert page_text(b"\xe2", "text/plain", url, "no-such-codec") == ("", "â")
