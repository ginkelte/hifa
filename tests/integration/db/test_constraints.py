"""DDL constraints: what the schema must reject and what it must accept."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations

NOW = "2026-11-08T12:00:00Z"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Migrated and seeded connection: workspace + teams ENG/OPS + one state per team."""
    writer = open_db(db_path)
    try:
        apply_migrations(writer)
    finally:
        writer.close()

    connection = open_db(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO workspace (id, name, url_key, created_at) VALUES ('ws', 'WS', 'ws', ?)",
            (NOW,),
        )
        for team_id, key in (("tm_eng", "ENG"), ("tm_ops", "OPS")):
            connection.execute(
                "INSERT INTO team (id, workspace_id, key, name, created_at, updated_at)"
                " VALUES (?, 'ws', ?, ?, ?, ?)",
                (team_id, key, key.title(), NOW, NOW),
            )
            connection.execute(
                "INSERT INTO workflow_state (id, team_id, name, type, position)"
                " VALUES (?, ?, 'Todo', 'unstarted', 1.0)",
                (f"st_{key}", team_id),
            )
            connection.execute(
                "INSERT INTO workflow_state (id, team_id, name, type, position)"
                " VALUES (?, ?, 'Done', 'completed', 2.0)",
                (f"st_{key}_done", team_id),
            )
        connection.execute("COMMIT")
        yield connection
    finally:
        connection.close()


def insert_issue(
    conn: sqlite3.Connection,
    *,
    issue_id: str = "is1",
    number: int = 1,
    identifier: str = "ENG-1",
    priority: int | None = None,
    due_date: str | None = None,
    state_id: str = "st_ENG",
) -> None:
    conn.execute(
        "INSERT INTO issue (id, workspace_id, team_id, number, identifier, title, priority,"
        " due_date, state_id, created_at, updated_at)"
        " VALUES (?, 'ws', 'tm_eng', ?, ?, 'Título', COALESCE(?, 0), ?, ?, ?, ?)",
        (issue_id, number, identifier, priority, due_date, state_id, NOW, NOW),
    )


@pytest.mark.parametrize("priority", [9, -1, 5])
def test_priority_out_of_range_is_rejected(conn: sqlite3.Connection, priority: int) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        insert_issue(conn, priority=priority)


def test_valid_priority_is_accepted(conn: sqlite3.Connection) -> None:
    insert_issue(conn, priority=4)
    assert conn.execute("SELECT priority FROM issue WHERE id = 'is1'").fetchone()[0] == 4


@pytest.mark.parametrize(
    "due_date", ["2026-13-45", "2026-02-30", "2026-3-1", "2026-03-01T00:00:00Z"]
)
def test_impossible_due_date_is_rejected(conn: sqlite3.Connection, due_date: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        insert_issue(conn, due_date=due_date)


@pytest.mark.parametrize("due_date", [None, "2026-03-01", "2028-02-29"])
def test_valid_due_date_is_accepted(conn: sqlite3.Connection, due_date: str | None) -> None:
    insert_issue(conn, due_date=due_date)
    assert conn.execute("SELECT due_date FROM issue WHERE id = 'is1'").fetchone()[0] == due_date


def test_duplicate_workspace_label_is_rejected(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO label (id, workspace_id, team_id, name, color)"
        " VALUES ('lb1', 'ws', NULL, 'Bug', '#ff0000')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(
            "INSERT INTO label (id, workspace_id, team_id, name, color)"
            " VALUES ('lb2', 'ws', NULL, 'Bug', '#00ff00')"
        )


def test_same_label_name_in_another_team_is_accepted(conn: sqlite3.Connection) -> None:
    """R6/D-010: same name in workspace, ENG and OPS; never twice within the same scope."""
    conn.execute(
        "INSERT INTO label (id, workspace_id, team_id, name, color)"
        " VALUES ('lb_ws', 'ws', NULL, 'Bug', '#fff')"
    )
    conn.execute(
        "INSERT INTO label (id, workspace_id, team_id, name, color)"
        " VALUES ('lb_eng', 'ws', 'tm_eng', 'Bug', '#fff')"
    )
    conn.execute(
        "INSERT INTO label (id, workspace_id, team_id, name, color)"
        " VALUES ('lb_ops', 'ws', 'tm_ops', 'Bug', '#fff')"
    )
    (count,) = conn.execute("SELECT COUNT(*) FROM label WHERE name = 'Bug'").fetchone()
    assert count == 3

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(
            "INSERT INTO label (id, workspace_id, team_id, name, color)"
            " VALUES ('lb_eng2', 'ws', 'tm_eng', 'Bug', '#fff')"
        )


def test_state_from_another_team_is_rejected(conn: sqlite3.Connection) -> None:
    """R4: the composite FK (team_id, state_id) prevents an OPS state on an ENG issue."""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        insert_issue(conn, state_id="st_OPS")


def test_foreign_keys_are_enabled(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_identifier_is_unindexed_in_fts(conn: sqlite3.Connection) -> None:
    """D-008: `ENG-1` is not tokenized; parse_query resolves it, not MATCH."""
    insert_issue(conn)
    # The quote keeps `-1` from being parsed as a column filter in the FTS query.
    hits = conn.execute(
        """SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH '"ENG-1"'"""
    ).fetchone()[0]
    assert hits == 0

    (identifier,) = conn.execute(
        "SELECT identifier FROM issue_fts WHERE issue_fts MATCH 'título'"
    ).fetchone()
    assert identifier == "ENG-1"  # se guarda igual, para el snippet


def test_duplicate_issue_by_team_and_number(conn: sqlite3.Connection) -> None:
    insert_issue(conn)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        insert_issue(conn, issue_id="is2", identifier="ENG-2")
