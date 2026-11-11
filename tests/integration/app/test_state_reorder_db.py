"""Integration tests for state reordering against a real migrated database (D-019, R25).

`update_state` persists the `position` the edge computed with `domain.ranking` and keeps it off
a slot a sibling already holds; `ORDER BY position` is the board order, so that is what these
tests read back. The neighbour arithmetic itself (which two positions surround a drop) is an
edge concern and is covered by `tests/e2e/cli/test_state_commands.py` and
`tests/e2e/web/test_board_reorder.py`; the helpers live in `tests/unit/domain/test_ranking.py`.
The canonical template order is checked against `DEFAULT_STATES`, the single source of
`hifa init` and `create_team`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateState, CreateTeam, UpdateState
from hifa.app.teams import DEFAULT_STATES, create_state, create_team, list_states, update_state
from hifa.domain.errors import Validation

ACTOR = Actor(source="cli", label="cli")
CANONICAL = ["Triage", "Todo", "Backlog", "In Progress", "Done", "Canceled"]


def _prepare(path: Path) -> UnitOfWork:
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(path, clock=FrozenClock(), idgen=SeqIdGen())
    create_team(uow, CreateTeam(key="ENG", name="Engineering"), ACTOR)
    return uow


def _ordered(uow: UnitOfWork) -> list[tuple[str, float]]:
    """`(name, position)` of the team ordered by position, straight from the table."""
    with uow.read() as tx:
        workspace = tx.teams.workspace()
        assert workspace is not None
        team = tx.teams.get_by_key(workspace.id, "ENG")
        assert team is not None
        return [(state.name, state.position) for state in tx.states.list_for_team(team.id)]


def _state_id(uow: UnitOfWork, name: str) -> str:
    with uow.read() as tx:
        workspace = tx.teams.workspace()
        assert workspace is not None
        team = tx.teams.get_by_key(workspace.id, "ENG")
        assert team is not None
        state = tx.states.get_by_name(team.id, name)
        assert state is not None
        return state.id


def test_canonical_template_puts_todo_before_backlog_and_marks_it_default() -> None:
    assert [entry[0] for entry in DEFAULT_STATES] == CANONICAL
    assert [entry[0] for entry in DEFAULT_STATES if entry[4]] == ["Todo"]
    assert [entry[3] for entry in DEFAULT_STATES] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_create_team_seeds_the_canonical_order(db_path: Path) -> None:
    uow = _prepare(db_path)
    try:
        ordered = _ordered(uow)
        listed = [summary.state.name for summary in list_states(uow, "ENG")]
    finally:
        uow.close()

    assert [name for name, _ in ordered] == CANONICAL
    assert listed == CANONICAL


def test_reordering_between_two_neighbours_leaves_a_fractional_position(db_path: Path) -> None:
    uow = _prepare(db_path)
    try:
        moved = update_state(
            uow,
            UpdateState(state=_state_id(uow, "Canceled"), position=2.5),
            ACTOR,
        )
        ordered = _ordered(uow)
    finally:
        uow.close()

    assert moved.position == 2.5  # between Todo (2.0) and Backlog (3.0)
    assert [name for name, _ in ordered] == [
        "Triage",
        "Todo",
        "Canceled",
        "Backlog",
        "In Progress",
        "Done",
    ]
    assert dict(ordered)["Canceled"] == 2.5


def test_a_position_a_sibling_holds_is_moved_to_the_nearest_free_slot(db_path: Path) -> None:
    """Two writers can compute the same midpoint; the second one must not share the slot."""
    uow = _prepare(db_path)
    try:
        moved = update_state(
            uow,
            UpdateState(state=_state_id(uow, "Canceled"), position=3.0),
            ACTOR,
        )
        positions = [position for _, position in _ordered(uow)]
    finally:
        uow.close()

    assert moved.position == 3.5  # halved towards In Progress (4.0), not on Backlog's 3.0
    assert len(set(positions)) == len(positions)


def test_an_exhausted_gap_is_rejected_instead_of_renormalized(db_path: Path) -> None:
    """v1 does not renumber `workflow_state` (SPEC-050 §2.5.6): the write fails, exit 2 / 422."""
    uow = _prepare(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE workflow_state SET position = 2.0 WHERE id = ?",
                (_state_id(uow, "Todo"),),
            )
            conn.execute(
                "UPDATE workflow_state SET position = 2.0 + 1e-9 WHERE id = ?",
                (_state_id(uow, "Backlog"),),
            )
        with pytest.raises(Validation) as error:
            update_state(uow, UpdateState(state=_state_id(uow, "Canceled"), position=2.0), ACTOR)
        ordered = [name for name, _ in _ordered(uow)]
    finally:
        uow.close()

    assert error.value.rule == "RANKING_EXHAUSTED"
    assert ordered == CANONICAL


def test_create_state_with_an_explicit_position_lands_in_between(db_path: Path) -> None:
    uow = _prepare(db_path)
    try:
        created = create_state(
            uow,
            CreateState(team="ENG", name="In Review", state_type="started", position=4.5),
            ACTOR,
        )
        ordered = [name for name, _ in _ordered(uow)]
    finally:
        uow.close()

    assert created.position == 4.5
    assert ordered == [
        "Triage",
        "Todo",
        "Backlog",
        "In Progress",
        "In Review",
        "Done",
        "Canceled",
    ]
