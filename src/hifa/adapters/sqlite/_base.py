"""Shared base for the SQLite repositories (private to `adapters.sqlite`).

It centralizes what they all share: the `readonly` guard (SPEC-030 §4.1 R6), fixed type
mapping (R8) and translation of `sqlite3.Error` into domain errors (R13).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from hifa.adapters.sqlite.errors import translate_sqlite_error
from hifa.domain.errors import Conflict, Validation
from hifa.ports import Clock, IdGen

T = TypeVar("T")

#: Normative code for write rejection inside a read unit of work (R6).
READ_ONLY_CODE = "READ_ONLY_UOW"


class Unset:
    """Sentinel for 'do not filter by this field'. Not `None`: `None` filters by `IS NULL`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = Unset()


def to_bool(value: Any) -> bool:
    """`INTEGER 0/1` → `bool`."""
    return bool(value)


def to_int_bool(value: bool) -> int:
    """`bool` → `INTEGER 0/1`."""
    return 1 if value else 0


def to_dt(value: str | None) -> datetime | None:
    """`TEXT ISO-8601 Z` → `datetime` aware UTC."""
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_date(value: str | None) -> date | None:
    """`TEXT YYYY-MM-DD` → `date`."""
    return None if value is None else date.fromisoformat(value)


def dt_text(value: datetime | None) -> str | None:
    """`datetime` aware → `TEXT ISO-8601 Z` (seconds, UTC).

    A naive `datetime` is `Validation(rule='NAIVE_DATETIME')` (R8) and is rejected before
    writing: UTC is assumed only when the value declares it.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise Validation("naive datetime", rule="NAIVE_DATETIME")
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_text(value: date | None) -> str | None:
    """`date` → `TEXT YYYY-MM-DD`."""
    return None if value is None else value.isoformat()


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    """`sqlite3.Row` → `dict` (the Row is never returned to the caller)."""
    return dict(row)


class BaseRepo:
    """Constructor and helpers shared by every repo (SPEC-030 §2.4)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._idgen = idgen
        self._readonly = readonly

    @property
    def connection(self) -> sqlite3.Connection:
        """The owning `UnitOfWork` connection (never one of its own)."""
        return self._conn

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def idgen(self) -> IdGen:
        return self._idgen

    @property
    def readonly(self) -> bool:
        """True when the repo lives in a read unit of work."""
        return self._readonly

    @property
    def now(self) -> datetime:
        """Shortcut to `clock.now()` (UTC aware)."""
        return self._clock.now()

    def new_id(self) -> str:
        """Shortcut to `idgen.new()`."""
        return self._idgen.new()

    def _guard_write(self) -> None:
        """R6: repos from `read()` never write. Raised **before** touching SQL."""
        if self._readonly:
            raise Conflict(
                "read-only unit of work",
                rule=READ_ONLY_CODE,
                code=READ_ONLY_CODE,
                details={"operation": "write"},
            )

    def _all(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc) from exc

    def _one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        try:
            return self._conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc) from exc

    def _scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        row = self._one(sql, params)
        return None if row is None else row[0]


__all__ = [
    "READ_ONLY_CODE",
    "BaseRepo",
    "date_text",
    "dt_text",
    "row_dict",
    "to_bool",
    "to_date",
    "to_dt",
    "to_int_bool",
]
