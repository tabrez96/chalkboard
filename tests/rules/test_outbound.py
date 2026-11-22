import re
from pathlib import Path

import httpx
import pytest

from chalkboard.ai import OllamaClient, OllamaConfig
from chalkboard.net import OutboundBlocked, guarded_client

OLLAMA = "http://ollama.test:11434"
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


def test_no_http_outside_guard():
    root = Path(__file__).resolve().parents[2] / "chalkboard"
    offenders = [
        str(path.relative_to(root.parent))
        for path in root.rglob("*.py")
        if path.name != "net.py" and FORBIDDEN.search(path.read_text())
    ]
    assert offenders == []
