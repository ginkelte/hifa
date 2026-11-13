"""Integration tests for the issue writes against a real migrated database.

These tests exercise `update_issue`, `move_issue`, `archive_issue`, `trash_issue` and
`restore_issue` end to end on SQLite: migrations, `BEGIN IMMEDIATE`, repositories, the clock
and the history table. What they pin is the write/no-write boundary: a command whose values
already match the row writes nothing (no history, `updated_at` untouched), `archive` never
moves `updated_at`, `trash` does, and `restore` writes one row per layer it clears.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import (
    Actor,
    ArchiveIssue,
    CreateIssue,
    MoveIssue,
    RestoreIssue,
    TrashIssue,
    UpdateIssue,
)
from hifa.app.issues import (
    archive_issue,
    create_issue,
    move_issue,
    restore_issue,
    trash_issue,
    update_issue,
)
from hifa.domain.entities import Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import InvalidTransition

#: (name, type, position, is_default) — `Todo` is the team default.
STATES = (
    ("Backlog", WorkflowStateType.BACKLOG, 1.0, False),
    ("Todo", WorkflowStateType.UNSTARTED, 2.0, True),
    ("In Progress", WorkflowStateType.STARTED, 3.0, False),
    ("Done", WorkflowStateType.COMPLETED, 4.0, False),
)


class Seed:
    """Migrated database with team ENG, its four states, one member and two issues."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.clock = FrozenClock()
        self.idgen = SeqIdGen()
        conn = open_db(path)
        try:
            apply_migrations(conn)
        finally:
            conn.close()
        uow = UnitOfWork(path, clock=self.clock, idgen=self.idgen)
        now = self.clock.now()
        with uow.write() as tx:
            self.workspace = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
            self.team = tx.teams.insert(
                Team(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    key="ENG",
                    name="Engineering",
                    created_at=now,
                    updated_at=now,
                )
            )
            self.states = {}
            for name, state_type, position, is_default in STATES:
                self.states[name] = tx.states.insert(
                    WorkflowState(
                        id=self.idgen.new(),
                        team_id=self.team.id,
                        name=name,
                        type=state_type,
                        position=position,
                        is_default=is_default,
                    )
                )
            self.member = tx.members.insert(
                Member(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    name="ana",
                    display_name="Ana",
                    email="ana@example.com",
                    created_at=now,
                )
            )
        for title in ("Alpha", "Beta"):
            create_issue(uow, CreateIssue(team="ENG", title=title), self.actor)
        uow.close()

    @property
    def actor(self) -> Actor:
        return Actor(member_id=self.member.id)


@pytest.fixture
def seed(db_path: Path) -> Seed:
    return Seed(db_path)


def _uow(seed: Seed, prefix: str = "01HWRITE") -> UnitOfWork:
    """A fresh connection to the same file, sharing the seed's clock so time is controllable."""
    return UnitOfWork(seed.path, clock=seed.clock, idgen=SeqIdGen(prefix))


def _rows(seed: Seed, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(seed.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _history(seed: Seed, issue_id: str) -> list[tuple[str, str | None, str | None]]:
    """History tuples of one issue, newest first, without the row `create` wrote."""
    return [
        (row["field"], row["old_value"], row["new_value"])
        for row in _rows(
            seed,
            "SELECT field, old_value, new_value FROM issue_history WHERE issue_id = ?"
            " AND field <> 'created' ORDER BY created_at DESC, id DESC",
            (issue_id,),
        )
    ]


def _iso(value: datetime) -> str:
    """The text the database stores for a timestamp."""
    return value.isoformat().replace("+00:00", "Z")


def _stored(seed: Seed, identifier: str) -> sqlite3.Row:
    return _rows(seed, "SELECT * FROM issue WHERE identifier = ?", (identifier,))[0]


# --- update ------------------------------------------------------------------------------


def test_edit_records_one_row_per_changed_column(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        edited = update_issue(
            uow, UpdateIssue(issue="ENG-1", title="Alpha v2", priority=2), seed.actor
        )
    finally:
        uow.close()
    assert (edited.title, int(edited.priority)) == ("Alpha v2", 2)
    assert edited.branch_name == "eng-1-alpha-v2", "the derived branch follows the new title"
    assert edited.updated_at == seed.clock.now()
    fields = [field for field, _, _ in _history(seed, edited.id)]
    assert sorted(fields) == ["branch_name", "priority", "title"]
    assert all(row[2] is not None for row in _history(seed, edited.id))


def test_edit_with_identical_values_writes_nothing(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        same = update_issue(uow, UpdateIssue(issue="ENG-1", priority=0), seed.actor)
    finally:
        uow.close()
    stored = _stored(seed, "ENG-1")
    assert _iso(same.updated_at) == stored["updated_at"]
    assert _history(seed, same.id) == []


def test_update_without_fields_writes_nothing(seed: Seed) -> None:
    before = _stored(seed, "ENG-1")
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        untouched = update_issue(uow, UpdateIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert _iso(untouched.updated_at) == before["updated_at"]
    assert _history(seed, untouched.id) == []


# --- move --------------------------------------------------------------------------------


def test_move_appends_to_the_end_of_the_target_column(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        moved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="In Progress"), seed.actor)
        second = move_issue(uow, MoveIssue(issue="ENG-2", target_state="In Progress"), seed.actor)
    finally:
        uow.close()
    assert moved.state_id == seed.states["In Progress"].id
    assert moved.started_at is not None
    assert second.board_order > moved.board_order
    fields = [field for field, _, _ in _history(seed, second.id)]
    assert {"state_id", "started_at"} <= set(fields)


def test_move_to_the_same_state_and_offset_is_a_noop(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        first = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), seed.actor)
        seed.clock.advance(minutes=1)
        again = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), seed.actor)
    finally:
        uow.close()
    assert again.state_id == first.state_id
    assert again.completed_at == first.completed_at, "the milestone is not restamped"


# --- archive, trash, restore -------------------------------------------------------------


def test_archive_stamps_archived_at_and_leaves_updated_at_alone(seed: Seed) -> None:
    before = _stored(seed, "ENG-1")
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        archived = archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert archived.archived_at is not None
    assert archived.archived_at == seed.clock.now()
    stored = _stored(seed, "ENG-1")
    assert stored["updated_at"] == before["updated_at"], "archive keeps `updated_at` quiet"
    assert stored["archived_at"] is not None
    assert [field for field, _, _ in _history(seed, archived.id)] == ["archived_at"]


def test_second_archive_writes_nothing(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        first = archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    rows = _history(seed, first.id)
    stored = _stored(seed, "ENG-1")
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=5)
        again = archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert again.archived_at == first.archived_at
    assert _history(seed, again.id) == rows
    after = _stored(seed, "ENG-1")
    assert after["archived_at"] == stored["archived_at"]
    assert after["updated_at"] == stored["updated_at"]


def test_trash_moves_updated_at(seed: Seed) -> None:
    before = _stored(seed, "ENG-1")
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        trashed = trash_issue(uow, TrashIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert trashed.trashed is True
    assert _stored(seed, "ENG-1")["updated_at"] > before["updated_at"]
    assert [field for field, _, _ in _history(seed, trashed.id)] == ["trashed"]


def test_restore_clears_the_archive_then_the_trash(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
        trash_issue(uow, TrashIssue(issue="ENG-1"), seed.actor)
        seed.clock.advance(minutes=1)
        restored = restore_issue(uow, RestoreIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert restored.archived_at is None
    assert restored.trashed is False
    chronological = list(reversed(_history(seed, restored.id)))
    assert [(field, new) for field, _, new in chronological[-2:]] == [
        ("archived_at", None),
        ("trashed", "0"),
    ], "the archive layer is cleared first and the trash after"
    stored = _stored(seed, "ENG-1")
    assert stored["archived_at"] is None and stored["trashed"] == 0


def test_restore_of_a_visible_issue_writes_nothing(seed: Seed) -> None:
    before = _stored(seed, "ENG-1")
    uow = _uow(seed)
    try:
        seed.clock.advance(minutes=1)
        same = restore_issue(uow, RestoreIssue(issue="ENG-1"), seed.actor)
    finally:
        uow.close()
    assert _stored(seed, "ENG-1")["updated_at"] == before["updated_at"]
    assert _history(seed, same.id) == []


def test_an_archived_issue_rejects_edits_and_moves(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
        with pytest.raises(InvalidTransition) as edit:
            update_issue(uow, UpdateIssue(issue="ENG-1", title="Nope"), seed.actor)
        with pytest.raises(InvalidTransition) as move:
            move_issue(uow, MoveIssue(issue="ENG-1", target_state="Todo"), seed.actor)
    finally:
        uow.close()
    assert edit.value.rule == move.value.rule == "ISSUE_ARCHIVED"
    assert edit.value.code == move.value.code == "INVALID_TRANSITION"
    assert edit.value.exit_code == 5
    assert _stored(seed, "ENG-1")["title"] == "Alpha"


def test_the_database_is_the_fixture_one(tmp_path: Path, seed: Seed) -> None:
    """The suite never touches the real `~/.local/share/hifa` file."""
    assert seed.path == tmp_path / "hifa.db"
    assert ".local/share/hifa" not in str(seed.path)
