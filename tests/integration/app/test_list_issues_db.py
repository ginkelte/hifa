"""`app.boards.list_issues`: the keyset port over a real migrated database.

The port keeps its single shape (`Database`, `ListFilters`) and owns the checks the
contract puts in this layer: the page size, the ordering vocabulary, the canonical
cursor token and the team reference. The query itself belongs to the adapter.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import FrozenClock, SeqIdGen
from helpers_world import STATES, make_issue
from hifa.adapters.sqlite.connection import Database, open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.reads import ListFilters, decode_cursor, encode_cursor, sort_token
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app import boards
from hifa.domain.entities import Team, WorkflowState
from hifa.domain.errors import NotFound, Validation


@pytest.fixture
def world(db_path: Path) -> Iterator[SimpleNamespace]:
    """Migrated database with the team `ENG`, its states and its deterministic id generator."""
    clock = FrozenClock()
    idgen = SeqIdGen()
    conn = open_db(db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(db_path, clock=clock, idgen=idgen)
    with uow.write() as tx:
        workspace = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
        team = tx.teams.insert(
            Team(
                id=idgen.new(),
                workspace_id=workspace.id,
                key="ENG",
                name="Engineering",
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
        states: dict[str, WorkflowState] = {}
        for name, state_type, position, is_default in STATES:
            states[name] = tx.states.insert(
                WorkflowState(
                    id=idgen.new(),
                    team_id=team.id,
                    name=name,
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
        team=team,
        states=states,
    )
    try:
        yield seeded
    finally:
        uow.close()


def _seed(world: SimpleNamespace, count: int) -> list:
    issues = []
    with world.uow.write() as tx:
        for _ in range(count):
            issues.append(make_issue(tx, world))
            world.clock.advance(seconds=1)
    return issues


def test_the_port_reads_a_page_for_a_team_key(world) -> None:
    issues = _seed(world, 3)

    page = boards.list_issues(world.db, ListFilters(team_id="ENG"))

    assert [item.id for item in page.items] == [issue.id for issue in issues]
    assert page.has_more is False


def test_the_port_resolves_a_team_ulid_to_the_same_page(world) -> None:
    _seed(world, 3)

    by_key = boards.list_issues(world.db, ListFilters(team_id="ENG"))
    by_ulid = boards.list_issues(world.db, ListFilters(team_id=world.team.id))

    assert [item.id for item in by_ulid.items] == [item.id for item in by_key.items]


def test_an_unknown_team_is_not_found(world) -> None:
    with pytest.raises(NotFound) as exc:
        boards.list_issues(world.db, ListFilters(team_id="NOPE"))

    assert exc.value.resource == "team"
    assert exc.value.details["key"] == "NOPE"


def test_a_limit_outside_the_range_is_refused_even_past_the_constructor(world) -> None:
    filters = ListFilters(team_id="ENG")
    object.__setattr__(filters, "limit", 0)

    with pytest.raises(Validation) as exc:
        boards.list_issues(world.db, filters)

    assert exc.value.rule == "LIMIT_RANGE"


def test_an_order_outside_the_vocabulary_is_refused(world) -> None:
    filters = ListFilters(team_id="ENG")
    object.__setattr__(filters, "order", "name")

    with pytest.raises(Validation) as exc:
        boards.list_issues(world.db, filters)

    assert exc.value.rule == "ORDER_INVALID"


def test_an_unreadable_cursor_is_invalid_cursor(world) -> None:
    _seed(world, 2)

    with pytest.raises(Validation) as exc:
        boards.list_issues(world.db, ListFilters(team_id="ENG", cursor="not-a-cursor"))

    assert exc.value.rule == "INVALID_CURSOR"


def test_a_cursor_from_another_order_is_invalid_cursor(world) -> None:
    issues = _seed(world, 2)

    with pytest.raises(Validation) as exc:
        boards.list_issues(
            world.db,
            ListFilters(
                team_id="ENG",
                order="due",
                cursor=encode_cursor(sort_token("manual", False), issues[0].id),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_a_cursor_from_the_other_direction_is_invalid_cursor(world) -> None:
    issues = _seed(world, 2)

    with pytest.raises(Validation) as exc:
        boards.list_issues(
            world.db,
            ListFilters(
                team_id="ENG",
                reverse=True,
                cursor=encode_cursor(sort_token("manual", False), issues[0].id),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_the_next_cursor_names_the_order_and_the_direction(world) -> None:
    issues = _seed(world, 4)

    page = boards.list_issues(world.db, ListFilters(team_id="ENG", limit=2, reverse=True))

    assert page.has_more is True
    assert decode_cursor(page.next_cursor or "") == (sort_token("manual", True), page.items[-1].id)
    assert page.items[0].id == issues[-1].id


def test_two_pages_of_the_port_share_no_id_and_lose_none(world) -> None:
    issues = _seed(world, 5)

    first = boards.list_issues(world.db, ListFilters(team_id="ENG", limit=2))
    second = boards.list_issues(
        world.db, ListFilters(team_id="ENG", limit=2, cursor=first.next_cursor)
    )

    first_ids = [item.id for item in first.items]
    second_ids = [item.id for item in second.items]

    assert not (set(first_ids) & set(second_ids))
    assert first_ids + second_ids == [issue.id for issue in issues[:4]]


def test_the_page_is_never_counted(world, monkeypatch) -> None:
    _seed(world, 3)
    statements: list[str] = []
    original = world.db.ro_connection

    def traced_connection():
        conn = original()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(world.db, "ro_connection", traced_connection)
    page = boards.list_issues(world.db, ListFilters(team_id="ENG", limit=2))

    assert page.has_more is True
    assert statements
    assert not any("COUNT(" in statement.upper() for statement in statements)
