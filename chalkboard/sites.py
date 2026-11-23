"""Allowed websites: the outbound guard and page saving use `may_fetch`; DNS-to-private-address checks belong to the page-fetch ticket."""

import ipaddress
import re
from dataclasses import dataclass

import httpx

NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
SCHEMES = ("http", "https")
PORTS = (None, 80, 443)
ADD_MESSAGE = "Enter a site like nasa.gov"
IP_MESSAGE = "Enter a domain name, not an IP address"


class InvalidSite(ValueError):
    """The text is not a public domain name; str(exc) is the message shown under the field."""


@dataclass(frozen=True)
class SiteAccess:
    open_mode: bool
    domains: frozenset[str]  # the subject's ticked, normalised domains


def _parse(text: str) -> httpx.URL | None:
    if "://" not in text:
        text = "http://" + text
    try:
        return httpx.URL(text)
    except httpx.InvalidURL, ValueError:
        return None


def _host(u: httpx.URL) -> str:
    return u.raw_host.decode("ascii").lower().rstrip(".")


def is_ip_like(host: str) -> bool:
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Legacy IPv4 spellings: "127.1", "2130706433", "0x7f.0.0.0x1".
        return bool(NUMERIC_LABEL.match(host.rsplit(".", 1)[-1]))
    return True


def _labels_valid(host: str) -> bool:
    return all(LABEL.match(label) for label in host.split("."))


def normalise_domain(text: str) -> str:
    text = text.strip()
    if not text:
        raise InvalidSite(ADD_MESSAGE)
    u = _parse(text)
    if u is None or u.scheme not in SCHEMES or u.userinfo:
        raise InvalidSite(ADD_MESSAGE)
    host = _host(u)
    if is_ip_like(host):
        raise InvalidSite(IP_MESSAGE)
    host = host.removeprefix("www.")
    if len(host) > 253 or "." not in host or not _labels_valid(host):
        raise InvalidSite(ADD_MESSAGE)
    return host


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def may_fetch(url: str, access: SiteAccess) -> bool:
    if "://" not in url:
        return False
    u = _parse(url)
    if u is None or u.scheme not in SCHEMES or u.userinfo or u.port not in PORTS:
        return False
    host = _host(u)
    if not host or is_ip_like(host) or not _labels_valid(host):
        return False
    if access.open_mode:
        return True
    return any(host_matches(host, d) for d in access.domains)


def subjects_by_site(teacher) -> dict[int, list[str]]:
    # Filled in by the subjects ticket (subject_sites).
    return {}
