"""Migrations: empty database, idempotency, checksum, schema_too_new and backup."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import (
    MAX_VERSION,
    MigrationResult,
    apply_migrations,
    schema_version,
)
from hifa.domain.errors import MigrationChecksumMismatch, SchemaTooNew

DOMAIN_TABLES = {
    "workspace",
    "team",
    "workflow_state",
    "label",
    "member",
    "team_membership",
    "project",
    "project_milestone",
    "cycle",
    "issue",
    "issue_label",
    "issue_relation",
    "comment",
    "attachment",
    "issue_history",
    "schema_migration",
    "team_previous_key",
}


def migrate(db_path: Path, migrations_dir: Path | None = None) -> MigrationResult:
    conn = open_db(db_path)
    try:
        return apply_migrations(conn, migrations_dir=migrations_dir)
    finally:
        conn.close()


def user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows if not row[0].startswith("issue_fts")}


def test_migration_on_empty_database(db_path: Path) -> None:
    result = migrate(db_path)

    assert result.applied == [1, 2, 3]
    assert result.from_version == 0
    assert result.to_version == MAX_VERSION == 3
    assert result.backup_path is None  # base virgen: no hay nada que respaldar

    conn = open_db(db_path, readonly=True)
    try:
        assert schema_version(conn) == 3
        assert user_tables(conn) == DOMAIN_TABLES
        assert len(user_tables(conn)) == 17

        views = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
        }
        assert views == {"v_issue", "v_priority", "v_team_progress"}

        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            )
        }
        assert triggers == {"issue_fts_ai", "issue_fts_ad", "issue_fts_au"}

        rows = conn.execute(
            "SELECT version, name, applied_at, checksum FROM schema_migration ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in rows] == [1, 2, 3]
        assert [row["name"] for row in rows] == [
            "001_init.sql",
            "002_schema_migration.sql",
            "003_team_previous_key.sql",
        ]
        assert all(len(row["checksum"]) == 64 for row in rows)
        assert all(row["applied_at"].endswith("Z") for row in rows)
    finally:
        conn.close()


def test_migration_is_idempotent(db_path: Path) -> None:
    first = migrate(db_path)
    second = migrate(db_path)

    assert first.applied == [1, 2, 3]
    assert second == MigrationResult([], 3, 3, None)

    conn = open_db(db_path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()
        assert count == 3
        assert schema_version(conn) == 3
    finally:
        conn.close()


def test_altered_checksum_aborts(db_path: Path, migrations_dir: Path, tmp_path: Path) -> None:
    copied = tmp_path / "migrations"
    shutil.copytree(migrations_dir, copied)
    migrate(db_path, copied)

    init = copied / "001_init.sql"
    init.write_text(init.read_text(encoding="utf-8") + "\n-- edited after applying\n")

    with pytest.raises(MigrationChecksumMismatch) as excinfo:
        migrate(db_path, copied)
    assert excinfo.value.code == "migration_checksum_mismatch"
    assert excinfo.value.exit_code == 6

    conn = open_db(db_path)
    try:
        assert schema_version(conn) == 3  # nothing was touched
    finally:
        conn.close()


def test_newer_binary_database_is_left_untouched(db_path: Path) -> None:
    migrate(db_path)
    conn = open_db(db_path)
    try:
        conn.execute("PRAGMA user_version = 4")
    finally:
        conn.close()

    with pytest.raises(SchemaTooNew) as excinfo:
        migrate(db_path)
    assert excinfo.value.code == "schema_too_new"
    assert excinfo.value.exit_code == 4

    conn = open_db(db_path)
    try:
        assert schema_version(conn) == 4
        assert user_tables(conn) == DOMAIN_TABLES
    finally:
        conn.close()


def test_backup_before_migrating_database_with_data(
    db_path: Path, migrations_dir: Path, tmp_path: Path
) -> None:
    only_init = tmp_path / "only_init"
    only_init.mkdir()
    shutil.copy(migrations_dir / "001_init.sql", only_init / "001_init.sql")

    assert migrate(db_path, only_init).applied == [1]
    result = migrate(db_path, migrations_dir)

    assert result.applied == [2, 3]
    assert result.backup_path is not None
    backup = Path(result.backup_path)
    assert backup.exists() and backup.stat().st_size > 0
    assert backup.name.endswith(".bak-1")

    backup_conn = open_db(backup, readonly=True)
    try:
        assert schema_version(backup_conn) == 1
    finally:
        backup_conn.close()


def test_fts_records_rows_after_insert(db_path: Path) -> None:
    """The 3 triggers keep `issue_fts` in sync with `issue`."""
    migrate(db_path)
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO workspace (id, name, url_key, created_at)"
            " VALUES ('ws1', 'WS', 'ws', '2026-11-08T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO team (id, workspace_id, key, name, created_at, updated_at)"
            " VALUES ('tm1', 'ws1', 'ENG', 'Engineering', '2026-11-08T00:00:00Z',"
            " '2026-11-08T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO workflow_state (id, team_id, name, type, position)"
            " VALUES ('st1', 'tm1', 'Todo', 'unstarted', 1.0)"
        )
        conn.execute(
            "INSERT INTO issue (id, workspace_id, team_id, number, identifier, title,"
            " state_id, created_at, updated_at)"
            " VALUES ('is1', 'ws1', 'tm1', 1, 'ENG-1', 'Búsqueda de cafeteras', 'st1',"
            " '2026-11-08T00:00:00Z', '2026-11-08T00:00:00Z')"
        )
        conn.execute("COMMIT")

        hits = conn.execute(
            "SELECT identifier FROM issue_fts WHERE issue_fts MATCH 'cafeteras'"
        ).fetchall()
        assert [row[0] for row in hits] == ["ENG-1"]

        conn.execute("UPDATE issue SET title = 'Búsqueda de teteras' WHERE id = 'is1'")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH 'cafeteras'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH 'teteras'"
            ).fetchone()[0]
            == 1
        )

        conn.execute("DELETE FROM issue WHERE id = 'is1'")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH 'teteras'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()
