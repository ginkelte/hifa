"""Board, listing and detail reads used by the web edge.

Thin functions over `adapters/sqlite/reads.py`: they resolve the team reference (key or ULID)
and translate a missing row into `NotFound`. They contain no SQL and never open a write
transaction: each call borrows one read-only connection and closes it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from hifa.adapters.sqlite import reads
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import Board, IssueDetail, IssueListItem, IssuePage, ListFilters
from hifa.domain.entities import IssueRelation, Team
from hifa.domain.errors import NotFound, Validation

#: Collections a detail read asks for. Owned by `issue_detail`, which rejects any other name.
DETAIL_INCLUDE: frozenset[str] = reads.DETAIL_INCLUDE

#: How a relation row reads from one end, in the order the detail publishes. `blocks` first, its
#: inverse second, then the symmetric types: the same vocabulary the `issue relations` table uses,
#: declared once so that the sort and the section headings cannot drift apart.
RELATION_READINGS: tuple[str, ...] = (
    "blocks",
    "blocked_by",
    "duplicate",
    "related",
    "similar",
)

#: The collections of a detail payload, in the order the object carries them. Every caller renders
#: them in this order, whatever order it asked for them in: two invocations with the same set of
#: requested collections print the same bytes.
DETAIL_COLLECTIONS: tuple[str, ...] = ("comments", "relations", "history", "children")


def _relation_reading(relation: IssueRelation, asking_id: str) -> str:
    """How one stored row reads from `asking_id`: its own type, or the inverse from the far end."""
    direction = "out" if relation.issue_id == asking_id else "in"
    return relation.type.name_as_seen(direction)


def _ordered_relations(
    relations: tuple[tuple[IssueRelation, IssueListItem], ...], asking_id: str
) -> tuple[tuple[IssueRelation, IssueListItem], ...]:
    """The relations in reading order, then by the far end's identifier as text.

    The statement that returns these rows selects them from both ends and carries no order, and
    which name a row reads as depends on the end it is being read from — so the order can only be
    produced here, where the row and its far end are one pair. The comparison is on the identifier
    as text, not on the number: `ENG-20` sorts before `ENG-9`, which is the order the printed
    table shows.
    """
    rank = {name: index for index, name in enumerate(RELATION_READINGS)}

    def key(pair: tuple[IssueRelation, IssueListItem]) -> tuple[int, str]:
        relation, far = pair
        return (rank[_relation_reading(relation, asking_id)], far.identifier)

    return tuple(sorted(relations, key=key))


def detail_collections(detail: IssueDetail) -> dict[str, tuple[object, ...]]:
    """The four collections of a detail, keyed in the object's fixed order.

    The caller picks the ones it renders; the order of the keys is this layer's, so an edge that
    formats them one section at a time does not have to remember it.
    """
    values: dict[str, tuple[object, ...]] = {
        "comments": tuple(detail.comments),
        "relations": tuple(detail.relations),
        "history": tuple(detail.history),
        "children": tuple(detail.children),
    }
    return {name: values[name] for name in DETAIL_COLLECTIONS}


def list_teams(db: Database) -> tuple[Team, ...]:
    """Every visible team, sorted by name (`page.teams`)."""
    conn = db.ro_connection()
    try:
        return reads.list_teams(conn)
    finally:
        conn.close()


def teams_with_counts(db: Database) -> tuple[tuple[Team, int], ...]:
    """Every visible team with its issue count (`open + closed`), one read-only connection.

    The sidebar is built from this on every request, so a team created by the CLI while the
    server runs shows up on the next page without restarting. A database
    that is not ready yields `()` instead of a `500`, exactly like `startup_state`.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = db.ro_connection()
        return tuple(
            (team, reads.team_counts(conn, team.id)["total"]) for team in reads.list_teams(conn)
        )
    except sqlite3.Error:  # pragma: no cover - the database is not ready yet
        return ()
    finally:
        if conn is not None:
            conn.close()


def require_team(db: Database, ref: str) -> Team:
    """Team by key or ULID; `NotFound(team, key=ref)` when it does not exist."""
    conn = db.ro_connection()
    try:
        team = reads.team_by_ref(conn, ref)
    finally:
        conn.close()
    if team is None:
        raise NotFound(resource="team", key=ref)
    return team


def resolve_filters(
    db: Database,
    team: str,
    *,
    state: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
) -> dict[str, str | None]:
    """Filter values (name or ULID) to ids; an unknown value is dropped, not an error."""
    conn = db.ro_connection()
    try:
        resolved = reads.team_by_ref(conn, team)
        if resolved is None:
            raise NotFound(resource="team", key=team)
        return reads.resolve_filters(conn, resolved.id, state=state, assignee=assignee, label=label)
    finally:
        conn.close()


def list_issues(db: Database, filters: ListFilters) -> IssuePage:
    """Keyset page of the listing; `filters.team_id` may be a key or a ULID.

    The page size and the ordering are re-checked here, not only in `ListFilters`: an object
    that skipped its constructor still must not reach the query. The cursor is decoded here
    only to compare its ordering against the token this layer would encode, so a page
    presented under another sort or the other direction is refused instead of silently
    restarting; the position and the anchor belong to the adapter.
    """
    limit = filters.limit if filters.limit is not None else reads.DEFAULT_LIMIT
    if not 1 <= limit <= reads.MAX_LIMIT:
        raise Validation("limit out of range", rule="LIMIT_RANGE", field="limit", value=limit)

    order = filters.order or "manual"
    if order not in reads.SORT_KEYS:
        raise Validation("unknown order", rule="ORDER_INVALID", field="order", value=order)

    if filters.cursor:
        token, _ = reads.decode_cursor(filters.cursor)
        if token != reads.sort_token(order, filters.reverse):
            raise Validation(
                "cursor names another ordering",
                rule="INVALID_CURSOR",
                field="cursor",
                value=filters.cursor,
            )

    conn = db.ro_connection()
    try:
        resolved = reads.team_by_ref(conn, filters.team_id)
        if resolved is None:
            raise NotFound(resource="team", key=filters.team_id)
        return reads.list_view(
            conn, replace(filters, team_id=resolved.id, order=order, limit=limit)
        )
    finally:
        conn.close()


def list_board(
    db: Database,
    *,
    team: str,
    filters: ListFilters | None = None,
    group_by: str = "state",
    limit_per_group: int = 50,
) -> Board:
    """Board of one team grouped by `group_by`.

    `filters` is the `ListFilters` of the listing; the team is resolved from `team` (key or
    ULID) and always wins over `filters.team_id`. `limit_per_group` caps each column.
    """
    conn = db.ro_connection()
    try:
        resolved = reads.team_by_ref(conn, team)
        if resolved is None:
            raise NotFound(resource="team", key=team)
        return reads.board_view(
            conn,
            team_id=resolved.id,
            filters=None if filters is None else replace(filters, team_id=resolved.id),
            group_by=group_by,
            limit_per_group=limit_per_group,
        )
    finally:
        conn.close()


def board_counts(db: Database, team: str) -> dict[str, int]:
    """`{"open", "urgent", "total"}` of one team."""
    conn = db.ro_connection()
    try:
        resolved = reads.team_by_ref(conn, team)
        if resolved is None:
            raise NotFound(resource="team", key=team)
        return reads.team_counts(conn, resolved.id)
    finally:
        conn.close()


# raises: NotFound(team)
def list_blocked(db: Database, *, team: str | None = None) -> tuple[IssueListItem, ...]:
    """The visible issues a `blocks` edge still holds back, by `identifier`, one query.

    `team` is a key or a ULID and is resolved here; `None` spans the workspace and is not
    "no team". The verdict — blocked while a blocker's state is neither `completed` nor
    `canceled` — is the adapter's, in a single statement: this function resolves the
    reference, borrows one read-only connection and filters nothing on its own. It has no
    cursor, no limit and no total, so it is never paginated and never counted.
    """
    conn = db.ro_connection()
    try:
        team_id: str | None = None
        if team is not None:
            resolved = reads.team_by_ref(conn, team)
            if resolved is None:
                raise NotFound(resource="team", key=team)
            team_id = resolved.id
        return reads.blocked_view(conn, team_id=team_id)
    finally:
        conn.close()


def issue_detail(
    db: Database,
    ref: str,
    *,
    include: frozenset[str] = frozenset(),
    include_trashed: bool = False,
) -> IssueDetail:
    """Detail by identifier or ULID; `NotFound(issue, key=ref)` when it does not exist.

    `relations` comes back ordered: the adapter hands over the rows of both ends unordered, and
    how one reads — `blocks` from the blocker, `blocked_by` from the blocked issue — is a function
    of the asking end, so the sort happens here, on the reading and then the far end's identifier.
    Every caller inherits it, because this is the one call that produces the tuple.

    `include_trashed` is the visibility argument of a caller that answers for an issue the
    trash layer holds (SPEC-070 §2.1.1, D-059): the default keeps the read's own predicate
    `i.trashed = 0`, so a trashed reference is a `NotFound`, and `True` drops that one
    predicate and nothing else — same statements, same order, same count — so the answer may
    carry `trashed = True`. The detail page and the panel are its only consumers; every
    listing, every count and the row/card fragments keep the default.
    """
    conn = db.ro_connection()
    try:
        detail = reads.issue_detail(conn, ref, include=include, include_trashed=include_trashed)
    finally:
        conn.close()
    if detail is None:
        raise NotFound(resource="issue", key=ref)
    return replace(detail, relations=_ordered_relations(detail.relations, detail.issue.id))


def viewer(db: Database) -> tuple[str | None, str | None]:
    """`(display_name, initials)` of the single local member; `(None, None)` when there is none.

    The web edge calls this at startup instead of opening its own connection: `web/`
    talks to `app/`, and only `adapters/sqlite/` talks to the database.
    """
    conn = db.ro_connection()
    try:
        return reads.local_member(conn)
    finally:
        conn.close()


def startup_state(db: Database) -> tuple[tuple[Team, ...], tuple[str | None, str | None]]:
    """Teams and local viewer for `create_app`: a database that is not ready is not a crash.

    One borrowed read-only connection for both reads. A `sqlite3.Error` (file missing, not a
    database, migrations not applied) yields `((), (None, None))` so the dashboard starts and
    reports the emptiness instead of dying at import time.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = db.ro_connection()
        return (reads.list_teams(conn), reads.local_member(conn))
    except sqlite3.Error:  # pragma: no cover - the database is not ready yet
        return ((), (None, None))
    finally:
        if conn is not None:
            conn.close()


def database_ok(db: Database) -> bool:
    """`True` when the file answers `SELECT 1` and `PRAGMA quick_check`; never raises.

    `quick_check` is the cheap variant of `integrity_check` (no page-by-page rebuild): it is
    what `/healthz` runs on every probe.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = db.ro_connection()
        conn.execute("SELECT 1").fetchone()
        row = conn.execute("PRAGMA quick_check").fetchone()
        return bool(row) and row[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()


__all__ = [
    "DETAIL_COLLECTIONS",
    "DETAIL_INCLUDE",
    "RELATION_READINGS",
    "board_counts",
    "database_ok",
    "detail_collections",
    "issue_detail",
    "list_board",
    "list_blocked",
    "list_issues",
    "list_teams",
    "require_team",
    "resolve_filters",
    "startup_state",
    "teams_with_counts",
    "viewer",
]
