"""The single outbound HTTP path.

Every request the app makes goes through `guarded_client`, which refuses any origin that is not on its allow-list
before a connection is opened. The configured Ollama server is allowed through `guarded_client` directly; saved web
pages go through `fetch_page`, which allows one origin per hop, re-checks every redirect against the subject's sites
and refuses private addresses. The SearXNG rules are added by a later ticket.
"""

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from chalkboard.sites import SiteAccess, may_fetch

DEFAULT_PORTS = {"http": 80, "https": 443}
Origin = tuple[str, str, int]  # (scheme, host, port)
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024
PAGE_DEADLINE = 60.0  # seconds for the whole download: resolving, redirects and reading
PAGE_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "Chalkboard (saves a page a teacher chose)"


class OutboundBlocked(Exception):
    """A request to a host the app may not contact; raised before any connection is opened."""


class PageBlocked(OutboundBlocked):
    """The page, or a redirect on the way to it, is on a site the subject may not use or a private address."""


class PageError(Exception):
    """The page was reached but can't be saved (bad status, too big, not a web page, too many redirects)."""


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    content_type: str  # lower-cased media type without parameters, e.g. "text/html"
    body: bytes
    charset: str | None = None  # declared in the Content-Type header, if any


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
        follow_redirects=False,  # redirects are followed manually and re-checked (`fetch_page`)
        trust_env=False,  # proxy env vars must not route requests elsewhere
    )


def resolve_host(host: str, port: int) -> list[str]:
    return [
        info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ]


def is_public(addresses: list[str]) -> bool:
    return bool(addresses) and all(
        ipaddress.ip_address(a.split("%", 1)[0]).is_global for a in addresses
    )


def fetch_page(
    url: str,
    access: SiteAccess,
    *,
    transport: httpx.BaseTransport | None = None,
    resolve: Callable[[str, int], list[str]] = resolve_host,
) -> FetchedPage:
    """Download one page. Every hop must pass `may_fetch` and resolve to public addresses before it is requested."""
    deadline = time.monotonic() + PAGE_DEADLINE
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            raise PageError("The page took too long")
        if not may_fetch(url, access):
            raise PageBlocked(f"Refused {url}: site not chosen")
        scheme, host, port = origin(url)
        if not is_public(resolve(host, port)):
            raise PageBlocked(f"Refused {host}: not a public address")
        hop = f"{scheme}://{host}:{port}"
        with (
            guarded_client(
                [hop],
                transport=transport,
                timeout=max(1.0, min(30.0, deadline - time.monotonic())),
            ) as client,
            client.stream(
                "GET",
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html, text/plain;q=0.8",
                    # Identity only: a compressed body is decoded before the size limit can see it.
                    "Accept-Encoding": "identity",
                },
            ) as response,
        ):
            if response.is_redirect and "location" in response.headers:
                url = str(response.url.join(response.headers["location"]))
                continue
            if response.status_code != 200:
                raise PageError(f"HTTP {response.status_code}")
            encoding = response.headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise PageError(f"Unsupported content encoding: {encoding}")
            header = response.headers.get("content-type", "")
            ctype = header.split(";", 1)[0].strip().lower()
            if ctype not in PAGE_TYPES:
                raise PageError(f"Not a web page: {ctype or 'unknown type'}")
            body = bytearray()
            for block in response.iter_bytes():
                body.extend(block)
                if len(body) > MAX_PAGE_BYTES:
                    raise PageError("The page is too big")
                if time.monotonic() > deadline:
                    raise PageError("The page took too long")
            return FetchedPage(
                str(response.url), ctype, bytes(body), response.charset_encoding
            )
    raise PageError("Too many redirects")
