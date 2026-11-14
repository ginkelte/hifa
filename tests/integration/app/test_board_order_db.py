"""Integration test for the 200-move acceptance scenario of the board ranking.

Pins the scenario end to end against a real migrated database: three columns, twelve issues,
200 seeded moves through `move_issue`, and the invariants a column must hold after every one
of them — the order the moves imply, a unique `board_order` per column, and no pair of
neighbours closer than `MIN_GAP`. The fixture starts "In Progress" degraded (`ENG-7` at
`2048.0000005`, `5e-7` from `ENG-6`), so the repair that renumbers a column whose two
neighbours are closer than the minimum gap runs on the first move instead of waiting until a
column has no float gap left between two neighbours; with that repair disabled the gap
assertion below is what fails, on move 1.

The expected order is **not** read back from the database. `_sample` replays the same 200
moves over plain Python lists — `--after` inserts immediately after the anchor, no anchor
appends, a repair only renumbers and therefore preserves the order — and the test compares
the stored column against that model after each move. The invariants are checked after every
move and not only at the end for two reasons: a violation then names the move that caused it
instead of the end of the sample, and they are properties the column must hold after **each**
move, so a later move that happens to repair or mask an earlier violation cannot turn the
test green.

Nothing here spawns a process: the scenario is heavy at the acceptance level, but in this
test it is a few hundred SQLite writes, so it belongs in the default loop and carries no
`slow` marker.

RNG contract, as the scenario declares it: `random.Random(20261113)`; per sampled move the
issue, then the target state, then one value in `[0, 1)` — below `0.25`, or an empty target
column, means no anchor, otherwise a second draw picks the `--after` anchor among the other
issues of the target column as the moves have built it so far. Move 1 is pinned by the
fixture (`ENG-12` into "In Progress", no anchor) and draws nothing; moves 2-200 move an
issue other than `ENG-6`/`ENG-7`, their own state included.
"""

from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from pathlib import Path

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateIssue, CreateTeam, MoveIssue
from hifa.app.issues import create_issue, move_issue
from hifa.app.teams import create_team
from hifa.domain.ranking import MIN_GAP

ACTOR = Actor(source="cli", label="cli")

#: Seed of the sample, fixed by the scenario.
SEED = 20261113
#: Size of the sample, fixed by the scenario.
SAMPLE_SIZE = 200
#: One draw in four, or an empty target column, means "no anchor".
ANCHORLESS = 0.25

COLUMNS: tuple[str, ...] = ("Todo", "In Progress", "Done")

#: `?, ?, ?` for a `NOT IN` over `COLUMNS`; values stay parameterized.
_PLACEHOLDERS = ", ".join("?" for _ in COLUMNS)

#: The fixture: identifier -> (state, `board_order`), in identifier order. `ENG-7` is the
#: declared exception, `5e-7` above `ENG-6` so "In Progress" starts degraded.
FIXTURE: dict[str, tuple[str, float]] = {
    "ENG-1": ("Todo", 1024.0),
    "ENG-2": ("Todo", 2048.0),
    "ENG-3": ("Todo", 3072.0),
    "ENG-4": ("Todo", 4096.0),
    "ENG-5": ("In Progress", 1024.0),
    "ENG-6": ("In Progress", 2048.0),
    "ENG-7": ("In Progress", 2048.0000005),
    "ENG-8": ("In Progress", 3072.0),
    "ENG-9": ("Done", 1024.0),
    "ENG-10": ("Done", 2048.0),
    "ENG-11": ("Done", 3072.0),
    "ENG-12": ("Done", 4096.0),
}

#: Never moved by the sample: their tight pair is the degradation move 1 repairs.
FROZEN: frozenset[str] = frozenset({"ENG-6", "ENG-7"})

#: `(issue, target state, anchor or None)`, in the order the sample runs them.
Move = tuple[str, str, str | None]


def _prepare(path: Path) -> tuple[UnitOfWork, str]:
    """Migrated database with the fixture team, its three columns and its twelve issues."""
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(path, clock=FrozenClock(), idgen=SeqIdGen())
    team = create_team(uow, CreateTeam(key="ENG", name="Engineering"), ACTOR)
    _keep_columns(path, team.id)
    for identifier, (state, board_order) in FIXTURE.items():
        create_issue(
            uow,
            CreateIssue(
                team="ENG",
                title=f"Issue {identifier}",
                state=state,
                board_order=board_order,
            ),
            ACTOR,
        )
    return uow, team.id


def _keep_columns(path: Path, team_id: str) -> None:
    """Drop the template states the scenario leaves out; no issue references them yet."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            f"DELETE FROM workflow_state WHERE team_id = ? AND name NOT IN ({_PLACEHOLDERS})",
            (team_id, *COLUMNS),
        )
        conn.commit()


def _columns(conn: sqlite3.Connection, team_id: str) -> dict[str, list[tuple[str, float]]]:
    """`[(identifier, board_order)]` per column, in the declared read order, from `issue`."""
    return {
        name: conn.execute(
            """
            SELECT i.identifier, i.board_order
            FROM issue AS i
            JOIN workflow_state AS s ON s.id = i.state_id
            WHERE i.team_id = ? AND s.name = ?
            ORDER BY i.board_order, i.id
            """,
            (team_id, name),
        ).fetchall()
        for name in COLUMNS
    }


def _apply(order: dict[str, list[str]], issue: str, state: str, anchor: str | None) -> None:
    """Replay one move on the model: out of its column, then right after the anchor or last."""
    for column in order.values():
        if issue in column:
            column.remove(issue)
    if anchor is None:
        order[state].append(issue)
    else:
        assert anchor in order[state], f"{anchor} is not in {state} when {issue} moves"
        order[state].insert(order[state].index(anchor) + 1, issue)


def _sample() -> tuple[list[Move], list[dict[str, list[str]]]]:
    """The 200 moves and, after each one, the order their replay implies.

    Move 1 is the fixture's pinned move and consumes no draw; every later move consumes the
    three (or four) draws the scenario declares, in that order.
    """
    order: dict[str, list[str]] = {name: [] for name in COLUMNS}
    for identifier, (state, _) in FIXTURE.items():
        order[state].append(identifier)

    moves: list[Move] = [("ENG-12", "In Progress", None)]
    _apply(order, "ENG-12", "In Progress", None)
    implied: list[dict[str, list[str]]] = [{name: list(column) for name, column in order.items()}]

    rng = random.Random(SEED)
    movers = [identifier for identifier in FIXTURE if identifier not in FROZEN]
    while len(moves) < SAMPLE_SIZE:
        issue = rng.choice(movers)
        state = rng.choice(COLUMNS)
        others = [member for member in order[state] if member != issue]
        anchor = None if rng.random() < ANCHORLESS or not others else rng.choice(others)
        _apply(order, issue, state, anchor)
        moves.append((issue, state, anchor))
        implied.append({name: list(column) for name, column in order.items()})
    return moves, implied


def _command(issue: str, state: str, anchor: str | None) -> MoveIssue:
    """The move use-case command the sample asks for: anchored, or appended to the column."""
    if anchor is None:
        return MoveIssue(issue=issue, target_state=state)
    return MoveIssue(issue=issue, target_state=state, position="after", after_id=anchor)


def _assert_invariants(
    stored: dict[str, list[tuple[str, float]]],
    implied: dict[str, list[str]],
    *,
    step: int,
) -> None:
    """The three invariants, for every column, at move `step` of the sample."""
    for name in COLUMNS:
        column = stored[name]
        assert [identifier for identifier, _ in column] == implied[name], (
            f"move {step}: {name} is not in the order the moves imply"
        )
        positions = [board_order for _, board_order in column]
        assert len(set(positions)) == len(positions), f"move {step}: {name} repeats a board_order"
        for earlier, later in zip(positions, positions[1:], strict=False):
            assert later - earlier >= MIN_GAP, (
                f"move {step}: {name} keeps two neighbours {later - earlier!r} apart"
            )


def test_200_random_moves_keep_every_column_in_order_and_its_positions_unique(
    db_path: Path,
) -> None:
    moves, implied = _sample()
    uow, team_id = _prepare(db_path)
    try:
        with closing(sqlite3.connect(db_path)) as reader:
            seeded = _columns(reader, team_id)
            assert [board_order for _, board_order in seeded["Todo"]] == [
                1024.0,
                2048.0,
                3072.0,
                4096.0,
            ]
            assert [board_order for _, board_order in seeded["Done"]] == [
                1024.0,
                2048.0,
                3072.0,
                4096.0,
            ]
            in_progress = [board_order for _, board_order in seeded["In Progress"]]
            assert in_progress == [1024.0, 2048.0, 2048.0000005, 3072.0]
            assert in_progress[2] - in_progress[1] < MIN_GAP  # the fixture starts degraded

            for step, (issue, state, anchor) in enumerate(moves, start=1):
                move_issue(uow, _command(issue, state, anchor), ACTOR)
                _assert_invariants(_columns(reader, team_id), implied[step - 1], step=step)
    finally:
        uow.close()


def test_the_sample_starts_with_the_pinned_move_and_spares_the_frozen_pair() -> None:
    """The sample is 200 moves, opens pinned, and never moves `ENG-6`/`ENG-7` afterwards."""
    moves, _ = _sample()
    assert len(moves) == SAMPLE_SIZE
    assert moves[0] == ("ENG-12", "In Progress", None)
    assert not FROZEN & {issue for issue, _, _ in moves[1:]}
