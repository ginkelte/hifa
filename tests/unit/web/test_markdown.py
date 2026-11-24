"""Unit tests of the markdown allowlist.

`render_markdown` already returns escaped, allowlisted HTML, so the templates can apply `|safe`.
These tests pin every escape hatch that the spec names: raw HTML, `javascript:` links,
`on*=` attributes and the one `style` an aligned table column carries.
"""

from __future__ import annotations

import re

import pytest

from hifa.web.markdown import (
    ALLOWED_TAGS,
    _Allowlist,
    _safe_href,
    _site_relative,
    issue_ref_candidates,
    render_markdown,
)

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


# --- the reference autolink (SPEC-070 §2.3.6, R30, D-081) --------------------------------

ENG12 = {"ENG-12": ("ENG", "ENG-12")}
ENG1 = {"ENG-1": ("ENG", "ENG-1")}


def test_the_candidates_of_a_text_are_folded_distinct_and_in_order() -> None:
    """`issue_ref_candidates` folds with `str.upper()` and answers each candidate once."""
    assert issue_ref_candidates("see ENG-12, hifa-103 and ENG-12") == ["ENG-12", "HIFA-103"]
    assert issue_ref_candidates("") == []
    assert issue_ref_candidates(None) == []


def test_the_maximal_run_is_the_candidate_so_a_glued_token_is_one_unresolvable_word() -> None:
    """`HIFA-1034` and `XHIFA-103` are each one candidate and never `HIFA-103` beside a digit."""
    assert issue_ref_candidates("HIFA-1034, XHIFA-103 and HIFA-103") == [
        "HIFA-1034",
        "XHIFA-103",
        "HIFA-103",
    ]
    # `SPEC-050` is not even a candidate: `IDENTIFIER_PATTERN` refuses a number with a leading
    # zero, so the spec's own id never reaches the mapping; `SQL-7` is one and resolves to nothing.
    assert issue_ref_candidates("SPEC-050, SQL-7") == ["SQL-7"]


def test_no_mapping_renders_what_the_function_rendered_before_the_parameter() -> None:
    """`links=None` is revision 44 byte for byte: the candidate stays the text it was."""
    assert render_markdown("x") == "<p>x</p>\n"
    assert render_markdown("see ENG-12") == "<p>see ENG-12</p>\n"
    assert render_markdown("see ENG-12", None) == render_markdown("see ENG-12")
    assert render_markdown("see ENG-12", {}) == render_markdown("see ENG-12")


@pytest.mark.parametrize(
    "markdown",
    [
        "a & b < c > d",
        "&amp; ENG-12 &copy;",
        "**bold** and *it*",
        "> quote\n> more",
        "1. one\n2. two",
        "# h\n\npara",
        "```\ncode ENG-1\n```",
        "| A | B |\n|:-:|---|\n| x | y |",
        "[x](https://e.com) and `c`",
    ],
)
def test_a_mapping_that_resolves_nothing_changes_nothing(markdown: str) -> None:
    """The rewriting path is invisible: a mapping with no match renders revision 44's bytes."""
    assert render_markdown(markdown, {"ENG-999": ("ENG", "ENG-999")}) == render_markdown(markdown)


def test_a_resolved_reference_becomes_an_anchor_of_the_issue_it_names() -> None:
    """`see ENG-12` ⇒ the class, the canonical path, and the author's spelling as the text."""
    assert '<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>' in render_markdown(
        "see ENG-12", ENG12
    )


def test_another_case_resolves_through_the_fold_and_links_the_canonical_href() -> None:
    """`hifa-103` links `/HIFA/issue/HIFA-103`, and the anchor prints `hifa-103`."""
    html = render_markdown("follows hifa-103", {"HIFA-103": ("HIFA", "HIFA-103")})
    assert '<a class="issue-ref" href="/HIFA/issue/HIFA-103">hifa-103</a>' in html


def test_a_candidate_the_mapping_does_not_carry_stays_the_text_it_is() -> None:
    """No anchor, no `href`, no 404: the mapping's absence is the rule, not a 404 to serve."""
    html = render_markdown("blocked by ENG-12 and SPEC-050", ENG12)
    assert '<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>' in html
    assert "SPEC-050" in html
    assert html.count('class="issue-ref"') == 1


def test_a_reference_glued_to_other_characters_is_never_half_linked() -> None:
    """A scanner that split `HIFA-1034` or fired after the `X` is the only way to add an anchor."""
    html = render_markdown("HIFA-1034, XHIFA-103 and HIFA-103", {"HIFA-103": ("HIFA", "HIFA-103")})
    assert html.count('class="issue-ref"') == 1
    assert '<a class="issue-ref" href="/HIFA/issue/HIFA-103">HIFA-103</a>' in html
    assert "HIFA-1034" in html
    assert "XHIFA-103" in html


@pytest.mark.parametrize(
    "markdown",
    ["`ENG-12` and ENG-1", "    ENG-12\n\nENG-1\n", "```\nENG-12\n```\n\nENG-1\n"],
    ids=["code_inline", "code_block", "fence"],
)
def test_nothing_inside_code_is_linked(markdown: str) -> None:
    """§2.3.6: the code keeps its text, and the reference beside it is the only anchor rendered."""
    html = render_markdown(markdown, {**ENG12, **ENG1})
    assert html.count('class="issue-ref"') == 1
    assert '<a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a>' in html
    assert "ENG-12" in html


def test_the_text_of_a_link_the_author_wrote_is_not_linked() -> None:
    """`[ENG-12](https://…)` stays the author's link: the reference beside it is the only anchor."""
    html = render_markdown("[ENG-12](https://example.com/ENG-12) and ENG-1", {**ENG12, **ENG1})
    assert '<a href="https://example.com/ENG-12">ENG-12</a>' in html
    assert html.count('class="issue-ref"') == 1
    assert '<a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a>' in html


def test_a_site_relative_link_keeps_its_anchor_and_loses_the_href() -> None:
    """R15's scheme rule drops `/ENG/list` on every anchor but the one carrying the class."""
    html = render_markdown("[ENG-12](/ENG/list) and ENG-1", {**ENG12, **ENG1})
    assert "<a>ENG-12</a>" in html
    assert html.count('class="issue-ref"') == 1
    assert '<a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a>' in html


def test_a_reference_renders_in_every_text_container_of_the_document() -> None:
    """The scan rewrites a paragraph, a heading, a list item, a blockquote and a table cell."""
    html = render_markdown(
        "# ENG-1\n\na paragraph names ENG-1\n\n- ENG-1\n\n> ## ENG-1\n\n| A |\n|---|\n| ENG-1 |\n",
        ENG1,
    )
    assert html.count('class="issue-ref"') == 5
    assert '<h1><a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a></h1>' in html
    assert '<p>a paragraph names <a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a></p>' in html
    assert '<blockquote>\n<h2><a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a></h2>' in html
    assert '<td><a class="issue-ref" href="/ENG/issue/ENG-1">ENG-1</a></td>' in html


def test_the_class_is_unreachable_from_user_text() -> None:
    """`html=False` escapes an `<a class="issue-ref">` an author writes, so no input grants it."""
    html = render_markdown('<a class="issue-ref" href="/evil">x</a>', ENG12)
    assert 'class="issue-ref"' not in html
    assert "&lt;a" in html


def test_the_walker_keeps_the_class_only_on_an_anchor_and_only_that_literal() -> None:
    """The one new permission: `("a", "class")` with the exact value, and the `href` it grants."""
    html = _sanitize('<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>')
    assert html == '<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>'
    other = _sanitize('<a class="evil" href="/ENG/issue/ENG-12">x</a>')
    assert "class=" not in other
    assert "href=" not in other


@pytest.mark.parametrize(
    "hostile",
    [
        "//evil.example/x",
        "/\\evil.example/x",
        "/\t/evil.example/x",
        "/\r/x",
        "/\n/x",
        "/ /x",
        "/\x00/x",
        "/\x7f/x",
    ],
    ids=[
        "protocol-relative",
        "backslash",
        "tab",
        "carriage-return",
        "line-feed",
        "space",
        "nul",
        "delete",
    ],
)
def test_the_class_never_grants_a_path_of_another_host(hostile: str) -> None:
    """`//host/x`, `/\\host/x` and a stripped TAB/CR/LF/space before the host are another host."""
    assert _site_relative(hostile) is False
    assert _sanitize(f'<a class="issue-ref" href="{hostile}">x</a>') == '<a class="issue-ref">x</a>'


def test_the_class_grants_the_canonical_path_a_mapping_resolved() -> None:
    """The path the renderer writes carries nothing stripped, so the tightened rule keeps it."""
    assert _site_relative("/ENG/issue/ENG-12") is True
    html = render_markdown("see ENG-12", ENG12)
    assert '<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>' in html
