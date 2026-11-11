"""`LabelRepo`: uniqueness per team/workspace, soft retire and usage (SPEC-030 §2.4, D-010)."""

from __future__ import annotations

import pytest

from helpers_world import make_issue
from hifa.domain.entities import Label
from hifa.domain.errors import Conflict, NotFound


def test_insert_and_get(world) -> None:
    with world.uow.read() as tx:
        label = tx.labels.get(world.labels["bug"].id)

    assert label.name == "bug"
    assert label.color == "#EB5757"
    assert label.is_workspace_wide is False
    assert label.is_retired is False


def test_get_by_name_prefers_the_team_label(world) -> None:
    with world.uow.read() as tx:
        assert tx.labels.get_by_name(world.workspace.id, world.team.id, "bug").id
        assert tx.labels.get_by_name(world.workspace.id, None, "docs").id == (
            world.labels["docs"].id
        )
        assert tx.labels.get_by_name(world.workspace.id, world.team.id, "docs").id == (
            world.labels["docs"].id
        )
        assert tx.labels.get_by_name(world.workspace.id, world.team.id, "nope") is None


def test_list_without_filter_returns_all_workspace_labels(world) -> None:
    with world.uow.read() as tx:
        names = [label.name for label in tx.labels.list(world.workspace.id)]

    assert names == ["bug", "docs", "feature"]


def test_list_with_team_id_none_returns_workspace_only(world) -> None:
    with world.uow.read() as tx:
        names = [label.name for label in tx.labels.list(world.workspace.id, team_id=None)]

    assert names == ["docs"]


def test_list_with_team_id_filters_by_team(world) -> None:
    with world.uow.read() as tx:
        names = [label.name for label in tx.labels.list(world.workspace.id, team_id=world.team.id)]

    assert names == ["bug", "feature"]


def test_list_excludes_retired_unless_requested(world) -> None:
    with world.uow.write() as tx:
        tx.labels.retire(world.labels["feature"].id, world.clock.now())

    with world.uow.read() as tx:
        assert [label.name for label in tx.labels.list(world.workspace.id)] == ["bug", "docs"]
        assert len(tx.labels.list(world.workspace.id, include_retired=True)) == 3
        assert tx.labels.get(world.labels["feature"].id).retired_at == world.clock.now()


def test_duplicate_label_in_the_same_team_is_conflict(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        tx.labels.insert(
            Label(
                id="lb_dup",
                workspace_id=world.workspace.id,
                team_id=world.team.id,
                name="bug",
                color="#000000",
            )
        )

    assert exc.value.rule == "DUPLICATE_LABEL"


def test_duplicate_label_in_workspace_is_conflict(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        tx.labels.insert(
            Label(
                id="lb_dup_ws",
                workspace_id=world.workspace.id,
                name="docs",
                color="#000000",
            )
        )

    assert exc.value.rule == "DUPLICATE_LABEL"


def test_same_name_in_two_teams_does_not_collide(world) -> None:
    from hifa.domain.entities import Team

    with world.uow.write() as tx:
        other = tx.teams.insert(
            Team(
                id="tm_ops2",
                workspace_id=world.workspace.id,
                key="OPS",
                name="Ops",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )
        label = tx.labels.insert(
            Label(
                id="lb_ops_bug",
                workspace_id=world.workspace.id,
                team_id=other.id,
                name="bug",
                color="#123456",
            )
        )
        assert tx.labels.get_by_name(world.workspace.id, other.id, "bug").id == label.id


def test_update_changes_color_and_description(world) -> None:
    with world.uow.write() as tx:
        updated = tx.labels.update(
            world.labels["bug"].model_copy(update={"color": "#FF0000", "description": "rojo"})
        )

    assert updated.color == "#FF0000"
    assert updated.description == "rojo"


def test_retire_never_deletes_the_row(world) -> None:
    with world.uow.write() as tx:
        tx.labels.retire(world.labels["docs"].id, world.clock.now())

    with world.uow.read() as tx:
        assert tx.labels.get(world.labels["docs"].id) is not None
        assert tx.labels.count_uses(world.labels["docs"].id) == 0


def test_count_uses_issue_label_rows(world) -> None:
    with world.uow.write() as tx:
        first = make_issue(tx, world)
        second = make_issue(tx, world)
        tx.issues.add_label(first.id, world.labels["bug"].id)
        tx.issues.add_label(second.id, world.labels["bug"].id)
        tx.issues.add_label(second.id, world.labels["feature"].id)

    with world.uow.read() as tx:
        assert tx.labels.count_uses(world.labels["bug"].id) == 2
        assert tx.labels.count_uses(world.labels["feature"].id) == 1


def test_require_raises_not_found(world) -> None:
    with world.uow.read() as tx, pytest.raises(NotFound) as exc:
        tx.labels.require("no-existe")

    assert exc.value.resource == "label"
