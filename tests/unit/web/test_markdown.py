"""Unit tests of the markdown allowlist (SPEC-070 §2.3.6, R15, REQ-S070-01).

`render_markdown` already returns escaped, allowlisted HTML, so the templates can apply `|safe`.
These tests pin every escape hatch that the spec names: raw HTML, `javascript:` links and
`on*=` attributes.
"""

from __future__ import annotations

import pytest

from hifa.web.markdown import _safe_href, render_markdown

ON_ATTRIBUTE = 'onerror="'


def test_empty_input_renders_nothing() -> None:
    assert render_markdown(None) == ""
    assert render_markdown("   \n ") == ""


def test_raw_html_is_escaped_not_interpreted() -> None:
    html = render_markdown("before <img src=x onerror=alert(1)> after")
    assert "<img" not in html
    assert "&lt;img" in html
    assert ON_ATTRIBUTE not in html


def test_script_tag_never_survives() -> None:
    html = render_markdown("<script>alert(1)</script>")
    assert "<script" not in html
    assert "&lt;script&gt;" in html


def test_javascript_link_loses_the_href() -> None:
    html = render_markdown("[x](javascript:alert(1))")
    assert 'href="javascript:' not in html
    assert "<a" not in html or "href=" not in html


def test_valid_https_link_survives() -> None:
    html = render_markdown("[docs](https://example.com/guide)")
    assert '<a href="https://example.com/guide">docs</a>' in html


def test_an_allowed_tag_drops_every_attribute_but_href() -> None:
    html = render_markdown('[docs](https://example.com "a title")')
    assert 'href="https://example.com"' in html
    assert "title=" not in html
    assert "onmouseover=" not in html


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.com/x",
        "ftp://example.com/file",
        "",
    ],
)
def test_safe_href_rejects_everything_outside_the_allowlist(url: str) -> None:
    assert _safe_href(url) is False


@pytest.mark.parametrize(
    "url",
    ["https://example.com/a", "http://example.com/a?b=1&c=2", "mailto:someone@example.com"],
)
def test_safe_href_accepts_http_https_and_mailto(url: str) -> None:
    assert _safe_href(url) is True
