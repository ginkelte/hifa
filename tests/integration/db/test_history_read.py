"""`HistoryRepo.list_for_issue`: the read's window, its order and the rank a move wrote.

The rule under test is the one the row itself carries: a `board_order` row is dropped when the
history row the same issue wrote immediately before it, by `id`, is a `state_id` row **of the same
instant** — one `record` batch, the move and the rank it wrote. Nothing here filters in Python — the
assertion is about the rows the statement returns.
"""

from __future__ import annotations

from helpers_world import World, make_issue
from hifa.adapters.sqlite.history_repo import HistoryRepo
from hifa.adapters.sqlite.reads import issue_detail
from hifa.app.commands import Actor, MoveIssue
from hifa.app.issues import move_issue

ACTOR = Actor(source="cli", label="cli")


def _move(tx: object, issue_id: str, *, old: str, new: str, actor: str) -> None:
    """The two rows `move_issue` writes in one batch, in the order it writes them."""
    tx.history.record(  # type: ignore[attr-defined]
        issue_id,
        [("state_id", old, new), ("board_order", 1024.0, 8192.0)],
        actor_id=actor,
        now=tx.history.clock.now(),  # type: ignore[attr-defined]
    )


def test_a_rank_the_move_wrote_is_not_in_the_window(world: World) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        _move(
            tx,
            issue.id,
            old=world.states["Todo"].id,
            new=world.states["Done"].id,
            actor=world.members["ana"].id,
        )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert [row.field for row in rows] == ["state_id"]


def test_a_rank_with_no_state_row_before_it_is_in_the_window(world: World) -> None:
    """A drag inside one column writes the rank alone, and the read hands it over."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.history.record(
            issue.id,
            [("board_order", 8192.0, 10240.0)],
            actor_id=None,
            now=tx.history.clock.now(),
        )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert [(row.field, row.new_value) for row in rows] == [("board_order", "10240.0")]


def test_the_preceding_row_is_read_within_the_same_issue(world: World) -> None:
    """A `state_id` row of another issue is not the predecessor of this one's rank."""
    with world.uow.write() as tx:
        other = make_issue(tx, world)
        issue = make_issue(tx, world)
        tx.history.record(
            other.id,
            [("state_id", world.states["Todo"].id, world.states["Done"].id)],
            actor_id=None,
            now=tx.history.clock.now(),
        )
        tx.history.record(
            issue.id,
            [("board_order", 1024.0, 2048.0)],
            actor_id=None,
            now=tx.history.clock.now(),
        )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert [row.field for row in rows] == ["board_order"]


def test_a_drag_after_a_move_into_an_empty_column_is_an_event_of_its_own(world: World) -> None:
    """The state-only row of a move does not swallow the rank the next drag writes.

    `_move_once` records a rank only when it differs from the stored one, so a move into an empty
    column writes the `state_id` row alone. The next genuine drag is a second `record` call at a
    second instant, and its `board_order` row is adjacent to a `state_id` row of the same issue: the
    predicate names the batch by the shared `created_at`, so nothing but the batch is dropped. Both
    edges read this window — `HistoryRepo.list_for_issue` and `issue_detail`'s `include={"history"}`
    — so the assertion is about the statement and not about one caller's filter.
    """
    with world.uow.write() as tx:
        # `1024.0` is the rank an empty column hands the first issue, so the move below changes the
        # state and not the rank: `_move_once` then records the `state_id` row alone.
        dragged = make_issue(tx, world, board_order=1024.0)
        neighbour = make_issue(tx, world, board_order=1024.0)
        dragged_id, dragged_ref = dragged.id, dragged.identifier
        neighbour_ref = neighbour.identifier

    # The empty column: the state row, and no rank, because the rank did not change.
    move_issue(world.uow, MoveIssue(issue=dragged_ref, target_state="Backlog"), ACTOR)
    world.clock.advance(seconds=18)  # type: ignore[attr-defined]
    with world.uow.write() as tx:
        stamp = world.clock.now()  # type: ignore[attr-defined]
        tx.issues.set_state(neighbour.id, world.states["Backlog"].id, now=stamp)
    world.clock.advance(seconds=18)  # type: ignore[attr-defined]
    # The drag: a second write, a second instant, a rank that really moves this time.
    move_issue(
        world.uow,
        MoveIssue(
            issue=dragged_ref,
            target_state="Backlog",
            position="before",
            before_id=neighbour_ref,
        ),
        ACTOR,
    )

    with world.uow.read() as tx:
        raw = [
            item[0]
            for item in tx.history.connection.execute(
                "SELECT field FROM issue_history WHERE issue_id = ? ORDER BY id", (dragged_id,)
            ).fetchall()
        ]
        kept = [row.field for row in tx.history.list_for_issue(dragged_id)]
        detail = issue_detail(tx.issues.connection, dragged_ref, include=frozenset({"history"}))

    assert raw == ["state_id", "board_order"]
    # Newest-first as the repo answers it; nothing of the two writes is missing.
    assert kept == ["board_order", "state_id"]
    assert detail is not None
    assert [row.field for row in detail.history] == ["board_order", "state_id"]


def test_a_rank_written_before_the_state_row_is_not_dropped(world: World) -> None:
    """The rule is the immediately preceding row, not "a state row exists somewhere"."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.history.record(
            issue.id,
            [
                ("board_order", 1024.0, 2048.0),
                ("state_id", world.states["Todo"].id, world.states["Done"].id),
            ],
            actor_id=None,
            now=tx.history.clock.now(),
        )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert [row.field for row in rows] == ["state_id", "board_order"]


def test_the_window_is_the_newest_hundred_rows(world: World) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        for index in range(105):
            tx.history.record(
                issue.id,
                [("title", f"old {index}", f"new {index}")],
                actor_id=None,
                now=tx.history.clock.now(),
            )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert len(rows) == 100
    assert rows[0].new_value == "new 104"
    assert rows[-1].new_value == "new 5"


def test_the_window_order_is_newest_first(world: World) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        for index in range(3):
            tx.history.record(
                issue.id,
                [("title", f"old {index}", f"new {index}")],
                actor_id=None,
                now=tx.history.clock.now(),
            )

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)

    assert [row.new_value for row in rows] == ["new 2", "new 1", "new 0"]


def test_the_rank_rows_stay_out_of_a_full_window(world: World) -> None:
    """The filter and the limit are one statement: a move costs the window one row, not two."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        for _ in range(60):
            _move(
                tx,
                issue.id,
                old=world.states["Todo"].id,
                new=world.states["Done"].id,
                actor=world.members["ana"].id,
            )

    with world.uow.read() as tx:
        kept = tx.history.list_for_issue(issue.id, limit=120)

    assert len(kept) == 60
    assert {row.field for row in kept} == {"state_id"}


def test_the_repo_is_the_one_that_reads_an_issue(world: World) -> None:
    """The suppression lives in the statement, not in a caller's filter."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        _move(
            tx,
            issue.id,
            old=world.states["Todo"].id,
            new=world.states["Done"].id,
            actor=world.members["ana"].id,
        )

    with world.uow.read() as tx:
        assert isinstance(tx.history, HistoryRepo)
        assert (
            len(
                tx.history.connection.execute(
                    "SELECT * FROM issue_history WHERE issue_id = ?", (issue.id,)
                ).fetchall()
            )
            == 2
        )
        assert len(tx.history.list_for_issue(issue.id)) == 1


def test_the_read_and_the_formatter_answer_one_story(world: World) -> None:
    """The window the read returns and the sentences it resolves: one story, oldest-first."""
    from hifa.app.history import history_rows

    now = world.clock.now()  # type: ignore[attr-defined]
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.history.record(
            issue.id, [("created", None, None)], actor_id=world.members["ana"].id, now=now
        )
        tx.history.record(
            issue.id,
            [
                ("state_id", world.states["Todo"].id, world.states["Done"].id),
                ("board_order", 1024.0, 8192.0),
                ("label", None, "bug"),
            ],
            actor_id=world.members["ana"].id,
            now=now,
        )

    with world.uow.read() as tx:
        detail = issue_detail(
            tx.issues.connection, issue.identifier, include=frozenset({"history"})
        )
        assert detail is not None
        page = history_rows(
            detail.history,
            states={state.id: state.name for state in detail.states},
            members=detail.members,
            parents=detail.history_parents,
            state_colors={state.id: state.color for state in detail.states},
            now=now,
        )

    assert [row.sentence for row in page.rows] == [
        "created the issue",
        "moved from Todo to Done",
        "added the label bug",
    ]
    assert page.last_activity is not None
    assert page.last_activity.text == "Ana P\u00e9rez added the label bug \u00b7 just now"
