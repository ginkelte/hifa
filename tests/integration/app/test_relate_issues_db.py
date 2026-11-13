"""`app.issues.relate_issues` and `app.issues.unrelate_issues` over a migrated database.

The use cases own no rule: the domain decides the refusals (self-edge, workspace, duplicate),
the type normalises the stored order of the pair in the repository, and this layer only
resolves the two references, opens one transaction and leaves the history row.

The cases pinned here are the ones the layer can get wrong: an edge between two teams of one
workspace is legal while an edge across workspaces is not, the reverse spelling of a symmetric
edge is the same stored row, `blocks` keeps the direction it was given, and the undo of an
edge that was never written is reported instead of swallowed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import FrozenClock, SeqIdGen
from helpers_world import STATES
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, RelateIssues, UnrelateIssues
from hifa.app.issues import relate_issues, unrelate_issues
from hifa.domain.entities import Issue, Member, Team, WorkflowState
from hifa.domain.errors import Conflict, NotFound, Validation


@pytest.fixture
def world(db_path: Path) -> Iterator[SimpleNamespace]:
    """Migrated database with two workspaces: `ENG`/`OPS` in one and `EXT` in the other."""
    clock = FrozenClock()
    idgen = SeqIdGen()
    conn = open_db(db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(db_path, clock=clock, idgen=idgen)
    teams: dict[str, Team] = {}
    states: dict[str, dict[str, WorkflowState]] = {}
    workspaces: dict[str, str] = {}
    with uow.write() as tx:
        home = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
        workspaces["home"] = home.id
        far = idgen.new()
        tx.teams.connection.execute(
            "INSERT INTO workspace (id, name, url_key, created_at) VALUES (?, ?, ?, ?)",
            (far, "Far", "far", "2026-11-13T12:00:00Z"),
        )
        workspaces["far"] = far
        for key, name, workspace_id in (
            ("ENG", "Engineering", home.id),
            ("OPS", "Operations", home.id),
            ("EXT", "External", far),
        ):
            team = tx.teams.insert(
                Team(
                    id=idgen.new(),
                    workspace_id=workspace_id,
                    key=key,
                    name=name,
                    created_at=clock.now(),
                    updated_at=clock.now(),
                )
            )
            teams[key] = team
            states[key] = {}
            for state_name, state_type, position, is_default in STATES:
                states[key][state_name] = tx.states.insert(
                    WorkflowState(
                        id=idgen.new(),
                        team_id=team.id,
                        name=state_name,
                        type=state_type,
                        position=position,
                        is_default=is_default,
                    )
                )
        member = tx.members.insert(
            Member(
                id=idgen.new(),
                workspace_id=home.id,
                name="ana",
                display_name="Ana",
                email="ana@example.com",
                created_at=clock.now(),
            )
        )
    seeded = SimpleNamespace(
        path=db_path,
        uow=uow,
        clock=clock,
        idgen=idgen,
        teams=teams,
        states=states,
        workspaces=workspaces,
        member=member,
        actor=Actor(member_id=member.id),
    )
    try:
        yield seeded
    finally:
        uow.close()


def _issue(tx: object, world: SimpleNamespace, *, team: str = "ENG", **overrides) -> Issue:
    """One issue of `team`, numbered inside its own team, in the workspace of that team."""
    selected = world.teams[team]
    number = tx.issues.next_number(selected.id)  # type: ignore[attr-defined]
    data: dict[str, object] = {
        "id": world.idgen.new(),
        "workspace_id": selected.workspace_id,
        "team_id": selected.id,
        "number": number,
        "identifier": f"{selected.key}-{number}",
        "title": f"{selected.key} issue {number}",
        "state_id": world.states[team]["Todo"].id,
        "created_at": world.clock.now(),
        "updated_at": world.clock.now(),
    }
    data.update(overrides)
    return tx.issues.insert(Issue(**data))  # type: ignore[attr-defined,arg-type]


def _relation_rows(world: SimpleNamespace) -> list[tuple[str, str, str]]:
    """Every stored edge as `(issue_id, related_issue_id, type)`."""
    conn = open_db(world.path)
    try:
        rows = conn.execute(
            "SELECT issue_id, related_issue_id, type FROM issue_relation ORDER BY created_at, id"
        ).fetchall()
    finally:
        conn.close()
    return [(row["issue_id"], row["related_issue_id"], row["type"]) for row in rows]


def _history_rows(world: SimpleNamespace) -> list[tuple[str, str | None, str | None, str | None]]:
    """Every history row as `(field, old_value, new_value, actor_id)` in insertion order."""
    conn = open_db(world.path)
    try:
        rows = conn.execute(
            "SELECT field, old_value, new_value, actor_id FROM issue_history ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [(row["field"], row["old_value"], row["new_value"], row["actor_id"]) for row in rows]


# --- the scope of an edge ------------------------------------------------------------------


def test_an_edge_between_two_teams_of_one_workspace_is_allowed(world) -> None:
    with world.uow.write() as tx:
        local = _issue(tx, world, team="ENG")
        far_end = _issue(tx, world, team="OPS")

    stored = relate_issues(
        world.uow,
        RelateIssues(issue=local.identifier, related=far_end.identifier),
        world.actor,
    )

    assert {stored.issue_id, stored.related_issue_id} == {local.id, far_end.id}
    assert len(_relation_rows(world)) == 1


def test_an_edge_across_workspaces_is_refused(world) -> None:
    with world.uow.write() as tx:
        local = _issue(tx, world, team="ENG")
        far_end = _issue(tx, world, team="EXT")

    with pytest.raises(Validation) as excinfo:
        relate_issues(
            world.uow,
            RelateIssues(issue=local.identifier, related=far_end.identifier),
            world.actor,
        )

    assert excinfo.value.rule == "RELATION_WORKSPACE_MISMATCH"
    assert excinfo.value.code == "RELATION_WORKSPACE_MISMATCH"
    assert excinfo.value.details["source_workspace_id"] == world.workspaces["home"]
    assert excinfo.value.details["target_workspace_id"] == world.workspaces["far"]
    assert _relation_rows(world) == []
    assert _history_rows(world) == []


def test_an_issue_cannot_be_related_to_itself(world) -> None:
    with world.uow.write() as tx:
        lonely = _issue(tx, world)

    with pytest.raises(Validation) as excinfo:
        relate_issues(
            world.uow,
            RelateIssues(issue=lonely.identifier, related=lonely.identifier),
            world.actor,
        )

    assert excinfo.value.rule == "RELATION_SELF"
    assert _relation_rows(world) == []


def test_a_missing_reference_is_not_found_with_the_reference_it_received(world) -> None:
    with world.uow.write() as tx:
        lonely = _issue(tx, world)

    with pytest.raises(NotFound) as excinfo:
        relate_issues(
            world.uow, RelateIssues(issue=lonely.identifier, related="ENG-404"), world.actor
        )

    assert excinfo.value.resource == "issue"
    assert excinfo.value.key == "ENG-404"
    assert _relation_rows(world) == []


# --- one edge, one row ---------------------------------------------------------------------


def test_the_same_edge_twice_is_a_duplicate_and_one_row(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)

    relate_issues(
        world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )
    with pytest.raises(Conflict) as excinfo:
        relate_issues(
            world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
        )

    assert excinfo.value.rule == "RELATION_DUPLICATE"
    assert excinfo.value.code == "RELATION_DUPLICATE"
    assert len(_relation_rows(world)) == 1


@pytest.mark.parametrize("kind", ["related", "similar", "duplicate"])
def test_the_reverse_spelling_of_a_symmetric_edge_is_the_same_duplicate(world, kind: str) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)

    relate_issues(
        world.uow,
        RelateIssues(issue=first.identifier, related=second.identifier, type=kind),
        world.actor,
    )
    with pytest.raises(Conflict) as excinfo:
        relate_issues(
            world.uow,
            RelateIssues(issue=second.identifier, related=first.identifier, type=kind),
            world.actor,
        )

    assert excinfo.value.rule == "RELATION_DUPLICATE"
    rows = _relation_rows(world)
    assert len(rows) == 1
    assert rows[0][2] == kind


def test_blocks_keeps_the_direction_it_was_given(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        blocked = _issue(tx, world)

    stored = relate_issues(
        world.uow,
        RelateIssues(issue=blocker.identifier, related=blocked.identifier, type="blocks"),
        world.actor,
    )

    assert (stored.issue_id, stored.related_issue_id) == (blocker.id, blocked.id)
    assert _relation_rows(world) == [(blocker.id, blocked.id, "blocks")]


def test_blocked_by_is_stored_as_the_inverse_blocks(world) -> None:
    with world.uow.write() as tx:
        blocked = _issue(tx, world)
        blocker = _issue(tx, world)

    stored = relate_issues(
        world.uow,
        RelateIssues(issue=blocked.identifier, related=blocker.identifier, type="blocked_by"),
        world.actor,
    )

    assert (stored.issue_id, stored.related_issue_id) == (blocker.id, blocked.id)
    assert _relation_rows(world) == [(blocker.id, blocked.id, "blocks")]


def test_an_unknown_type_is_a_field_error_before_any_read(world) -> None:
    # Neither reference exists: the token is rejected before the first read, so the failure
    # is the field and not the missing issue.
    with pytest.raises(Validation) as excinfo:
        relate_issues(
            world.uow,
            RelateIssues(issue="ENG-404", related="ENG-405", type="depends_on"),
            world.actor,
        )

    assert excinfo.value.rule == "COMMAND_FIELD"
    assert excinfo.value.field == "type"
    assert _relation_rows(world) == []


# --- history ------------------------------------------------------------------------------


def test_relate_leaves_one_history_row_of_the_relation_kind(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)

    relate_issues(
        world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )

    assert _history_rows(world) == [
        ("relation", None, f"related {second.identifier}", world.member.id)
    ]


def test_the_history_of_a_blocks_edge_reads_from_the_side_that_acted(world) -> None:
    with world.uow.write() as tx:
        blocker = _issue(tx, world)
        blocked = _issue(tx, world)

    relate_issues(
        world.uow,
        RelateIssues(issue=blocker.identifier, related=blocked.identifier, type="blocks"),
        world.actor,
    )
    relate_issues(
        world.uow,
        RelateIssues(issue=blocked.identifier, related=blocker.identifier, type="related"),
        world.actor,
    )

    assert [row[2] for row in _history_rows(world)] == [
        f"blocks {blocked.identifier}",
        f"related {blocker.identifier}",
    ]


# --- the undo -----------------------------------------------------------------------------


def test_unrelate_deletes_the_edge_and_records_it(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)
    relate_issues(
        world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )

    result = unrelate_issues(
        world.uow, UnrelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )

    assert result is None
    assert _relation_rows(world) == []
    assert _history_rows(world)[-1] == (
        "relation",
        f"related {second.identifier}",
        None,
        world.member.id,
    )


def test_unrelate_without_a_type_removes_whichever_edge_joins_the_pair(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)
    relate_issues(
        world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )

    unrelate_issues(
        world.uow, UnrelateIssues(issue=second.identifier, related=first.identifier), world.actor
    )

    assert _relation_rows(world) == []


def test_unrelate_addresses_the_row_blocked_by_created(world) -> None:
    with world.uow.write() as tx:
        blocked = _issue(tx, world)
        blocker = _issue(tx, world)
    relate_issues(
        world.uow,
        RelateIssues(issue=blocked.identifier, related=blocker.identifier, type="blocked_by"),
        world.actor,
    )

    unrelate_issues(
        world.uow,
        UnrelateIssues(issue=blocked.identifier, related=blocker.identifier, type="blocked_by"),
        world.actor,
    )

    assert _relation_rows(world) == []


def test_unrelate_a_pair_without_an_edge_is_not_found(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)

    with pytest.raises(NotFound) as excinfo:
        unrelate_issues(
            world.uow,
            UnrelateIssues(issue=first.identifier, related=second.identifier),
            world.actor,
        )

    assert excinfo.value.resource == "relation"
    assert _relation_rows(world) == []
    assert _history_rows(world) == []


def test_unrelate_a_different_type_does_not_delete_the_stored_edge(world) -> None:
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)
    relate_issues(
        world.uow, RelateIssues(issue=first.identifier, related=second.identifier), world.actor
    )

    with pytest.raises(NotFound) as excinfo:
        unrelate_issues(
            world.uow,
            UnrelateIssues(issue=first.identifier, related=second.identifier, type="blocks"),
            world.actor,
        )

    assert excinfo.value.resource == "relation"
    assert len(_relation_rows(world)) == 1


def test_unrelate_undoes_nothing_when_the_reverse_blocks_edge_is_the_one_stored(world) -> None:
    """`blocks` is directional: the edge `second blocks first` is not `first blocks second`."""
    with world.uow.write() as tx:
        first = _issue(tx, world)
        second = _issue(tx, world)
    relate_issues(
        world.uow,
        RelateIssues(issue=second.identifier, related=first.identifier, type="blocks"),
        world.actor,
    )

    with pytest.raises(NotFound):
        unrelate_issues(
            world.uow,
            UnrelateIssues(issue=first.identifier, related=second.identifier, type="blocks"),
            world.actor,
        )

    assert _relation_rows(world) == [(second.id, first.id, "blocks")]
