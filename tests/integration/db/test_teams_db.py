"""Integration tests for `create_team` against a real migrated database.

They prove the atomicity the contract asks for: the workspace, the team and its six states are
one transaction, so a failure while inserting a state leaves **no** team behind.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.state_repo import StateRepo
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateTeam
from hifa.app.teams import create_team
from hifa.domain.errors import Conflict, HifaError


def _prepare(path: Path) -> None:
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()


def _uow(path: Path) -> UnitOfWork:
    return UnitOfWork(path, clock=FrozenClock(), idgen=SeqIdGen())


def _rows(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _count(db: Path, table: str) -> int:
    return int(_rows(db, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])


def test_create_team_seeds_workspace_six_states_and_todo_default(db_path: Path) -> None:
    _prepare(db_path)
    uow = _uow(db_path)
    try:
        team = create_team(uow, CreateTeam(key="eng", name="Engineering"), Actor())
    finally:
        uow.close()

    assert team.key == "ENG"
    assert _count(db_path, "workspace") == 1
    assert _count(db_path, "team") == 1
    states = _rows(
        db_path,
        "SELECT name, type, position, is_default, color FROM workflow_state"
        " WHERE team_id = ? ORDER BY position",
        (team.id,),
    )
    assert [(row["name"], row["type"], row["position"]) for row in states] == [
        ("Triage", "triage", 1.0),
        ("Todo", "unstarted", 2.0),
        ("Backlog", "backlog", 3.0),
        ("In Progress", "started", 4.0),
        ("Done", "completed", 5.0),
        ("Canceled", "canceled", 6.0),
    ]
    assert [row["name"] for row in states if row["is_default"]] == ["Todo"]
    assert len({row["color"] for row in states}) == 5


def test_a_second_team_reuses_the_workspace(db_path: Path) -> None:
    _prepare(db_path)
    uow = _uow(db_path)
    try:
        first = create_team(uow, CreateTeam(key="ENG", name="Engineering"), Actor())
        second = create_team(uow, CreateTeam(key="OPS", name="Operations"), Actor())
    finally:
        uow.close()

    assert first.workspace_id == second.workspace_id
    assert _count(db_path, "workspace") == 1
    assert _count(db_path, "team") == 2
    assert _count(db_path, "workflow_state") == 12


def test_duplicate_key_is_a_conflict_and_writes_nothing(db_path: Path) -> None:
    _prepare(db_path)
    uow = _uow(db_path)
    try:
        create_team(uow, CreateTeam(key="ENG", name="Engineering"), Actor())
        with pytest.raises(Conflict) as caught:
            create_team(uow, CreateTeam(key="ENG", name="Another"), Actor())
    finally:
        uow.close()

    assert caught.value.rule == "UNIQUE_TEAM_KEY"
    assert _count(db_path, "team") == 1
    assert _count(db_path, "workflow_state") == 6


def test_a_failing_state_insert_leaves_no_orphan_rows(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(db_path)
    original = StateRepo.insert
    calls = {"n": 0}

    def failing(self: StateRepo, state: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise HifaError("state insert failed", code="TEST_FAILURE")
        return original(self, state)

    monkeypatch.setattr(StateRepo, "insert", failing)

    uow = _uow(db_path)
    try:
        with pytest.raises(HifaError):
            create_team(uow, CreateTeam(key="ENG", name="Engineering"), Actor())
    finally:
        uow.close()

    assert calls["n"] == 3
    assert _count(db_path, "workspace") == 0
    assert _count(db_path, "team") == 0
    assert _count(db_path, "workflow_state") == 0
