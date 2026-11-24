"""Allowed websites: the outbound guard and page saving use `may_fetch`; DNS-to-private-address checks live in `net.fetch_page`."""

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


def site_access(subject, teacher) -> SiteAccess:
    """What this subject may fetch now. Uploads-only subjects may fetch nothing."""
    if subject.web_access != "web":
        return SiteAccess(open_mode=False, domains=frozenset())
    return SiteAccess(
        open_mode=teacher.allow_any_site,
        domains=frozenset(s.domain for s in subject.sites),
    )


def can_save_pages(access: SiteAccess) -> bool:
    return access.open_mode or bool(access.domains)


def on_allowed_list(url: str, teacher) -> bool:
    """True when the link's host is one of the teacher's allowed sites (ticked for this subject or not)."""
    from sqlalchemy import select

    from chalkboard.db import get_session
    from chalkboard.models import AllowedSite

    u = _parse(url)
    if u is None or not u.host:
        return False
    host = _host(u)
    domains = get_session().scalars(
        select(AllowedSite.domain).where(AllowedSite.teacher_id == teacher.id)
    )
    return any(host_matches(host, d) for d in domains)


def page_origin(url: str) -> str:
    """The From value of a saved page: its host without a leading www."""
    return _host(_parse(url)).removeprefix("www.")


def subjects_by_site(teacher) -> dict[int, list[str]]:
    """Names of the teacher's subjects that use each allowed site, by site id."""
    from sqlalchemy import select

    from chalkboard.db import get_session
    from chalkboard.models import Subject, subject_sites

    rows = get_session().execute(
        select(subject_sites.c.allowed_site_id, Subject.name)
        .join(Subject, Subject.id == subject_sites.c.subject_id)
        .where(Subject.teacher_id == teacher.id, Subject.web_access == "web")
        .order_by(Subject.name)
    )
    used: dict[int, list[str]] = {}
    for site_id, name in rows:
        used.setdefault(site_id, []).append(name)
    return used
