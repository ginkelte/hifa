"""`app.teams.bootstrap` over a migrated database: the seed obeys the label catalogue's rule.

The seed is the third writer of `label` rows, so it answers the same question the two catalogue
writes answer: a level that already carries a name in another spelling already has the label, and
the seed writes nothing beside it. Every test here reads `SELECT name FROM label` back, because
the rule is about the rows and not about what the run reports.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, UpdateLabel
from hifa.app.labels import update_label
from hifa.app.teams import DEFAULT_LABELS, BootstrapOptions, BootstrapResult, bootstrap


def _migrated(path: Path) -> None:
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()


def _seed(path: Path, clock: FrozenClock, ids: SeqIdGen) -> BootstrapResult:
    """One `hifa init`: the seed path under test, over an already migrated file."""
    uow = UnitOfWork(path, clock=clock, idgen=ids)
    try:
        return bootstrap(uow, BootstrapOptions(), clock=clock, ids=ids)
    finally:
        uow.close()


def _names(path: Path) -> list[str]:
    """Every `label.name` of the file, name ASC: the catalogue the rule is about."""
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT name FROM label ORDER BY name")]
    finally:
        conn.close()


def _rename(path: Path, clock: FrozenClock, ids: SeqIdGen, old: str, new: str) -> None:
    """The rename a reader can make: `Bug` -> `bug` is legal, because a row may change its case."""
    uow = UnitOfWork(path, clock=clock, idgen=ids)
    try:
        update_label(uow, UpdateLabel(label=old, name=new), Actor())
    finally:
        uow.close()


@pytest.fixture
def seeded(db_path: Path) -> tuple[Path, FrozenClock, SeqIdGen]:
    clock, ids = FrozenClock(), SeqIdGen()
    _migrated(db_path)
    _seed(db_path, clock, ids)
    return db_path, clock, ids


def test_the_first_seed_writes_one_row_per_default_label(
    seeded: tuple[Path, FrozenClock, SeqIdGen],
) -> None:
    path = seeded[0]
    assert _names(path) == sorted(name for name, _ in DEFAULT_LABELS)


def test_the_seed_does_not_add_a_second_spelling_of_a_seeded_name(
    seeded: tuple[Path, FrozenClock, SeqIdGen],
) -> None:
    """`Bug` renamed to `bug` is the label; a second run must not put `Bug` back beside it.

    The uniqueness indexes of `label` are case-sensitive, so the pair is a legal set of rows and
    the catalogue would then hold one name in two spellings — the exact state every other
    creation path refuses to write.
    """
    path, clock, ids = seeded

    _rename(path, clock, ids, "Bug", "bug")
    assert _names(path) == ["Docs", "Feature", "Improvement", "bug"]

    second = _seed(path, clock, ids)

    assert _names(path) == ["Docs", "Feature", "Improvement", "bug"]
    assert "label:Bug" in second.skipped
    assert second.created == []


def test_the_seed_still_writes_a_label_the_workspace_lost(
    seeded: tuple[Path, FrozenClock, SeqIdGen],
) -> None:
    """The case-insensitive check is not a blanket skip: a name that is gone is still seeded."""
    path, clock, ids = seeded

    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM label WHERE name = 'Docs'")
        conn.commit()
    finally:
        conn.close()

    second = _seed(path, clock, ids)

    assert _names(path) == ["Bug", "Docs", "Feature", "Improvement"]
    assert "label:Docs" in second.created
