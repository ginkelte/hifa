"""Integration tests for `create_issue` against a real migrated database.

These tests exercise the use case end to end on SQLite: migrations, `BEGIN IMMEDIATE`,
repositories and history. They cover identifier sequencing, the `created` history row,
label links (with de-duplication), the implicit default state, the rejection paths (which
must leave the database byte-for-byte untouched) and number reuse after `trash`.

The last test is a real race: twenty threads, each with **its own connection**, creating
issues on the same file at the same time.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateIssue
from hifa.app.issues import create_issue
from hifa.domain.entities import Label, Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import NotFound, Validation

WORKERS = 20

#: (name, type, position, is_default) — `Todo` is the team default.
STATES = (
    ("Backlog", WorkflowStateType.BACKLOG, 1.0, False),
    ("Todo", WorkflowStateType.UNSTARTED, 2.0, True),
    ("Done", WorkflowStateType.COMPLETED, 3.0, False),
)


class Seed:
    """Migrated database: team ENG with states/member/labels plus a second team OPS.

    OPS exists only to provide a workflow state owned by another team, which `create_issue`
    must reject (`STATE_TEAM_MISMATCH`).
    """

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
            self.labels = {}
            for name, color, team_id in (
                ("bug", "#EB5757", self.team.id),
                ("docs", "#26B5CE", None),
            ):
                self.labels[name] = tx.labels.insert(
                    Label(
                        id=self.idgen.new(),
                        workspace_id=self.workspace.id,
                        team_id=team_id,
                        name=name,
                        color=color,
                    )
                )
            self.other_team = tx.teams.insert(
                Team(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    key="OPS",
                    name="Operations",
                    created_at=now,
                    updated_at=now,
                )
            )
            self.other_state = tx.states.insert(
                WorkflowState(
                    id=self.idgen.new(),
                    team_id=self.other_team.id,
                    name="Todo",
                    type=WorkflowStateType.UNSTARTED,
                    position=1.0,
                    is_default=True,
                )
            )
        uow.close()


@pytest.fixture
def seed(db_path: Path) -> Seed:
    return Seed(db_path)


def _uow(seed: Seed, prefix: str = "01HISSUE") -> UnitOfWork:
    """A fresh connection to the same file: the UoW owns its own `sqlite3.Connection`."""
    return UnitOfWork(seed.path, clock=FrozenClock(), idgen=SeqIdGen(prefix))


def _actor(seed: Seed) -> Actor:
    return Actor(member_id=seed.member.id)


def _rows(seed: Seed, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(seed.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _counts(seed: Seed) -> tuple[int, int, int]:
    """`(issues, history_rows, label_links)` — the invariant every rejected create preserves."""
    row = _rows(
        seed,
        """
        SELECT (SELECT COUNT(*) FROM issue) AS issues,
               (SELECT COUNT(*) FROM issue_history) AS history,
               (SELECT COUNT(*) FROM issue_label) AS links
        """,
    )[0]
    return (row["issues"], row["history"], row["links"])


def test_identifiers_are_sequential(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        created = [
            create_issue(uow, CreateIssue(team="ENG", title=f"Issue {n}"), _actor(seed))
            for n in range(1, 4)
        ]
    finally:
        uow.close()
    assert [issue.identifier for issue in created] == ["ENG-1", "ENG-2", "ENG-3"]
    assert [issue.number for issue in created] == [1, 2, 3]


def test_created_history_row_is_written(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        issue = create_issue(uow, CreateIssue(team="ENG", title="History"), _actor(seed))
    finally:
        uow.close()
    rows = _rows(
        seed,
        "SELECT field, old_value, new_value, actor_id FROM issue_history WHERE issue_id = ?",
        (issue.id,),
    )
    assert len(rows) == 1
    assert rows[0]["field"] == "created"
    assert rows[0]["old_value"] is None
    assert rows[0]["new_value"] == issue.identifier
    assert rows[0]["actor_id"] == seed.member.id


def test_labels_are_linked_and_deduplicated(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        issue = create_issue(
            uow,
            CreateIssue(team="ENG", title="Labeled", labels=("bug", "bug", "docs")),
            _actor(seed),
        )
    finally:
        uow.close()
    rows = _rows(
        seed,
        "SELECT label_id FROM issue_label WHERE issue_id = ? ORDER BY label_id",
        (issue.id,),
    )
    stored = [row["label_id"] for row in rows]
    assert sorted(stored) == sorted([seed.labels["bug"].id, seed.labels["docs"].id])
    assert len(stored) == 2, "repeated labels must be de-duplicated, not linked twice"


def test_default_state_is_used_without_state_flag(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        issue = create_issue(uow, CreateIssue(team="ENG", title="Default"), _actor(seed))
    finally:
        uow.close()
    assert issue.state_id == seed.states["Todo"].id


def test_state_from_another_team_is_rejected(seed: Seed) -> None:
    before = _counts(seed)
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as excinfo:
            create_issue(
                uow,
                CreateIssue(team="ENG", title="Wrong team", state=seed.other_state.id),
                _actor(seed),
            )
    finally:
        uow.close()
    assert excinfo.value.code == "STATE_TEAM_MISMATCH"
    assert _counts(seed) == before == (0, 0, 0)


@pytest.mark.parametrize(
    ("command", "code"),
    [
        (CreateIssue(team="ENG", title="   "), "TITLE_EMPTY"),
        (CreateIssue(team="ENG", title="Bad priority", priority=9), "PRIORITY_OUT_OF_RANGE"),
        (CreateIssue(team="ENG", title="Bad label", labels=("nope",)), "LABEL_NOT_FOUND"),
    ],
)
def test_rejected_creates_insert_nothing(seed: Seed, command: CreateIssue, code: str) -> None:
    before = _counts(seed)
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as excinfo:
            create_issue(uow, command, _actor(seed))
    finally:
        uow.close()
    assert excinfo.value.code == code
    assert _counts(seed) == before == (0, 0, 0)

    # The failed attempts must not consume a number either: the next create is still ENG-1.
    uow = _uow(seed, prefix="01AFTER")
    try:
        issue = create_issue(uow, CreateIssue(team="ENG", title="After failure"), _actor(seed))
    finally:
        uow.close()
    assert issue.identifier == "ENG-1"


def test_trashed_number_is_not_reused(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        first = create_issue(uow, CreateIssue(team="ENG", title="Trash me"), _actor(seed))
        with uow.write() as tx:
            tx.issues.trash(first.id, at=seed.clock.now())
        second = create_issue(uow, CreateIssue(team="ENG", title="After trash"), _actor(seed))
    finally:
        uow.close()
    assert second.number == first.number + 1
    assert second.identifier == "ENG-2", "a trashed identifier must never be handed out again"


def test_team_key_is_case_insensitive_and_ulid_is_accepted(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        by_key = create_issue(uow, CreateIssue(team="eng", title="Key"), _actor(seed))
        by_id = create_issue(uow, CreateIssue(team=seed.team.id, title="ULID"), _actor(seed))
    finally:
        uow.close()
    assert by_key.team_id == by_id.team_id == seed.team.id
    assert by_key.identifier == "ENG-1"


def test_unknown_team_raises_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound):
            create_issue(uow, CreateIssue(team="NOPE", title="Nope"), _actor(seed))
    finally:
        uow.close()
    assert _counts(seed) == (0, 0, 0)


# --- concurrency -------------------------------------------------------------------------


def _create_one(path: Path, member_id: str, index: int) -> str:
    """One worker: own connection, own clock and id generator, one `create_issue` call."""
    uow = UnitOfWork(path, clock=FrozenClock(), idgen=SeqIdGen(f"01CONC{index:02d}"))
    try:
        issue = create_issue(
            uow,
            CreateIssue(team="ENG", title=f"Parallel {index}"),
            Actor(member_id=member_id, source="cli"),
        )
        return issue.identifier
    finally:
        uow.close()


def test_twenty_parallel_creates_are_unique(seed: Seed) -> None:
    """20 threads x 20 connections racing for `next_number` on the same database."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        identifiers = list(
            pool.map(lambda index: _create_one(seed.path, seed.member.id, index), range(WORKERS))
        )

    assert len(identifiers) == WORKERS
    assert len(set(identifiers)) == WORKERS, f"duplicate identifiers: {sorted(identifiers)}"
    assert sorted(identifiers, key=lambda ref: int(ref.split("-")[1])) == [
        f"ENG-{n}" for n in range(1, WORKERS + 1)
    ]

    numbers = [row["number"] for row in _rows(seed, "SELECT number FROM issue ORDER BY number")]
    assert numbers == list(range(1, WORKERS + 1)), f"gaps or duplicates in numbers: {numbers}"
    assert _rows(seed, "SELECT COUNT(*) AS n FROM issue")[0]["n"] == WORKERS
    assert (
        _rows(seed, "SELECT COUNT(*) AS n FROM issue_history WHERE field = 'created'")[0]["n"]
        == WORKERS
    )
