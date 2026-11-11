"""Integration fixtures: migrated and seeded database (workspace, team, states, members, labels)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from helpers_world import STATES, World
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.domain.entities import Label, Member, Team, WorkflowState


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def idgen() -> SeqIdGen:
    return SeqIdGen()


@pytest.fixture
def world(db_path: Path, clock: FrozenClock, idgen: SeqIdGen) -> Iterator[World]:
    """Migrated database + workspace ``hifa``, team ``ENG`` with 6 states, 2 members, 3 labels."""
    conn = open_db(db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()

    uow = UnitOfWork(db_path, clock=clock, idgen=idgen)
    now = clock.now()
    with uow.write() as tx:
        workspace = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
        team = tx.teams.insert(
            Team(
                id=idgen.new(),
                workspace_id=workspace.id,
                key="ENG",
                name="Engineering",
                created_at=now,
                updated_at=now,
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
        members: dict[str, Member] = {}
        for key, display in (("ana", "Ana Pérez"), ("beto", "Beto Díaz")):
            members[key] = tx.members.insert(
                Member(
                    id=idgen.new(),
                    workspace_id=workspace.id,
                    name=key,
                    display_name=display,
                    email=f"{key}@example.com",
                    initials=display[0],
                    created_at=now,
                )
            )
            tx.teams.add_member(team.id, members[key].id)
        labels: dict[str, Label] = {}
        for name, color in (("bug", "#EB5757"), ("feature", "#5E6AD2")):
            labels[name] = tx.labels.insert(
                Label(
                    id=idgen.new(),
                    workspace_id=workspace.id,
                    team_id=team.id,
                    name=name,
                    color=color,
                )
            )
        labels["docs"] = tx.labels.insert(
            Label(id=idgen.new(), workspace_id=workspace.id, name="docs", color="#26B5CE")
        )

    world = World(
        path=db_path,
        uow=uow,
        clock=clock,
        idgen=idgen,
        workspace=workspace,
        team=team,
        states=states,
        members=members,
        labels=labels,
    )
    try:
        yield world
    finally:
        uow.close()
