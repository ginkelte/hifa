"""Shared test doubles: frozen clock and sequential generator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

BASE_NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


class FrozenClock:
    """Deterministic `Clock`: `now()` always returns the same UTC instant."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or BASE_NOW

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value


class SeqIdGen:
    """Deterministic `IdGen`: fake ULIDs that are unique and ordered."""

    def __init__(self, prefix: str = "01HIFA") -> None:
        self._prefix = prefix
        self._n = 0

    def new(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:020d}"

    @property
    def issued(self) -> int:
        return self._n
