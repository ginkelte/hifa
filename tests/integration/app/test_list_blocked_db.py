"""`app.boards.list_blocked`: the blocked read over a real migrated database.

The port transports a read and decides no rule: the verdict (an issue is blocked while a
`blocks` edge points at it and its blocker's state is neither `completed` nor `canceled`)
belongs to the adapter, which answers it in one statement. The port owns the team reference
and its `NotFound`, nothing else.

The cases pinned here are the ones the predicate exists for: a finished blocker releases the
issue, two blockers do not duplicate the row, and a hidden row (`visible = 0`) never appears.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import FrozenClock, SeqIdGen
from helpers_world import STATES
from hifa.adapters.sqlite import reads
from hifa.adapters.sqlite.connection import Database, open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app import boards
from hifa.domain.entities import Issue, IssueRelation, Team, WorkflowState
from hifa.domain.enums import RelationType
from hifa.domain.errors import NotFound


@pytest.fixture
def world(db_path: Path) -> Iterator[SimpleNamespace]:
    """Migrated database with two teams (`ENG`, `OPS`) and their states."""
    clock = FrozenClock()
    idgen = SeqIdGen()
    conn = open_db(db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(db_path, clock=clock, idgen=idgen)
    teams: dict[str, Team] = {}
    states: dict[str, dict[str, WorkflowState]] = {}
    with uow.write() as tx:
        workspace = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
        for key, name in (("ENG", "Engineering"), ("OPS", "Operations")):
            team = tx.teams.insert(
                Team(
                    id=idgen.new(),
                    workspace_id=workspace.id,
                    key=key,
                    name=name,
                    created_at=clock.now(),
                    updated_at=clock.now(),
                )
            )
            teams[key] = team
            states[key] = {}
            for state_name, state_type, position, is_default in STATES:
                states[key][state_name] = tx.states.insert(
                    WorkflowState(
                        id=idgen.new(),
                        team_id=team.id,
                        name=state_name,
                        type=state_type,
                        position=position,
                        is_default=is_default,
                    )
                )
    seeded = SimpleNamespace(
        path=db_path,
        db=Database(db_path),
        uow=uow,
        clock=clock,
        idgen=idgen,
        workspace=workspace,
        teams=teams,
        states=states,
    )
    try:
        yield seeded
    finally:
        uow.close()


def _issue(
    tx: object, world: SimpleNamespace, *, team: str = "ENG", state: str = "Todo", **overrides
) -> Issue:
    """One issue of `team` in `state`, numbered inside its own team."""
    selected = world.teams[team]
    number = tx.issues.next_number(selected.id)  # type: ignore[attr-defined]
    data: dict[str, object] = {
        "id": world.idgen.new(),
        "workspace_id": world.workspace.id,
        "team_id": selected.id,
        "number": number,
        "identifier": f"{selected.key}-{number}",
        "title": f"{selected.key} issue {number}",
        "state_id": world.states[team][state].id,
        "created_at": world.clock.now(),
        "updated_at": world.clock.now(),
    }
    data.update(overrides)
    return tx.issues.insert(Issue(**data))  # type: ignore[attr-defined,arg-type]


def _block(tx: object, world: SimpleNamespace, blocker: Issue, blocked: Issue) -> IssueRelation:
    """Stores `blocker blocks blocked` in the direction the edge keeps."""
    return tx.issues.add_relation(  # type: ignore[attr-defined]
        IssueRelation(
            id=world.idgen.new(),
            issue_id=blocker.id,
            related_issue_id=blocked.id,
            type=RelationType.BLOCKS,
            created_at=world.clock.now(),
        )
    )


def _identifiers(items) -> list[str]:
    return [item.identifier for item in items]


def test_an_issue_whose_blocker_is_unfinished_is_listed(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        blocked = _issue(tx, world)
        _block(tx, world, blocker, blocked)

    assert _identifiers(boards.list_blocked(world.db)) == [blocked.identifier]


def test_a_completed_blocker_no_longer_blocks(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world, state="Done")
        blocked = _issue(tx, world)
        _block(tx, world, blocker, blocked)

    assert blocker.identifier == "ENG-1"
    assert _identifiers(boards.list_blocked(world.db)) == []


def test_a_canceled_blocker_no_longer_blocks(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world, state="Canceled")
        blocked = _issue(tx, world)
        _block(tx, world, blocker, blocked)

    assert _identifiers(boards.list_blocked(world.db)) == []


def test_one_finished_and_one_live_blocker_list_the_issue_once(world) -> None:
    with world.uow.write() as tx:
        finished = _issue(tx, world, state="Done")
        live = _issue(tx, world, state="In Progress")
        blocked = _issue(tx, world)
        _block(tx, world, finished, blocked)
        _block(tx, world, live, blocked)

    assert _identifiers(boards.list_blocked(world.db)) == [blocked.identifier]


def test_two_live_blockers_do_not_duplicate_the_issue(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world, state="In Progress")
        blocked = _issue(tx, world)
        _block(tx, world, first, blocked)
        _block(tx, world, second, blocked)

    assert _identifiers(boards.list_blocked(world.db)) == [blocked.identifier]


def test_an_issue_without_blockers_is_not_listed(world) -> None:
    with world.uow.write() as tx:
        _issue(tx, world)
        _issue(tx, world)
        _issue(tx, world)

    assert _identifiers(boards.list_blocked(world.db)) == []


def test_a_relation_of_another_type_does_not_block(world) -> None:
    with world.uow.write() as tx:
        source = _issue(tx, world)
        target = _issue(tx, world)
        tx.issues.add_relation(
            IssueRelation(
                id=world.idgen.new(),
                issue_id=source.id,
                related_issue_id=target.id,
                type=RelationType.RELATED,
                created_at=world.clock.now(),
            )
        )

    assert _identifiers(boards.list_blocked(world.db)) == []


def test_a_closed_issue_whose_blocker_is_live_is_still_listed(world) -> None:
    """The blocked issue's own state is not filtered: only the blocker's releases it."""
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        closed = _issue(tx, world, state="Done")
        _block(tx, world, blocker, closed)

    assert _identifiers(boards.list_blocked(world.db)) == [closed.identifier]


def test_an_archived_issue_is_not_listed(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        archived = _issue(tx, world, archived_at=world.clock.now())
        _block(tx, world, blocker, archived)

    assert _identifiers(boards.list_blocked(world.db)) == []


def test_a_trashed_issue_is_not_listed(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        trashed = _issue(tx, world, trashed=True)
        _block(tx, world, blocker, trashed)

    assert _identifiers(boards.list_blocked(world.db)) == []


def test_the_rows_are_ordered_by_identifier(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        first = _issue(tx, world)
        second = _issue(tx, world)
        _block(tx, world, blocker, second)
        _block(tx, world, blocker, first)

    assert _identifiers(boards.list_blocked(world.db)) == [first.identifier, second.identifier]


def test_no_team_reads_the_whole_workspace(world) -> None:
    with world.uow.write() as tx:
        eng_blocker = _issue(tx, world)
        eng_blocked = _issue(tx, world)
        ops_blocker = _issue(tx, world, team="OPS")
        ops_blocked = _issue(tx, world, team="OPS")
        _block(tx, world, eng_blocker, eng_blocked)
        _block(tx, world, ops_blocker, ops_blocked)

    assert _identifiers(boards.list_blocked(world.db)) == [
        eng_blocked.identifier,
        ops_blocked.identifier,
    ]


def test_the_team_filter_takes_the_key(world) -> None:
    with world.uow.write() as tx:
        eng_blocker = _issue(tx, world)
        eng_blocked = _issue(tx, world)
        ops_blocker = _issue(tx, world, team="OPS")
        ops_blocked = _issue(tx, world, team="OPS")
        _block(tx, world, eng_blocker, eng_blocked)
        _block(tx, world, ops_blocker, ops_blocked)

    assert _identifiers(boards.list_blocked(world.db, team="ENG")) == [eng_blocked.identifier]
    assert _identifiers(boards.list_blocked(world.db, team="OPS")) == [ops_blocked.identifier]


def test_the_team_filter_takes_the_ulid(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world, team="OPS")
        blocked = _issue(tx, world, team="OPS")
        _block(tx, world, blocker, blocked)

    by_key = boards.list_blocked(world.db, team="OPS")
    by_ulid = boards.list_blocked(world.db, team=world.teams["OPS"].id)

    assert _identifiers(by_ulid) == _identifiers(by_key) == [blocked.identifier]


def test_an_unknown_team_is_not_found(world) -> None:
    with pytest.raises(NotFound) as exc:
        boards.list_blocked(world.db, team="NOPE")

    assert exc.value.resource == "team"
    assert exc.value.details["key"] == "NOPE"


def test_the_view_answers_in_one_statement(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        blocked = _issue(tx, world)
        _block(tx, world, blocker, blocked)

    statements: list[str] = []
    conn = world.db.ro_connection()
    try:
        conn.set_trace_callback(statements.append)
        items = reads.blocked_view(conn)
    finally:
        conn.close()

    assert _identifiers(items) == [blocked.identifier]
    assert len(statements) == 1
    assert not any("COUNT(" in statement.upper() for statement in statements)


def test_the_port_resolves_the_team_and_then_reads_once(world, monkeypatch) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        blocked = _issue(tx, world)
        _block(tx, world, blocker, blocked)

    statements: list[str] = []
    original = world.db.ro_connection

    def traced_connection():
        conn = original()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(world.db, "ro_connection", traced_connection)
    boards.list_blocked(world.db, team="ENG")

    assert len(statements) == 2
    assert not any("COUNT(" in statement.upper() for statement in statements)
