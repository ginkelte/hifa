"""Listing read model: `ListFilters`, `IssuePage` and `list_view` (SPEC-030 §2.6, D-011)."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from hifa.adapters.sqlite._base import to_date, to_dt
from hifa.domain.entities import (
    Attachment,
    Comment,
    Cycle,
    IssueHistory,
    IssueRelation,
    Member,
    Project,
    Team,
    WorkflowState,
)
from hifa.domain.errors import Validation

OrderKey = Literal["manual", "created", "updated", "priority", "due"]

#: `<sort_key>` allowlist per `order` (D-011). Never built from user input.
SORT_KEYS: dict[str, str] = {
    "manual": "i.sort_order",
    "created": "i.created_at",
    "updated": "i.updated_at",
    "priority": "CASE i.priority WHEN 0 THEN 5 ELSE i.priority END",
    "due": "COALESCE(i.due_date, '9999-12-31')",
}

MAX_LIMIT = 200
MAX_OFFSET = 500
DEFAULT_LIMIT = 50

#: The only collections `issue_detail` loads; any other name is `INCLUDE_INVALID` (SPEC-040 §2.2).
DETAIL_INCLUDE: frozenset[str] = frozenset({"comments", "attachments", "history"})

#: Escape character of every `LIKE` in this module (`ESCAPE '\'` in the SQL text).
LIKE_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class LabelRef:
    """Label aggregated onto an issue in the listing (no N+1)."""

    id: str
    name: str
    color: str
    is_group: bool
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class ListFilters:
    """Listing filters; all of them combine with AND (REQ-F030-10)."""

    team_id: str
    state_id: str | None = None
    state_type: str | None = None
    assignee_id: str | None = None
    unassigned: bool = False
    project_id: str | None = None
    cycle_id: str | None = None
    parent_id: str | None = None
    priority: int | None = None
    label_ids: tuple[str, ...] = ()
    due_before: date | None = None
    include_archived: bool = False
    include_trashed: bool = False
    order: OrderKey = "manual"
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None  # base64url(sort_key + "|" + id), opaque
    offset: int = 0  # WEB only (SPEC-070), capped at 500
    q: str | None = None  # title substring (SPEC-070 §2.1.1); LIKE, case-insensitive for ASCII

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_LIMIT:
            raise Validation(
                "limit out of range", rule="LIMIT_RANGE", field="limit", value=self.limit
            )
        if not 0 <= self.offset <= MAX_OFFSET:
            raise Validation(
                "offset out of range", rule="OFFSET_RANGE", field="offset", value=self.offset
            )
        if self.order not in SORT_KEYS:
            raise Validation("unknown order", rule="ORDER_INVALID", field="order", value=self.order)


def encode_cursor(sort_key: str, issue_id: str) -> str:
    """`base64url(f'{sort_key}|{issue_id}')` without padding."""
    payload = f"{sort_key}|{issue_id}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Inverse of `encode_cursor`; invalid cursor → `Validation(rule='INVALID_CURSOR')`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise Validation("invalid cursor", rule="INVALID_CURSOR", field="cursor") from exc
    if "|" not in raw:
        raise Validation("invalid cursor", rule="INVALID_CURSOR", field="cursor")
    sort_key, issue_id = raw.rsplit("|", 1)
    if not sort_key or not issue_id:
        raise Validation("invalid cursor", rule="INVALID_CURSOR", field="cursor")
    return sort_key, issue_id


@dataclass(frozen=True, slots=True)
class IssueListItem:
    """Summary row of the listing/board (no description and no history)."""

    id: str
    identifier: str
    title: str
    priority: int
    priority_label: str
    state_id: str
    state_name: str
    state_type: str
    team_key: str
    assignee_id: str | None
    assignee_name: str | None
    project_id: str | None
    cycle_id: str | None
    due_date: date | None
    estimate: float | None
    parent_id: str | None
    updated_at: datetime
    is_open: bool
    labels: tuple[LabelRef, ...]
    state_color: str = "#6B6F76"
    sort_order: float = 0.0
    board_order: float = 0.0
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssuePage:
    """Keyset page: `next_cursor` is `None` when `has_more` is False."""

    items: tuple[IssueListItem, ...]
    next_cursor: str | None
    has_more: bool


_SQL = """
SELECT i.id, i.identifier, i.title, i.priority, i.priority_label,
       i.state_id, s.name AS state_name, s.type AS state_type, s.color AS state_color,
       i.team_id, t.key AS team_key,
       i.assignee_id, m.display_name AS assignee_name,
       i.project_id, i.cycle_id, i.due_date, i.estimate, i.parent_id,
       i.sort_order, i.board_order, i.created_at,
       i.updated_at, i.is_open, i.visible,
       COALESCE((
         SELECT json_group_array(json_object(
                  'id', l.id, 'name', l.name, 'color', l.color,
                  'is_group', l.is_group, 'parent_id', l.parent_id))
         FROM issue_label il
         JOIN label l ON l.id = il.label_id
         WHERE il.issue_id = i.id
       ), '[]') AS labels_json,
       {sort_key} AS sort_value
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
WHERE i.team_id = :team_id
  AND (:include_archived = 1 OR i.archived_at IS NULL)
  AND (:include_trashed  = 1 OR i.trashed = 0)
  AND (:state_id      IS NULL OR i.state_id = :state_id)
  AND (:state_type    IS NULL OR s.type = :state_type)
  AND (:assignee_id   IS NULL OR i.assignee_id = :assignee_id)
  AND (:unassigned    = 0    OR i.assignee_id IS NULL)
  AND (:project_id    IS NULL OR i.project_id = :project_id)
  AND (:cycle_id      IS NULL OR i.cycle_id = :cycle_id)
  AND (:parent_id     IS NULL OR i.parent_id = :parent_id)
  AND (:priority      IS NULL OR i.priority = :priority)
  AND (:due_before    IS NULL OR i.due_date <= :due_before)
  AND (:label_count   = 0 OR (
        SELECT COUNT(DISTINCT il2.label_id) FROM issue_label il2
        WHERE il2.issue_id = i.id AND il2.label_id IN ({label_placeholders})
      ) = :label_count)
  AND (:q IS NULL OR i.title LIKE :q ESCAPE '\\')
{keyset}
ORDER BY {sort_key}, i.id
LIMIT :limit_plus_one OFFSET :offset
"""


def _like(text: str | None) -> str | None:
    """`%term%` for a `LIKE`, or `None` when the term is empty (filter dropped).

    `%`, `_` and the escape character itself are escaped, so `?q=%` matches a literal
    percent sign instead of every issue (SPEC-070 §2.1.1).
    """
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    escaped = (
        cleaned.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


def _labels(raw: str | None) -> tuple[LabelRef, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover - json_group_array always emits JSON
        return ()
    return tuple(
        LabelRef(
            id=item["id"],
            name=item["name"],
            color=item["color"],
            is_group=bool(item["is_group"]),
            parent_id=item["parent_id"],
        )
        for item in parsed
    )


def _item(row: sqlite3.Row) -> IssueListItem:
    return IssueListItem(
        id=row["id"],
        identifier=row["identifier"],
        title=row["title"],
        priority=int(row["priority"]),
        priority_label=row["priority_label"],
        state_id=row["state_id"],
        state_name=row["state_name"],
        state_type=row["state_type"],
        state_color=row["state_color"] or "#6B6F76",
        team_key=row["team_key"],
        assignee_id=row["assignee_id"],
        assignee_name=row["assignee_name"],
        project_id=row["project_id"],
        cycle_id=row["cycle_id"],
        due_date=to_date(row["due_date"]),
        estimate=row["estimate"],
        parent_id=row["parent_id"],
        updated_at=to_dt(row["updated_at"]),
        is_open=bool(row["is_open"]),
        labels=_labels(row["labels_json"]),
        sort_order=float(row["sort_order"] or 0.0),
        board_order=float(row["board_order"] or 0.0),
        created_at=to_dt(row["created_at"]),
    )


def _cursor_value(order: str, key: str) -> Any:
    """Return the cursor value typed like the ordered column."""
    if order in ("manual", "priority"):
        try:
            return float(key)
        except ValueError as exc:
            raise Validation("invalid cursor", rule="INVALID_CURSOR", field="cursor") from exc
    return key


def list_view(conn: sqlite3.Connection, filters: ListFilters) -> IssuePage:
    """One query (SQL-4): asks for `limit + 1`, trims to `limit` and reports `has_more`."""
    sort_key = SORT_KEYS[filters.order]
    params: dict[str, Any] = {
        "team_id": filters.team_id,
        "include_archived": int(filters.include_archived),
        "include_trashed": int(filters.include_trashed),
        "state_id": filters.state_id,
        "state_type": filters.state_type,
        "assignee_id": filters.assignee_id,
        "unassigned": int(filters.unassigned),
        "project_id": filters.project_id,
        "cycle_id": filters.cycle_id,
        "parent_id": filters.parent_id,
        "priority": filters.priority,
        "due_before": None if filters.due_before is None else filters.due_before.isoformat(),
        "label_count": len(filters.label_ids),
        "q": _like(filters.q),
        "limit_plus_one": filters.limit + 1,
        "offset": filters.offset,
    }

    placeholders: list[str] = []
    for index, label_id in enumerate(filters.label_ids):
        name = f"label_{index}"
        placeholders.append(f":{name}")
        params[name] = label_id
    label_sql = ", ".join(placeholders) if placeholders else "NULL"

    keyset_sql = ""
    cursor: tuple[str, str] | None = None
    if filters.cursor:
        cursor_key, cursor_id = decode_cursor(filters.cursor)
        params["cursor_key"] = _cursor_value(filters.order, cursor_key)
        params["cursor_id"] = cursor_id
        keyset_sql = (
            f"  AND ({sort_key} > :cursor_key"
            f" OR ({sort_key} = :cursor_key AND i.id > :cursor_id))\n"
        )
        cursor = (cursor_key, cursor_id)
    del cursor

    sql = _SQL.format(sort_key=sort_key, label_placeholders=label_sql, keyset=keyset_sql)
    rows = conn.execute(sql, params).fetchall()

    has_more = len(rows) > filters.limit
    page_rows = rows[: filters.limit]
    items = tuple(_item(row) for row in page_rows)

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(str(last["sort_value"]), last["id"])

    return IssuePage(items=items, next_cursor=next_cursor, has_more=has_more)


# --- board, detail and team reads (SPEC-030 §2.6, SPEC-070 §2.1.4) ---------------------


@dataclass(frozen=True, slots=True)
class BoardGroup:
    """One board column: `total` counts every issue, `issues` is capped at the limit."""

    key: str | None
    label: str
    state_type: str | None
    position: float
    total: int
    issues: tuple[IssueListItem, ...]
    color: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Board:
    """Board read model, grouped by state (SPEC-030 §2.6)."""

    team_id: str
    group_by: str
    groups: tuple[BoardGroup, ...]


@dataclass(frozen=True, slots=True)
class IssueDetail:
    """Detail read model: the header row plus the collections of one issue.

    `members` maps `member.id` to `display_name` for the authors of the comments and the
    actors of the history of *this* issue (one query, no N+1). The view layer never guesses
    an author: a missing id is rendered as unknown.
    """

    issue: IssueListItem
    description: str | None
    team: Team
    state: WorkflowState
    assignee: Member | None
    creator: Member | None
    project: Project | None
    cycle: Cycle | None
    parent: IssueListItem | None
    children: tuple[IssueListItem, ...]
    relations: tuple[tuple[IssueRelation, IssueListItem], ...]
    comments: tuple[Comment, ...]
    history: tuple[IssueHistory, ...]
    attachments: tuple[Attachment, ...]
    members: Mapping[str, str] = field(default_factory=dict)


#: Columns of an `IssueListItem`, shared by the listing, the board and the detail header.
_ITEM_COLUMNS = """
       i.id, i.identifier, i.title, i.priority, i.priority_label,
       i.state_id, s.name AS state_name, s.type AS state_type, s.color AS state_color,
       i.team_id, t.key AS team_key,
       i.assignee_id, m.display_name AS assignee_name,
       i.project_id, i.cycle_id, i.due_date, i.estimate, i.parent_id,
       i.sort_order, i.board_order, i.created_at,
       i.updated_at, i.is_open, i.visible,
       COALESCE((
         SELECT json_group_array(json_object(
                  'id', l.id, 'name', l.name, 'color', l.color,
                  'is_group', l.is_group, 'parent_id', l.parent_id))
         FROM issue_label il
         JOIN label l ON l.id = il.label_id
         WHERE il.issue_id = i.id
       ), '[]') AS labels_json
"""

_BOARD_SQL = (
    "SELECT "
    + _ITEM_COLUMNS
    + """
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
WHERE i.team_id = :team_id
  AND i.archived_at IS NULL
  AND i.trashed = 0
  AND (:state_id    IS NULL OR i.state_id = :state_id)
  AND (:assignee_id IS NULL OR i.assignee_id = :assignee_id)
  AND (:project_id  IS NULL OR i.project_id = :project_id)
  AND (:cycle_id    IS NULL OR i.cycle_id = :cycle_id)
  AND (:label_id    IS NULL OR EXISTS (
        SELECT 1 FROM issue_label ilx WHERE ilx.issue_id = i.id AND ilx.label_id = :label_id))
  AND (:q IS NULL OR i.title LIKE :q ESCAPE '\\')
ORDER BY s.position, i.board_order, i.id
"""
)

_ITEM_QUERY = (
    "SELECT "
    + _ITEM_COLUMNS
    + """
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
WHERE {where}
"""
)


def _items(rows: list[sqlite3.Row]) -> tuple[IssueListItem, ...]:
    return tuple(_item(row) for row in rows)


def _one_item(conn: sqlite3.Connection, where: str, params: dict[str, Any]) -> IssueListItem | None:
    row = conn.execute(_ITEM_QUERY.format(where=where), params).fetchone()
    return None if row is None else _item(row)


def list_teams(conn: sqlite3.Connection) -> tuple[Team, ...]:
    """Every non-archived team, sorted by name (SPEC-070 §2.3.1)."""
    from hifa.adapters.sqlite.team_repo import team_from_row

    rows = conn.execute("SELECT * FROM team WHERE archived_at IS NULL ORDER BY name").fetchall()
    return tuple(team_from_row(row) for row in rows)


def team_by_ref(conn: sqlite3.Connection, ref: str) -> Team | None:
    """Resolve a team by ULID or by key (case-insensitive)."""
    from hifa.adapters.sqlite.team_repo import team_from_row

    text = (ref or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT * FROM team WHERE id = :ref OR key = :key OR key = :key_upper LIMIT 1",
        {"ref": text, "key": text, "key_upper": text.upper()},
    ).fetchone()
    return None if row is None else team_from_row(row)


def local_member(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """`(display_name, initials)` of the first active member: the single local viewer."""
    row = conn.execute(
        "SELECT display_name, initials FROM member WHERE is_active = 1 ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row is None:
        return (None, None)
    return (row["display_name"] or None, row["initials"])


def team_counts(conn: sqlite3.Connection, team_id: str) -> dict[str, int]:
    """`{"open", "urgent", "total"}` counters of one team (SPEC-070 §2.3.2)."""
    row = conn.execute(
        "SELECT open_count, closed_count, urgent_open FROM v_team_progress WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if row is None:
        return {"open": 0, "urgent": 0, "total": 0}
    return {
        "open": int(row["open_count"]),
        "urgent": int(row["urgent_open"]),
        "total": int(row["open_count"]) + int(row["closed_count"]),
    }


def resolve_filters(
    conn: sqlite3.Connection,
    team_id: str,
    *,
    state: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
) -> dict[str, str | None]:
    """Names or ULIDs to ids; an unknown value is dropped (`None`), it does not break the page."""
    out: dict[str, str | None] = {"state_id": None, "assignee_id": None, "label_id": None}
    if state:
        row = conn.execute(
            "SELECT id FROM workflow_state WHERE team_id = ? AND (name = ? OR id = ?)"
            " AND archived_at IS NULL",
            (team_id, state, state),
        ).fetchone()
        out["state_id"] = None if row is None else row["id"]
    if assignee:
        row = conn.execute(
            "SELECT id FROM member WHERE display_name = ? OR id = ?",
            (assignee, assignee),
        ).fetchone()
        out["assignee_id"] = None if row is None else row["id"]
    if label:
        row = conn.execute(
            "SELECT id FROM label WHERE name = ? OR id = ?", (label, label)
        ).fetchone()
        out["label_id"] = None if row is None else row["id"]
    return out


def board_view(
    conn: sqlite3.Connection,
    *,
    team_id: str,
    filters: ListFilters | None = None,
    group_by: str = "state",
    limit_per_group: int = 50,
) -> Board:
    """One pass over the visible issues, grouped by `workflow_state.position`.

    `filters` is the same `ListFilters` of the listing (SPEC-040 §2.2): only the fields the
    board understands (`state_id`, `assignee_id`, `label_ids` first element, `project_id`,
    `cycle_id`, `q`) are applied. `limit_per_group` caps each column, not the query.
    """
    filters = filters or ListFilters(team_id=team_id)
    state_id = filters.state_id
    assignee_id = filters.assignee_id
    label_id = filters.label_ids[0] if filters.label_ids else None
    project_id = filters.project_id
    cycle_id = filters.cycle_id
    limit = max(1, min(int(limit_per_group), MAX_LIMIT))
    rows = conn.execute(
        _BOARD_SQL,
        {
            "team_id": team_id,
            "state_id": state_id,
            "assignee_id": assignee_id,
            "project_id": project_id,
            "cycle_id": cycle_id,
            "label_id": label_id,
            "q": _like(filters.q),
        },
    ).fetchall()

    states = conn.execute(
        "SELECT id, name, type, color, position FROM workflow_state"
        " WHERE team_id = ? AND archived_at IS NULL ORDER BY position, id",
        (team_id,),
    ).fetchall()

    buckets: dict[str, list[IssueListItem]] = {row["id"]: [] for row in states}
    for row in rows:
        buckets.setdefault(row["state_id"], []).append(_item(row))

    groups: list[BoardGroup] = []
    for row in states:
        items = buckets.get(row["id"], [])
        groups.append(
            BoardGroup(
                key=row["id"],
                label=row["name"],
                state_type=row["type"],
                position=float(row["position"]),
                total=len(items),
                issues=tuple(items[:limit]),
                color=row["color"],
                truncated=len(items) > limit,
            )
        )
    return Board(team_id=team_id, group_by=group_by, groups=tuple(groups))


def _member(conn: sqlite3.Connection, member_id: str | None) -> Member | None:
    from hifa.adapters.sqlite.member_repo import member_from_row

    if not member_id:
        return None
    row = conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
    return None if row is None else member_from_row(row)


def issue_detail(
    conn: sqlite3.Connection, ref: str, *, include: frozenset[str] = frozenset()
) -> IssueDetail | None:
    """Detail of one issue by identifier or ULID; `None` when it does not exist.

    `include` gates the heavy collections (`comments`, `history`, `attachments`). A name outside
    `DETAIL_INCLUDE` is a `Validation(rule="INCLUDE_INVALID")`, not a silent no-op (SPEC-040 §2.2).
    """
    from hifa.adapters.sqlite.comment_repo import comment_from_row
    from hifa.adapters.sqlite.state_repo import state_from_row
    from hifa.adapters.sqlite.team_repo import team_from_row

    unknown = sorted(set(include) - DETAIL_INCLUDE)
    if unknown:
        raise Validation(
            f"unknown include: {', '.join(unknown)}",
            field="include",
            rule="INCLUDE_INVALID",
            value=",".join(unknown),
        )

    text = (ref or "").strip()
    if not text:
        return None
    header = _one_item(
        conn, "(i.identifier = :ref OR i.id = :ref) AND i.trashed = 0", {"ref": text}
    )
    if header is None:
        return None

    row = conn.execute("SELECT * FROM issue WHERE id = ?", (header.id,)).fetchone()
    if row is None:  # pragma: no cover - the view is built on top of the table
        return None

    team_row = conn.execute("SELECT * FROM team WHERE id = ?", (row["team_id"],)).fetchone()
    state_row = conn.execute(
        "SELECT * FROM workflow_state WHERE id = ?", (row["state_id"],)
    ).fetchone()
    if team_row is None or state_row is None:  # pragma: no cover - FK guarantees both
        return None

    project = None
    if row["project_id"]:
        project_row = conn.execute("SELECT * FROM project WHERE id = ?", (row["project_id"],))
        found = project_row.fetchone()
        project = None if found is None else Project.model_validate(dict(found))
    cycle = None
    if row["cycle_id"]:
        cycle_row = conn.execute("SELECT * FROM cycle WHERE id = ?", (row["cycle_id"],))
        found = cycle_row.fetchone()
        cycle = None if found is None else Cycle.model_validate(dict(found))

    parent = (
        _one_item(conn, "i.id = :parent", {"parent": row["parent_id"]})
        if row["parent_id"]
        else None
    )
    children = _items(
        conn.execute(
            _ITEM_QUERY.format(where="i.parent_id = :parent")
            + "ORDER BY i.sub_issue_sort_order, i.number",
            {"parent": row["id"]},
        ).fetchall()
    )

    relations: list[tuple[IssueRelation, IssueListItem]] = []
    for relation_row in conn.execute(
        "SELECT * FROM issue_relation WHERE issue_id = ? OR related_issue_id = ?",
        (row["id"], row["id"]),
    ).fetchall():
        other_id = (
            relation_row["related_issue_id"]
            if relation_row["issue_id"] == row["id"]
            else relation_row["issue_id"]
        )
        other = _one_item(conn, "i.id = :other", {"other": other_id})
        if other is None:
            continue
        relations.append((IssueRelation.model_validate(dict(relation_row)), other))

    comments: tuple[Comment, ...] = ()
    if "comments" in include:
        comments = tuple(
            comment_from_row(item)
            for item in conn.execute(
                "SELECT * FROM comment WHERE issue_id = ? ORDER BY created_at, id",
                (row["id"],),
            ).fetchall()
        )
    history: tuple[IssueHistory, ...] = ()
    if "history" in include:
        history = tuple(
            IssueHistory.model_validate(dict(item))
            for item in conn.execute(
                "SELECT * FROM issue_history WHERE issue_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT 100",
                (row["id"],),
            ).fetchall()
        )
    attachments: tuple[Attachment, ...] = ()
    if "attachments" in include:
        attachments = tuple(
            Attachment.model_validate(dict(item))
            for item in conn.execute(
                "SELECT * FROM attachment WHERE issue_id = ? ORDER BY created_at",
                (row["id"],),
            ).fetchall()
        )

    members: Mapping[str, str] = {}
    if "comments" in include or "history" in include:
        members = {
            item["id"]: item["display_name"]
            for item in conn.execute(
                "SELECT id, display_name FROM member WHERE id IN ("
                "  SELECT author_id FROM comment WHERE issue_id = ? AND author_id IS NOT NULL"
                "  UNION"
                "  SELECT actor_id FROM issue_history WHERE issue_id = ? AND actor_id IS NOT NULL"
                ")",
                (row["id"], row["id"]),
            ).fetchall()
            if item["display_name"]
        }

    return IssueDetail(
        issue=header,
        description=row["description"],
        team=team_from_row(team_row),
        state=state_from_row(state_row),
        assignee=_member(conn, row["assignee_id"]),
        creator=_member(conn, row["creator_id"]),
        project=project,
        cycle=cycle,
        parent=parent,
        children=children,
        relations=tuple(relations),
        comments=comments,
        history=history,
        attachments=attachments,
        members=members,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DETAIL_INCLUDE",
    "MAX_LIMIT",
    "MAX_OFFSET",
    "SORT_KEYS",
    "Board",
    "BoardGroup",
    "IssueDetail",
    "IssueListItem",
    "IssuePage",
    "LabelRef",
    "ListFilters",
    "board_view",
    "decode_cursor",
    "encode_cursor",
    "issue_detail",
    "list_teams",
    "local_member",
    "list_view",
    "resolve_filters",
    "team_by_ref",
    "team_counts",
]
