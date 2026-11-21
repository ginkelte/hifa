"""Listing read model: `ListFilters`, `IssuePage` and `list_view`."""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from hifa.adapters.sqlite import state_repo
from hifa.adapters.sqlite._base import to_date, to_dt
from hifa.adapters.sqlite.history_repo import HISTORY_ROW_VISIBLE
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

#: `<sort_key>` expression per `order`, evaluated over `v_issue`. Never built from user
#: input. Absence is `NULL`, never a sentinel: a sentinel is a value and a descending
#: direction would move it to the top, while `NULL` cannot enter the key comparison.
SORT_KEYS: dict[str, str] = {
    "manual": "i.sort_order",
    "created": "i.created_at",
    "updated": "i.updated_at",
    "priority": "CASE i.priority WHEN 0 THEN NULL ELSE i.priority END",
    "due": "i.due_date",
}

#: Values `issue.priority` may hold: `0` is `No priority` and it is a value in the column.
PRIORITY_VALUES: frozenset[int] = frozenset({0, 1, 2, 3, 4})

MAX_LIMIT = 200
MAX_OFFSET = 500
DEFAULT_LIMIT = 50


#: The only collections `issue_detail` loads; any other name is `INCLUDE_INVALID`.
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
    """The whole listing filter: every field is one predicate, families combine with AND.

    Repeated values inside a family (state, state type, priority, label) are a union; the
    adapter de-duplicates them before building the query. `archived_only` supersedes
    `include_archived` and `trashed_only` supersedes `include_trashed` by precedence, and
    the trash is the deeper layer: `trashed_only` makes the archive predicate vacuous so an
    issue archived and then trashed stays reachable.
    """

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
    cursor: str | None = None  # base64url("<order>:<asc|desc>|<ULID>"), opaque
    offset: int = 0  # WEB only, capped at 500
    q: str | None = None  # title substring; LIKE, case-insensitive for ASCII
    state_ids: tuple[str, ...] = ()  # OR: any of these states (union with `state_id`)
    state_types: tuple[str, ...] = ()  # OR: any of these state types (union with `state_type`)
    priorities: tuple[int, ...] = ()  # OR: any of these priorities (union with `priority`)
    is_open: bool | None = None  # True: open only; False: closed only; None: no filter
    no_cycle: bool = False  # cycle_id IS NULL
    archived_only: bool = False  # archived_at IS NOT NULL
    trashed_only: bool = False  # trashed = 1, and the archive axis adds no predicate
    reverse: bool = False  # flips the key and the id tie-breaker, never the absent rows

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
        for value in self.priorities:
            if value not in PRIORITY_VALUES:
                raise Validation(
                    "priority out of range",
                    rule="PRIORITY_OUT_OF_RANGE",
                    field="priorities",
                    value=value,
                )


def sort_token(order: str, reverse: bool = False) -> str:
    """Canonical name of an ordering: `order:dir`, the direction **in lowercase**.

    It is the `sort_key` component of the cursor, not the SQL keyword: uppercase belongs
    to the `ORDER BY` clause and to nothing else.
    """
    return f"{order}:{'desc' if reverse else 'asc'}"


def encode_cursor(sort_key: str, issue_id: str) -> str:
    """`base64url(f'{sort_key}|{issue_id}')` without padding."""
    payload = f"{sort_key}|{issue_id}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


_ORDERING = re.compile(r"^(?P<order>[a-z]+):(?P<direction>asc|desc)$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_ID_CHARS = frozenset(string.ascii_uppercase + string.digits)


def _invalid_cursor(message: str = "invalid cursor") -> Validation:
    return Validation(message, rule="INVALID_CURSOR", field="cursor")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Inverse of `encode_cursor`: `("order:dir", ULID)`; invalid → `INVALID_CURSOR`.

    The token is the only place a page carries its ordering, so the `sort_key` component is
    the canonical ordering name and never the row's value: a cursor presented under another
    order or the other direction is detectable.
    """
    text = cursor or ""
    if "=" in text or not _BASE64URL.match(text):
        raise _invalid_cursor("cursor is not unpadded base64url")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise _invalid_cursor("cursor is not base64url") from exc
    if base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=") != text:
        raise _invalid_cursor("cursor is not canonical base64url")
    parts = raw.split("|")
    if len(parts) != 2:
        raise _invalid_cursor("cursor needs exactly one separator")
    sort_key, issue_id = parts
    if not sort_key or not issue_id:
        raise _invalid_cursor("cursor has an empty component")
    match = _ORDERING.match(sort_key)
    if match is None or match.group("order") not in SORT_KEYS:
        raise _invalid_cursor("cursor names an unknown ordering")
    if len(issue_id) != 26 or any(char not in _ID_CHARS for char in issue_id):
        raise _invalid_cursor("cursor does not carry a 26-character id")
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
    #: `issue.milestone_id` and `project_milestone.name` of the same row, loaded by the join
    #: every projection of this module carries. No v1 write stores an id, so both are `None`
    #: on every row the product produces today; they are read anyway, so a row written by
    #: hand answers what it holds instead of a key that can only ever be `None`.
    milestone_id: str | None = None
    milestone_name: str | None = None


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
       i.project_id, i.cycle_id, i.milestone_id, pm.name AS milestone_name,
       i.due_date, i.estimate, i.parent_id,
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
LEFT JOIN project_milestone pm ON pm.id = i.milestone_id
WHERE {where}
ORDER BY {order_by}
LIMIT :limit_plus_one OFFSET :offset
"""


def _dedupe(values: Iterable[Any]) -> tuple[Any, ...]:
    """Insertion-ordered unique values with `None` dropped: one family, one union."""
    unique: dict[Any, None] = {}
    for value in values:
        if value is not None:
            unique.setdefault(value, None)
    return tuple(unique)


def _bind(prefix: str, values: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
    """`:prefix_0, :prefix_1, ...` and its named parameters (never value interpolation)."""
    names = [f":{prefix}_{index}" for index in range(len(values))]
    params = {f"{prefix}_{index}": value for index, value in enumerate(values)}
    return ", ".join(names), params


def _like(text: str | None) -> str | None:
    """`%term%` for a `LIKE`, or `None` when the term is empty (filter dropped).

    `%`, `_` and the escape character itself are escaped, so `?q=%` matches a literal
    percent sign instead of every issue.
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
        milestone_id=row["milestone_id"],
        milestone_name=row["milestone_name"],
    )


def _visibility_clauses(filters: ListFilters) -> list[str]:
    """The archive and trash axes, in the order the layers stack.

    `archived_only` narrows to the archive slice; `include_archived` opens it; `trashed_only`
    opens it too, because the trash is the deeper layer and an issue archived and then trashed
    has to stay reachable from the trash listing.
    """
    clauses: list[str] = []
    if filters.archived_only:
        clauses.append("i.archived_at IS NOT NULL")
    elif not (filters.include_archived or filters.trashed_only):
        clauses.append("i.archived_at IS NULL")
    if filters.trashed_only:
        clauses.append("i.trashed = 1")
    elif not filters.include_trashed:
        clauses.append("i.trashed = 0")
    return clauses


def _filter_clauses(filters: ListFilters, params: dict[str, Any]) -> list[str]:
    """Every filter family as its own clause; the caller joins them with AND."""
    clauses = _visibility_clauses(filters)

    if filters.is_open is not None:
        clauses.append("i.is_open = :is_open")
        params["is_open"] = int(filters.is_open)

    state_ids = _dedupe((filters.state_id, *filters.state_ids))
    if state_ids:
        placeholders, bound = _bind("state", state_ids)
        clauses.append(f"i.state_id IN ({placeholders})")
        params.update(bound)

    state_types = _dedupe((filters.state_type, *filters.state_types))
    if state_types:
        placeholders, bound = _bind("stype", state_types)
        clauses.append(f"s.type IN ({placeholders})")
        params.update(bound)

    priorities = _dedupe((filters.priority, *filters.priorities))
    if priorities:
        placeholders, bound = _bind("prio", priorities)
        clauses.append(f"i.priority IN ({placeholders})")
        params.update(bound)

    if filters.assignee_id is not None:
        clauses.append("i.assignee_id = :assignee_id")
        params["assignee_id"] = filters.assignee_id
    if filters.unassigned:
        clauses.append("i.assignee_id IS NULL")
    if filters.project_id is not None:
        clauses.append("i.project_id = :project_id")
        params["project_id"] = filters.project_id
    if filters.cycle_id is not None:
        clauses.append("i.cycle_id = :cycle_id")
        params["cycle_id"] = filters.cycle_id
    if filters.no_cycle:
        clauses.append("i.cycle_id IS NULL")
    if filters.parent_id is not None:
        clauses.append("i.parent_id = :parent_id")
        params["parent_id"] = filters.parent_id
    if filters.due_before is not None:
        clauses.append("i.due_date <= :due_before")
        params["due_before"] = filters.due_before.isoformat()

    term = _like(filters.q)
    if term is not None:
        clauses.append(f"i.title LIKE :q ESCAPE '{LIKE_ESCAPE}'")
        params["q"] = term

    label_ids = _dedupe(filters.label_ids)
    if label_ids:
        placeholders, bound = _bind("label", label_ids)
        clauses.append(
            "(SELECT COUNT(DISTINCT il2.label_id) FROM issue_label il2"
            f" WHERE il2.issue_id = i.id AND il2.label_id IN ({placeholders})) = :label_count"
        )
        params.update(bound)
        params["label_count"] = len(label_ids)

    return clauses


def _keyset_clause(
    conn: sqlite3.Connection,
    filters: ListFilters,
    sort_key: str,
    operator: str,
    params: dict[str, Any],
) -> str | None:
    """The predicate of the next page: form A for a valued anchor, form B for an absent one.

    The cursor names its ordering, so a token from another order or the other direction is
    rejected before any lookup. The anchor's key is read by primary key, because the token
    has no room for the value, and an anchor outside the team is rejected instead of
    restarting the listing.
    """
    if not filters.cursor:
        return None
    token, cursor_id = decode_cursor(filters.cursor)
    if token != sort_token(filters.order, filters.reverse):
        raise _invalid_cursor("cursor names another ordering")

    row = conn.execute(
        f"SELECT {sort_key} AS anchor FROM v_issue i"
        " WHERE i.id = :cursor_id AND i.team_id = :team_id",
        {"cursor_id": cursor_id, "team_id": filters.team_id},
    ).fetchone()
    if row is None:
        raise _invalid_cursor("cursor anchor is not in this team")

    params["cursor_id"] = cursor_id
    anchor = row["anchor"]
    if anchor is None:
        return f"({sort_key} IS NULL AND i.id {operator} :cursor_id)"
    params["cursor_key"] = anchor
    return (
        f"(({sort_key} IS NULL) OR ({sort_key} {operator} :cursor_key)"
        f" OR ({sort_key} = :cursor_key AND i.id {operator} :cursor_id))"
    )


def list_view(conn: sqlite3.Connection, filters: ListFilters) -> IssuePage:
    """One query (SQL-4): asks for `limit + 1`, trims to `limit` and reports `has_more`.

    The order is total and has two parts: `(<sort_key> IS NULL) ASC` keeps the rows without
    a value last in both directions, and `<sort_key> {dir}, i.id {dir}` is the part the
    direction inverts. `COUNT(*)` is never executed to paginate.
    """
    sort_key = SORT_KEYS[filters.order]
    direction = "DESC" if filters.reverse else "ASC"
    operator = "<" if filters.reverse else ">"
    params: dict[str, Any] = {
        "team_id": filters.team_id,
        "limit_plus_one": filters.limit + 1,
        "offset": filters.offset,
    }

    clauses = ["i.team_id = :team_id", *_filter_clauses(filters, params)]
    keyset = _keyset_clause(conn, filters, sort_key, operator, params)
    if keyset is not None:
        clauses.append(keyset)

    sql = _SQL.format(
        sort_key=sort_key,
        where="\n  AND ".join(clauses),
        order_by=f"({sort_key} IS NULL) ASC, {sort_key} {direction}, i.id {direction}",
    )
    rows = conn.execute(sql, params).fetchall()

    has_more = len(rows) > filters.limit
    page_rows = rows[: filters.limit]
    items = tuple(_item(row) for row in page_rows)

    next_cursor: str | None = None
    if has_more and page_rows:
        next_cursor = encode_cursor(
            sort_token(filters.order, filters.reverse), str(page_rows[-1]["id"])
        )

    return IssuePage(items=items, next_cursor=next_cursor, has_more=has_more)


# --- board, detail and team reads ---------------------


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
    """Board read model, grouped by state."""

    team_id: str
    group_by: str
    groups: tuple[BoardGroup, ...]


@dataclass(frozen=True, slots=True)
class IssueDetail:
    """Detail read model: the header row plus the collections of one issue.

    `members` maps `member.id` to `display_name` for the authors of the comments and the
    actors of the history of *this* issue (one query, no N+1). The view layer never guesses
    an author: a missing id is rendered as unknown.

    `archived_at` and `trashed` are the archive layers of the row this read already loads, so a
    render decides whether the issue may be written without a second read. `trashed` is `False` on
    every **default** read: a trashed issue answers `None` instead of a model, and the field exists
    so that the gate of a render reads one model and not two. It is `True` only on the answer of
    `include_trashed=True` (D-059), the read the detail page and the panel pass.

    `states` carries the team's non-archived workflow states, ordered by `position` then `name`,
    so the state control of a render offers the columns the team's own list offers, in the same
    order, out of the same read.
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
    #: `issue.id` → `issue.identifier` for the rows of `history` whose `field` is `parent_id`: the
    #: one kind of history value neither `members` nor `states` resolves. Empty when the window
    #: names no parent, which is why the lookup is one statement for the kind and never one per row.
    history_parents: Mapping[str, str] = field(default_factory=dict)
    archived_at: datetime | None = None
    trashed: bool = False
    states: tuple[WorkflowState, ...] = ()
    #: The identifier the caller's reference stood for when it was a key the team has left
    #: behind, spelled as the rotation stored it (`ENG-3`); `None` when the reference named the
    #: issue directly. A render uses it for the one line it says about the rename and never as
    #: the issue's own name, which is `issue.identifier`.
    previous_identifier: str | None = None


#: Columns of an `IssueListItem`, shared by the listing, the board and the detail header.
_ITEM_COLUMNS = """
       i.id, i.identifier, i.title, i.priority, i.priority_label,
       i.state_id, s.name AS state_name, s.type AS state_type, s.color AS state_color,
       i.team_id, t.key AS team_key,
       i.assignee_id, m.display_name AS assignee_name,
       i.project_id, i.cycle_id, i.milestone_id, pm.name AS milestone_name,
       i.due_date, i.estimate, i.parent_id,
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

#: The four joins every projection above needs: the state, the team, the assignee and the
#: milestone of the row, so that one row of `IssueListItem` is one statement.
_ITEM_JOINS = """
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
LEFT JOIN project_milestone pm ON pm.id = i.milestone_id
"""

_ITEM_QUERY = "SELECT " + _ITEM_COLUMNS + "FROM v_issue i" + _ITEM_JOINS + "WHERE {where}\n"

_BOARD_SQL = (
    "SELECT "
    + _ITEM_COLUMNS
    + "FROM v_issue i"
    + _ITEM_JOINS
    + """
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


def _items(rows: list[sqlite3.Row]) -> tuple[IssueListItem, ...]:
    return tuple(_item(row) for row in rows)


def _one_item(conn: sqlite3.Connection, where: str, params: dict[str, Any]) -> IssueListItem | None:
    row = conn.execute(_ITEM_QUERY.format(where=where), params).fetchone()
    return None if row is None else _item(row)


def list_teams(conn: sqlite3.Connection) -> tuple[Team, ...]:
    """Every non-archived team, sorted by name."""
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


def team_by_previous_key(conn: sqlite3.Connection, key: str) -> tuple[Team, str] | None:
    """The team that left `key` behind, with the key as the rotation stored it.

    `team_previous_key` is keyed by team and key, so two teams may have left the same key; the
    most recent rotation answers, and an exact tie of `changed_at` is settled by the greater
    `team_id` so that the answer is the same on every run. The stored spelling travels beside
    the team: a caller renders the rotation's own key, never the one it typed.

    The key is compared with the caller's spelling folded, and the rotation stores it folded
    too, so `upper(:key)` is the whole comparison and `idx_team_previous_key_key` answers it;
    a collation on the column would leave the index unreadable and scan the table.
    """
    from hifa.adapters.sqlite.team_repo import team_from_row

    text = (key or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT t.*, pk.key AS previous_key"
        " FROM team_previous_key pk"
        " JOIN team t ON t.id = pk.team_id"
        " WHERE pk.key = upper(:key)"
        " ORDER BY pk.changed_at DESC, pk.team_id DESC"
        " LIMIT 1",
        {"key": text},
    ).fetchone()
    return None if row is None else (team_from_row(row), str(row["previous_key"]))


def issue_id_by_ref(conn: sqlite3.Connection, ref: str) -> str | None:
    """`issue.id` of the issue a ULID or a current identifier names, `None` when neither does.

    The two direct forms in one statement: the identifier branch folds the caller's spelling
    (`upper(:ref)`), the ULID branch compares what was typed, and each branch is its own index
    seek. This is the detail read's own predicate, projected to the one column a resolution
    needs, so a caller that has to decide which form was typed pays a seek and not a read.
    """
    text = (ref or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT id FROM issue WHERE identifier = upper(:ref) OR id = :ref LIMIT 1",
        {"ref": text},
    ).fetchone()
    return None if row is None else str(row["id"])


def issue_id_by_number(conn: sqlite3.Connection, *, team_id: str, number: int) -> str | None:
    """`issue.id` of the issue numbered `number` inside one team; `None` when the team has none.

    The lookup is the team's half of the identifier, and the caller decides visibility: the detail
    read applies its own trash predicate to the id this answers.
    """
    row = conn.execute(
        "SELECT id FROM issue WHERE team_id = ? AND number = ?", (team_id, number)
    ).fetchone()
    return None if row is None else str(row["id"])


def local_member(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """`(display_name, initials)` of the first active member: the single local viewer."""
    row = conn.execute(
        "SELECT display_name, initials FROM member WHERE is_active = 1 ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row is None:
        return (None, None)
    return (row["display_name"] or None, row["initials"])


def team_counts(conn: sqlite3.Connection, team_id: str) -> dict[str, int]:
    """`{"open", "urgent", "total"}` counters of one team."""
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

    `filters` is the same `ListFilters` of the listing: only the fields the
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


_BLOCKED_SQL = (
    "SELECT "
    + _ITEM_COLUMNS
    + "FROM v_issue i"
    + _ITEM_JOINS
    + """
WHERE (:team_id IS NULL OR i.team_id = :team_id)
  AND i.visible = 1
  AND EXISTS (
        SELECT 1
        FROM issue_relation r
        JOIN issue blocker     ON blocker.id = r.issue_id
        JOIN workflow_state bs ON bs.id = blocker.state_id
        WHERE r.type = 'blocks'
          AND r.related_issue_id = i.id
          AND bs.type NOT IN ('completed', 'canceled')
      )
ORDER BY i.identifier
"""
)


def blocked_view(
    conn: sqlite3.Connection, *, team_id: str | None = None
) -> tuple[IssueListItem, ...]:
    """One query: the visible issues a live `blocks` edge points at, ordered by identifier.

    The edge is read in the direction it is stored — `issue_id` is the blocker and
    `related_issue_id` the issue it holds back — and the release condition is the blocker's
    own state type, so a blocker that reaches `completed` or `canceled` stops holding the
    issue back without touching the edge. `EXISTS` answers "is there at least one blocker"
    instead of joining, so an issue with several blockers is still one row. The projection is
    the listing's, labels included, in the same statement: no N+1 and no `COUNT(*)`.
    `team_id=None` spans the workspace; resolving the reference belongs to the caller.
    """
    rows = conn.execute(_BLOCKED_SQL, {"team_id": team_id}).fetchall()
    return _items(rows)


def _member(conn: sqlite3.Connection, member_id: str | None) -> Member | None:
    from hifa.adapters.sqlite.member_repo import member_from_row

    if not member_id:
        return None
    row = conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
    return None if row is None else member_from_row(row)


#: The team's live columns, in the order a render offers them: an archived state is not one,
#: and two states at the same position come back by name. The projection is the state repo's
#: own column list, so a column added there is a column this read answers without a second edit.
_TEAM_STATES_SQL = (
    f"SELECT {state_repo.COLUMNS}"
    " FROM workflow_state WHERE team_id = ? AND archived_at IS NULL"
    " ORDER BY position, name"
)


def issue_detail(
    conn: sqlite3.Connection,
    ref: str,
    *,
    include: frozenset[str] = frozenset(),
    include_trashed: bool = False,
) -> IssueDetail | None:
    """Detail of one issue by identifier or ULID; `None` when it does not exist.

    The identifier is compared with the caller's spelling folded — `upper(:ref)`, a function of
    the bound parameter — and never with the column collated: `issue.identifier` is written
    normalised (the team's key is uppercased on the way in and the number is digits), so folding
    what the caller typed is the whole of the comparison, and `upper(:ref)` is a constant to the
    planner, which keeps both branches of the `OR` on their own index. `COLLATE NOCASE` on the
    column reads as the same comparison and is not one: it makes neither index usable, and every
    detail read in the tree — `show`, the board card, the issue panel — becomes a scan of `issue`.

    `include` gates the heavy collections (`comments`, `history`, `attachments`). A name outside
    `DETAIL_INCLUDE` is a `Validation(rule="INCLUDE_INVALID")`, not a silent no-op.

    A neighbour the trash layer holds is not part of the answer: a trashed child is absent from
    `children`, a trashed far end from `relations`, and a pointer to a trashed parent answers
    `None`. Those three predicates are unconditional — they describe the neighbours, not the issue
    the caller asked about — while the issue itself is excluded only on a default read. The
    `relations` tuple comes back unordered: how a row reads is a function of the asking end, so
    its order belongs to the caller that holds the far end.

    `include_trashed` is the one declared way to read an issue the trash layer holds (D-059,
    SQL-7 row 1): `False` — the default — binds `:include_trashed = 0`, which reduces the
    header's predicate to `i.trashed = 0`, so a trashed reference answers `None`; `True` binds
    `1`, the `OR` short-circuits and that one predicate drops. It is a parameter of row 1 and
    not a statement of its own, so the same statements run in the same order and the two totals
    of the read (§2.5) do not move either way.

    The model carries everything a render of the issue decides from: the archive flags of the
    row, read from the entity this call already loads, and the team's live workflow states,
    read with one statement of their own. One call is one model, so an edge composes a page, a
    panel or a refusal without opening a second read of the same issue.
    """
    from hifa.adapters.sqlite.comment_repo import comment_from_row
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
        conn,
        "(i.identifier = upper(:ref) OR i.id = :ref) AND (:include_trashed = 1 OR i.trashed = 0)",
        {"ref": text, "include_trashed": int(include_trashed)},
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
        _one_item(conn, "i.id = :parent AND i.trashed = 0", {"parent": row["parent_id"]})
        if row["parent_id"]
        else None
    )
    children = _items(
        conn.execute(
            _ITEM_QUERY.format(where="i.parent_id = :parent AND i.trashed = 0")
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
        other = _one_item(conn, "i.id = :other AND i.trashed = 0", {"other": other_id})
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
                "SELECT h.* FROM issue_history h WHERE h.issue_id = ?"
                f" AND {HISTORY_ROW_VISIBLE}"
                " ORDER BY h.created_at DESC, h.id DESC LIMIT 100",
                (row["id"],),
            ).fetchall()
        )

    history_parents: Mapping[str, str] = {}
    if history:
        parent_ids = {
            side
            for entry in history
            if entry.field == "parent_id"
            for side in (entry.old_value, entry.new_value)
            if side
        }
        if parent_ids:
            placeholders = ", ".join("?" for _ in parent_ids)
            history_parents = {
                item["id"]: item["identifier"]
                for item in conn.execute(
                    f"SELECT id, identifier FROM issue WHERE id IN ({placeholders})",
                    tuple(sorted(parent_ids)),
                ).fetchall()
            }
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

    states = tuple(
        state_repo.state_from_row(item)
        for item in conn.execute(_TEAM_STATES_SQL, (row["team_id"],)).fetchall()
    )

    return IssueDetail(
        issue=header,
        description=row["description"],
        team=team_from_row(team_row),
        state=state_repo.state_from_row(state_row),
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
        history_parents=history_parents,
        archived_at=to_dt(row["archived_at"]),
        trashed=bool(row["trashed"]),
        states=states,
    )


# --- search -----------------------------------------------------------------------------

#: The four messages that send a `MATCH` to the `like` path: an FTS5 engine error, an
#: unreadable image, a build without the module and an index that is not there.
SEARCH_RETRY_TRIGGERS: tuple[str, ...] = (
    "fts5:",
    "malformed",
    "no such module: fts5",
    "no such table",
)

#: A complete identifier and an identifier prefix, the two shapes the ranking bands read.
_IDENT_EXACT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}-[0-9]{1,9}$")
_IDENT_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}-[0-9]{0,9}$")

#: Characters around the matched term in a `like` snippet, and the fallback's length.
LIKE_WINDOW = 60
SHORT_SNIPPET = 120
MARK_START = "<mark>"
MARK_END = "</mark>"


@dataclass(frozen=True, slots=True)
class SearchRow:
    """One hit as the query returned it; `snippet` carries the literal marker pair."""

    issue_id: str
    identifier: str
    title: str
    state_name: str
    state_type: str
    priority: int
    due_date: str | None
    assignee_display_name: str | None
    updated_at: str
    snippet: str
    score: float


def _not(clause: str) -> str:
    return f"NOT ({clause})"


def _search_filter_clauses(filters: Any, params: dict[str, Any]) -> list[str]:
    """Every `AND (…)` of the filters present, as constant fragments with bound values.

    `filters` is the search filter object of the application layer: this module reads its
    attributes and does not import it, which is the direction the layering allows.
    """
    negate = set(getattr(filters, "negate", ()) or ())
    clauses: list[str] = []

    scope = getattr(filters, "is_", None)
    if scope not in {"archived", "trashed", "all"}:
        clauses.append("i.trashed = 0")
        clauses.append("i.archived_at IS NULL")
    if scope == "open":
        clauses.append("s.type NOT IN ('completed','canceled')")
    elif scope == "closed":
        clauses.append("s.type IN ('completed','canceled')")
    elif scope == "archived":
        clauses.append("i.archived_at IS NOT NULL")
    elif scope == "trashed":
        clauses.append("i.trashed = 1")

    team = getattr(filters, "team", None)
    if team:
        params["team_key"] = team
        clause = "i.team_id IN (SELECT id FROM team WHERE key = :team_key)"
        clauses.append(_not(clause) if "team" in negate else clause)

    state = getattr(filters, "state", None)
    if state:
        params["state"] = state
        clause = (
            "EXISTS (SELECT 1 FROM workflow_state ws WHERE ws.id = i.state_id"
            " AND (lower(ws.name) = lower(:state) OR ws.type = lower(:state)))"
        )
        clauses.append(_not(clause) if "state" in negate else clause)

    assignee = getattr(filters, "assignee", None)
    if assignee:
        lowered = assignee.strip().lower()
        if lowered == "me":
            clause = "i.assignee_id = (SELECT id FROM member ORDER BY created_at, id LIMIT 1)"
        elif lowered == "none":
            clause = "i.assignee_id IS NULL"
        else:
            params["assignee"] = assignee
            clause = (
                "i.assignee_id IN (SELECT id FROM member WHERE lower(email) = lower(:assignee)"
                " OR lower(display_name) = lower(:assignee))"
            )
        if "assignee" in negate:
            clause = "i.assignee_id IS NOT NULL" if lowered == "none" else _not(clause)
        clauses.append(clause)

    label = getattr(filters, "label", None)
    if label:
        params["label"] = label
        clause = (
            "EXISTS (SELECT 1 FROM issue_label il JOIN label l ON l.id = il.label_id"
            " WHERE il.issue_id = i.id AND lower(l.name) = lower(:label))"
        )
        clauses.append(_not(clause) if "label" in negate else clause)

    priority = getattr(filters, "priority", None)
    if priority is not None and priority != "":
        params["priority"] = int(priority)
        clauses.append(
            "i.priority <> :priority" if "priority" in negate else "i.priority = :priority"
        )

    due = getattr(filters, "due", None)
    if due:
        if due == "none":
            clauses.append("i.due_date IS NULL")
        elif due == "overdue":
            params["today"] = params.get("today") or ""
            clauses.append("i.due_date < :today AND s.type NOT IN ('completed','canceled')")
        elif due == "today":
            params["today"] = params.get("today") or ""
            clauses.append("i.due_date = :today")
        elif due == "week":
            params["today"] = params.get("today") or ""
            clauses.append("i.due_date BETWEEN :today AND date(:today, '+7 days')")
        elif due[:1] in {">", "<"}:
            params["due_date"] = due[1:]
            clauses.append(f"i.due_date {due[0]} :due_date")
        else:
            params["due_date"] = due
            clauses.append("i.due_date = :due_date")

    return clauses


def _identifier_bounds(text: Any) -> tuple[str, str]:
    """`(exact, prefix)` derived from the predicate's single positive term.

    An identifier is one word, so only a query whose whole text is one term can name one; a
    complete identifier is band 0 and a prefix is band 2. Both are `""` otherwise, which is what
    makes `identifier = ''` and `identifier LIKE ''` always false.
    """
    terms = tuple(getattr(text, "like_terms", ()) or ())
    if len(terms) != 1:
        return "", ""
    candidate = terms[0].rstrip("*").strip()
    exact = candidate.upper() if _IDENT_EXACT_RE.fullmatch(candidate) else ""
    prefix = candidate.upper() + "%" if _IDENT_PREFIX_RE.fullmatch(candidate) else ""
    return exact, prefix


def _fts_order(order: str) -> str:
    if order == "updated":
        return "i.updated_at DESC, i.id ASC"
    if order == "created":
        return "i.created_at DESC, i.id ASC"
    if order == "priority":
        return "CASE i.priority WHEN 0 THEN 5 ELSE i.priority END ASC, i.updated_at DESC, i.id ASC"
    if order == "due":
        return "(i.due_date IS NULL) ASC, i.due_date ASC, i.id ASC"
    return (
        "band ASC, score DESC, CASE i.priority WHEN 0 THEN 5 ELSE i.priority END ASC,"
        " i.updated_at DESC, i.id ASC"
    )


def _plain_order(order: str, *, ranked: bool) -> str:
    if order == "updated":
        return "i.updated_at DESC, i.id ASC"
    if order == "created":
        return "i.created_at DESC, i.id ASC"
    if order == "priority":
        return "CASE i.priority WHEN 0 THEN 5 ELSE i.priority END ASC, i.updated_at DESC, i.id ASC"
    if order == "due":
        return "(i.due_date IS NULL) ASC, i.due_date ASC, i.id ASC"
    return "band ASC, i.updated_at DESC, i.id ASC" if ranked else "i.updated_at DESC, i.id ASC"


#: Both CTEs are materialized: flattened into the join, SQLite probes the FTS5 virtual table
#: once per row of `issue` (an index scan each time, ~68 ms over 10 000 rows) instead of reading
#: the match set once. `AS MATERIALIZED` keeps the text of the query and makes the cost the one
#: the budget describes: one `MATCH` plus one lookup per candidate.
_FTS_CTE = """WITH fts AS MATERIALIZED (
  SELECT rowid AS rid,
         bm25(issue_fts, 10.0, 1.0, 0.0) AS bm,
         snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12) AS snip
  FROM issue_fts
  WHERE issue_fts MATCH :match
),
title_hit AS MATERIALIZED (
  SELECT rowid AS rid FROM issue_fts WHERE issue_fts MATCH :match_title
)
"""

_FTS_COLUMNS = """
       i.id AS issue_id, i.identifier AS identifier, i.title AS title,
       s.name AS state_name, s.type AS state_type, i.priority AS priority,
       i.due_date AS due_date, m.display_name AS assignee_display_name,
       i.updated_at AS updated_at, COALESCE(f.snip, '') AS snippet,
       CASE WHEN i.identifier = :ident_exact THEN 0
            WHEN t.rid IS NOT NULL THEN 1
            WHEN i.identifier LIKE :ident_like ESCAPE '\\' THEN 2
            ELSE 3 END AS band,
       -COALESCE(f.bm, 0.0) AS score
"""

_FTS_FROM = """
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m ON m.id = i.assignee_id
LEFT JOIN fts f ON f.rid = i.rowid
LEFT JOIN title_hit t ON t.rid = i.rowid
WHERE (f.rid IS NOT NULL OR i.identifier LIKE :ident_like ESCAPE '\\')
"""

_PLAIN_COLUMNS = """
       i.id AS issue_id, i.identifier AS identifier, i.title AS title,
       s.name AS state_name, s.type AS state_type, i.priority AS priority,
       i.due_date AS due_date, m.display_name AS assignee_display_name,
       i.updated_at AS updated_at, '' AS snippet,
       {band} AS band, 0.0 AS score
"""

#: The `like` path reads the description too: its snippet is built from the text, not in SQL.
_LIKE_COLUMNS = """
       i.id AS issue_id, i.identifier AS identifier, i.title AS title,
       s.name AS state_name, s.type AS state_type, i.priority AS priority,
       i.due_date AS due_date, m.display_name AS assignee_display_name,
       i.updated_at AS updated_at, i.description AS description, '' AS snippet,
       {band} AS band, 0.0 AS score
"""

_PLAIN_FROM = """
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m ON m.id = i.assignee_id
WHERE 1 = 1
"""


def _where(base: str, clauses: list[str]) -> str:
    return base.rstrip() + "".join(f"\n  AND {clause}" for clause in clauses)


def _count(conn: sqlite3.Connection, cte: str, from_where: str, params: dict[str, Any]) -> int:
    row = conn.execute(f"{cte}SELECT COUNT(DISTINCT i.rowid){from_where}", params).fetchone()
    return int(row[0]) if row is not None else 0


def _escape_like(value: str) -> str:
    """Escapes `\\`, `%` and `_` so a term is a literal inside a `LIKE` with `ESCAPE '\\'`."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_bindings(terms: tuple[str, ...], prefix: str) -> dict[str, Any]:
    return {f"{prefix}{index}": f"%{_escape_like(term)}%" for index, term in enumerate(terms)}


def _marked(text: str, start: int, length: int) -> str:
    begin = max(0, start - LIKE_WINDOW)
    end = min(len(text), start + length + LIKE_WINDOW)
    return f"{text[begin:start]}{MARK_START}{text[start : start + length]}{MARK_END}{text[end:]}"


def _like_snippet(title: str, description: str | None, term: str | None) -> str:
    """The snippet of the `like` path: the first occurrence marked, or 120 characters of text.

    `title` is searched first and the description second; with no occurrence, or with no
    positive term at all, the answer is the description truncated to 120 characters, or the
    title when the description is empty.
    """
    if term:
        for haystack in (title, description or ""):
            found = haystack.lower().find(term.lower())
            if found != -1:
                return _marked(haystack, found, len(term))
    fallback = (description or "").strip() or title
    return fallback[:SHORT_SNIPPET]


def _search_row(row: sqlite3.Row | dict[str, Any], *, snippet: str | None = None) -> SearchRow:
    return SearchRow(
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        title=str(row["title"]),
        state_name=str(row["state_name"]),
        state_type=str(row["state_type"]),
        priority=int(row["priority"]),
        due_date=None if row["due_date"] is None else str(row["due_date"]),
        assignee_display_name=(
            None if row["assignee_display_name"] is None else str(row["assignee_display_name"])
        ),
        updated_at=str(row["updated_at"]),
        snippet=snippet if snippet is not None else str(row["snippet"] or ""),
        score=float(row["score"]),
    )


def search(
    conn: sqlite3.Connection,
    *,
    text: Any,
    filters: Any,
    order: str,
    limit: int,
    offset: int,
    count: bool,
    today: str,
) -> tuple[list[SearchRow], int | None, Literal["fts", "like", "none"]]:
    """Runs one search and answers `(rows, total, backend)`.

    The three text states are decided from the predicate's own fields: a `MATCH` expression is
    the `fts` path, a predicate with terms and no expression is the `like` path (with positives
    or — when every term is negated — with negations only), and a predicate with neither is no
    text at all: the filters alone, `backend="none"`. A `MATCH` that raises one of the four
    messages of `SEARCH_RETRY_TRIGGERS` is retried once through the `like` path.

    `limit` arrives already clamped and `limit + 1` rows are requested, so the caller derives
    `has_more` and trims; `total` is computed only when `count` is set.
    """
    params: dict[str, Any] = {"limit_plus_one": limit + 1, "offset": offset, "today": today or ""}
    clauses = _search_filter_clauses(filters, params)
    ident_exact, ident_like = _identifier_bounds(text)
    params["ident_exact"] = ident_exact
    params["ident_like"] = ident_like

    like_terms = tuple(getattr(text, "like_terms", ()) or ())
    not_terms = tuple(getattr(text, "not_terms", ()) or ())
    match = getattr(text, "match", None)
    match_title = getattr(text, "match_title", None)

    if match is not None:
        params["match"] = match
        params["match_title"] = match_title or match
        from_where = _where(_FTS_FROM, clauses)
        sql = (
            f"{_FTS_CTE}SELECT{_FTS_COLUMNS}{from_where}"
            f"\nORDER BY {_fts_order(order)}\nLIMIT :limit_plus_one OFFSET :offset"
        )
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if not any(trigger in str(exc) for trigger in SEARCH_RETRY_TRIGGERS):
                raise
        else:
            total = _count(conn, _FTS_CTE, from_where, params) if count else None
            return [_search_row(row) for row in rows], total, "fts"

    if like_terms or not_terms:
        return _like_page(
            conn,
            clauses=clauses,
            params=params,
            order=order,
            count=count,
            like_terms=like_terms,
            not_terms=not_terms,
        )

    from_where = _where(_PLAIN_FROM, clauses)
    columns = _PLAIN_COLUMNS.format(band=0)
    sql = (
        f"SELECT{columns}{from_where}\nORDER BY {_plain_order(order, ranked=False)}"
        "\nLIMIT :limit_plus_one OFFSET :offset"
    )
    rows = conn.execute(sql, params).fetchall()
    total = _count(conn, "", from_where, params) if count else None
    return [_search_row(row) for row in rows], total, "none"


def _like_page(
    conn: sqlite3.Connection,
    *,
    clauses: list[str],
    params: dict[str, Any],
    order: str,
    count: bool,
    like_terms: tuple[str, ...],
    not_terms: tuple[str, ...],
) -> tuple[list[SearchRow], int | None, Literal["like"]]:
    """The `like` path: the positives minus the negated ones, or negations alone."""
    bindings = dict(params)
    text_clauses: list[str] = []
    first: str | None = None

    if like_terms:
        bindings.update(_like_bindings(like_terms, "p"))
        for index in range(len(like_terms)):
            text_clauses.append(
                f"(i.title LIKE :p{index} ESCAPE '\\' OR i.description LIKE :p{index} ESCAPE '\\'"
                f" OR i.identifier LIKE :p{index} ESCAPE '\\')"
            )
        first = like_terms[0].rstrip("*")
        bindings["first_prefix"] = f"{_escape_like(first)}%"
        bindings["first_contains"] = f"%{_escape_like(first)}%"
        band = """CASE WHEN i.identifier = :ident_exact THEN 0
            WHEN i.title LIKE :first_prefix ESCAPE '\\' THEN 1
            WHEN i.title LIKE :first_contains ESCAPE '\\' THEN 2
            ELSE 3 END"""
    else:
        band = "0"

    bindings.update(_like_bindings(not_terms, "n"))
    for index in range(len(not_terms)):
        text_clauses.append(
            f"NOT (i.title LIKE :n{index} ESCAPE '\\'"
            f" OR COALESCE(i.description, '') LIKE :n{index} ESCAPE '\\')"
        )

    from_where = _where(_PLAIN_FROM, text_clauses + clauses)
    columns = _LIKE_COLUMNS.format(band=band)
    sql = (
        f"SELECT{columns}{from_where}\nORDER BY {_plain_order(order, ranked=bool(like_terms))}"
        "\nLIMIT :limit_plus_one OFFSET :offset"
    )
    rows = conn.execute(sql, bindings).fetchall()
    total = _count(conn, "", from_where, bindings) if count else None
    return (
        [
            _search_row(row, snippet=_like_snippet(str(row["title"]), _description(row), first))
            for row in rows
        ],
        total,
        "like",
    )


def _description(row: sqlite3.Row) -> str | None:
    """The description of a `like` row: the snippet is built from it and never returned."""
    try:
        value = row["description"]
    except (IndexError, KeyError):  # pragma: no cover - the query always selects it
        return None
    return None if value is None else str(value)


def team_timezone(conn: sqlite3.Connection, *, team_key: str) -> str | None:
    """The IANA zone of one team, or `None` when no team carries that key."""
    row = conn.execute("SELECT timezone FROM team WHERE key = ?", (team_key,)).fetchone()
    if row is None:
        return None
    value = row["timezone"]
    return None if value is None else str(value)


__all__ = [
    "DEFAULT_LIMIT",
    "DETAIL_INCLUDE",
    "MAX_LIMIT",
    "MAX_OFFSET",
    "SEARCH_RETRY_TRIGGERS",
    "SORT_KEYS",
    "Board",
    "BoardGroup",
    "IssueDetail",
    "IssueListItem",
    "IssuePage",
    "LabelRef",
    "ListFilters",
    "SearchRow",
    "board_view",
    "blocked_view",
    "decode_cursor",
    "encode_cursor",
    "issue_detail",
    "issue_id_by_number",
    "issue_id_by_ref",
    "list_teams",
    "local_member",
    "list_view",
    "resolve_filters",
    "search",
    "team_by_previous_key",
    "team_by_ref",
    "team_counts",
    "team_timezone",
]
