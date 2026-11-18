"""`app.issues.comment_on_issue` over a migrated database.

The use case owns no rule: the domain decides the body, the parent scope and the depth, and
this layer resolves the issue, refuses a parked one in the order the two layers ask for, and
writes the one row a comment is. What these tests pin is the row a root and a reply leave
behind, the count that does not move when a write is refused, and the order that decides
whether the trash answers as a validation or as a transition.
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
from hifa.app.commands import Actor, ArchiveIssue, CommentOnIssue, CreateIssue, TrashIssue
from hifa.app.issues import archive_issue, comment_on_issue, create_issue, trash_issue
from hifa.domain.entities import Comment, Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import InvalidTransition, NotFound, Validation

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


def _uow(seed: Seed, prefix: str = "01HCMT") -> UnitOfWork:
    """A fresh connection to the same file, sharing the seed's clock so time is controllable."""
    return UnitOfWork(seed.path, clock=seed.clock, idgen=SeqIdGen(prefix))


def _rows(seed: Seed, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(seed.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _comments(seed: Seed) -> list[tuple[str, str | None, str | None, str, str]]:
    """Every stored comment as `(issue_id, parent_id, author_id, body, created_at)` by `id`."""
    return [
        (
            row["issue_id"],
            row["parent_id"],
            row["author_id"],
            row["body"],
            row["created_at"],
        )
        for row in _rows(
            seed,
            "SELECT issue_id, parent_id, author_id, body, created_at FROM comment ORDER BY id",
        )
    ]


def _history_count(seed: Seed) -> int:
    rows = _rows(seed, "SELECT COUNT(*) AS n FROM issue_history")
    return int(rows[0]["n"])


def _issue_row(seed: Seed, identifier: str) -> sqlite3.Row:
    rows = _rows(seed, "SELECT * FROM issue WHERE identifier = ?", (identifier,))
    assert len(rows) == 1
    return rows[0]


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the row a comment leaves -------------------------------------------------------------


def test_a_root_comment_lands_with_no_parent_and_the_actor(seed: Seed) -> None:
    before = _history_count(seed)
    stored_before = _iso(seed.clock.now())
    seed.clock.advance(hours=1)

    uow = _uow(seed)
    try:
        stored = comment_on_issue(
            uow, CommentOnIssue(issue="ENG-1", body="  first look  "), seed.actor
        )
    finally:
        uow.close()

    rows = _comments(seed)
    assert len(rows) == 1
    assert rows[0] == (
        stored.issue_id,
        None,
        seed.member.id,
        "first look",
        _iso(seed.clock.now()),
    )
    assert stored.parent_id is None
    assert stored.author_id == seed.member.id
    assert _history_count(seed) == before
    # The comment is its own record: the issue it hangs from is not stamped.
    assert _issue_row(seed, "ENG-1")["updated_at"] == stored_before


def test_a_reply_lands_under_the_comment_it_names(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        root = comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body="root"), seed.actor)
        reply = comment_on_issue(
            uow, CommentOnIssue(issue="ENG-1", body="reply", parent_id=root.id), seed.actor
        )
    finally:
        uow.close()

    assert reply.parent_id == root.id
    assert [row[1] for row in _comments(seed)] == [None, root.id]


def test_a_thread_three_deep_takes_its_last_reply_and_refuses_the_next(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        first = comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body="one"), seed.actor)
        second = comment_on_issue(
            uow, CommentOnIssue(issue="ENG-1", body="two", parent_id=first.id), seed.actor
        )
        third = comment_on_issue(
            uow, CommentOnIssue(issue="ENG-1", body="three", parent_id=second.id), seed.actor
        )
        assert third.parent_id == second.id
        assert len(_comments(seed)) == 3

        with pytest.raises(Validation) as excinfo:
            comment_on_issue(
                uow, CommentOnIssue(issue="ENG-1", body="four", parent_id=third.id), seed.actor
            )
    finally:
        uow.close()

    assert excinfo.value.rule == "COMMENT_DEPTH_EXCEEDED"
    assert excinfo.value.field == "parent_id"
    assert len(_comments(seed)) == 3


# --- the body -----------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
def test_a_comment_without_a_body_is_refused(seed: Seed, body: str) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as excinfo:
            comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body=body), seed.actor)
    finally:
        uow.close()

    assert excinfo.value.rule == "BODY_EMPTY"
    assert excinfo.value.field == "body"
    assert _comments(seed) == []


# --- the layers of the issue --------------------------------------------------------------


def test_a_trashed_issue_takes_no_comment(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        trash_issue(uow, TrashIssue(issue="ENG-1"), seed.actor)
        with pytest.raises(Validation) as excinfo:
            comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body="hello"), seed.actor)
    finally:
        uow.close()

    assert excinfo.value.rule == "ISSUE_TRASHED"
    assert _comments(seed) == []


def test_an_archived_issue_answers_the_transition_and_not_a_validation(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        archive_issue(uow, ArchiveIssue(issue="ENG-1"), seed.actor)
        with pytest.raises(InvalidTransition) as excinfo:
            comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body="hello"), seed.actor)
    finally:
        uow.close()

    assert excinfo.value.rule == "ISSUE_ARCHIVED"
    assert _comments(seed) == []


# --- the parent ---------------------------------------------------------------------------


def test_a_parent_from_another_issue_is_a_mismatch(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        foreign = comment_on_issue(uow, CommentOnIssue(issue="ENG-1", body="on alpha"), seed.actor)
        with pytest.raises(Validation) as excinfo:
            comment_on_issue(
                uow, CommentOnIssue(issue="ENG-2", body="reply", parent_id=foreign.id), seed.actor
            )
    finally:
        uow.close()

    assert excinfo.value.rule == "COMMENT_PARENT_MISMATCH"
    assert excinfo.value.field == "parent_id"
    assert len(_comments(seed)) == 1


def test_a_parent_that_does_not_exist_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as excinfo:
            comment_on_issue(
                uow,
                CommentOnIssue(issue="ENG-1", body="reply", parent_id="01HIFA0000000000000999"),
                seed.actor,
            )
    finally:
        uow.close()

    assert excinfo.value.resource == "comment"
    assert _comments(seed) == []


def test_a_missing_issue_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as excinfo:
            comment_on_issue(uow, CommentOnIssue(issue="ENG-404", body="hello"), seed.actor)
    finally:
        uow.close()

    assert excinfo.value.resource == "issue"
    assert _comments(seed) == []


# --- the author ---------------------------------------------------------------------------


def test_the_command_author_wins_over_the_actor(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with uow.write() as tx:
            other = tx.members.insert(
                Member(
                    id=seed.idgen.new(),
                    workspace_id=seed.workspace.id,
                    name="bea",
                    display_name="Bea",
                    email="bea@example.com",
                    created_at=seed.clock.now(),
                )
            )
        stored = comment_on_issue(
            uow, CommentOnIssue(issue="ENG-1", body="hello", author_id=other.id), seed.actor
        )
    finally:
        uow.close()

    assert stored.author_id == other.id
    assert isinstance(stored, Comment)
