"""`list_teams` cost and counters.

Two properties that a green suite did not detect before:

- the archived filter was dead (`list()` already dropped `archived_at IS NOT NULL`, so the
  Python filter after it never saw an archived team);
- the counters were an N+1: one `team` query plus one `v_team_progress` query per team.

The statement count is measured with `sqlite3.Connection.set_trace_callback`, i.e. the real
statements the UoW sends, not an estimate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from helpers_world import World
from hifa.app.teams import list_teams
from hifa.domain.entities import Team

TEAM_COUNT = 5


def add_teams(world: World, count: int) -> None:
    """Create `count` more teams so the list has `TEAM_COUNT` rows in total."""
    with world.uow.write() as tx:
        now = world.clock.now()
        for index in range(count):
            tx.teams.insert(
                Team(
                    id=world.idgen.new(),
                    workspace_id=world.workspace.id,
                    key=f"T{index:02d}",
                    name=f"Team {index:02d}",
                    created_at=now,
                    updated_at=now,
                )
            )


@contextmanager
def statements(uow: object) -> Iterator[list[str]]:
    """Collect every statement the UoW connection executes inside the block."""
    connection: sqlite3.Connection = uow.connection  # type: ignore[attr-defined]
    seen: list[str] = []
    connection.set_trace_callback(seen.append)
    try:
        yield seen
    finally:
        connection.set_trace_callback(None)


def test_list_teams_is_not_an_n_plus_one(world: World) -> None:
    """With 5 teams the list costs ≤ 3 statements (`BEGIN`, the one query, `COMMIT`).

    Before the fix: 8 statements — 1 `team` + 5 `v_team_progress` (+ BEGIN/COMMIT).
    """
    add_teams(world, TEAM_COUNT - 1)

    with statements(world.uow) as seen:
        summaries = list_teams(world.uow)

    assert len(summaries) == TEAM_COUNT
    assert len(seen) <= 3, seen
    selects = [sql for sql in seen if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects


def test_list_teams_reports_zero_for_a_team_without_issues(world: World) -> None:
    """`LEFT JOIN` + `COALESCE`: no row in the view means 0, not a missing team."""
    add_teams(world, 1)

    summaries = list_teams(world.uow)

    assert [summary.team.key for summary in summaries] == ["ENG", "T00"]
    assert [(summary.open_issues, summary.closed_issues) for summary in summaries] == [
        (0, 0),
        (0, 0),
    ]
    assert [summary.issues_count for summary in summaries] == [0, 0]


def test_list_teams_include_archived_sees_the_archived_team(world: World) -> None:
    """The archived filter is in the query, so `include_archived` really returns the team."""
    add_teams(world, 1)
    with world.uow.write() as tx:
        tx.teams.archive(world.team.id, world.clock.now())

    visible = list_teams(world.uow)
    every = list_teams(world.uow, include_archived=True)

    assert [summary.team.key for summary in visible] == ["T00"]
    assert [summary.team.key for summary in every] == ["ENG", "T00"]
    assert every[0].team.archived_at is not None
