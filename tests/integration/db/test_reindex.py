"""`reindex(conn)`: the one write the FTS index accepts outside its triggers.

Four starting states are the contract — index present, index dropped, triggers dropped, both
gone — and every one of them ends with the same row count, the three triggers back and the
search answering from the index again. The bytes of the file are not the invariant.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.reindex import REINDEX_STATEMENTS, reindex


def _counts(world: World) -> tuple[int, int]:
    """`(rows of issue, rows the index answers for 'issue')`.

    A plain `COUNT(*)` of `issue_fts` reads the content table of the external index and would
    count the same rows whether the index is populated or not, so the second number is what the
    index *answers*: an emptied index answers zero even though `issue` still holds every row.
    """
    conn = sqlite3.connect(world.path)
    try:
        issues = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH 'issue'"
        ).fetchone()[0]
        return int(issues), int(indexed)
    finally:
        conn.close()


def _triggers(world: World) -> set[str]:
    conn = sqlite3.connect(world.path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'issue_fts_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def _seed(world: World, count: int = 12) -> None:
    with world.uow.write() as tx:
        for index in range(count):
            make_issue(tx, world, title=f"Issue {index}")


def _reindex(world: World) -> int:
    conn = open_db(world.path)
    try:
        return reindex(conn)
    finally:
        conn.close()


def test_the_sequence_is_eight_statements_between_begin_and_commit() -> None:
    assert len(REINDEX_STATEMENTS) == 8
    assert REINDEX_STATEMENTS[0].strip().startswith("CREATE VIRTUAL TABLE IF NOT EXISTS issue_fts")
    assert REINDEX_STATEMENTS[-1] == "INSERT INTO issue_fts(issue_fts) VALUES('rebuild')"


def test_a_present_index_is_rebuilt_and_reports_every_issue_row(world: World) -> None:
    _seed(world, 12)

    count = _reindex(world)

    assert count == 12
    assert _counts(world) == (12, 12)


def test_an_emptied_index_is_refilled(world: World) -> None:
    _seed(world, 5)
    conn = sqlite3.connect(world.path)
    try:
        conn.execute("DELETE FROM issue_fts")
        conn.commit()
    finally:
        conn.close()
    assert _counts(world) == (5, 0)

    assert _reindex(world) == 5

    assert _counts(world) == (5, 5)


def test_a_dropped_index_is_recreated_with_its_triggers(world: World) -> None:
    _seed(world, 4)
    conn = sqlite3.connect(world.path)
    try:
        conn.execute("DROP TABLE issue_fts")
        conn.commit()
    finally:
        conn.close()

    assert _reindex(world) == 4
    assert _counts(world) == (4, 4)
    assert _triggers(world) == {"issue_fts_ai", "issue_fts_ad", "issue_fts_au"}

    # The triggers are back: a new row reaches the index without a second rebuild.
    with world.uow.write() as tx:
        make_issue(tx, world, title="Después del reindex")
    conn = sqlite3.connect(world.path)
    try:
        hits = conn.execute(
            "SELECT COUNT(*) FROM issue_fts WHERE issue_fts MATCH 'despues'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert hits == 1


def test_dropped_triggers_are_recreated(world: World) -> None:
    _seed(world, 3)
    conn = sqlite3.connect(world.path)
    try:
        for name in ("issue_fts_ai", "issue_fts_ad", "issue_fts_au"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.commit()
    finally:
        conn.close()

    _reindex(world)

    assert _triggers(world) == {"issue_fts_ai", "issue_fts_ad", "issue_fts_au"}


def test_reindexing_twice_leaves_the_same_rows_and_the_same_results(world: World) -> None:
    _seed(world, 6)

    first = _reindex(world)
    second = _reindex(world)

    assert (first, second) == (6, 6)
    assert _counts(world) == (6, 6)


def test_a_foreign_file_is_refused_by_the_schema_and_not_by_the_index(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.db"
    conn = sqlite3.connect(foreign)
    try:
        conn.execute("CREATE TABLE notes (x INTEGER)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.OperationalError):
        conn = open_db(foreign)
        try:
            reindex(conn)
        finally:
            conn.close()
