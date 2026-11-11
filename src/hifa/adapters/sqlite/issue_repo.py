"""`IssueRepo` — `issue`, `issue_label` e `issue_relation` (SPEC-030 §2.4/§2.5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from hifa.adapters.sqlite._base import (
    BaseRepo,
    date_text,
    dt_text,
    row_dict,
    to_bool,
    to_date,
    to_dt,
)
from hifa.domain.entities import Issue, IssueRelation
from hifa.domain.errors import NotFound, Validation
from hifa.ports import Clock, IdGen

#: The 31 `issue` columns, in DDL order (SQL-2).
COLUMNS = (
    "id, workspace_id, team_id, number, identifier, title, description, priority,"
    " priority_sort_order, estimate, due_date, state_id, assignee_id, creator_id,"
    " project_id, milestone_id, cycle_id, parent_id, sort_order, board_order,"
    " sub_issue_sort_order, started_at, completed_at, canceled_at, snoozed_until_at,"
    " archived_at, auto_archived_at, trashed, branch_name, created_at, updated_at"
)

_INSERT = f"INSERT INTO issue ({COLUMNS}) VALUES ({', '.join(['?'] * 31)}) RETURNING *"

_UPDATE = (
    "UPDATE issue SET title = ?, description = ?, priority = ?, priority_sort_order = ?,"
    " estimate = ?, due_date = ?, state_id = ?, assignee_id = ?, project_id = ?,"
    " milestone_id = ?, cycle_id = ?, parent_id = ?, sort_order = ?, board_order = ?,"
    " sub_issue_sort_order = ?, started_at = ?, completed_at = ?, canceled_at = ?,"
    " snoozed_until_at = ?, archived_at = ?, auto_archived_at = ?, trashed = ?,"
    " branch_name = ?, updated_at = ? WHERE id = ? RETURNING *"
)

#: `positions()` allowlist (D-019). `backlog` is the alias used by SPEC-040 §5.
SCOPES: dict[str, str] = {
    "sort": "sort_order",
    "backlog": "sort_order",
    "board": "board_order",
    "sub": "sub_issue_sort_order",
}


def issue_from_row(row: sqlite3.Row | dict[str, Any]) -> Issue:
    """Map an `issue`/`v_issue` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return Issue(
        id=data["id"],
        workspace_id=data["workspace_id"],
        team_id=data["team_id"],
        number=int(data["number"]),
        identifier=data["identifier"],
        title=data["title"],
        description=data["description"],
        priority=int(data["priority"]),
        priority_sort_order=float(data["priority_sort_order"]),
        estimate=data["estimate"],
        due_date=to_date(data["due_date"]),
        state_id=data["state_id"],
        assignee_id=data["assignee_id"],
        creator_id=data["creator_id"],
        project_id=data["project_id"],
        milestone_id=data["milestone_id"],
        cycle_id=data["cycle_id"],
        parent_id=data["parent_id"],
        sort_order=float(data["sort_order"]),
        board_order=float(data["board_order"]),
        sub_issue_sort_order=float(data["sub_issue_sort_order"]),
        started_at=to_dt(data["started_at"]),
        completed_at=to_dt(data["completed_at"]),
        canceled_at=to_dt(data["canceled_at"]),
        snoozed_until_at=to_dt(data["snoozed_until_at"]),
        archived_at=to_dt(data["archived_at"]),
        auto_archived_at=to_dt(data["auto_archived_at"]),
        trashed=to_bool(data["trashed"]),
        branch_name=data["branch_name"],
        created_at=to_dt(data["created_at"]),
        updated_at=to_dt(data["updated_at"]),
    )


def relation_from_row(row: sqlite3.Row | dict[str, Any]) -> IssueRelation:
    """Map an `issue_relation` row to the domain entity."""
    data = row_dict(row) if isinstance(row, sqlite3.Row) else dict(row)
    return IssueRelation(
        id=data["id"],
        issue_id=data["issue_id"],
        related_issue_id=data["related_issue_id"],
        type=data["type"],
        created_at=to_dt(data["created_at"]),
    )


class IssueRepo(BaseRepo):
    """Issue, issue label and relation repository; never opens connections."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
    ) -> None:
        super().__init__(conn, clock=clock, idgen=idgen, readonly=readonly)

    # --- reads ----------------------------------------------------------------------
    def get(self, issue_id: str) -> Issue | None:
        row = self._one(f"SELECT {COLUMNS} FROM issue WHERE id = ?", (issue_id,))
        return None if row is None else issue_from_row(row)

    def get_by_identifier(self, identifier: str) -> Issue | None:
        row = self._one(
            f"SELECT {COLUMNS} FROM issue WHERE identifier = ?", (identifier.strip().upper(),)
        )
        return None if row is None else issue_from_row(row)

    def require(self, ref: str) -> Issue:
        """Accept a ULID or `ENG-123`; when missing, `NotFound(resource='issue')`."""
        issue = self.get(ref) or self.get_by_identifier(ref)
        if issue is None:
            raise NotFound(resource="issue", key=ref)
        return issue

    def next_number(self, team_id: str) -> int:
        """SQL-1: `MAX(number) + 1`; runs under the UoW `BEGIN IMMEDIATE`."""
        value = self._scalar(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM issue WHERE team_id = ?", (team_id,)
        )
        return int(value)

    def list_page(self, filters: Any) -> Any:
        """Keyset listing (D-011); delegates to `reads.list_view` (a single query)."""
        from hifa.adapters.sqlite.reads import list_view

        return list_view(self._conn, filters)

    def list_children(self, issue_id: str, *, include_closed: bool = True) -> list[Issue]:
        sql = f"SELECT {COLUMNS} FROM issue WHERE parent_id = ?"
        if not include_closed:
            sql += " AND id IN (SELECT id FROM v_issue WHERE is_open = 1)"
        sql += " ORDER BY sub_issue_sort_order, number"
        return [issue_from_row(row) for row in self._all(sql, (issue_id,))]

    def count_open_children(self, issue_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM v_issue WHERE parent_id = ? AND is_open = 1 AND visible = 1",
                (issue_id,),
            )
        )

    def count_open(self, team_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM v_issue WHERE team_id = ? AND is_open = 1 AND visible = 1",
                (team_id,),
            )
        )

    def ancestor_chain(self, issue_id: str, *, max_depth: int = 32) -> dict[str, str | None]:
        """`{issue_id: parent_id}` from the issue upwards, in a single query."""
        rows = self._all(
            "WITH RECURSIVE up(id, parent_id, depth) AS ("
            "  SELECT id, parent_id, 0 FROM issue WHERE id = ?"
            "  UNION ALL"
            "  SELECT i.id, i.parent_id, up.depth + 1 FROM issue i"
            "  JOIN up ON i.id = up.parent_id WHERE up.depth < ?"
            ") SELECT id, parent_id FROM up ORDER BY depth",
            (issue_id, max_depth),
        )
        return {row[0]: row[1] for row in rows}

    def positions(
        self,
        team_id: str,
        scope: str = "sort",
        parent_id: str | None = None,
        state_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """`[(issue_id, position)]` ascending; `state_id` filters the board (D-019)."""
        column = SCOPES.get(scope)
        if column is None:
            raise Validation("unknown position scope", rule="INVALID_SCOPE", value=scope)
        clauses = ["team_id = ?", "trashed = 0"]
        params: list[Any] = [team_id]
        if scope == "sub":
            clauses.append("parent_id IS ?")
            params.append(parent_id)
        elif scope in ("sort", "backlog"):
            clauses.append("archived_at IS NULL")
        if state_id is not None:
            clauses.append("state_id = ?")
            params.append(state_id)
        sql = (
            f"SELECT id, {column} AS position FROM issue"
            f" WHERE {' AND '.join(clauses)} ORDER BY {column}, id"
        )
        return [(row[0], float(row[1])) for row in self._all(sql, params)]

    # --- writes --------------------------------------------------------------------
    def insert(self, issue: Issue) -> Issue:
        self._guard_write()
        row = self._one(
            _INSERT,
            (
                issue.id,
                issue.workspace_id,
                issue.team_id,
                issue.number,
                issue.identifier,
                issue.title,
                issue.description,
                int(issue.priority),
                issue.priority_sort_order,
                issue.estimate,
                date_text(issue.due_date),
                issue.state_id,
                issue.assignee_id,
                issue.creator_id,
                issue.project_id,
                issue.milestone_id,
                issue.cycle_id,
                issue.parent_id,
                issue.sort_order,
                issue.board_order,
                issue.sub_issue_sort_order,
                dt_text(issue.started_at),
                dt_text(issue.completed_at),
                dt_text(issue.canceled_at),
                dt_text(issue.snoozed_until_at),
                dt_text(issue.archived_at),
                dt_text(issue.auto_archived_at),
                int(issue.trashed),
                issue.branch_name,
                dt_text(issue.created_at),
                dt_text(issue.updated_at),
            ),
        )
        return issue_from_row(row)

    def update(self, issue: Issue) -> Issue:
        """SQL-3: 24 updatable columns (`team_id`/`identifier` never move)."""
        self._guard_write()
        row = self._one(
            _UPDATE,
            (
                issue.title,
                issue.description,
                int(issue.priority),
                issue.priority_sort_order,
                issue.estimate,
                date_text(issue.due_date),
                issue.state_id,
                issue.assignee_id,
                issue.project_id,
                issue.milestone_id,
                issue.cycle_id,
                issue.parent_id,
                issue.sort_order,
                issue.board_order,
                issue.sub_issue_sort_order,
                dt_text(issue.started_at),
                dt_text(issue.completed_at),
                dt_text(issue.canceled_at),
                dt_text(issue.snoozed_until_at),
                dt_text(issue.archived_at),
                dt_text(issue.auto_archived_at),
                int(issue.trashed),
                issue.branch_name,
                dt_text(issue.updated_at),
                issue.id,
            ),
        )
        if row is None:
            raise NotFound(resource="issue", key=issue.id)
        return issue_from_row(row)

    def set_state(self, issue_id: str, state_id: str, *, now: datetime) -> Issue:
        """Touches **only** `state_id` and `updated_at`; milestones belong to SPEC-040 R7."""
        self._guard_write()
        row = self._one(
            "UPDATE issue SET state_id = ?, updated_at = ? WHERE id = ? RETURNING *",
            (state_id, dt_text(now), issue_id),
        )
        if row is None:
            raise NotFound(resource="issue", key=issue_id)
        return issue_from_row(row)

    def set_positions(
        self,
        issue_id: str,
        *,
        sort_order: float | None = None,
        board_order: float | None = None,
        sub_issue_sort_order: float | None = None,
        now: datetime,
    ) -> None:
        """`None` = leave the column untouched (internal allowlist, no value interpolation)."""
        self._guard_write()
        assignments: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("sort_order", sort_order),
            ("board_order", board_order),
            ("sub_issue_sort_order", sub_issue_sort_order),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(float(value))
        assignments.append("updated_at = ?")
        params.append(dt_text(now))
        params.append(issue_id)
        self._conn.execute(f"UPDATE issue SET {', '.join(assignments)} WHERE id = ?", params)

    def archive(self, issue_id: str, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE issue SET archived_at = ?, updated_at = ? WHERE id = ?",
            (dt_text(at), dt_text(at), issue_id),
        )

    def unarchive(self, issue_id: str, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE issue SET archived_at = NULL, updated_at = ? WHERE id = ?",
            (dt_text(at), issue_id),
        )

    def trash(self, issue_id: str, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE issue SET trashed = 1, updated_at = ? WHERE id = ?", (dt_text(at), issue_id)
        )

    def restore(self, issue_id: str, at: datetime) -> None:
        self._guard_write()
        self._conn.execute(
            "UPDATE issue SET trashed = 0, updated_at = ? WHERE id = ?", (dt_text(at), issue_id)
        )

    # --- labels -----------------------------------------------------------------------
    def label_ids(self, issue_id: str) -> tuple[str, ...]:
        rows = self._all(
            "SELECT il.label_id FROM issue_label il JOIN label l ON l.id = il.label_id"
            " WHERE il.issue_id = ? ORDER BY l.name",
            (issue_id,),
        )
        return tuple(row[0] for row in rows)

    def set_labels(self, issue_id: str, label_ids: Sequence[str]) -> None:
        """Diff: DELETE of the leftovers + `executemany` of the new ones (1 + 1)."""
        self._guard_write()
        wanted = list(dict.fromkeys(label_ids))
        current = set(self.label_ids(issue_id))
        keep = set(wanted)
        for stale in sorted(current - keep):
            self._conn.execute(
                "DELETE FROM issue_label WHERE issue_id = ? AND label_id = ?", (issue_id, stale)
            )
        missing = [(issue_id, label_id) for label_id in wanted if label_id not in current]
        if missing:
            self._conn.executemany(
                "INSERT OR IGNORE INTO issue_label (issue_id, label_id) VALUES (?, ?)", missing
            )

    def add_label(self, issue_id: str, label_id: str) -> None:
        self._guard_write()
        self._conn.execute(
            "INSERT OR IGNORE INTO issue_label (issue_id, label_id) VALUES (?, ?)",
            (issue_id, label_id),
        )

    def remove_label(self, issue_id: str, label_id: str) -> bool:
        self._guard_write()
        cursor = self._conn.execute(
            "DELETE FROM issue_label WHERE issue_id = ? AND label_id = ?", (issue_id, label_id)
        )
        return cursor.rowcount > 0

    # --- relations -------------------------------------------------------------------
    def relations(self, issue_id: str) -> list[IssueRelation]:
        rows = self._all(
            "SELECT id, issue_id, related_issue_id, type, created_at FROM issue_relation"
            " WHERE issue_id = ? OR related_issue_id = ? ORDER BY created_at, id",
            (issue_id, issue_id),
        )
        return [relation_from_row(row) for row in rows]

    def add_relation(self, relation: IssueRelation) -> IssueRelation:
        self._guard_write()
        row = self._one(
            "INSERT INTO issue_relation (id, issue_id, related_issue_id, type, created_at)"
            " VALUES (?, ?, ?, ?, ?) RETURNING *",
            (
                relation.id,
                relation.issue_id,
                relation.related_issue_id,
                str(relation.type),
                dt_text(relation.created_at),
            ),
        )
        return relation_from_row(row)

    def remove_relation(self, relation_id: str) -> bool:
        self._guard_write()
        cursor = self._conn.execute("DELETE FROM issue_relation WHERE id = ?", (relation_id,))
        return cursor.rowcount > 0

    def get_relation(self, relation_id: str) -> IssueRelation | None:
        row = self._one(
            "SELECT id, issue_id, related_issue_id, type, created_at FROM issue_relation"
            " WHERE id = ?",
            (relation_id,),
        )
        return None if row is None else relation_from_row(row)


__all__ = ["COLUMNS", "SCOPES", "IssueRepo", "issue_from_row", "relation_from_row"]
