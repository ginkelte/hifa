"""Production implementation of the `IdGen` port."""

from __future__ import annotations

from ulid import ULID

ULID_LENGTH = 26


class UlidGen:
    """Generate 26-character ULIDs, time-ordered and unique.

    Monotonic within the process: if two calls fall in the same millisecond, the
    second ULID is incremented over the previous one, so lexicographic order matches
    creation order (what `ORDER BY id` needs as a tie-breaker).
    """

    __slots__ = ("_last",)

    def __init__(self) -> None:
        self._last: ULID | None = None

    def new(self) -> str:
        """Return a new ULID as `str`."""
        candidate = ULID()
        if self._last is not None and candidate <= self._last:
            candidate = self._last.increment()
        self._last = candidate
        return str(candidate)


#: Alias kept for compatibility; the normative name is `UlidGen`.
UlidIdGen = UlidGen

__all__ = ["ULID_LENGTH", "UlidGen", "UlidIdGen"]
