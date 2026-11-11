"""`domain.ranking` — the 4 `between` cases, exhaustion and rebalancing (REQ-F020-03/04)."""

from __future__ import annotations

import pytest

from hifa.domain import ranking
from hifa.domain.errors import Validation


def test_constantes() -> None:
    assert ranking.INITIAL_GAP == 1024.0
    assert ranking.MIN_GAP == 1e-6
    assert ranking.UNIT_GAP == 1.0


def test_between_on_the_unit_scale_keeps_the_ordinal_grid() -> None:
    """Board columns (`UNIT_GAP`) stay on `1.0, 2.0, 3.0…`: an edge step is one unit, not 1024."""
    assert ranking.between(None, None, gap=ranking.UNIT_GAP) == 1.0
    assert ranking.between(1.0, None, gap=ranking.UNIT_GAP) == 2.0
    assert ranking.between(None, 3.0, gap=ranking.UNIT_GAP) == 2.0
    assert ranking.between(2.0, 3.0, gap=ranking.UNIT_GAP) == 2.5


def test_last_on_the_unit_scale_appends_one_unit() -> None:
    assert ranking.last([1.0, 2.0, 6.0], gap=ranking.UNIT_GAP) == 7.0
    assert ranking.last([], gap=ranking.UNIT_GAP) == 1.0


@pytest.mark.parametrize(
    ("prev", "next", "expected"),
    [
        (None, None, 1024.0),
        (None, 2048.0, 1024.0),
        (1024.0, None, 2048.0),
        (1024.0, 2048.0, 1536.0),
        (0.0, None, 1024.0),
        (None, 512.0, -512.0),
        (1024.0, 1024.5, 1024.25),
    ],
)
def test_between_four_cases(prev: float | None, next: float | None, expected: float) -> None:
    assert ranking.between(prev, next) == expected


def test_between_rejects_inverted_range() -> None:
    with pytest.raises(Validation) as excinfo:
        ranking.between(2048.0, 1024.0)
    assert excinfo.value.rule == "RANKING_INVALID_RANGE"


def test_between_rejects_degenerate_range() -> None:
    with pytest.raises(Validation) as excinfo:
        ranking.between(1024.0, 1024.0)
    assert excinfo.value.rule == "RANKING_INVALID_RANGE"


def test_between_detects_exhausted_gap() -> None:
    with pytest.raises(Validation) as excinfo:
        ranking.between(1536.0, 1536.0000005)
    assert excinfo.value.rule == "RANKING_EXHAUSTED"
    assert excinfo.value.details["prev"] == 1536.0


def test_between_accepts_gap_of_exactly_two_min_gap() -> None:
    prev = 10.0
    next = 10.0 + 2 * ranking.MIN_GAP
    assert ranking.between(prev, next) == pytest.approx(10.0 + ranking.MIN_GAP)


def test_between_rejects_slightly_smaller_gap() -> None:
    prev = 10.0
    next = 10.0 + 2 * ranking.MIN_GAP * 0.99
    with pytest.raises(Validation) as excinfo:
        ranking.between(prev, next)
    assert excinfo.value.rule == "RANKING_EXHAUSTED"


def test_first_is_initial_gap() -> None:
    assert ranking.first() == ranking.INITIAL_GAP


def test_last_adds_initial_gap_to_the_maximum() -> None:
    assert ranking.last([1024.0, 512.0, 2048.0]) == 3072.0
    assert ranking.last([]) == ranking.INITIAL_GAP


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1024.0, 1024.0000001, 3072.0], True),
        ([1024.0, 2048.0, 3072.0], False),
        ([], False),
        ([1024.0], False),
        ([3072.0, 1024.0, 1024.0000001], True),
    ],
)
def test_needs_rebalance(values: list[float], expected: bool) -> None:
    assert ranking.needs_rebalance(values) is expected


def test_needs_rebalance_with_configurable_min_gap() -> None:
    assert ranking.needs_rebalance([0.0, 0.5], min_gap=1.0) is True
    assert ranking.needs_rebalance([0.0, 0.5], min_gap=0.1) is False


def test_rebalance_repairs_the_degraded_group() -> None:
    degraded = [1024.0, 1024.0000001, 3072.0]
    fixed = ranking.rebalance(degraded)
    assert fixed == [1024.0, 2048.0, 3072.0]
    assert ranking.needs_rebalance(fixed) is False


def test_rebalance_preserves_input_order() -> None:
    assert ranking.rebalance([3072.0, 1024.0, 2048.0]) == [3072.0, 1024.0, 2048.0]


def test_rebalance_with_configurable_gap() -> None:
    assert ranking.rebalance([10.0, 20.0], gap=5.0) == [5.0, 10.0]


def test_rebalance_breaks_ties_by_sequence_order() -> None:
    assert ranking.rebalance([5.0, 5.0, 1.0]) == [2048.0, 3072.0, 1024.0]


def test_rebalance_does_not_mutate_input_and_is_deterministic() -> None:
    values = [1024.0, 2048.0, 3072.0]
    first = ranking.rebalance(values)
    second = ranking.rebalance(values)
    assert first == second
    assert values == [1024.0, 2048.0, 3072.0]


def test_rebalance_of_empty_list() -> None:
    assert ranking.rebalance([]) == []


def test_between_is_deterministic() -> None:
    assert ranking.between(1.0, 3.0) == ranking.between(1.0, 3.0) == 2.0
