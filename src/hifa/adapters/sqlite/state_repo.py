"""`StateRepo` — `workflow_state` (SPEC-030 §2.4, SPEC-010 §3)."""

from __future__ import annotations

import sqlite3
from typing import Any

from hifa.adapters.sqlite._base import BaseRepo, dt_text, row_dict, to_bool, to_dt
from hifa.domain.entities import WorkflowState
from hifa.domain.errors import NotFound
from hifa.ports import Clock, IdGen

COLUMNS = "id, team_id, name, type, color, position, is_default, archived_at"

_INSERT = f"INSERT INTO workflow_state ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *"
_UPDATE = (
    "UPDATE workflow_state SET name = ?, type = ?, color = ?, position = ?, is_default = ?,"
    " archived_at = ? WHERE id = ? RETURNING *"
)


def state_from_row(row: sqlite3.Row | dict[str, Any]) -> WorkflowState:
    """Map a `workflow_state` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return WorkflowState(
        id=data["id"],
        team_id=data["team_id"],
        name=data["name"],
        type=data["type"],
        color=data["color"],
        position=float(data["position"]),
        is_default=to_bool(data["is_default"]),
        archived_at=to_dt(data["archived_at"]),
    )


class StateRepo(BaseRepo):
    """Workflow state repository; never opens connections."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    def get(self, state_id: str) -> WorkflowState | None:
        row = self._one(f"SELECT {COLUMNS} FROM workflow_state WHERE id = ?", (state_id,))
        return None if row is None else state_from_row(row)

    def require(self, state_id: str) -> WorkflowState:
        state = self.get(state_id)
        if state is None:
            raise NotFound(resource="workflow_state", key=state_id)
        return state

    def get_by_name(self, team_id: str, name: str) -> WorkflowState | None:
        row = self._one(
            f"SELECT {COLUMNS} FROM workflow_state WHERE team_id = ? AND name = ?",
            (team_id, name),
        )
        return None if row is None else state_from_row(row)

    def list_for_team(self, team_id: str, *, include_archived: bool = False) -> list[WorkflowState]:
        sql = f"SELECT {COLUMNS} FROM workflow_state WHERE team_id = ?"
        if not include_archived:
            sql += " AND archived_at IS NULL"
        sql += " ORDER BY position, name"
        return [state_from_row(row) for row in self._all(sql, (team_id,))]

    def default(self, team_id: str) -> WorkflowState | None:
        row = self._one(
            f"SELECT {COLUMNS} FROM workflow_state"
            " WHERE team_id = ? AND is_default = 1 AND archived_at IS NULL"
            " ORDER BY position LIMIT 1",
            (team_id,),
        )
        return None if row is None else state_from_row(row)

    def next_position(self, team_id: str) -> float:
        """The team `MAX(position) + 1` (0.0 when there are no states)."""
        value = self._scalar(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM workflow_state WHERE team_id = ?",
            (team_id,),
        )
        return float(value)

    def issue_counts(self, team_id: str) -> dict[str, int]:
        """Live issues per state id of one team: `{state_id: count}` (ISSUES column).

        One `LEFT JOIN` so a state without issues reports `0` instead of disappearing.
        """
        rows = self._all(
            "SELECT s.id AS state_id, COUNT(i.id) AS total"
            " FROM workflow_state s"
            " LEFT JOIN issue i ON i.state_id = s.id AND i.archived_at IS NULL AND i.trashed = 0"
            " WHERE s.team_id = ? AND s.archived_at IS NULL"
            " GROUP BY s.id",
            (team_id,),
        )
        return {row["state_id"]: int(row["total"]) for row in rows}

    def insert(self, state: WorkflowState) -> WorkflowState:
        self._guard_write()
        row = self._one(
            _INSERT,
            (
                state.id,
                state.team_id,
                state.name,
                str(state.type),
                state.color,
                state.position,
                int(state.is_default),
                dt_text(state.archived_at),
            ),
        )
        return state_from_row(row)

    def update(self, state: WorkflowState) -> WorkflowState:
        self._guard_write()
        row = self._one(
            _UPDATE,
            (
                state.name,
                str(state.type),
                state.color,
                state.position,
                int(state.is_default),
                dt_text(state.archived_at),
                state.id,
            ),
        )
        if row is None:
            raise NotFound(resource="workflow_state", key=state.id)
        return state_from_row(row)

    def archive(self, state_id: str, at: object) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE workflow_state SET archived_at = ? WHERE id = ?", (dt_text(at), state_id)
        )


__all__ = ["COLUMNS", "StateRepo", "state_from_row"]
