"""Fractional float ordering.

`between` only computes: it does not renumber or write. If it returns `RANKING_EXHAUSTED`,
`app/` calls `rebalance` and retries once.
"""

from __future__ import annotations

from collections.abc import Sequence

from hifa.domain.errors import Validation

INITIAL_GAP: float = 1024.0
MIN_GAP: float = 1e-6

#: Gap of an ordering that is seeded `1.0, 2.0, 3.0…` instead of `1024, 2048, 3072…`: the board
#: columns (`workflow_state.position`) keep the ordinal scale
#: of `DEFAULT_STATES` and one decimal of headroom between neighbours. Issues keep `INITIAL_GAP`.
UNIT_GAP: float = 1.0


def between(prev: float | None, next: float | None, *, gap: float = INITIAL_GAP) -> float:
    """Position to insert between `prev` and `next`.

    (None, None) -> `gap`; (None, next) -> next - `gap`; (prev, None) -> prev + `gap`;
    (prev, next) -> (prev + next) / 2. `gap` is only the step used at the two edges: the
    midpoint of an inner slot does not depend on it. The exhaustion check is always `MIN_GAP`.
    """
    if prev is None:
        return gap if next is None else next - gap
    if next is None:
        return prev + gap
    if next <= prev:
        raise Validation(
            "ranking range is inverted",
            rule="RANKING_INVALID_RANGE",
            field="sort_order",
            value=(prev, next),
            details={"prev": prev, "next": next},
            code="RANKING_INVALID_RANGE",
        )
    if next - prev < 2 * MIN_GAP:
        raise Validation(
            "no float gap left between the two positions",
            rule="RANKING_EXHAUSTED",
            field="sort_order",
            value=(prev, next),
            details={"prev": prev, "next": next, "gap": next - prev},
            code="RANKING_EXHAUSTED",
        )
    return (prev + next) / 2


def first(*, gap: float = INITIAL_GAP) -> float:
    """First position of an empty list: `gap`."""
    return gap


def last(values: Sequence[float], *, gap: float = INITIAL_GAP) -> float:
    """`max(values) + gap`; empty list -> `gap`."""
    return gap if not values else max(values) + gap


def needs_rebalance(values: Sequence[float], *, min_gap: float = MIN_GAP) -> bool:
    """True if some consecutive pair (after sorting) is separated by less than `min_gap`."""
    ordered = sorted(values)
    return any(b - a < min_gap for a, b in zip(ordered, ordered[1:], strict=False))


def rebalance(values: Sequence[float], *, gap: float = INITIAL_GAP) -> list[float]:
    """Positions spaced by `gap`, 1..n, preserving the input order.

    Ties are resolved by the order of the received sequence.
    """
    ranked = sorted(range(len(values)), key=lambda i: (values[i], i))
    result: list[float] = [0.0] * len(values)
    for position, index in enumerate(ranked, start=1):
        result[index] = position * gap
    return result


__all__ = [
    "INITIAL_GAP",
    "MIN_GAP",
    "UNIT_GAP",
    "between",
    "first",
    "last",
    "needs_rebalance",
    "rebalance",
]
