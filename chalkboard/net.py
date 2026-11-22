"""The single outbound HTTP path.

Every request the app makes goes through `guarded_client`, which refuses any origin that is not on its allow-list
before a connection is opened. Only the configured Ollama server is allowed for now; the SearXNG and page-fetch
rules are added by later tickets.
"""

import httpx

DEFAULT_PORTS = {"http": 80, "https": 443}
Origin = tuple[str, str, int]  # (scheme, host, port)


class OutboundBlocked(Exception):
    """A request to a host the app may not contact; raised before any connection is opened."""


def origin(url: str | httpx.URL) -> Origin:
    u = httpx.URL(url)
    return (u.scheme, (u.host or "").lower(), u.port or DEFAULT_PORTS.get(u.scheme, 0))


def netloc(url: str) -> str:
    """'http://192.168.1.40:11434/' -> '192.168.1.40:11434' (for messages)."""
    u = httpx.URL(url)
    return f"{u.host}:{u.port}" if u.port else u.host


class GuardedTransport(httpx.BaseTransport):
    def __init__(self, allowed: list[str], inner: httpx.BaseTransport | None = None):
        self.allowed = {origin(a) for a in allowed}
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if origin(request.url) not in self.allowed:
            raise OutboundBlocked(
                f"Refused request to {request.url.host}: not an allowed host"
            )
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def guarded_client(
    allowed: list[str],
    *,
    transport: httpx.BaseTransport | None = None,
    base_url: str = "",
    timeout: httpx.Timeout | float = 30.0,
) -> httpx.Client:
    return httpx.Client(
        transport=GuardedTransport(allowed, transport),
        base_url=base_url,
        timeout=timeout,
        follow_redirects=False,  # redirects are followed manually and re-checked (page-fetch ticket)
        trust_env=False,  # proxy env vars must not route requests elsewhere
    )
