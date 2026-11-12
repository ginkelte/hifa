"""`LabelRepo` — `label`."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from hifa.adapters.sqlite._base import UNSET, BaseRepo, Unset, dt_text, row_dict, to_bool, to_dt
from hifa.domain.entities import Label
from hifa.domain.errors import NotFound
from hifa.ports import Clock, IdGen

COLUMNS = (
    "id, workspace_id, team_id, parent_id, name, color, description, is_group, group_type,"
    " retired_at"
)

_INSERT = f"INSERT INTO label ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *"
_UPDATE = (
    "UPDATE label SET team_id = ?, parent_id = ?, name = ?, color = ?, description = ?,"
    " is_group = ?, group_type = ?, retired_at = ? WHERE id = ? RETURNING *"
)


def label_from_row(row: sqlite3.Row | dict[str, Any]) -> Label:
    """Map a `label` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Label(
        id=data["id"],
        workspace_id=data["workspace_id"],
        team_id=data["team_id"],
        parent_id=data["parent_id"],
        name=data["name"],
        color=data["color"],
        description=data["description"],
        is_group=to_bool(data["is_group"]),
        group_type=data["group_type"],
        retired_at=to_dt(data["retired_at"]),
    )


class LabelRepo(BaseRepo):
    """Label repository; never opens connections nor deletes rows."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    def get(self, label_id: str) -> Label | None:
        row = self._one(f"SELECT {COLUMNS} FROM label WHERE id = ?", (label_id,))
        return None if row is None else label_from_row(row)

    def require(self, label_id: str) -> Label:
        label = self.get(label_id)
        if label is None:
            raise NotFound(resource="label", key=label_id)
        return label

    def get_by_name(self, workspace_id: str, team_id: str | None, name: str) -> Label | None:
        """Look up by name: the team label first, then the workspace one."""
        if team_id is not None:
            row = self._one(
                f"SELECT {COLUMNS} FROM label WHERE workspace_id = ? AND team_id = ? AND name = ?",
                (workspace_id, team_id, name),
            )
            if row is not None:
                return label_from_row(row)
        row = self._one(
            f"SELECT {COLUMNS} FROM label WHERE workspace_id = ? AND team_id IS NULL AND name = ?",
            (workspace_id, name),
        )
        return None if row is None else label_from_row(row)

    def list(
        self,
        workspace_id: str,
        *,
        team_id: str | None | Unset = UNSET,
        include_retired: bool = False,
    ) -> list[Label]:
        """`UNSET` = all in the workspace; `None` = workspace-only; str = the team's ones."""
        clauses = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if team_id is None:
            clauses.append("team_id IS NULL")
        elif not isinstance(team_id, Unset):
            clauses.append("team_id = ?")
            params.append(team_id)
        if not include_retired:
            clauses.append("retired_at IS NULL")
        sql = f"SELECT {COLUMNS} FROM label WHERE {' AND '.join(clauses)} ORDER BY name"
        return [label_from_row(row) for row in self._all(sql, params)]

    def insert(self, label: Label) -> Label:
        self._guard_write()
        row = self._one(
            _INSERT,
            (
                label.id,
                label.workspace_id,
                label.team_id,
                label.parent_id,
                label.name,
                label.color,
                label.description,
                int(label.is_group),
                None if label.group_type is None else str(label.group_type),
                dt_text(label.retired_at),
            ),
        )
        return label_from_row(row)

    def update(self, label: Label) -> Label:
        self._guard_write()
        row = self._one(
            _UPDATE,
            (
                label.team_id,
                label.parent_id,
                label.name,
                label.color,
                label.description,
                int(label.is_group),
                None if label.group_type is None else str(label.group_type),
                dt_text(label.retired_at),
                label.id,
            ),
        )
        if row is None:
            raise NotFound(resource="label", key=label.id)
        return label_from_row(row)

    def retire(self, label_id: str, at: Any) -> None:
        self._guard_write()
        self._conn.execute("UPDATE label SET retired_at = ? WHERE id = ?", (dt_text(at), label_id))

    def count_uses(self, label_id: str) -> int:
        return int(self._scalar("SELECT COUNT(*) FROM issue_label WHERE label_id = ?", (label_id,)))

    def ids_for_issue(self, issue_id: str) -> tuple[str, ...]:
        rows = self._all(
            "SELECT label_id FROM issue_label WHERE issue_id = ? ORDER BY label_id", (issue_id,)
        )
        return tuple(row[0] for row in rows)


def label_ids(rows: Sequence[sqlite3.Row]) -> tuple[str, ...]:
    """Read helper for tests and read models."""
    return tuple(row[0] for row in rows)


__all__ = ["COLUMNS", "LabelRepo", "label_from_row", "label_ids"]
