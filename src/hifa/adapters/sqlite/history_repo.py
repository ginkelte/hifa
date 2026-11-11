"""`HistoryRepo` — `issue_history` (SPEC-030 §2.4, SQL-8)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from hifa.adapters.sqlite._base import BaseRepo, dt_text, to_dt
from hifa.domain.entities import IssueHistory

COLUMNS = "id, issue_id, actor_id, field, old_value, new_value, created_at"


def _serialize(value: Any) -> str | None:
    """Fixed serialization of §2.4: enum→int, bool→'1'/'0', date/datetime→ISO, None→NULL."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return dt_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    return str(value)


class HistoryRepo(BaseRepo):
    """Change history: INSERT and read only; never UPDATE or DELETE."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Any,
        idgen: Any,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    def record(
        self,
        issue_id: str,
        changes: Sequence[tuple[str, Any, Any]],
        *,
        actor_id: str | None,
        now: datetime,
    ) -> int:
        """Insert every change with a single `executemany`; return the inserted row count."""
        self._guard_write()
        if not changes:
            return 0
        created = dt_text(now)
        rows = [
            (issue_id, actor_id, field, _serialize(old), _serialize(new), created)
            for field, old, new in changes
        ]
        self._conn.executemany(
            "INSERT INTO issue_history (issue_id, actor_id, field, old_value, new_value,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)

    def list_for_issue(self, issue_id: str, *, limit: int = 100) -> list[IssueHistory]:
        rows = self._all(
            f"SELECT {COLUMNS} FROM issue_history WHERE issue_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (issue_id, limit),
        )
        return [
            IssueHistory(
                id=int(row["id"]),
                issue_id=row["issue_id"],
                actor_id=row["actor_id"],
                field=row["field"],
                old_value=row["old_value"],
                new_value=row["new_value"],
                created_at=to_dt(row["created_at"]),
            )
            for row in rows
        ]


__all__ = ["COLUMNS", "HistoryRepo"]
