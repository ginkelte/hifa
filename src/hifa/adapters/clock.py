"""Production implementation of the `Clock` port (SPEC-030 §2.7, D-017)."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """System clock: always UTC aware.

    `fixed` exists only for tests and bootstrap utilities: when passed, `now()`
    returns that instant converted to UTC (never the process local time).
    """

    __slots__ = ("_fixed",)

    def __init__(self, fixed: datetime | None = None) -> None:
        if fixed is not None:
            fixed = fixed.replace(tzinfo=UTC) if fixed.tzinfo is None else fixed.astimezone(UTC)
        self._fixed = fixed

    def now(self) -> datetime:
        """Current instant in UTC (`tzinfo is timezone.utc`)."""
        if self._fixed is not None:
            return self._fixed
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return f"SystemClock(fixed={self._fixed!r})"


__all__ = ["SystemClock"]
