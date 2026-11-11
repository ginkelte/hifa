"""`TeamRepo` — `workspace`, `team`, `team_membership` y `team_previous_key` (SPEC-030 §2.4)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from hifa.adapters.sqlite._base import BaseRepo, dt_text, row_dict, to_bool, to_dt
from hifa.adapters.sqlite.member_repo import MemberRepo, member_from_row
from hifa.adapters.sqlite.state_repo import StateRepo
from hifa.domain.entities import Member, Team, TeamPreviousKey, WorkflowState, Workspace
from hifa.domain.errors import NotFound
from hifa.ports import Clock, IdGen

COLUMNS = (
    "id, workspace_id, key, name, icon, color, description, timezone, visibility, parent_id,"
    " cycles_enabled, cycle_duration, cycle_start_day, issue_estimation_type,"
    " default_issue_estimate, triage_enabled, auto_close_period, archived_at, created_at,"
    " updated_at"
)

_INSERT = (
    f"INSERT INTO team ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
    " ?, ?, ?) RETURNING *"
)

_UPDATE = (
    "UPDATE team SET name = ?, icon = ?, color = ?, description = ?, timezone = ?,"
    " visibility = ?, parent_id = ?, cycles_enabled = ?, cycle_duration = ?,"
    " cycle_start_day = ?, issue_estimation_type = ?, default_issue_estimate = ?,"
    " triage_enabled = ?, auto_close_period = ?, archived_at = ?, updated_at = ?"
    " WHERE id = ? RETURNING *"
)


def team_from_row(row: sqlite3.Row | dict[str, Any]) -> Team:
    """Map a `team` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Team(
        id=data["id"],
        workspace_id=data["workspace_id"],
        key=data["key"],
        name=data["name"],
        icon=data["icon"],
        color=data["color"],
        description=data["description"],
        timezone=data["timezone"],
        visibility=data["visibility"],
        parent_id=data["parent_id"],
        cycles_enabled=to_bool(data["cycles_enabled"]),
        cycle_duration=int(data["cycle_duration"]),
        cycle_start_day=int(data["cycle_start_day"]),
        issue_estimation_type=data["issue_estimation_type"],
        default_issue_estimate=data["default_issue_estimate"],
        triage_enabled=to_bool(data["triage_enabled"]),
        auto_close_period=data["auto_close_period"],
        archived_at=to_dt(data["archived_at"]),
        created_at=to_dt(data["created_at"]),
        updated_at=to_dt(data["updated_at"]),
    )


def workspace_from_row(row: sqlite3.Row | dict[str, Any]) -> Workspace:
    """Map a `workspace` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Workspace(
        id=data["id"],
        name=data["name"],
        url_key=data["url_key"],
        created_at=to_dt(data["created_at"]),
    )


class TeamRepo(BaseRepo):
    """Team, workspace, state and membership repository; never opens connections."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    # --- sub-repos over the SAME connection ------------------------------------------
    def _states(self) -> StateRepo:
        return StateRepo(self._conn, clock=self._clock, idgen=self._idgen, readonly=self._readonly)

    def _members(self) -> MemberRepo:
        return MemberRepo(self._conn, clock=self._clock, idgen=self._idgen, readonly=self._readonly)

    # --- workspace -------------------------------------------------------------------
    def workspace(self) -> Workspace | None:
        row = self._one("SELECT id, name, url_key, created_at FROM workspace LIMIT 1")
        return None if row is None else workspace_from_row(row)

    def ensure_workspace(self, *, name: str, url_key: str) -> Workspace:
        """Idempotent by `url_key`: returns the existing one, or creates it."""
        row = self._one(
            "SELECT id, name, url_key, created_at FROM workspace WHERE url_key = ?", (url_key,)
        )
        if row is not None:
            return workspace_from_row(row)
        existing = self.workspace()
        if existing is not None:
            return existing
        self._guard_write()
        new = Workspace(id=self.new_id(), name=name, url_key=url_key, created_at=self.now)
        row = self._one(
            "INSERT INTO workspace (id, name, url_key, created_at) VALUES (?, ?, ?, ?) RETURNING *",
            (new.id, new.name, new.url_key, dt_text(new.created_at)),
        )
        return workspace_from_row(row)

    # --- team ------------------------------------------------------------------------
    def get(self, team_id: str) -> Team | None:
        row = self._one(f"SELECT {COLUMNS} FROM team WHERE id = ?", (team_id,))
        return None if row is None else team_from_row(row)

    def require(self, team_id: str) -> Team:
        team = self.get(team_id)
        if team is None:
            raise NotFound(resource="team", key=team_id)
        return team

    def get_by_key(self, workspace_id: str, key: str) -> Team | None:
        row = self._one(
            f"SELECT {COLUMNS} FROM team WHERE workspace_id = ? AND key = ?",
            (workspace_id, key.strip().upper()),
        )
        return None if row is None else team_from_row(row)

    def list(self, *, include_archived: bool = False) -> list[Team]:
        """Teams ordered by name; archived ones only when `include_archived` (SPEC-030 §2.4)."""
        sql = f"SELECT {COLUMNS} FROM team"
        if not include_archived:
            sql += " WHERE team.archived_at IS NULL"
        sql += " ORDER BY team.name"
        return [team_from_row(row) for row in self._all(sql)]

    def list_with_progress(self, *, include_archived: bool = False) -> list[tuple[Team, int, int]]:
        """Teams with their `v_team_progress` counters in **one** statement (SPEC-050 §2.5.2).

        `LEFT JOIN` + `COALESCE`: a team without issues has no row in the view, so it must
        still come back with zeros instead of disappearing — and the whole list costs one
        query, not one per team (no N+1). Returns `(team, open_count, closed_count)`.
        """
        sql = (
            f"SELECT {COLUMNS}, COALESCE(p.open_count, 0) AS open_count,"
            " COALESCE(p.closed_count, 0) AS closed_count"
            " FROM team LEFT JOIN v_team_progress p ON p.team_id = team.id"
        )
        if not include_archived:
            sql += " WHERE team.archived_at IS NULL"
        sql += " ORDER BY team.name"
        return [
            (team_from_row(row), int(row["open_count"]), int(row["closed_count"]))
            for row in self._all(sql)
        ]

    def insert(self, team: Team) -> Team:
        self._guard_write()
        row = self._one(
            _INSERT,
            (
                team.id,
                team.workspace_id,
                team.key,
                team.name,
                team.icon,
                team.color,
                team.description,
                team.timezone,
                str(team.visibility),
                team.parent_id,
                int(team.cycles_enabled),
                team.cycle_duration,
                team.cycle_start_day,
                str(team.issue_estimation_type),
                team.default_issue_estimate,
                int(team.triage_enabled),
                team.auto_close_period,
                dt_text(team.archived_at),
                dt_text(team.created_at),
                dt_text(team.updated_at),
            ),
        )
        return team_from_row(row)

    def update(self, team: Team) -> Team:
        self._guard_write()
        row = self._one(
            _UPDATE,
            (
                team.name,
                team.icon,
                team.color,
                team.description,
                team.timezone,
                str(team.visibility),
                team.parent_id,
                int(team.cycles_enabled),
                team.cycle_duration,
                team.cycle_start_day,
                str(team.issue_estimation_type),
                team.default_issue_estimate,
                int(team.triage_enabled),
                team.auto_close_period,
                dt_text(team.archived_at),
                dt_text(team.updated_at),
                team.id,
            ),
        )
        if row is None:
            raise NotFound(resource="team", key=team.id)
        return team_from_row(row)

    def archive(self, team_id: str, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE team SET archived_at = ?, updated_at = ? WHERE id = ?",
            (dt_text(at), dt_text(at), team_id),
        )

    def count_open_issues(self, team_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM v_issue WHERE team_id = ? AND is_open = 1 AND visible = 1",
                (team_id,),
            )
        )

    def progress(self, team_id: str) -> dict[str, int]:
        """`{open_count, closed_count, urgent_open}` of one team (`v_team_progress`, SPEC-010 §3.3).

        The keys are the view column names: they are the ones SPEC-050 §2.5.2 fixes for
        `team show --json`. A team without issues has no row in the view: the counters come
        back as zeros, never as `KeyError`.
        """
        row = self._one(
            "SELECT open_count, closed_count, urgent_open FROM v_team_progress WHERE team_id = ?",
            (team_id,),
        )
        if row is None:
            return {"open_count": 0, "closed_count": 0, "urgent_open": 0}
        return {
            "open_count": int(row["open_count"]),
            "closed_count": int(row["closed_count"]),
            "urgent_open": int(row["urgent_open"]),
        }

    def next_number(self, team_id: str) -> int:
        """Shortcut for `IssueRepo.next_number` (SQL-1): the team `MAX(number) + 1`."""
        value = self._scalar(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM issue WHERE team_id = ?", (team_id,)
        )
        return int(value)

    # --- states (delegated to StateRepo, same connection) ----------------------------
    def states(self, team_id: str, *, include_archived: bool = False) -> list[WorkflowState]:
        return self._states().list_for_team(team_id, include_archived=include_archived)

    def get_state(self, state_id: str) -> WorkflowState | None:
        return self._states().get(state_id)

    def get_state_by_name(self, team_id: str, name: str) -> WorkflowState | None:
        return self._states().get_by_name(team_id, name)

    def default_state(self, team_id: str) -> WorkflowState | None:
        return self._states().default(team_id)

    def insert_state(self, state: WorkflowState) -> WorkflowState:
        return self._states().insert(state)

    def update_state(self, state: WorkflowState) -> WorkflowState:
        return self._states().update(state)

    # --- memberships ------------------------------------------------------------------
    def members(self, team_id: str) -> list[Member]:
        rows = self._all(
            "SELECT m.id, m.workspace_id, m.name, m.display_name, m.email, m.initials,"
            " m.avatar_url, m.timezone, m.is_admin, m.is_active, m.created_at"
            " FROM team_membership tm JOIN member m ON m.id = tm.member_id"
            " WHERE tm.team_id = ? ORDER BY tm.sort_order, m.display_name, m.name",
            (team_id,),
        )
        return [member_from_row(row) for row in rows]

    def add_member(
        self,
        team_id: str,
        member_id: str,
        *,
        is_owner: bool = False,
        sort_order: float | None = None,
    ) -> None:
        self._guard_write()
        if sort_order is None:
            sort_order = float(
                self._scalar(
                    "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM team_membership"
                    " WHERE team_id = ?",
                    (team_id,),
                )
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO team_membership (team_id, member_id, is_owner, sort_order)"
            " VALUES (?, ?, ?, ?)",
            (team_id, member_id, int(is_owner), sort_order),
        )

    def membership(self, team_id: str, member_id: str) -> bool:
        row = self._one(
            "SELECT 1 FROM team_membership WHERE team_id = ? AND member_id = ?",
            (team_id, member_id),
        )
        return row is not None

    # --- historical keys (SPEC-010 R3) ----------------------------------------------
    def previous_keys(self, team_id: str) -> tuple[str, ...]:
        rows = self._all(
            "SELECT key FROM team_previous_key WHERE team_id = ? ORDER BY changed_at DESC",
            (team_id,),
        )
        return tuple(row[0] for row in rows)

    def add_previous_key(self, team_id: str, key: str, *, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "INSERT OR IGNORE INTO team_previous_key (team_id, key, changed_at) VALUES (?, ?, ?)",
            (team_id, key.strip().upper(), dt_text(at)),
        )

    def previous_key_rows(self, team_id: str) -> list[TeamPreviousKey]:
        rows = self._all(
            "SELECT team_id, key, changed_at FROM team_previous_key"
            " WHERE team_id = ? ORDER BY changed_at DESC",
            (team_id,),
        )
        return [
            TeamPreviousKey(team_id=row[0], key=row[1], changed_at=to_dt(row[2])) for row in rows
        ]


__all__ = ["COLUMNS", "TeamRepo", "team_from_row", "workspace_from_row"]
