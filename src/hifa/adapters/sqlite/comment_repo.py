"""`CommentRepo` — `comment` (SPEC-030 §2.4)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from hifa.adapters.sqlite._base import BaseRepo, dt_text, row_dict, to_dt
from hifa.domain.entities import Comment
from hifa.domain.errors import NotFound
from hifa.ports import Clock, IdGen

COLUMNS = "id, issue_id, parent_id, author_id, body, resolved_at, edited_at, created_at, updated_at"


def comment_from_row(row: sqlite3.Row | dict[str, Any]) -> Comment:
    """Map a `comment` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Comment(
        id=data["id"],
        issue_id=data["issue_id"],
        parent_id=data["parent_id"],
        author_id=data["author_id"],
        body=data["body"],
        resolved_at=to_dt(data["resolved_at"]),
        edited_at=to_dt(data["edited_at"]),
        created_at=to_dt(data["created_at"]),
        updated_at=to_dt(data["updated_at"]),
    )


class CommentRepo(BaseRepo):
    """Comment repository; never opens connections nor deletes rows (R11)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    def get(self, comment_id: str) -> Comment | None:
        row = self._one(f"SELECT {COLUMNS} FROM comment WHERE id = ?", (comment_id,))
        return None if row is None else comment_from_row(row)

    def require(self, comment_id: str) -> Comment:
        comment = self.get(comment_id)
        if comment is None:
            raise NotFound(resource="comment", key=comment_id)
        return comment

    def list_for_issue(self, issue_id: str, *, include_resolved: bool = True) -> list[Comment]:
        sql = f"SELECT {COLUMNS} FROM comment WHERE issue_id = ?"
        if not include_resolved:
            sql += " AND resolved_at IS NULL"
        sql += " ORDER BY created_at, id"
        return [comment_from_row(row) for row in self._all(sql, (issue_id,))]

    def insert(self, comment: Comment) -> Comment:
        self._guard_write()
        row = self._one(
            f"INSERT INTO comment ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                comment.id,
                comment.issue_id,
                comment.parent_id,
                comment.author_id,
                comment.body,
                dt_text(comment.resolved_at),
                dt_text(comment.edited_at),
                dt_text(comment.created_at),
                dt_text(comment.updated_at),
            ),
        )
        return comment_from_row(row)

    def update_body(self, comment_id: str, body: str, at: datetime) -> Comment:
        self._guard_write()
        row = self._one(
            "UPDATE comment SET body = ?, edited_at = ?, updated_at = ? WHERE id = ? RETURNING *",
            (body, dt_text(at), dt_text(at), comment_id),
        )
        if row is None:
            raise NotFound(resource="comment", key=comment_id)
        return comment_from_row(row)

    def resolve(self, comment_id: str, at: datetime) -> None:
        """Idempotent: only sets `resolved_at` when it was NULL."""
        self._guard_write()
        self._conn.execute(
            "UPDATE comment SET resolved_at = ?, updated_at = ?"
            " WHERE id = ? AND resolved_at IS NULL",
            (dt_text(at), dt_text(at), comment_id),
        )

    def unresolve(self, comment_id: str) -> None:
        self._guard_write()
        self._conn.execute("UPDATE comment SET resolved_at = NULL WHERE id = ?", (comment_id,))


__all__ = ["COLUMNS", "CommentRepo", "comment_from_row"]
