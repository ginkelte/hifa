"""`_position_text`: the table cell the CLI prints for a stored position.

The table's only job is the order, so two positions that read differently must not print the
same number (a fixed one decimal printed `4.8 / 4.9 / 4.9` for `4.75 / 4.875 / 4.9375`), and a
column the team has halved below `1e-4` is still a plain decimal: `repr` alone falls back to
`1e-06`, and a guard that looks for a `.` would then answer `1e-06.0`. The helper is driven
directly because it is the whole formatting decision of the table; asserting on `state list`
would only prove that one seed reaches it.
"""

from __future__ import annotations

import pytest

from hifa.cli.commands import state as state_module


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1e-06, "0.000001"),
        (1e-07, "0.0000001"),
        (4.9375, "4.9375"),
        (2.0, "2.0"),
        (None, "–"),
    ],
)
def test_the_position_prints_the_plain_decimal_the_value_needs(
    value: float | None, text: str
) -> None:
    """Never an exponent, never `1e-06.0`, and an integral position keeps its `.0`."""
    assert state_module._position_text(value) == text
