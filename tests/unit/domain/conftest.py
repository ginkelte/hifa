"""Domain entity fixtures: minimal valid constructions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hifa.domain.entities import (
    Issue,
    Label,
    Team,
    WorkflowState,
    Workspace,
)
from hifa.domain.enums import WorkflowStateType

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(id="01WSP", name="Personal", url_key="personal", created_at=NOW)


@pytest.fixture
def team(workspace: Workspace) -> Team:
    return Team(
        id="01TEAM",
        workspace_id=workspace.id,
        key="ENG",
        name="Engineering",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def other_team(workspace: Workspace) -> Team:
    return Team(
        id="01OPS",
        workspace_id=workspace.id,
        key="OPS",
        name="Operations",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def state(team: Team) -> WorkflowState:
    return WorkflowState(
        id="01STTODO",
        team_id=team.id,
        name="Todo",
        type=WorkflowStateType.UNSTARTED,
        position=1024.0,
    )


@pytest.fixture
def started_state(team: Team) -> WorkflowState:
    return WorkflowState(
        id="01STDOING",
        team_id=team.id,
        name="In Progress",
        type=WorkflowStateType.STARTED,
        position=2048.0,
    )


@pytest.fixture
def done_state(team: Team) -> WorkflowState:
    return WorkflowState(
        id="01STDONE",
        team_id=team.id,
        name="Done",
        type=WorkflowStateType.COMPLETED,
        position=3072.0,
    )


@pytest.fixture
def label(team: Team, workspace: Workspace) -> Label:
    return Label(
        id="01LBLBUG",
        workspace_id=workspace.id,
        team_id=team.id,
        name="bug",
        color="#EB5757",
    )


def issue_kwargs(team: Team, state: WorkflowState, **overrides: object) -> dict[str, object]:
    """Full kwargs to build a valid `Issue`, with overrides."""
    base: dict[str, object] = {
        "id": "01ISSUE",
        "workspace_id": team.workspace_id,
        "team_id": team.id,
        "number": 1,
        "identifier": f"{team.key}-1",
        "title": "Fix login",
        "state_id": state.id,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


@pytest.fixture
def issue(team: Team, state: WorkflowState) -> Issue:
    return Issue(**issue_kwargs(team, state))  # type: ignore[arg-type]
