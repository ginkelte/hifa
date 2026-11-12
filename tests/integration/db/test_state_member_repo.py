"""`StateRepo` y `MemberRepo`."""

from __future__ import annotations

import pytest

from hifa.domain.entities import Member, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, NotFound


def test_state_insert_and_get(world) -> None:
    with world.uow.write() as tx:
        state = tx.states.insert(
            WorkflowState(
                id="st_review",
                team_id=world.team.id,
                name="Review",
                type=WorkflowStateType.STARTED,
                position=4.5,
            )
        )
        loaded = tx.states.get(state.id)

    assert loaded == state
    assert loaded.type is WorkflowStateType.STARTED
    assert loaded.is_default is False


def test_state_next_position(world) -> None:
    with world.uow.read() as tx:
        assert tx.states.next_position(world.team.id) == 7.0
        assert tx.states.next_position("otro-team") == 1.0


def test_state_get_by_name_and_require(world) -> None:
    with world.uow.read() as tx:
        assert tx.states.get_by_name(world.team.id, "Done").name == "Done"
        assert tx.states.get_by_name(world.team.id, "Nope") is None
        with pytest.raises(NotFound) as exc:
            tx.states.require("no-existe")

    assert exc.value.resource == "workflow_state"


def test_state_update_and_archive(world) -> None:
    with world.uow.write() as tx:
        updated = tx.states.update(
            world.states["Backlog"].model_copy(update={"name": "Icebox", "color": "#111111"})
        )
        tx.states.archive(updated.id, world.clock.now())

    assert updated.name == "Icebox"
    assert updated.color == "#111111"
    with world.uow.read() as tx:
        assert tx.states.get(updated.id).archived_at == world.clock.now()


def test_state_default_ignores_archived(world) -> None:
    with world.uow.write() as tx:
        tx.states.archive(world.states["Todo"].id, world.clock.now())

    with world.uow.read() as tx:
        assert tx.states.default(world.team.id) is None


def test_state_update_missing_raises_not_found(world) -> None:
    with pytest.raises(NotFound), world.uow.write() as tx:
        tx.states.update(
            WorkflowState(
                id="st_ghost",
                team_id=world.team.id,
                name="Ghost",
                type=WorkflowStateType.BACKLOG,
                position=1.0,
            )
        )


def test_member_insert_get_and_list(world) -> None:
    with world.uow.write() as tx:
        carla = tx.members.insert(
            Member(
                id="mb_carla",
                workspace_id=world.workspace.id,
                name="carla",
                display_name="Carla Soto",
                email="carla@example.com",
                created_at=world.clock.now(),
            )
        )

    assert carla.is_active is True
    assert carla.is_admin is False
    with world.uow.read() as tx:
        names = [m.display_name for m in tx.members.list(world.workspace.id)]
        assert names == ["Ana Pérez", "Beto Díaz", "Carla Soto"]


def test_member_deactivate_hides_it_from_the_listing(world) -> None:
    with world.uow.write() as tx:
        tx.members.deactivate(world.members["beto"].id)

    with world.uow.read() as tx:
        assert [m.name for m in tx.members.list(world.workspace.id)] == ["ana"]
        assert len(tx.members.list(world.workspace.id, include_inactive=True)) == 2
        assert tx.members.get(world.members["beto"].id).is_active is False


def test_member_get_by_email_and_require(world) -> None:
    with world.uow.read() as tx:
        assert tx.members.get_by_email("ana@example.com").id == world.members["ana"].id
        assert tx.members.get_by_email("nadie@example.com") is None
        with pytest.raises(NotFound) as exc:
            tx.members.require("no-existe")

    assert exc.value.resource == "member"


def test_duplicate_member_email_is_conflict(world) -> None:
    with pytest.raises(Conflict), world.uow.write() as tx:
        tx.members.insert(
            Member(
                id="mb_dup",
                workspace_id=world.workspace.id,
                name="dup",
                display_name="Dup",
                email="ana@example.com",
                created_at=world.clock.now(),
            )
        )


def test_member_update_changes_display_name(world) -> None:
    with world.uow.write() as tx:
        updated = tx.members.update(
            world.members["ana"].model_copy(update={"display_name": "Ana P."})
        )

    assert updated.display_name == "Ana P."
    with world.uow.read() as tx:
        assert tx.members.get(updated.id).display_name == "Ana P."
