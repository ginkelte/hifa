"""Relations and hierarchy against a migrated database under `/var/tmp`.

The database path is explicit: `HIFA_DB` points at a scratch directory created with
`tempfile.mkdtemp(dir=/var/tmp)`, and the very same path is what `UnitOfWork` opens. Nothing
here can reach a real database under `~/.local/share/`.

What is asserted at the SQLite level, not with a mock:

- one symmetric edge has exactly one storable row: the write path stores the canonical order
  and a raw `INSERT` of the reversed pair is refused by `UNIQUE(issue_id, related_issue_id, type)`;
- one row answers both ends of the edge;
- the hierarchy and scope refusals, with the subtree height read from the children rows.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite.connection import open_db
from hifa.app.commands import Actor, UpdateIssue
from hifa.app.issues import update_issue
from hifa.config import resolve_db_path
from hifa.domain import rules
from hifa.domain.entities import Issue, IssueRelation, Team, WorkflowState
from hifa.domain.enums import RelationType, WorkflowStateType
from hifa.domain.errors import Conflict, Validation

DB_ROOT = Path("/var/tmp")


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh database under `/var/tmp`, exported through `HIFA_DB` before anything opens it."""
    directory = Path(tempfile.mkdtemp(prefix="hifa-t09-relations-", dir=DB_ROOT))
    path = directory / "hifa.db"
    monkeypatch.setenv("HIFA_DB", str(path))
    resolved, source, config_file = resolve_db_path(env={"HIFA_DB": str(path)}, cwd=directory)
    assert (resolved, source, config_file) == (path.resolve(), "env", None)
    assert "local/share" not in str(resolved)
    try:
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _relation(
    world: World, issue_id: str, related_issue_id: str, type: RelationType
) -> IssueRelation:
    return IssueRelation(
        id=world.idgen.new(),  # type: ignore[attr-defined]
        issue_id=issue_id,
        related_issue_id=related_issue_id,
        type=type,
        created_at=world.clock.now(),  # type: ignore[attr-defined]
    )


def _link(tx: object, parent: Issue, child: Issue) -> None:
    """Writes `parent_id` through the repository, as a reparent would."""
    tx.issues.update(child.model_copy(update={"parent_id": parent.id}))  # type: ignore[attr-defined]


def _add_team(
    tx: object, world: World, key: str, *, workspace_id: str | None = None
) -> tuple[Team, WorkflowState]:
    """A second team with one state, in `workspace_id` (the seeded workspace by default)."""
    now = world.clock.now()  # type: ignore[attr-defined]
    team = tx.teams.insert(  # type: ignore[attr-defined]
        Team(
            id=world.idgen.new(),  # type: ignore[attr-defined]
            workspace_id=workspace_id or world.workspace.id,
            key=key,
            name=key.title(),
            created_at=now,
            updated_at=now,
        )
    )
    state = tx.states.insert(  # type: ignore[attr-defined]
        WorkflowState(
            id=world.idgen.new(),  # type: ignore[attr-defined]
            team_id=team.id,
            name="Todo",
            type=WorkflowStateType.UNSTARTED,
            position=1.0,
            is_default=True,
        )
    )
    return team, state


# --- one edge, one stored order ---------------------------------------------------------


def test_the_reversed_symmetric_edge_fails_at_the_database(world: World) -> None:
    """The reversed pair is refused by SQLite, not by a mock and not by a pre-check."""
    with world.uow.write() as tx:
        first = make_issue(tx, world)
        second = make_issue(tx, world)
        # The caller hands the pair reversed; the write path stores the canonical order.
        stored = tx.issues.add_relation(_relation(world, second.id, first.id, RelationType.RELATED))
        assert (stored.issue_id, stored.related_issue_id) == (first.id, second.id)

        statements: list[str] = []
        tx.issues.connection.set_trace_callback(statements.append)
        try:
            with pytest.raises(Conflict) as excinfo:
                tx.issues.add_relation(_relation(world, second.id, first.id, RelationType.RELATED))
        finally:
            tx.issues.connection.set_trace_callback(None)

    assert excinfo.value.rule == "RELATION_DUPLICATE"
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)
    assert "issue_relation" in str(excinfo.value.__cause__)
    inserts = [statement for statement in statements if "INSERT INTO issue_relation" in statement]
    assert len(inserts) == 1
    # The statement SQLite refused already carried the canonical order.
    assert inserts[0].index(first.id) < inserts[0].index(second.id)

    conn = open_db(world.path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM issue_relation").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO issue_relation (id, issue_id, related_issue_id, type, created_at)"
                " VALUES (?, ?, ?, 'related', ?)",
                ("01RAWR", first.id, second.id, "2026-11-08T12:00:00Z"),
            )
    finally:
        conn.close()
    assert count == 1


def test_one_symmetric_row_answers_both_ends(world: World) -> None:
    with world.uow.write() as tx:
        first = make_issue(tx, world)
        second = make_issue(tx, world)
        edge = tx.issues.add_relation(_relation(world, second.id, first.id, RelationType.DUPLICATE))
        assert tx.issues.relations(first.id) == [edge]
        assert tx.issues.relations(second.id) == [edge]
        assert edge.type.name_as_seen("out") == "duplicate"
        assert edge.type.name_as_seen("in") == "duplicate"


def test_blocks_keeps_its_direction_and_reads_as_blocked_by_from_the_target(world: World) -> None:
    with world.uow.write() as tx:
        blocker = make_issue(tx, world)
        blocked = make_issue(tx, world)
        edge = tx.issues.add_relation(_relation(world, blocker.id, blocked.id, RelationType.BLOCKS))
        assert (edge.issue_id, edge.related_issue_id) == (blocker.id, blocked.id)
        from_blocker = tx.issues.relations(blocker.id)
        from_blocked = tx.issues.relations(blocked.id)
        assert from_blocker == from_blocked == [edge]
        assert from_blocker[0].type.name_as_seen("out") == "blocks"
        assert from_blocked[0].type.name_as_seen("in") == "blocked_by"
        # `blocks` is not symmetric: the opposite statement is a different edge.
        reverse = tx.issues.add_relation(
            _relation(world, blocked.id, blocker.id, RelationType.BLOCKS)
        )
        assert (reverse.issue_id, reverse.related_issue_id) == (blocked.id, blocker.id)
        assert len(tx.issues.relations(blocker.id)) == 2


# --- the four hierarchy refusals and the two scope refusals -----------------------------
#
# The hierarchy refusals are asserted through `update_issue`, the only production caller of the
# rule, so a call shape the application never builds cannot make the suite green. After every
# rejection `parent_id` is read straight from SQLite: the write must not have happened.


def _actor(world: World) -> Actor:
    """The member the use cases run as."""
    return Actor(member_id=world.members["ana"].id)


def _stored_parent_id(world: World, issue: Issue) -> str | None:
    """`parent_id` as the database holds it, read outside the unit of work that rejected a write."""
    conn = sqlite3.connect(world.path)
    try:
        row = conn.execute("SELECT parent_id FROM issue WHERE id = ?", (issue.id,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def test_a_self_parent_is_rejected(world: World) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
    with pytest.raises(Validation) as excinfo:
        update_issue(
            world.uow,
            UpdateIssue(issue=issue.identifier, parent=issue.identifier),
            _actor(world),
        )
    assert excinfo.value.rule == "PARENT_SELF"
    assert _stored_parent_id(world, issue) is None


def test_a_parent_from_another_team_is_rejected(world: World) -> None:
    with world.uow.write() as tx:
        child = make_issue(tx, world)
        ops_team, ops_state = _add_team(tx, world, "OPS")
        make_issue(tx, world, team_id=ops_team.id, state_id=ops_state.id, identifier="OPS-1")
    with pytest.raises(Validation) as excinfo:
        update_issue(world.uow, UpdateIssue(issue=child.identifier, parent="OPS-1"), _actor(world))
    assert excinfo.value.rule == "PARENT_TEAM_MISMATCH"
    assert _stored_parent_id(world, child) is None


def test_a_three_level_cycle_is_rejected(world: World) -> None:
    """`first -> second -> third`: hanging `first` below `third` closes the loop."""
    with world.uow.write() as tx:
        home = make_issue(tx, world)
        first = make_issue(tx, world, parent_id=home.id)
        second = make_issue(tx, world)
        third = make_issue(tx, world)
        _link(tx, first, second)
        _link(tx, second, third)
    assert _stored_parent_id(world, first) == home.id
    with pytest.raises(Validation) as excinfo:
        update_issue(
            world.uow, UpdateIssue(issue=first.identifier, parent=third.identifier), _actor(world)
        )
    assert excinfo.value.rule == "PARENT_CYCLE"
    assert _stored_parent_id(world, first) == home.id, "the cycle never reaches parent_id"


def test_moving_a_subtree_past_the_depth_bound_is_rejected(world: World) -> None:
    """A height-2 subtree under a level-4 issue would leave its deepest leaf at level 7."""
    with world.uow.write() as tx:
        chain = [make_issue(tx, world) for _ in range(4)]
        for parent, child in zip(chain, chain[1:], strict=False):
            _link(tx, parent, child)
        home = make_issue(tx, world)
        moving = [make_issue(tx, world) for _ in range(3)]
        _link(tx, moving[0], moving[1])
        _link(tx, moving[1], moving[2])
        _link(tx, home, moving[0])
        target, root = chain[-1], moving[0]
        assert len(tx.issues.ancestor_chain(target.id)) == 4
        assert tx.issues.subtree_height(root.id) == 2
    with pytest.raises(Validation) as excinfo:
        update_issue(
            world.uow,
            UpdateIssue(issue=root.identifier, parent=target.identifier),
            _actor(world),
        )
    assert excinfo.value.rule == "PARENT_DEPTH_EXCEEDED"
    assert excinfo.value.details["depth"] == 7
    assert _stored_parent_id(world, root) == home.id


def test_a_subtree_that_lands_on_the_last_level_is_accepted(world: World) -> None:
    """Level-3 parent + the moved issue + one level below = exactly five levels."""
    with world.uow.write() as tx:
        chain = [make_issue(tx, world) for _ in range(3)]
        for parent, child in zip(chain, chain[1:], strict=False):
            _link(tx, parent, child)
        moving = [make_issue(tx, world) for _ in range(2)]
        _link(tx, moving[0], moving[1])
        target, root = chain[-1], moving[0]
        assert len(tx.issues.ancestor_chain(target.id)) == 3
        assert tx.issues.subtree_height(root.id) == 1
    reparented = update_issue(
        world.uow,
        UpdateIssue(issue=root.identifier, parent=target.identifier),
        _actor(world),
    )
    assert reparented.parent_id == target.id
    assert _stored_parent_id(world, root) == target.id


def test_a_relation_of_an_issue_with_itself_is_rejected(world: World) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        with pytest.raises(Validation) as excinfo:
            rules.validate_relation(issue.id, issue.id, RelationType.RELATED, [])
        assert excinfo.value.rule == "RELATION_SELF"


def test_a_relation_across_workspaces_is_rejected(world: World) -> None:
    with world.uow.write() as tx:
        local = make_issue(tx, world)
        other_workspace = world.idgen.new()  # type: ignore[attr-defined]
        tx.teams.connection.execute(
            "INSERT INTO workspace (id, name, url_key, created_at) VALUES (?, ?, ?, ?)",
            (other_workspace, "Other", "other", "2026-11-08T12:00:00Z"),
        )
        ops_team, ops_state = _add_team(tx, world, "OPS", workspace_id=other_workspace)
        far_end = make_issue(
            tx,
            world,
            workspace_id=other_workspace,
            team_id=ops_team.id,
            state_id=ops_state.id,
            identifier="OPS-1",
        )
        with pytest.raises(Validation) as excinfo:
            rules.validate_relation_scope(local, far_end)
        assert excinfo.value.rule == "RELATION_WORKSPACE_MISMATCH"


# --- the reads behind the depth bound ----------------------------------------------------


def test_the_subtree_height_and_the_ancestor_chain_cost_one_statement_each(world: World) -> None:
    """No N+1 per level: each read is one recursive query, whatever the depth."""
    with world.uow.write() as tx:
        chain = [make_issue(tx, world) for _ in range(4)]
        for parent, child in zip(chain, chain[1:], strict=False):
            _link(tx, parent, child)

        assert tx.issues.subtree_height(chain[-1].id) == 0  # a leaf has no downward path
        statements: list[str] = []
        tx.issues.connection.set_trace_callback(statements.append)
        try:
            height = tx.issues.subtree_height(chain[0].id)
            ancestors = tx.issues.ancestor_chain(chain[-1].id)
        finally:
            tx.issues.connection.set_trace_callback(None)

    assert height == 3
    assert len(ancestors) == 4
    assert list(ancestors) == [issue.id for issue in reversed(chain)]
    statements = [statement for statement in statements if statement.strip()]
    assert len(statements) == 2
    assert all("recursive" in statement.lower() for statement in statements)
