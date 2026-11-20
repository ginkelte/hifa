"""`HistoryRepo.record`: one `executemany`, one row per change, one instant (REQ-F030-20).

The count is of the **call** and not of the statements SQLite reports: `set_trace_callback` fires
once per row an `executemany` steps, so it cannot tell one batch of three from three batches of
one. The spy below is transparent — everything but `executemany` is the real connection.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC
from typing import Any

from helpers_world import World, make_issue
from hifa.adapters.sqlite.history_repo import HistoryRepo


class _Counting:
    """A connection that records each `executemany` call and hands it to the real one."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.calls: list[tuple[str, list[tuple[Any, ...]]]] = []

    def executemany(self, sql: str, params: Any) -> sqlite3.Cursor:
        rows = list(params)
        self.calls.append((sql, rows))
        return self._conn.executemany(sql, rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_three_changes_are_one_statement_three_rows_and_one_instant(world: World) -> None:
    """The requirement is the batch itself: 1 INSERT, 3 rows, and the same `created_at` in all."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        counting = _Counting(tx.issues.connection)
        repo = HistoryRepo(counting, clock=world.clock, idgen=world.idgen)  # type: ignore[arg-type]
        now = world.clock.now()  # type: ignore[attr-defined]

        inserted = repo.record(
            issue.id,
            [
                ("title", "Old", "New"),
                ("priority", 0, 2),
                ("assignee_id", None, world.members["ana"].id),
            ],
            actor_id=world.members["ana"].id,
            now=now,
        )
        stored = tx.issues.connection.execute(
            "SELECT field, created_at FROM issue_history WHERE issue_id = ? ORDER BY id",
            (issue.id,),
        ).fetchall()

    assert inserted == 3
    assert len(counting.calls) == 1
    sql, rows = counting.calls[0]
    assert sql.lstrip().upper().startswith("INSERT INTO ISSUE_HISTORY")
    assert sql.count("?") == 6  # one placeholder per column, bound once per row
    assert len(rows) == 3
    assert [row["field"] for row in stored] == ["title", "priority", "assignee_id"]
    assert len({row["created_at"] for row in stored}) == 1
    assert stored[0]["created_at"] == now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
