"""The stylesheet's one breakpoint, and the two-column page that has to collapse under it.

A layout rule with no test is not a rule. The detail page lays the description beside a fixed
260 px side, which is right on a desktop and leaves the description 58 px wide on a 390 px phone
— every word on its own line — and nothing at all at 320 px. The mobile block carried the
sidebar, the panel and the board, and forgot the page the reader spends the most time on.

These tests read the stylesheet itself. They cannot prove the page looks right; what they pin is
that the rule which makes it legible is still there, and still inside the breakpoint that
applies on a phone, so that deleting it is a red test instead of a silent regression.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLESHEET = Path(__file__).parents[3] / "src" / "hifa" / "web" / "static" / "css" / "app.css"

#: One breakpoint, and the only one: a rule outside it never applies to a phone.
MOBILE = re.compile(r"@media \(max-width: 900px\) \{(.*?)\n\}", re.S)


def _mobile_blocks() -> str:
    """Every declaration the stylesheet applies below 900 px, concatenated."""
    return "\n".join(MOBILE.findall(STYLESHEET.read_text(encoding="utf-8")))


def test_the_detail_page_stacks_into_one_column_below_the_breakpoint() -> None:
    blocks = _mobile_blocks()

    assert re.search(r"\.detail-body\s*\{[^}]*flex-direction:\s*column", blocks), (
        "the detail's two columns stop collapsing: the description is squeezed beside a "
        "fixed 260 px side"
    )
    assert re.search(r"\.detail-side\s*\{[^}]*width:\s*auto", blocks), (
        "the detail's side keeps its fixed width below the breakpoint"
    )


def test_the_stacked_detail_page_has_no_fixed_pixel_side_left() -> None:
    """A width in pixels below the breakpoint is the bug: there are 320 px to share, not 260."""
    blocks = _mobile_blocks()

    for selector in (r"\.detail-side", r"\.detail-body", r"\.detail\b"):
        for match in re.finditer(rf"{selector}\s*\{{([^}}]*)\}}", blocks):
            declarations = match.group(1)
            assert not re.search(r"\bwidth:\s*\d+px|\bflex:\s*0 0 \d+px", declarations), (
                f"a fixed width survived the breakpoint: {selector} {{{declarations.strip()}}}"
            )


def test_a_wide_code_block_scrolls_inside_the_description() -> None:
    blocks = _mobile_blocks()

    assert re.search(r"\.description pre[^}]*overflow-x:\s*auto", blocks), (
        "a pre wider than the phone pushes the page sideways instead of scrolling itself"
    )
