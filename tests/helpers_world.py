"""Seeded world for the integration tests: entities, ids and the issue factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.domain.entities import Issue, Label, Member, Team, WorkflowState, Workspace
from hifa.domain.enums import WorkflowStateType

STATES: tuple[tuple[str, WorkflowStateType, float, bool], ...] = (
    ("Triage", WorkflowStateType.TRIAGE, 1.0, False),
    ("Backlog", WorkflowStateType.BACKLOG, 2.0, False),
    ("Todo", WorkflowStateType.UNSTARTED, 3.0, True),
    ("In Progress", WorkflowStateType.STARTED, 4.0, False),
    ("Done", WorkflowStateType.COMPLETED, 5.0, False),
    ("Canceled", WorkflowStateType.CANCELED, 6.0, False),
)


@dataclass
class World:
    """Seeded world shared by the repository tests."""

    path: Path
    uow: UnitOfWork
    clock: object
    idgen: object
    workspace: Workspace
    team: Team
    states: dict[str, WorkflowState] = field(default_factory=dict)
    members: dict[str, Member] = field(default_factory=dict)
    labels: dict[str, Label] = field(default_factory=dict)

    @property
    def team_key(self) -> str:
        return self.team.key


def make_issue(tx: object, world: World, **overrides: object) -> Issue:
    """Creates an issue in `tx` with sequential number and identifier for team ENG."""
    repo = tx.issues  # type: ignore[attr-defined]
    number = repo.next_number(world.team.id)
    data: dict[str, object] = {
        "id": world.idgen.new(),  # type: ignore[attr-defined]
        "workspace_id": world.workspace.id,
        "team_id": world.team.id,
        "number": number,
        "identifier": f"{world.team.key}-{number}",
        "title": f"Issue {number}",
        "state_id": world.states["Todo"].id,
        "created_at": world.clock.now(),  # type: ignore[attr-defined]
        "updated_at": world.clock.now(),  # type: ignore[attr-defined]
    }
    if overrides.pop("due", None):
        data["due_date"] = date(2026, 11, 10)
    data.update(overrides)
    return repo.insert(Issue(**data))  # type: ignore[arg-type]


__all__ = ["STATES", "World", "make_issue"]
