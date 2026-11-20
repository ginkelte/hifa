"""`open_probe`: the read-only identification of a file, and the four verdicts it answers.

The probe is the instrument both `hifa init` and `hifa db reindex` use before anything is
written, so the properties that matter are the negative ones: it applies no pragma, it creates
nothing, it leaves the bytes alone, and a refusal does not leak the handle it opened.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from hifa.adapters.sqlite.connection import (
    FTS5_ENABLED_SQL,
    PROBE_SQL,
    open_db,
    open_probe,
    require_fts5,
)
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.domain.errors import Conflict, CorruptDatabase, DatabaseNotFound, Fts5Unavailable


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migrated(path: Path) -> Path:
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    return path


def test_the_probe_is_one_statement_over_tables_and_views() -> None:
    assert PROBE_SQL == "SELECT type, name FROM sqlite_master WHERE type IN ('table','view')"
    assert PROBE_SQL.count(";") == 0


def test_a_missing_path_raises_database_not_found_and_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"

    with pytest.raises(DatabaseNotFound) as raised:
        open_probe(missing)

    assert raised.value.code == "database_not_found"
    assert raised.value.key == str(missing)
    assert not missing.exists()


def test_an_empty_file_is_not_an_error_and_carries_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")

    probe = open_probe(empty)
    try:
        assert probe.tables == frozenset()
        assert probe.objects == frozenset()
    finally:
        probe.conn.close()
    assert empty.read_bytes() == b""


def test_a_file_sqlite_refuses_raises_corrupt_database(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a sqlite database at all" * 10)
    before = _digest(garbage)

    with pytest.raises(CorruptDatabase) as raised:
        open_probe(garbage)

    assert raised.value.code == "corrupt_database"
    assert raised.value.detail == "file is not a database"
    assert _digest(garbage) == before


def test_a_migrated_database_answers_both_projections(tmp_path: Path) -> None:
    path = _migrated(tmp_path / "hifa.db")
    before = _digest(path)

    probe = open_probe(path)
    try:
        assert {"workspace", "issue"} <= probe.tables
        assert {"workspace", "issue", "v_issue", "v_priority"} <= probe.objects
        foreign_keys = probe.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        probe.conn.close()

    assert foreign_keys == 0  # no pragma of the tuple ran: this one is off by default
    assert _digest(path) == before


def test_a_foreign_view_named_workspace_is_an_object_and_not_a_table(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.db"
    conn = sqlite3.connect(foreign)
    try:
        conn.execute("CREATE TABLE notes (x INTEGER)")
        conn.execute("CREATE TABLE base (id TEXT)")
        conn.execute("CREATE VIEW workspace AS SELECT id FROM base")
        conn.commit()
    finally:
        conn.close()

    probe = open_probe(foreign)
    try:
        assert "workspace" in probe.objects
        assert "workspace" not in probe.tables
        assert "notes" in probe.tables
    finally:
        probe.conn.close()


def test_a_refusal_from_the_read_closes_the_connection_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "garbage.db"
    path.write_bytes(b"not a database" * 20)
    closed: list[bool] = []

    class FakeConnection:
        def __init__(self) -> None:
            self.is_closed = False

        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise sqlite3.DatabaseError("file is not a database")

        def close(self) -> None:
            self.is_closed = True
            closed.append(True)

    fake = FakeConnection()
    monkeypatch.setattr("hifa.adapters.sqlite.connection.sqlite3.connect", lambda *_a, **_k: fake)

    with pytest.raises(CorruptDatabase):
        open_probe(path)

    assert fake.is_closed is True
    assert closed == [True]


def test_a_wal_database_in_a_directory_it_cannot_write_answers_db_readonly(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "locked"
    directory.mkdir()
    path = _migrated(directory / "hifa.db")
    before = _digest(path)
    os.chmod(directory, 0o500)
    try:
        with pytest.raises(Conflict) as raised:
            open_probe(path)
    finally:
        os.chmod(directory, 0o700)

    assert raised.value.code == "DB_READONLY"
    assert raised.value.rule == "DB_READONLY"
    assert _digest(path) == before


def test_require_fts5_passes_on_a_build_that_carries_the_module(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "fts.db")
    try:
        assert conn.execute(FTS5_ENABLED_SQL).fetchone() is not None
        require_fts5(conn)
    finally:
        conn.close()


def test_require_fts5_raises_the_one_code_it_owns() -> None:
    class EmptyResult:
        def fetchone(self) -> None:
            return None

    class NoFts5:
        def execute(self, *_args: object, **_kwargs: object) -> EmptyResult:
            return EmptyResult()

    with pytest.raises(Fts5Unavailable) as raised:
        require_fts5(NoFts5())  # type: ignore[arg-type]

    assert raised.value.code == "fts5_unavailable"
    assert raised.value.exit_code == 6
