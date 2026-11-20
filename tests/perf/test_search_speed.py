"""The performance budget of the ranked query: 10 000 issues, thirty searches, two terms.

The fixture is built by writing the rows and then rebuilding the index once — the same statement
`hifa db reindex` runs — so the measurement is the query and not the seeding. The budget is the
one the contract publishes: p50 under 50 ms, p95 under 120 ms, and the index answering every
one of the thirty.
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import Database, open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.search import SearchFilters, SearchQuery, search_issues
from hifa.app.teams import BootstrapOptions, bootstrap

NOW = datetime(2026, 11, 20, 2, 0, 0, tzinfo=UTC)
ISSUES = 10_000
RUNS = 30
#: A vocabulary wide enough that a two-term query matches tens of rows and not thousands: with a
#: narrow one every hit would compute a snippet for nearly every issue, which is a fixture
#: artefact and not the workload the budget describes.
VOCABULARY = 2_000


def _word(index: int) -> str:
    """One word of the corpus: 2 000 spellings, each in about twenty issues."""
    return f"word{index % VOCABULARY}"


def _seed(path: Path) -> None:
    conn = open_db(path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    uow = UnitOfWork(path, clock=SystemClock(), idgen=UlidGen())
    try:
        result = bootstrap(
            uow,
            BootstrapOptions(team_key="ENG", team_name="Engineering", workspace_name="Perf"),
            clock=uow.clock,
            ids=uow.idgen,
        )
        team_id = result.team_id
        workspace_id = result.workspace_id
    finally:
        uow.close()

    conn = open_db(path)
    try:
        state_id = conn.execute(
            "SELECT id FROM workflow_state WHERE team_id = ? ORDER BY position LIMIT 1",
            (team_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    rows = []
    for number in range(1, ISSUES + 1):
        title = " ".join(_word(number * 7 + offset) for offset in range(4))
        description = " ".join(_word(number * 3 + offset) for offset in range(4)) * 10
        rows.append(
            (
                f"issue-{number}",
                workspace_id,
                team_id,
                number,
                f"ENG-{number}",
                title,
                description,
                state_id,
                "2026-11-01T00:00:00Z",
                "2026-11-01T00:00:00Z",
            )
        )
    conn = open_db(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO issue (id, workspace_id, team_id, number, identifier, title, description,"
            " state_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("INSERT INTO issue_fts(issue_fts) VALUES('rebuild')")
        conn.execute("COMMIT")
    finally:
        conn.close()


@pytest.mark.slow
def test_thirty_two_term_searches_stay_inside_the_budget(tmp_path: Path) -> None:
    path = tmp_path / "perf.db"
    _seed(path)
    database = Database(path)

    durations: list[float] = []
    backends: list[str] = []
    for run in range(RUNS):
        query = SearchQuery(
            text=f"{_word(run)} {_word(run * 5 + 3)}",
            filters=SearchFilters(team="ENG"),
            limit=50,
        )
        started = time.perf_counter()
        page = search_issues(database, query, now=NOW)
        durations.append((time.perf_counter() - started) * 1000)
        backends.append(page.backend)

    p50 = statistics.median(durations)
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert backends == ["fts"] * RUNS
    assert p50 < 50.0, f"p50 {p50:.1f} ms"
    assert p95 < 120.0, f"p95 {p95:.1f} ms"
