"""Unit tests for `app.teams.create_team` (SPEC-040 §2.2, REQ-F040-14) with in-memory fakes.

The transaction itself is covered by `tests/integration/db/test_teams_db.py`; here we pin the
validation rules and the shape of what the use case writes: key normalization, defaults,
workspace creation and the six canonical states taken from `DEFAULT_STATES`.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.app.commands import Actor, CreateTeam
from hifa.app.teams import DEFAULT_STATES, create_team
from hifa.domain.entities import Team, Workspace
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, Validation

NOW = FrozenClock().now()


class _Teams:
    def __init__(self, workspace: Workspace | None, existing: Team | None) -> None:
        self._workspace = workspace
        self._existing = existing
        self.ensured: list[tuple[str, str]] = []
        self.inserted: list[Team] = []

    def workspace(self) -> Workspace | None:
        return self._workspace

    def ensure_workspace(self, *, name: str, url_key: str) -> Workspace:
        self.ensured.append((name, url_key))
        self._workspace = Workspace(id="WS", name=name, url_key=url_key, created_at=NOW)
        return self._workspace

    def get_by_key(self, workspace_id: str, key: str) -> Team | None:
        return self._existing if key == "ENG" else None

    def insert(self, team: Team) -> Team:
        self.inserted.append(team)
        return team


class _States:
    def __init__(self) -> None:
        self.inserted: list[Any] = []

    def insert(self, state: Any) -> Any:
        self.inserted.append(state)
        return state


class _Repos:
    def __init__(self, teams: _Teams, states: _States) -> None:
        self.teams = teams
        self.states = states


class _UoW:
    def __init__(self, repos: _Repos) -> None:
        self._repos = repos
        self.clock = FrozenClock()
        self.idgen = SeqIdGen()
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def write(self):
        try:
            yield self._repos
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def build(*, workspace: bool = True, existing: bool = False) -> tuple[Any, _Teams, _States]:
    ws = Workspace(id="WS", name="Hifa", url_key="hifa", created_at=NOW) if workspace else None
    team = (
        Team(
            id="T1",
            workspace_id="WS",
            key="ENG",
            name="Engineering",
            created_at=NOW,
            updated_at=NOW,
        )
        if existing
        else None
    )
    teams = _Teams(ws, team)
    states = _States()
    return cast(Any, _UoW(_Repos(teams, states))), teams, states


# --- errors -------------------------------------------------------------------------------


def test_invalid_key_is_team_key_invalid() -> None:
    uow, teams, states = build()
    with pytest.raises(Validation) as caught:
        create_team(uow, CreateTeam(key="1BAD", name="Bad"), Actor())

    assert caught.value.rule == "TEAM_KEY_INVALID"
    assert caught.value.field == "key"
    assert teams.inserted == [] and states.inserted == []
    assert not uow.committed


def test_empty_name_is_rejected() -> None:
    uow, _, _ = build()
    with pytest.raises(Validation) as caught:
        create_team(uow, CreateTeam(key="OPS", name="   "), Actor())

    assert caught.value.rule == "TEAM_NAME_INVALID"
    assert caught.value.field == "name"


def test_invalid_color_is_rejected() -> None:
    uow, _, _ = build()
    with pytest.raises(Validation) as caught:
        create_team(uow, CreateTeam(key="OPS", name="Operations", color="red"), Actor())

    assert caught.value.rule == "TEAM_COLOR_INVALID"
    assert caught.value.field == "color"


def test_invalid_timezone_is_rejected() -> None:
    uow, _, _ = build()
    with pytest.raises(Validation) as caught:
        create_team(uow, CreateTeam(key="OPS", name="Operations", timezone="Mars/Base"), Actor())

    assert caught.value.rule == "TEAM_TIMEZONE_INVALID"
    assert caught.value.field == "timezone"


def test_duplicate_key_is_a_conflict() -> None:
    uow, teams, states = build(existing=True)
    with pytest.raises(Conflict) as caught:
        create_team(uow, CreateTeam(key="ENG", name="Engineering"), Actor())

    assert caught.value.rule == "UNIQUE_TEAM_KEY"
    assert caught.value.field == "key"
    assert teams.inserted == [] and states.inserted == []
    assert not uow.committed


# --- defaults and normalization -----------------------------------------------------------


def test_defaults_come_from_the_config_module() -> None:
    from hifa.config import DEFAULT_COLOR, DEFAULT_ICON, DEFAULT_TIMEZONE

    uow, teams, _ = build()
    create_team(uow, CreateTeam(key="OPS", name="Operations"), Actor())

    team = teams.inserted[0]
    assert (team.icon, team.color, team.timezone) == (DEFAULT_ICON, DEFAULT_COLOR, DEFAULT_TIMEZONE)


def test_key_and_name_are_normalized_and_flags_are_honored() -> None:
    uow, teams, _ = build()
    create_team(
        uow,
        CreateTeam(
            key="  ops  ",
            name="  Operations  ",
            icon="🛠",
            color="#123ABC",
            timezone="UTC",
            description="  on call  ",
        ),
        Actor(),
    )

    team = teams.inserted[0]
    assert team.key == "OPS"
    assert team.name == "Operations"
    assert team.icon == "🛠"
    assert team.color == "#123ABC"
    assert team.timezone == "UTC"
    assert team.description == "on call"
    assert uow.committed


# --- workspace ----------------------------------------------------------------------------


def test_workspace_is_created_when_missing() -> None:
    uow, teams, _ = build(workspace=False)
    team = create_team(uow, CreateTeam(key="OPS", name="Operations"), Actor())

    assert len(teams.ensured) == 1
    name, url_key = teams.ensured[0]
    assert name == f"{Path.cwd().name[:1].upper()}{Path.cwd().name[1:]}"
    assert url_key and url_key == url_key.lower()
    assert team.workspace_id == "WS"


def test_existing_workspace_is_reused() -> None:
    uow, teams, _ = build()
    team = create_team(uow, CreateTeam(key="OPS", name="Operations"), Actor())

    assert teams.ensured == []
    assert team.workspace_id == "WS"


# --- states -------------------------------------------------------------------------------


def test_six_canonical_states_are_inserted_with_todo_as_default() -> None:
    uow, _, states = build()
    team = create_team(uow, CreateTeam(key="OPS", name="Operations"), Actor())

    assert len(states.inserted) == 6
    assert [
        (state.name, state.type, state.position, state.is_default, state.color)
        for state in states.inserted
    ] == [
        (name, WorkflowStateType(kind), position, bool(default), color)
        for name, kind, color, position, default in DEFAULT_STATES
    ]
    assert [state.name for state in states.inserted if state.is_default] == ["Todo"]
    assert all(state.team_id == team.id for state in states.inserted)
    assert len({state.id for state in states.inserted}) == 6
