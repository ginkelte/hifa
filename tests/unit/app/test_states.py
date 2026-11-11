"""Unit tests for `app.teams.create_state` / `update_state` (SPEC-040 §2.2, D-035).

The transaction and the SQL are covered by `tests/integration/app/test_state_reorder_db.py`;
here we pin the rules of the use case against in-memory fakes: type and color validation, the
case-insensitive duplicate check, the end/`before`/`after` positions and the "exactly one
default" invariant.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateState, UpdateState
from hifa.app.teams import create_state, update_state
from hifa.domain.entities import Team, WorkflowState, Workspace
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, NotFound, Validation

NOW = FrozenClock().now()
TEAM_ID = "TEAM"
ACTOR = Actor(source="cli", label="cli")


class _Teams:
    def __init__(self, workspace: Workspace, team: Team) -> None:
        self._workspace = workspace
        self._team = team

    def workspace(self) -> Workspace:
        return self._workspace

    def get_by_key(self, workspace_id: str, key: str) -> Team | None:
        return self._team if key.upper() == self._team.key else None

    def get(self, team_id: str) -> Team | None:
        return self._team if team_id == self._team.id else None

    def list(self) -> list[Team]:
        return [self._team]


class _States:
    def __init__(self, states: list[WorkflowState]) -> None:
        self.states = list(states)
        self.inserted: list[WorkflowState] = []
        self.updated: list[WorkflowState] = []

    def list_for_team(self, team_id: str, *, include_archived: bool = False) -> list[WorkflowState]:
        rows = [
            state
            for state in self.states
            if state.team_id == team_id and (include_archived or state.archived_at is None)
        ]
        return sorted(rows, key=lambda state: (state.position, state.name))

    def get(self, state_id: str) -> WorkflowState | None:
        return next((state for state in self.states if state.id == state_id), None)

    def next_position(self, team_id: str) -> float:
        return max((s.position for s in self.states if s.team_id == team_id), default=0.0) + 1.0

    def issue_counts(self, team_id: str) -> dict[str, int]:
        return {}

    def insert(self, state: WorkflowState) -> WorkflowState:
        self.states.append(state)
        self.inserted.append(state)
        return state

    def update(self, state: WorkflowState) -> WorkflowState:
        self.states = [state if row.id == state.id else row for row in self.states]
        self.updated.append(state)
        return state


class _Uow:
    """Minimal `UnitOfWork`: one repos bundle, no real transaction (the UoW is tested apart)."""

    def __init__(self, states: _States) -> None:
        self.repos = type("Repos", (), {"teams": None, "states": states})()
        self.clock = FrozenClock()
        self.idgen = SeqIdGen()

    @contextmanager
    def write(self) -> Any:
        yield self.repos

    @contextmanager
    def read(self) -> Any:
        yield self.repos

    def close(self) -> None:
        pass


def _team() -> Team:
    return Team(
        id=TEAM_ID,
        workspace_id="WS",
        key="ENG",
        name="Engineering",
        icon="🚀",
        color="#5E6AD2",
        timezone="UTC",
        created_at=NOW,
        updated_at=NOW,
    )


def _state(
    state_id: str,
    name: str,
    position: float,
    *,
    is_default: bool = False,
    state_type: str = "unstarted",
) -> WorkflowState:
    return WorkflowState(
        id=state_id,
        team_id=TEAM_ID,
        name=name,
        type=WorkflowStateType(state_type),
        color="#6B6F76",
        position=position,
        is_default=is_default,
    )


def _fixture(
    states: list[WorkflowState] | None = None,
) -> tuple[UnitOfWork, _States]:
    bundle = (
        states
        if states is not None
        else [
            _state("S1", "Triage", 1.0, state_type="triage"),
            _state("S2", "Todo", 2.0, is_default=True),
            _state("S3", "Backlog", 3.0, state_type="backlog"),
        ]
    )
    repo = _States(bundle)
    uow = _Uow(repo)
    uow.repos.teams = _Teams(Workspace(id="WS", name="W", url_key="w", created_at=NOW), _team())
    return cast(UnitOfWork, uow), repo


def test_create_state_rejects_a_type_outside_the_state_machine() -> None:
    uow, _ = _fixture()

    with pytest.raises(Validation) as error:
        create_state(uow, CreateState(team="ENG", name="Blocked", state_type="blocked"), ACTOR)

    assert error.value.rule == "STATE_TYPE_INVALID"
    assert error.value.field == "type"


def test_create_state_rejects_a_color_that_is_not_rrggbb() -> None:
    uow, _ = _fixture()

    with pytest.raises(Validation) as error:
        create_state(
            uow, CreateState(team="ENG", name="In Review", state_type="started", color="red"), ACTOR
        )

    assert error.value.rule == "COLOR_FORMAT"


def test_create_state_rejects_a_duplicate_name_case_insensitively() -> None:
    uow, repo = _fixture()

    with pytest.raises(Conflict) as error:
        create_state(uow, CreateState(team="ENG", name="triage", state_type="triage"), ACTOR)

    assert error.value.rule == "UNIQUE_STATE_NAME"
    assert repo.inserted == []


def test_create_state_appends_at_the_end_without_an_anchor() -> None:
    uow, _ = _fixture()

    state = create_state(
        uow, CreateState(team="ENG", name="In Review", state_type="started"), ACTOR
    )

    assert state.position == 4.0
    assert state.color == "#6B6F76"
    assert state.is_default is False


def test_create_state_with_an_explicit_position_uses_it() -> None:
    uow, _ = _fixture()

    state = create_state(
        uow,
        CreateState(team="ENG", name="In Review", state_type="started", position=2.5),
        ACTOR,
    )

    assert state.position == 2.5  # between Todo (2.0) and Backlog (3.0), computed by the edge


def test_create_state_moves_off_a_position_a_sibling_already_holds() -> None:
    """Two writers may compute the same midpoint; the second one gets a free slot next to it."""
    uow, repo = _fixture()

    state = create_state(
        uow,
        CreateState(team="ENG", name="In Review", state_type="started", position=2.0),
        ACTOR,
    )

    assert state.position == 2.5  # halved towards Backlog (3.0), not on Todo's 2.0
    assert [row.position for row in repo.inserted].count(2.0) == 0


def test_create_state_keeps_an_appended_position_above_the_last_state() -> None:
    uow, _ = _fixture()

    created = create_state(
        uow,
        CreateState(team="ENG", name="Shipped", state_type="completed", position=4.0),
        ACTOR,
    )

    assert created.position == 4.0


def test_create_state_with_an_unknown_team_is_not_found() -> None:
    uow, _ = _fixture()

    with pytest.raises(NotFound):
        create_state(uow, CreateState(team="OPS", name="Blocked", state_type="started"), ACTOR)


def test_update_state_renames_and_recolors() -> None:
    uow, _ = _fixture()

    state = update_state(uow, UpdateState(state="S2", name="Next up", color="#123456"), ACTOR)

    assert (state.name, state.color) == ("Next up", "#123456")
    assert state.position == 2.0
    assert state.is_default is True


def test_update_state_rejects_a_duplicate_name() -> None:
    uow, _ = _fixture()

    with pytest.raises(Conflict) as error:
        update_state(uow, UpdateState(state="S2", name="backlog"), ACTOR)

    assert error.value.rule == "UNIQUE_STATE_NAME"


def test_update_state_moves_between_two_neighbours() -> None:
    uow, _ = _fixture(
        [
            _state("S1", "Triage", 1.0, state_type="triage"),
            _state("S2", "Todo", 2.0, is_default=True),
            _state("S3", "Backlog", 3.0, state_type="backlog"),
            _state("S4", "Done", 4.0, state_type="completed"),
        ]
    )

    moved = update_state(uow, UpdateState(state="S4", position=2.5), ACTOR)

    assert moved.position == 2.5
    assert [s.id for s in uow.repos.states.list_for_team(TEAM_ID)] == ["S1", "S2", "S4", "S3"]


def test_update_state_moves_off_a_position_a_sibling_already_holds() -> None:
    uow, _ = _fixture(
        [
            _state("S1", "Triage", 1.0, state_type="triage"),
            _state("S2", "Todo", 2.0, is_default=True),
            _state("S3", "Backlog", 3.0, state_type="backlog"),
            _state("S4", "Done", 4.0, state_type="completed"),
        ]
    )

    moved = update_state(uow, UpdateState(state="S4", position=3.0), ACTOR)

    assert moved.position == 2.5  # halved below Backlog's slot instead of sharing it


def test_update_state_rejects_a_team_without_exactly_one_default() -> None:
    uow, _ = _fixture(
        [
            _state("S1", "Triage", 1.0, state_type="triage"),
            _state("S2", "Todo", 2.0, is_default=True),
            _state("S3", "Backlog", 3.0, is_default=True, state_type="backlog"),
        ]
    )

    with pytest.raises(Conflict) as error:
        update_state(uow, UpdateState(state="S3", name="Icebox"), ACTOR)

    assert error.value.rule == "STATE_DEFAULT_INVARIANT"


def test_update_state_refuses_to_leave_the_team_without_a_default() -> None:
    uow, _ = _fixture(
        [
            _state("S1", "Triage", 1.0, state_type="triage"),
            _state("S2", "Todo", 2.0),
        ]
    )

    with pytest.raises(Conflict) as error:
        update_state(uow, UpdateState(state="S1", name="Inbox"), ACTOR)

    assert error.value.rule == "STATE_DEFAULT_INVARIANT"


def test_update_state_of_an_unknown_state_is_not_found() -> None:
    uow, _ = _fixture()

    with pytest.raises(NotFound):
        update_state(uow, UpdateState(state="NOPE", name="Whatever"), ACTOR)


def test_update_state_keeps_the_color_when_none_is_passed() -> None:
    uow, _ = _fixture()
    before = next(s for s in uow.repos.states.list_for_team(TEAM_ID) if s.id == "S2")

    after = update_state(uow, UpdateState(state="S2", color=None), ACTOR)

    assert after.color == before.color == "#6B6F76"
