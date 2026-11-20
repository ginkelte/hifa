"""Search against a real database: the three backends, the ranking and the visibility rules.

The fixture is the seeded world of the repository tests (team `ENG`, its states, two members,
three labels). Every assertion is about the rows a query returns and the shape of the page, not
about the text of the SQL.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite import reads
from hifa.adapters.sqlite.connection import Database, open_db
from hifa.app.search import (
    SearchFilters,
    SearchQuery,
    build_text_predicate,
    search_issues,
)

NOW = datetime(2026, 11, 20, 2, 0, 0, tzinfo=UTC)


def _search(world: World, text: str = "", **filters: object) -> Any:
    query = SearchQuery(text=text, filters=SearchFilters(**filters))  # type: ignore[arg-type]
    return search_issues(Database(world.path), query, now=NOW)


def _titles(page: Any) -> list[str]:
    return [hit.title for hit in page.hits]


def _seed(world: World, *issues: dict[str, object]) -> None:
    with world.uow.write() as tx:
        for item in issues:
            make_issue(tx, world, **item)


def _raw(world: World, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(world.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_free_text_matches_by_title_and_marks_the_term_in_the_snippet(world: World) -> None:
    _seed(
        world,
        {"title": "Deploy roto en producción", "description": "el deploy falló"},
        {"title": "Arreglar deploy de staging", "description": None},
        {"title": "Diseño del dashboard", "description": "otra cosa"},
    )

    page = _search(world, "deploy")

    assert page.backend == "fts"
    assert len(page.hits) == 2
    assert all("<mark>deploy</mark>" in hit.snippet.lower() for hit in page.hits)
    assert all(hit.score > 0 for hit in page.hits)


def test_diacritics_do_not_affect_the_search(world: World) -> None:
    _seed(world, {"title": "Diseño del dashboard"})

    page = _search(world, "diseno")

    assert _titles(page) == ["Diseño del dashboard"]
    assert page.hits[0].score != 0.0


def test_a_quoted_phrase_requires_the_words_in_order(world: World) -> None:
    _seed(world, {"title": "deploy roto"}, {"title": "roto deploy"})

    page = _search(world, '"deploy roto"')

    assert _titles(page) == ["deploy roto"]


def test_a_prefix_matches_the_word_it_starts(world: World) -> None:
    _seed(world, {"title": "Autenticación OAuth"}, {"title": "Otra cosa"})

    page = _search(world, "autent*")

    assert _titles(page) == ["Autenticación OAuth"]


def test_an_operator_shaped_query_is_text_and_returns_nothing(world: World) -> None:
    _seed(world, {"title": "Broken login"})

    page = _search(world, "foo OR bar NOT baz NEAR")

    assert page.backend == "fts"
    assert page.hits == []


def test_an_unbalanced_quote_still_matches(world: World) -> None:
    _seed(world, {"title": "Deploy roto en producción"})

    page = _search(world, 'deploy "roto')

    assert _titles(page) == ["Deploy roto en producción"]


def test_an_exact_identifier_wins_over_issues_whose_title_carries_the_key(world: World) -> None:
    _seed(world, *[{"title": f"eng {index}"} for index in range(5)], {"title": "Cualquier cosa"})
    identifier = _raw(world, "SELECT identifier FROM issue WHERE title = 'Cualquier cosa'")[0][0]

    page = _search(world, identifier)

    assert page.hits[0].identifier == identifier
    # Band 0 is the exact identifier: its score is the bm25 of a row the index did not match,
    # which is 0.0, and it is still first.
    assert page.hits[0].score == 0.0


def test_a_title_match_ranks_above_a_description_match(world: World) -> None:
    _seed(
        world,
        {"title": "login", "description": "una descripción larga " * 20},
        {"title": "otra cosa", "description": "menciona login una vez"},
    )

    page = _search(world, "login")

    assert _titles(page) == ["login", "otra cosa"]
    assert page.hits[0].score > page.hits[1].score


def test_trashed_and_archived_issues_are_excluded_by_default_and_return_with_all(
    world: World,
) -> None:
    _seed(
        world,
        {"title": "Deploy vigente"},
        {"title": "Deploy archivado", "archived_at": NOW},
        {"title": "Deploy borrado", "trashed": True},
    )

    default = _search(world, "deploy")
    everything = _search(world, "deploy", is_="all")

    assert _titles(default) == ["Deploy vigente"]
    assert sorted(_titles(everything)) == [
        "Deploy archivado",
        "Deploy borrado",
        "Deploy vigente",
    ]


def test_the_filters_compose_with_the_text(world: World) -> None:
    _seed(
        world,
        {
            "title": "Deploy roto",
            "state_id": world.states["In Progress"].id,
            "priority": 1,
        },
        {"title": "Deploy roto", "state_id": world.states["Done"].id, "priority": 1},
    )
    with world.uow.write() as tx:
        bug = world.labels["bug"]
        issue = tx.issues.get_by_identifier("ENG-1")
        assert issue is not None
        tx.issues.add_label(issue.id, bug.id)

    page = _search(world, "deploy", state="In Progress", label="bug", priority="1")

    assert len(page.hits) == 1
    assert page.hits[0].state_name == "In Progress"


def test_a_filters_only_search_adds_no_text_predicate(world: World) -> None:
    _seed(
        world,
        {"title": "Uno", "state_id": world.states["Done"].id},
        {"title": "Dos", "state_id": world.states["Done"].id},
        {"title": "Tres"},
    )

    page = _search(world, state="Done")

    assert page.backend == "none"
    assert sorted(_titles(page)) == ["Dos", "Uno"]


def test_the_like_path_still_answers_when_the_index_is_gone(world: World) -> None:
    _seed(world, {"title": "Deploy roto en producción"})
    with sqlite3.connect(world.path) as conn:
        conn.execute("DROP TABLE issue_fts")

    page = _search(world, "deploy")

    assert page.backend == "like"
    assert _titles(page) == ["Deploy roto en producción"]
    assert page.hits[0].score == 0.0
    assert "<mark>Deploy</mark>" in page.hits[0].snippet


def test_a_purely_negative_query_keeps_the_rows_without_a_description(world: World) -> None:
    """The `NOT (…)` block treats a NULL description as empty: the row survives it."""
    _seed(
        world,
        {"title": "Deploy roto", "description": "nada que ver"},
        {"title": "Otra cosa", "description": None},
        {"title": "Otra mas", "description": "tiene bug dentro"},
    )

    page = _search(world, "-bug")

    assert page.backend == "like"
    assert sorted(_titles(page)) == ["Deploy roto", "Otra cosa"]
    assert all(hit.score == 0.0 for hit in page.hits)
    assert all("<mark>" not in hit.snippet for hit in page.hits)


def test_a_negative_term_also_survives_a_null_description_beside_a_positive_one(
    world: World,
) -> None:
    _seed(
        world,
        {"title": "Deploy limpio", "description": None},
        {"title": "Deploy con bug", "description": "tiene bug"},
    )

    page = _search(world, "deploy -bug")

    assert page.backend == "fts"
    assert _titles(page) == ["Deploy limpio"]


def test_an_injection_in_a_filter_returns_no_rows_and_leaves_the_table_alone(
    world: World,
) -> None:
    _seed(world, {"title": "Deploy roto"})
    before = _raw(world, "SELECT COUNT(*) FROM issue")[0][0]

    page = _search(world, "deploy", label="x' OR 1=1 --")

    assert page.hits == []
    assert _raw(world, "SELECT COUNT(*) FROM issue")[0][0] == before


def test_pagination_asks_for_one_more_row_and_reports_has_more(world: World) -> None:
    _seed(world, *[{"title": f"pago {index}"} for index in range(120)])

    first = search_issues(Database(world.path), SearchQuery(text="pago", limit=50), now=NOW)
    last = search_issues(
        Database(world.path), SearchQuery(text="pago", limit=50, offset=100, count=True), now=NOW
    )

    assert len(first.hits) == 50
    assert first.has_more is True
    assert len(last.hits) == 20
    assert last.has_more is False
    assert last.total == 120


def test_the_query_plan_of_the_ranked_query_does_not_scan_the_issue_table(world: World) -> None:
    _seed(world, {"title": "Deploy roto"})
    conn = open_db(world.path, readonly=True)
    try:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            WITH fts AS (
              SELECT rowid AS rid, bm25(issue_fts, 10.0, 1.0, 0.0) AS bm,
                     snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12) AS snip
              FROM issue_fts WHERE issue_fts MATCH ?
            ),
            title_hit AS (SELECT rowid AS rid FROM issue_fts WHERE issue_fts MATCH ?)
            SELECT i.id FROM issue i
            JOIN workflow_state s ON s.id = i.state_id
            LEFT JOIN fts f ON f.rid = i.rowid
            LEFT JOIN title_hit t ON t.rid = i.rowid
            WHERE (f.rid IS NOT NULL OR i.identifier LIKE ?)
              AND i.team_id IN (SELECT id FROM team WHERE key = ?)
              AND i.trashed = 0 AND i.archived_at IS NULL
            """,
            ('"deploy"', 'title : ("deploy")', "", "ENG"),
        ).fetchall()
    finally:
        conn.close()

    plan_text = "\n".join(str(row["detail"]) for row in plan)

    # `SCAN issue_fts` is the index being read, which is the point of the query; the table this
    # assertion is about is `issue`, and `\b` keeps the two apart.
    assert re.search(r"SCAN (i|issue)\b", plan_text) is None
    assert "SEARCH i USING INDEX" in plan_text


def test_the_snippet_is_not_escaped_by_the_component(world: World) -> None:
    _seed(world, {"title": "Deploy the <form> tag", "description": "con <b>markup</b>"})

    page = _search(world, "deploy")

    assert "<form>" in page.hits[0].snippet
    assert "<mark>" in page.hits[0].snippet


class _FlakyConnection:
    """A connection whose ranked query raises, and which answers everything else."""

    def __init__(self, conn: sqlite3.Connection, message: str) -> None:
        self._conn = conn
        self._message = message

    def execute(self, sql: str, *args: object, **kwargs: object) -> object:
        if "issue_fts MATCH :match" in sql:
            raise sqlite3.OperationalError(self._message)
        return self._conn.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._conn.close()


@pytest.mark.parametrize(
    "message",
    [
        "fts5: syntax error near something",
        "database disk image is malformed",
        "no such module: fts5",
        "no such table: main.issue_fts",
    ],
)
def test_each_of_the_four_retry_messages_falls_back_to_like(world: World, message: str) -> None:
    """The four strings of the retry list are the only ones that trigger the fallback."""
    _seed(world, {"title": "Deploy roto"})
    connection = _FlakyConnection(open_db(world.path, readonly=True), message)
    try:
        rows, _total, backend = reads.search(
            connection,  # type: ignore[arg-type]
            text=build_text_predicate("deploy"),
            filters=SearchFilters(),
            order="relevance",
            limit=50,
            offset=0,
            count=False,
            today="",
        )
    finally:
        connection.close()

    assert backend == "like"
    assert [row.identifier for row in rows] == ["ENG-1"]


def test_a_match_error_outside_the_four_messages_is_not_swallowed(world: World) -> None:
    _seed(world, {"title": "Deploy roto"})
    connection = _FlakyConnection(open_db(world.path, readonly=True), "unexpected token")
    try:
        with pytest.raises(sqlite3.OperationalError):
            reads.search(
                connection,  # type: ignore[arg-type]
                text=build_text_predicate("deploy"),
                filters=SearchFilters(),
                order="relevance",
                limit=50,
                offset=0,
                count=False,
                today="",
            )
    finally:
        connection.close()


def test_an_offset_past_the_end_is_an_empty_page(world: World) -> None:
    _seed(world, {"title": "Deploy uno"}, {"title": "Deploy dos"})

    page = search_issues(
        Database(world.path), SearchQuery(text="deploy", offset=100, count=True), now=NOW
    )

    assert page.hits == []
    assert page.has_more is False
    assert page.total == 2


def test_a_missing_team_key_returns_zero_rows_and_not_an_error(world: World) -> None:
    _seed(world, {"title": "Deploy roto"})

    page = _search(world, "deploy", team="NOPE")

    assert page.hits == []
