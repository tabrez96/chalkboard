import re
from pathlib import Path

import pytest

CSS = (
    Path(__file__).resolve().parent.parent / "chalkboard" / "static" / "css" / "app.css"
)
FONTS = CSS.parent.parent / "fonts"

TOKEN = re.compile(r"--([a-z0-9-]+):\s*light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\);")

# Colours from design/ux.md (light, dark). Draft and flag-line have no dark value in ux.md.
UX_COLOURS = {
    "page": ("#f5f7f6", "#0d1412"),
    "surface": ("#ffffff", "#141d1a"),
    "surface-2": ("#eef2f0", "#1b2622"),
    "line": ("#e1e6e4", "#25312d"),
    "ink": ("#111917", "#e7edeb"),
    "muted": ("#56625e", "#9aa7a2"),
    "primary": ("#0e7a64", "#2bb594"),
    "on-primary": ("#ffffff", "#04211a"),
    "primary-soft": ("#e2f3ee", "#163a31"),
    "on-primary-soft": ("#0a6452", "#74d9be"),
    "flag": ("#8a3a0c", "#ffc797"),
    "flag-soft": ("#fff0e0", "#3a2312"),
    "ok": ("#17603a", "#8fe0ae"),
    "ok-soft": ("#e0f4e8", "#13301f"),
    "web": ("#3a3ea6", "#bfc2ff"),
    "web-soft": ("#eceeff", "#22254a"),
    "danger": ("#b3261e", "#ff8a80"),
}
UX_LIGHT_ONLY = {
    "draft": "#6b5200",
    "draft-soft": "#fff3c4",
    "flag-line": "#e3843a",
}

PAIRS = [
    ("ink", "page"),
    ("ink", "surface"),
    ("ink", "surface-2"),
    ("muted", "page"),
    ("muted", "surface"),
    ("muted", "surface-2"),
    ("on-primary", "primary"),
    ("on-primary-soft", "primary-soft"),
    ("primary", "surface"),
    ("primary", "page"),
    ("flag", "flag-soft"),
    ("ok", "ok-soft"),
    ("draft", "draft-soft"),
    ("web", "web-soft"),
    ("danger", "surface"),
    ("danger", "danger-soft"),
    ("surface", "danger"),
]


def relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def parse_tokens(css: str) -> tuple[dict, dict]:
    light, dark = {}, {}
    for name, light_value, dark_value in TOKEN.findall(css):
        light[name], dark[name] = light_value, dark_value
    return light, dark


def test_contrast_helper():
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#0e7a64", "#0e7a64") == pytest.approx(1)


def test_tokens_match_ux_md():
    light, dark = parse_tokens(CSS.read_text())
    for name, (light_value, dark_value) in UX_COLOURS.items():
        assert light[name] == light_value, name
        assert dark[name] == dark_value, name
    for name, light_value in UX_LIGHT_ONLY.items():
        assert light[name] == light_value, name
        assert name in dark


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_text_contrast_meets_aa(theme, fg, bg):
    tokens = dict(zip(("light", "dark"), parse_tokens(CSS.read_text())))[theme]
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= 4.5, f"{theme}: {fg} on {bg} is {ratio:.2f}:1"


def test_theme_selectors_present():
    css = CSS.read_text()
    assert ':root[data-theme="light"]' in css
    assert ':root[data-theme="dark"]' in css
    assert "color-scheme: light dark" in css


def test_fonts_bundled():
    css = CSS.read_text()
    for name in ("inter-latin-wght-normal.woff2", "raleway-latin-wght-normal.woff2"):
        assert (FONTS / name).is_file()
        assert f"../fonts/{name}" in css
