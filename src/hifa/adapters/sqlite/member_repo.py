"""`MemberRepo` — `member`."""

from __future__ import annotations

import sqlite3
from typing import Any

from hifa.adapters.sqlite._base import BaseRepo, dt_text, row_dict, to_bool, to_dt
from hifa.domain.entities import Member
from hifa.domain.errors import NotFound
from hifa.ports import Clock, IdGen

COLUMNS = (
    "id, workspace_id, name, display_name, email, initials, avatar_url, timezone,"
    " is_admin, is_active, created_at"
)

_INSERT = f"INSERT INTO member ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *"
_UPDATE = (
    "UPDATE member SET name = ?, display_name = ?, email = ?, initials = ?, avatar_url = ?,"
    " timezone = ?, is_admin = ?, is_active = ? WHERE id = ? RETURNING *"
)


def member_from_row(row: sqlite3.Row | dict[str, Any]) -> Member:
    """Map a `member` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Member(
        id=data["id"],
        workspace_id=data["workspace_id"],
        name=data["name"],
        display_name=data["display_name"],
        email=data["email"],
        initials=data["initials"],
        avatar_url=data["avatar_url"],
        timezone=data["timezone"],
        is_admin=to_bool(data["is_admin"]),
        is_active=to_bool(data["is_active"]),
        created_at=to_dt(data["created_at"]),
    )


class MemberRepo(BaseRepo):
    """Member repository; never opens connections."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    def get(self, member_id: str) -> Member | None:
        row = self._one(f"SELECT {COLUMNS} FROM member WHERE id = ?", (member_id,))
        return None if row is None else member_from_row(row)

    def require(self, member_id: str) -> Member:
        member = self.get(member_id)
        if member is None:
            raise NotFound(resource="member", key=member_id)
        return member

    def get_by_email(self, email: str) -> Member | None:
        row = self._one(f"SELECT {COLUMNS} FROM member WHERE email = ?", (email,))
        return None if row is None else member_from_row(row)

    def list(self, workspace_id: str, *, include_inactive: bool = False) -> list[Member]:
        sql = f"SELECT {COLUMNS} FROM member WHERE workspace_id = ?"
        if not include_inactive:
            sql += " AND is_active = 1"
        sql += " ORDER BY display_name, name"
        return [member_from_row(row) for row in self._all(sql, (workspace_id,))]

    def insert(self, member: Member) -> Member:
        self._guard_write()
        row = self._one(
            _INSERT,
            (
                member.id,
                member.workspace_id,
                member.name,
                member.display_name,
                member.email,
                member.initials,
                member.avatar_url,
                member.timezone,
                int(member.is_admin),
                int(member.is_active),
                dt_text(member.created_at),
            ),
        )
        return member_from_row(row)

    def update(self, member: Member) -> Member:
        self._guard_write()
        row = self._one(
            _UPDATE,
            (
                member.name,
                member.display_name,
                member.email,
                member.initials,
                member.avatar_url,
                member.timezone,
                int(member.is_admin),
                int(member.is_active),
                member.id,
            ),
        )
        if row is None:
            raise NotFound(resource="member", key=member.id)
        return member_from_row(row)

    def deactivate(self, member_id: str) -> None:
        """`is_active = 0`; never `DELETE`."""
        self._guard_write()
        self._conn.execute("UPDATE member SET is_active = 0 WHERE id = ?", (member_id,))


__all__ = ["COLUMNS", "MemberRepo", "member_from_row"]
