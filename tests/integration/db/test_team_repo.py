"""`TeamRepo`: teams, workspace, estados, memberships y claves previas."""

from __future__ import annotations

import pytest

from helpers_world import make_issue  # noqa: F401  (documenta el factory compartido)
from hifa.domain.entities import Team
from hifa.domain.errors import Conflict, NotFound


def test_insert_and_get_round_trip(world) -> None:
    with world.uow.write() as tx:
        now = world.clock.now()
        team = tx.teams.insert(
            Team(
                id="tm_ops",
                workspace_id=world.workspace.id,
                key="OPS",
                name="Operations",
                icon="🛠",
                created_at=now,
                updated_at=now,
            )
        )
        loaded = tx.teams.get(team.id)

    assert loaded == team
    assert loaded.icon == "🛠"
    assert loaded.timezone == "America/Santiago"
    assert loaded.visibility.value == "public"


def test_get_by_key_normalizes_to_uppercase(world) -> None:
    with world.uow.read() as tx:
        assert tx.teams.get_by_key(world.workspace.id, "eng").id == world.team.id
        assert tx.teams.get_by_key(world.workspace.id, "ENG").id == world.team.id


def test_get_by_key_missing_returns_none(world) -> None:
    with world.uow.read() as tx:
        assert tx.teams.get_by_key(world.workspace.id, "NOPE") is None
        assert tx.teams.get("no-existe") is None


def test_require_raises_not_found(world) -> None:
    with world.uow.read() as tx, pytest.raises(NotFound) as exc:
        tx.teams.require("no-existe")

    assert exc.value.resource == "team"


def test_list_orders_by_name_and_excludes_archived(world) -> None:
    with world.uow.write() as tx:
        now = world.clock.now()
        tx.teams.insert(
            Team(
                id="tm_a",
                workspace_id=world.workspace.id,
                key="AAA",
                name="Alpha",
                created_at=now,
                updated_at=now,
            )
        )
        tx.teams.archive(world.team.id, now)

    with world.uow.read() as tx:
        visible = tx.teams.list()
        every = tx.teams.list(include_archived=True)

    assert [team.key for team in visible] == ["AAA"]
    assert {team.key for team in every} == {"AAA", "ENG"}


def test_duplicate_key_is_conflict(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        tx.teams.insert(
            Team(
                id="tm_dup",
                workspace_id=world.workspace.id,
                key="ENG",
                name="Otro",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )

    assert exc.value.rule == "UNIQUE"


def test_update_changes_the_name_and_returns_the_row(world) -> None:
    with world.uow.write() as tx:
        updated = tx.teams.update(world.team.model_copy(update={"name": "Plataforma"}))

    assert updated.name == "Plataforma"
    assert updated.key == "ENG"
    with world.uow.read() as tx:
        assert tx.teams.get(world.team.id).name == "Plataforma"


def test_archive_sets_archived_at(world) -> None:
    with world.uow.write() as tx:
        tx.teams.archive(world.team.id, world.clock.now())

    with world.uow.read() as tx:
        archived = tx.teams.get(world.team.id)

    assert archived.archived_at == world.clock.now()
    assert archived.updated_at == world.clock.now()


def test_count_open_issues_ignores_closed_and_archived(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)
        make_issue(tx, world)
        closed = make_issue(tx, world, state_id=world.states["Done"].id)
        tx.issues.archive(closed.id, world.clock.now())

    with world.uow.read() as tx:
        assert tx.teams.count_open_issues(world.team.id) == 2


def test_ensure_workspace_is_idempotent_by_url_key(world) -> None:
    with world.uow.write() as tx:
        again = tx.teams.ensure_workspace(name="Otro nombre", url_key="hifa")
        current = tx.teams.workspace()

    assert again.id == world.workspace.id
    assert current.url_key == "hifa"


def test_ensure_workspace_returns_existing_when_url_changes(world) -> None:
    with world.uow.write() as tx:
        current = tx.teams.ensure_workspace(name="Hifa", url_key="otra")

    assert current.id == world.workspace.id


def test_missing_workspace_returns_none(world) -> None:
    with world.uow.write() as tx:
        tx.teams.connection.execute("DELETE FROM issue")
        tx.teams.connection.execute("DELETE FROM workflow_state")
        tx.teams.connection.execute("DELETE FROM team_membership")
        tx.teams.connection.execute("DELETE FROM member")
        tx.teams.connection.execute("DELETE FROM label")
        tx.teams.connection.execute("DELETE FROM team")
        tx.teams.connection.execute("DELETE FROM workspace")

    with world.uow.read() as tx:
        assert tx.teams.workspace() is None


def test_states_ordered_by_position(world) -> None:
    with world.uow.read() as tx:
        names = [state.name for state in tx.teams.states(world.team.id)]

    assert names == ["Triage", "Backlog", "Todo", "In Progress", "Done", "Canceled"]


def test_states_excludes_archived_by_default(world) -> None:
    with world.uow.write() as tx:
        tx.states.archive(world.states["Triage"].id, world.clock.now())

    with world.uow.read() as tx:
        assert len(tx.teams.states(world.team.id)) == 5
        assert len(tx.teams.states(world.team.id, include_archived=True)) == 6


def test_get_state_by_name_and_default_state(world) -> None:
    with world.uow.read() as tx:
        todo = tx.teams.get_state_by_name(world.team.id, "Todo")
        default = tx.teams.default_state(world.team.id)
        by_id = tx.teams.get_state(world.states["Todo"].id)

    assert todo.id == default.id == by_id.id
    assert tx is not None


def test_duplicate_state_insert_is_conflict(world) -> None:
    from hifa.domain.entities import WorkflowState
    from hifa.domain.enums import WorkflowStateType

    with pytest.raises(Conflict), world.uow.write() as tx:
        tx.teams.insert_state(
            WorkflowState(
                id="st_dup",
                team_id=world.team.id,
                name="Todo",
                type=WorkflowStateType.UNSTARTED,
                position=9.0,
            )
        )


def test_members_ordered_and_add_member_idempotent(world) -> None:
    with world.uow.write() as tx:
        tx.teams.add_member(world.team.id, world.members["ana"].id, is_owner=True)

    with world.uow.read() as tx:
        members = tx.teams.members(world.team.id)
        assert [m.display_name for m in members] == ["Ana Pérez", "Beto Díaz"]
        assert tx.teams.membership(world.team.id, world.members["ana"].id) is True
        assert tx.teams.membership(world.team.id, "nadie") is False


def test_previous_keys_ordered_by_changed_at_desc(world) -> None:
    with world.uow.write() as tx:
        tx.teams.add_previous_key(world.team.id, "eng", at=world.clock.now())
        world.clock.advance(seconds=1)
        tx.teams.add_previous_key(world.team.id, "ENGINEERING", at=world.clock.now())

    with world.uow.read() as tx:
        assert tx.teams.previous_keys(world.team.id) == ("ENGINEERING", "ENG")
        assert len(tx.teams.previous_key_rows(world.team.id)) == 2


def test_next_number_delegates_to_team_max(world) -> None:
    with world.uow.write() as tx:
        assert tx.teams.next_number(world.team.id) == 1
        make_issue(tx, world)
        assert tx.teams.next_number(world.team.id) == 2
