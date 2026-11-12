"""`UnitOfWork`: transactions, readonly, no retry and the exception boundary."""

from __future__ import annotations

import pytest

from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.uow import UNSET, Repos, RetryPolicy, UnitOfWork, Unset
from hifa.domain.entities import Team
from hifa.domain.errors import Conflict, HifaError, Validation


def test_write_commits_on_exit(world) -> None:
    new_id = world.idgen.new()

    with world.uow.write() as tx:
        tx.teams.insert(
            Team(
                id=new_id,
                workspace_id=world.workspace.id,
                key="OPS",
                name="Ops",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )

    conn = open_db(world.path)
    try:
        row = conn.execute("SELECT key FROM team WHERE id = ?", (new_id,)).fetchone()
    finally:
        conn.close()
    assert row["key"] == "OPS"


def test_write_rolls_back_and_reraises_the_same_instance(world) -> None:
    boom = HifaError("boom", code="BOOM")
    new_id = world.idgen.new()

    with pytest.raises(HifaError) as exc, world.uow.write() as tx:
        tx.teams.insert(
            Team(
                id=new_id,
                workspace_id=world.workspace.id,
                key="OPS",
                name="Ops",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )
        raise boom

    assert exc.value is boom
    conn = open_db(world.path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM team WHERE id = ?", (new_id,)).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_write_in_readonly_uow_raises_read_only_uow(world) -> None:
    uow = UnitOfWork(world.path, clock=world.clock, idgen=world.idgen, readonly=True)

    with pytest.raises(Conflict) as exc, uow.write():
        pass

    assert exc.value.code == "READ_ONLY_UOW"
    uow.close()


def test_nested_write_raises_uow_nested(world) -> None:
    with pytest.raises(HifaError) as exc, world.uow.write(), world.uow.write():
        pass

    assert exc.value.code == "UOW_NESTED"


def test_read_does_not_allow_writes(world) -> None:
    with world.uow.read() as tx, pytest.raises(Conflict) as exc:
        tx.issues.add_label("x", "y")

    assert exc.value.code == "READ_ONLY_UOW"


@pytest.mark.parametrize(
    "operation",
    ["insert", "update", "archive", "trash", "next_write"],
)
def test_read_blocks_all_writes(world, operation: str) -> None:
    with world.uow.read() as tx, pytest.raises(Conflict) as exc:
        if operation == "insert":
            tx.teams.insert(
                Team(
                    id="x",
                    workspace_id=world.workspace.id,
                    key="OPS",
                    name="Ops",
                    created_at=world.clock.now(),
                    updated_at=world.clock.now(),
                )
            )
        elif operation == "update":
            tx.teams.update(world.team)
        elif operation == "archive":
            tx.teams.archive(world.team.id, world.clock.now())
        elif operation == "trash":
            tx.issues.trash("x", world.clock.now())
        else:
            tx.history.record("x", [("title", None, "t")], actor_id=None, now=world.clock.now())

    assert exc.value.code == "READ_ONLY_UOW"


def test_the_seven_repos_share_the_connection(world) -> None:
    with world.uow.write() as tx:
        expected = world.uow.connection
        assert isinstance(tx, Repos)
        repos = (tx.teams, tx.states, tx.labels, tx.members, tx.issues, tx.comments, tx.history)
        for repo in repos:
            assert repo.connection is expected
        assert tx.states.connection is tx.teams.connection


def test_clock_and_idgen_are_the_injected_dependencies(world) -> None:
    assert world.uow.clock is world.clock
    assert world.uow.idgen is world.idgen
    assert world.uow.now() == world.clock.now()
    assert world.uow.new_id().startswith("01HIFA")


def test_write_emits_begin_immediate(world) -> None:
    statements: list[str] = []
    world.uow.connection.set_trace_callback(statements.append)
    try:
        with world.uow.write():
            pass
    finally:
        world.uow.connection.set_trace_callback(None)

    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[-1] == "COMMIT"


def test_read_emits_deferred_begin(world) -> None:
    statements: list[str] = []
    world.uow.connection.set_trace_callback(statements.append)
    try:
        with world.uow.read():
            pass
    finally:
        world.uow.connection.set_trace_callback(None)

    assert statements[0] == "BEGIN"
    assert statements[-1] == "COMMIT"


def test_close_is_idempotent(world) -> None:
    uow = UnitOfWork(world.path, clock=world.clock, idgen=world.idgen)
    with uow.read():
        pass

    uow.close()
    uow.close()


def test_borrowed_connection_is_not_closed(world) -> None:
    conn = open_db(world.path)
    uow = UnitOfWork(conn, clock=world.clock, idgen=world.idgen)
    with uow.read():
        pass
    uow.close()

    assert conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()


def test_two_uows_on_the_same_path_are_independent(world) -> None:
    other = UnitOfWork(world.path, clock=world.clock, idgen=world.idgen)
    with other.read() as tx:
        assert tx.teams.get(world.team.id) is not None
    other.close()


def test_retry_policy_default(world) -> None:
    assert world.uow.retry == RetryPolicy()
    assert world.uow.retry.attempts == 3
    assert world.uow.retry.base_delay == 0.05


def test_unset_is_not_none_and_is_falsy() -> None:
    assert UNSET is not None
    assert isinstance(UNSET, Unset)
    assert repr(UNSET) == "UNSET"
    assert not UNSET


def test_write_translates_integrity_error_unique(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        tx.issues.connection.execute(
            "INSERT INTO member (id, workspace_id, name, display_name, email, created_at)"
            " VALUES ('m1', ?, 'x', 'X', 'ana@example.com', ?)",
            (world.workspace.id, "2026-11-08T12:00:00Z"),
        )

    assert exc.value.rule == "UNIQUE"


def test_write_translates_foreign_key(world) -> None:
    with pytest.raises(Validation) as exc, world.uow.write() as tx:
        tx.comments.connection.execute(
            "INSERT INTO comment (id, issue_id, body, created_at, updated_at)"
            " VALUES ('c1', 'no-existe', 'hola', ?, ?)",
            ("2026-11-08T12:00:00Z", "2026-11-08T12:00:00Z"),
        )

    assert exc.value.rule == "FOREIGN_KEY"


def test_write_translates_not_null(world) -> None:
    with pytest.raises(Validation) as exc, world.uow.write() as tx:
        tx.comments.connection.execute(
            "INSERT INTO comment (id, issue_id, created_at, updated_at) VALUES ('c2', 'x', ?, ?)",
            ("2026-11-08T12:00:00Z", "2026-11-08T12:00:00Z"),
        )

    assert exc.value.rule == "NOT_NULL"


def test_write_propagates_db_busy_without_retrying(world) -> None:
    other = open_db(world.path)
    world.uow.connection.execute("PRAGMA busy_timeout = 20")
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(Conflict) as exc, world.uow.write():
            pass
        other.execute("ROLLBACK")
    finally:
        other.close()

    assert exc.value.code == "DB_BUSY"


def test_readonly_connection_translates_db_readonly(world) -> None:
    conn = open_db(world.path, readonly=True)
    uow = UnitOfWork(conn, clock=world.clock, idgen=world.idgen)
    with pytest.raises(Conflict) as exc, uow.write() as tx:
        tx.members.deactivate(world.members["ana"].id)
    uow.close()

    assert exc.value.code in {"DB_READONLY", "READ_ONLY_UOW"}
