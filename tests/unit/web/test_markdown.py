"""Unit tests of the markdown allowlist.

`render_markdown` already returns escaped, allowlisted HTML, so the templates can apply `|safe`.
These tests pin every escape hatch that the spec names: raw HTML, `javascript:` links,
`on*=` attributes and the one `style` an aligned table column carries.
"""

from __future__ import annotations

import re

import pytest

from hifa.web.markdown import ALLOWED_TAGS, _Allowlist, _safe_href, render_markdown

ON_ATTRIBUTE = 'onerror="'

TABLE = "| Name | Qty |\n|:--|:-:|\n| Bolt | 4 |"


def _sanitize(markup: str) -> str:
    """The walker alone, without markdown-it: the layer that decides every tag and attribute."""
    parser = _Allowlist()
    parser.feed(markup)
    parser.close()
    return "".join(parser.parts)


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


def test_a_table_renders_with_its_header_and_its_cells() -> None:
    """SPEC-070 §2.3.6: `MD.enable("table")` turns pipes into a `<table>` of `<th>`/`<td>`."""
    html = render_markdown(TABLE)
    assert "<table>" in html
    assert re.search(r"<th[^>]*>Name</th>", html)
    assert re.search(r"<td[^>]*>Bolt</td>", html)


@pytest.mark.parametrize(
    ("marker", "alignment"),
    [(":--", "left"), (":-:", "center"), ("--:", "right")],
)
def test_each_of_the_three_alignments_survives_as_an_exact_style(
    marker: str, alignment: str
) -> None:
    """One literal per marker, on the header and on the cell: a gap is a silent misalignment."""
    html = render_markdown(f"| Name |\n|{marker}|\n| Bolt |")
    assert f'<th style="text-align:{alignment}">Name</th>' in html
    assert f'<td style="text-align:{alignment}">Bolt</td>' in html


def test_a_style_is_normalized_before_it_is_matched() -> None:
    """`html.parser` hands the value as written; the match is on the stripped, lowercased form."""
    html = _sanitize('<table><tr><td style="  TEXT-ALIGN:CENTER ">Bolt</td></tr></table>')
    assert '<td style="text-align:center">Bolt</td>' in html


def test_the_th_and_the_td_of_a_centered_column_keep_their_alignment() -> None:
    """D-079 (4): `:-:` survives as an exact `style`, on the header and on the cell."""
    html = render_markdown(TABLE)
    assert '<th style="text-align:center">Qty</th>' in html
    assert '<td style="text-align:center">4</td>' in html


def test_a_style_on_any_other_tag_is_dropped() -> None:
    """`style` is allowlisted for `th`/`td` only: on any other tag it goes and the text stays."""
    html = _sanitize('<p style="text-align:center">x</p><a style="text-align:center">y</a>')
    assert "style=" not in html
    assert "<p>x</p>" in html
    assert "<a>y</a>" in html


@pytest.mark.parametrize(
    "style",
    [
        "color:red",
        "text-align:left; background:url(x)",
        "text-align:center;",
        "position:fixed",
        "background:url(javascript:alert(1))",
    ],
)
def test_a_style_outside_the_three_alignments_is_dropped_and_the_cell_survives(
    style: str,
) -> None:
    """The allowlist accepts three literal strings, so a fourth value loses the attribute only."""
    html = _sanitize(f'<table><tr><td style="{style}">Bolt</td></tr></table>')
    assert "style=" not in html
    assert ">Bolt</td>" in html


def test_raw_html_inside_a_cell_stays_escaped() -> None:
    """`html=False` escapes the cell's markup before the walker sees it, exactly as outside one."""
    html = render_markdown("| A | B |\n|---|---|\n| <b>x</b> | 2 |")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>" not in html


def test_every_table_the_walker_emits_is_wrapped_once_in_the_scroll_box() -> None:
    """D-079 (5): one wrapper per table, opened before it and closed right after it."""
    html = render_markdown(TABLE + "\n\n" + TABLE)
    assert '<div class="table-scroll"><table>' in html
    assert html.count('<div class="table-scroll">') == 2
    assert html.count("</table></div>") == 2


def test_the_wrapper_is_the_walkers_own_so_div_is_not_an_allowed_tag() -> None:
    """A `<div>` written by the user is still text: only the walker can produce the wrapper."""
    assert "div" not in ALLOWED_TAGS
    html = render_markdown("before <div>x</div> after")
    assert "<div>x</div>" not in html
    assert "&lt;div&gt;x&lt;/div&gt;" in html
