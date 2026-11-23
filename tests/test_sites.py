import pytest

from chalkboard.sites import (
    ADD_MESSAGE,
    IP_MESSAGE,
    InvalidSite,
    SiteAccess,
    host_matches,
    may_fetch,
    normalise_domain,
    subjects_by_site,
)

OPENSTAX = SiteAccess(False, frozenset({"openstax.org"}))
OPEN = SiteAccess(True, frozenset())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://www.OpenStax.org/books/physics", "openstax.org"),
        ("nasa.gov:8080", "nasa.gov"),
        ("OpenStax.ORG.", "openstax.org"),
        ("  https://www.khanacademy.org/science?x=1 ", "khanacademy.org"),
        ("www.www.example.com", "www.example.com"),
    ],
)
def test_normalise_strips_scheme_path_port_www_case(text, expected):
    assert normalise_domain(text) == expected


def test_normalise_idn_to_punycode():
    assert normalise_domain("bücher.de") == "xn--bcher-kva.de"


@pytest.mark.parametrize(
    "text, message",
    [
        ("localhost", ADD_MESSAGE),
        ("192.168.1.10", IP_MESSAGE),
        ("[::1]", IP_MESSAGE),
        ("127.1", IP_MESSAGE),
        ("2130706433", IP_MESSAGE),
        ("0x7f.0.0.0x1", IP_MESSAGE),
        ("0x7f000001", IP_MESSAGE),
        ("ftp://x.org", ADD_MESSAGE),
        ("user@x.org", ADD_MESSAGE),
        ("https://u:p@x.org", ADD_MESSAGE),
        ("*.x.org", ADD_MESSAGE),
        ("-bad.org", ADD_MESSAGE),
        ("", ADD_MESSAGE),
        ("   ", ADD_MESSAGE),
        ("http://[", ADD_MESSAGE),
        ("a" * 64 + ".org", ADD_MESSAGE),
        (".".join(["abcdefghi"] * 30) + ".org", ADD_MESSAGE),
    ],
)
def test_normalise_rejects_invalid(text, message):
    with pytest.raises(InvalidSite) as error:
        normalise_domain(text)
    assert str(error.value) == message


def test_host_matches():
    assert host_matches("openstax.org", "openstax.org")
    assert host_matches("cnx.openstax.org", "openstax.org")
    assert not host_matches("evilopenstax.org", "openstax.org")


def test_may_fetch_exact_and_subdomain():
    assert may_fetch("https://openstax.org/x", OPENSTAX)
    assert may_fetch("https://cnx.www.openstax.org/x", OPENSTAX)
    assert may_fetch("https://OpenStax.org./x", OPENSTAX)


def test_may_fetch_rejects_lookalike():
    assert not may_fetch("https://evilopenstax.org/", OPENSTAX)
    assert not may_fetch("https://openstax.org.evil.com/", OPENSTAX)


@pytest.mark.parametrize("access", [OPENSTAX, OPEN])
def test_may_fetch_ports(access):
    host = "openstax.org" if not access.open_mode else "any.example"
    assert may_fetch(f"https://{host}:443/", access)
    assert may_fetch(f"http://{host}:80/", access)
    assert not may_fetch(f"https://{host}:8080/", access)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://2130706433/",
        "http://0x7f.0.0.0x1/",
        "http://0x7f000001/",
    ],
)
def test_may_fetch_ip_literals(url):
    assert not may_fetch(url, OPEN)


def test_may_fetch_open_mode():
    assert may_fetch("https://any.example/", OPEN)


def test_may_fetch_unticked_site():
    assert not may_fetch("https://ck12.org/", OPENSTAX)
    assert not may_fetch("https://openstax.org/", SiteAccess(False, frozenset()))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://openstax.org/",
        "https://u:p@openstax.org/",
        "openstax.org/x",
        "http://[",
        "https:///path",
    ],
)
def test_may_fetch_rejects_bad_urls(url):
    assert not may_fetch(url, OPEN)
    assert not may_fetch(url, OPENSTAX)


def test_subjects_by_site_empty_until_subjects_exist():
    assert subjects_by_site(None) == {}
