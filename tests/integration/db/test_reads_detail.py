"""`reads.issue_detail`: the `include` set is validated, never silently ignored.

The statement counts of this read are measured with `sqlite3.Connection.set_trace_callback`,
i.e. the real driver over a migrated database: a count taken from a mock would prove nothing
about what the read executes.
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import DETAIL_INCLUDE, IssueDetail, issue_detail
from hifa.app import boards
from hifa.app.commands import Actor, ArchiveIssue
from hifa.app.issues import archive_issue
from hifa.domain.entities import Comment, Label
from hifa.domain.errors import NotFound, Validation

#: Not statements of a read: the transaction boundary and the connection's own PRAGMAs.
NOT_A_READ = ("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")


def test_issue_detail_rejects_an_unknown_include(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx, pytest.raises(Validation) as caught:
        issue_detail(tx.issues.connection, "ENG-1", include=frozenset({"bogus"}))

    assert caught.value.rule == "INCLUDE_INVALID"
    assert caught.value.field == "include"
    assert caught.value.value == "bogus"


def test_issue_detail_rejects_one_unknown_name_among_known_ones(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx, pytest.raises(Validation) as caught:
        issue_detail(tx.issues.connection, "ENG-1", include=frozenset({"comments", "children"}))

    assert caught.value.rule == "INCLUDE_INVALID"
    assert caught.value.value == "children"


def test_issue_detail_accepts_every_declared_include(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx:
        detail = issue_detail(tx.issues.connection, "ENG-1", include=DETAIL_INCLUDE)

    assert isinstance(detail, IssueDetail)
    assert detail.comments == ()
    assert detail.members == {}


def test_issue_detail_without_include_loads_no_collection(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx:
        detail = issue_detail(tx.issues.connection, "ENG-1")

    assert detail is not None
    assert detail.comments == ()
    assert detail.history == ()
    assert detail.attachments == ()


def _statements(
    conn: sqlite3.Connection,
    ref: str,
    include: frozenset[str],
    *,
    include_trashed: bool = False,
) -> tuple[list[str], IssueDetail | None]:
    """Every statement `issue_detail` executes, in order, and the model it answers."""
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        detail = issue_detail(conn, ref, include=include, include_trashed=include_trashed)
    finally:
        conn.set_trace_callback(None)
    read = [
        statement for statement in seen if not statement.lstrip().upper().startswith(NOT_A_READ)
    ]
    return read, detail


def _crowded_issue(world: World) -> None:
    """The scenario's shape: a creator, 20 children, 4 labels, 30 comments, 40 history rows.

    No assignee, no parent and no relations, so the only statements the data can add are the
    ones a project, a cycle, a parent, an assignee or a creator adds, and this row carries the
    creator alone.
    """
    now = world.clock.now()  # type: ignore[attr-defined]
    with world.uow.write() as tx:
        fourth = tx.labels.insert(
            Label(
                id=world.idgen.new(),  # type: ignore[attr-defined]
                workspace_id=world.workspace.id,
                team_id=world.team.id,
                name="ui",
                color="#FFA500",
            )
        )
        issue = make_issue(tx, world, creator_id=world.members["ana"].id)
        tx.issues.set_labels(issue.id, [label.id for label in (*world.labels.values(), fourth)])
        for _ in range(20):
            make_issue(tx, world, parent_id=issue.id)
        for index in range(30):
            tx.comments.insert(
                Comment(
                    id=world.idgen.new(),  # type: ignore[attr-defined]
                    issue_id=issue.id,
                    author_id=world.members["beto"].id,
                    body=f"comment {index}",
                    created_at=now,
                    updated_at=now,
                )
            )
        tx.history.record(
            issue.id,
            [(f"field{index}", "old", "new") for index in range(40)],
            actor_id=world.members["ana"].id,
            now=now,
        )


def test_issue_detail_executes_a_fixed_number_of_statements(world) -> None:
    """12 with the full include set and 8 with none, whatever the collections hold.

    The counts are the contract: the read must not grow a statement per child, per label, per
    comment or per history row, and the team's states must cost the one statement they are and
    not a second read of the issue.
    """
    _crowded_issue(world)

    with world.uow.read() as tx:
        full, detail = _statements(tx.issues.connection, "ENG-1", DETAIL_INCLUDE)
        base, lean = _statements(tx.issues.connection, "ENG-1", frozenset())

    assert len(full) == 12, full
    assert len(base) == 8, base

    assert detail is not None
    assert len(detail.children) == 20
    assert len(detail.issue.labels) == 4
    assert len(detail.comments) == 30
    assert len(detail.history) == 40
    assert len(detail.states) > 1
    assert detail.archived_at is None

    assert lean is not None
    assert lean.comments == ()
    assert lean.history == ()
    assert lean.attachments == ()
    assert lean.children == detail.children


def test_the_states_statement_is_the_one_the_team_list_runs(world) -> None:
    """One statement, and the same set and order as `TeamRepo.states`: live columns only."""
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx:
        statements, detail = _statements(tx.issues.connection, "ENG-1", frozenset())
        listed = tx.teams.states(world.team.id)

    assert detail is not None
    states_statements = [s for s in statements if "FROM workflow_state WHERE team_id = " in s]
    assert len(states_statements) == 1, statements
    assert "archived_at IS NULL" in states_statements[0]
    assert "ORDER BY position, name" in states_statements[0]
    assert detail.states == tuple(listed)


def test_the_detail_carries_the_archive_flags_of_the_row(world) -> None:
    """`archived_at` is the row's own layer and `trashed` is always `False` on a detail."""
    with world.uow.write() as tx:
        make_issue(tx, world)
        make_issue(tx, world)

    archive_issue(world.uow, ArchiveIssue(issue="ENG-2"), Actor(source="cli", label="test"))

    with world.uow.read() as tx:
        live = issue_detail(tx.issues.connection, "ENG-1")
        archived = issue_detail(tx.issues.connection, "ENG-2")

    assert live is not None and live.archived_at is None
    assert live.trashed is False
    assert archived is not None
    assert archived.archived_at == world.clock.now()
    assert archived.trashed is False


def test_a_trashed_issue_has_no_detail(world) -> None:
    """`trashed` is `False` on every detail because a trashed row is never a detail.

    The header filters `i.trashed = 0`, so the field of the model can only ever be `False`:
    the answer for a trashed identifier is `None`. The row is trashed on the database and its
    own column is read beside the call, so this pins the mechanism and not only the answer —
    drop the filter from the header and this is the test that goes red. The live sibling is
    read too: `None` is the trashed row's answer, not a reference that resolved to nothing.
    """
    now = world.clock.now()
    with world.uow.write() as tx:
        trashed = make_issue(tx, world)
        make_issue(tx, world)
        tx.issues.trash(trashed.id, now)

    with world.uow.read() as tx:
        stored = tx.issues.connection.execute(
            "SELECT trashed FROM issue WHERE id = ?", (trashed.id,)
        ).fetchone()
        assert stored is not None and stored[0] == 1
        assert issue_detail(tx.issues.connection, "ENG-1") is None
        assert issue_detail(tx.issues.connection, trashed.id) is None
        sibling = issue_detail(tx.issues.connection, "ENG-2")

    assert sibling is not None and sibling.trashed is False


def test_a_trashed_issue_has_a_detail_when_the_read_asks_for_one(world) -> None:
    """`include_trashed=True` answers the trashed row's model, and the sibling above stays true.

    The sibling pins the **default**: a trashed reference is `None` because row 1 filters
    `i.trashed = 0`, and that is still exactly what a read without the keyword answers here.
    The keyword (D-059) binds that one predicate instead of adding a statement, so the answer
    is the row's own detail — `trashed` `True`, its title, its state — from the same eight
    statements with no include and the same twelve with `DETAIL_INCLUDE`. The column is read
    beside the calls, so a detail that came back while the row was not in the trash would not
    satisfy the test, and the identifier and the ULID are both asked for: the keyword widens
    the same predicate for both, never one of them. The `app` layer is the same call one
    connection higher, so its default and its keyword are asserted here too.
    """
    now = world.clock.now()
    with world.uow.write() as tx:
        trashed = make_issue(tx, world, title="Fix login")
        make_issue(tx, world)
        tx.issues.trash(trashed.id, now)

    with world.uow.read() as tx:
        stored = tx.issues.connection.execute(
            "SELECT trashed FROM issue WHERE id = ?", (trashed.id,)
        ).fetchone()
        assert stored is not None and stored[0] == 1
        assert issue_detail(tx.issues.connection, "ENG-1") is None
        by_identifier = issue_detail(tx.issues.connection, "ENG-1", include_trashed=True)
        by_ulid = issue_detail(tx.issues.connection, trashed.id, include_trashed=True)
        base, _ = _statements(tx.issues.connection, "ENG-1", frozenset(), include_trashed=True)
        full, _ = _statements(tx.issues.connection, "ENG-1", DETAIL_INCLUDE, include_trashed=True)
        sibling_base, _ = _statements(tx.issues.connection, "ENG-2", frozenset())
        sibling_full, _ = _statements(tx.issues.connection, "ENG-2", DETAIL_INCLUDE)

    assert len(base) == len(sibling_base), (base, sibling_base)
    assert len(full) == len(sibling_full), (full, sibling_full)

    assert by_identifier is not None
    assert by_identifier.trashed is True
    assert by_identifier.archived_at is None
    assert by_identifier.issue.identifier == "ENG-1"
    assert by_identifier.issue.title == "Fix login"
    assert by_identifier.state.name == "Todo"
    assert by_identifier.issue == by_ulid.issue if by_ulid is not None else False

    db = Database(world.path)
    assert boards.issue_detail(db, "ENG-2", include=boards.DETAIL_INCLUDE).trashed is False
    assert (
        boards.issue_detail(
            db, "ENG-1", include=boards.DETAIL_INCLUDE, include_trashed=True
        ).trashed
        is True
    )
    with pytest.raises(NotFound):
        boards.issue_detail(db, "ENG-1", include=boards.DETAIL_INCLUDE)


def test_the_detail_states_leave_an_archived_state_out(world) -> None:
    """An archived column is not offered, and two columns at one position come back by name."""
    with world.uow.write() as tx:
        make_issue(tx, world)
        done = world.states["Done"].model_copy(
            update={"archived_at": world.clock.now()}  # type: ignore[attr-defined]
        )
        tx.states.update(done)
        for name in ("Zulu", "Alfa"):
            tx.states.insert(
                world.states["Todo"].model_copy(
                    update={
                        "id": world.idgen.new(),  # type: ignore[attr-defined]
                        "name": name,
                        "is_default": False,
                        "position": 9.5,
                    }
                )
            )

    with world.uow.read() as tx:
        detail = issue_detail(tx.issues.connection, "ENG-1")
        listed = tx.teams.states(world.team.id)

    assert detail is not None
    names = [state.name for state in detail.states]
    assert names == ["Triage", "Backlog", "Todo", "In Progress", "Canceled", "Alfa", "Zulu"]
    assert "Done" not in names
    assert detail.states == tuple(listed)
