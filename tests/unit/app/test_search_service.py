"""`SearchService`: the three text states, `has_more`, `total` and the instant it is handed.

The port is a double here, so the tests pin the decisions that belong to the service and not the
SQL: which state the predicate selects, whether the extra row becomes `has_more` or a hit, that
the adapter never sets `total` unless `--count` asked for it, and that `today` is derived from
`now` in the query's timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hifa.adapters.sqlite.reads import SearchRow
from hifa.app.search import (
    SearchError,
    SearchFilters,
    SearchPage,
    SearchQuery,
    SearchService,
    TextPredicate,
    build_text_predicate,
)

NOW = datetime(2026, 11, 20, 2, 0, 0, tzinfo=UTC)


def _row(identifier: str, *, snippet: str = "", score: float = 0.0) -> SearchRow:
    return SearchRow(
        issue_id=f"id-{identifier}",
        identifier=identifier,
        title=f"Issue {identifier}",
        state_name="Todo",
        state_type="unstarted",
        priority=0,
        due_date=None,
        assignee_display_name=None,
        updated_at="2026-11-20T00:00:00Z",
        snippet=snippet,
        score=score,
    )


class FakeReads:
    """The read port: records what the service asked, answers what the test prepared."""

    def __init__(
        self,
        *,
        rows: list[SearchRow] | None = None,
        total: int | None = None,
        backend: str = "fts",
        zones: dict[str, str] | None = None,
    ) -> None:
        self.rows = list(rows or [])
        self.total = total
        self.backend = backend
        self.zones = dict(zones or {})
        self.calls: list[dict[str, Any]] = []
        self.timezone_calls: list[str] = []

    def search(self, **kwargs: Any) -> tuple[list[SearchRow], int | None, str]:
        self.calls.append(kwargs)
        # The adapter answers limit + 1 rows at most: the double honours the same contract.
        return self.rows, self.total, self.backend

    def team_timezone(self, *, team_key: str) -> str | None:
        self.timezone_calls.append(team_key)
        return self.zones.get(team_key)


def _service(reads: FakeReads) -> SearchService:
    return SearchService(reads)


def test_an_empty_query_with_no_filter_is_a_refusal_of_the_use_case() -> None:
    reads = FakeReads()

    with pytest.raises(SearchError) as raised:
        _service(reads).search(SearchQuery(text="   "), now=NOW)

    assert raised.value.code == "query_empty"
    assert raised.value.field == "q"
    assert reads.calls == []


def test_a_filters_only_query_is_a_search_and_not_a_refusal() -> None:
    """`--state Done` has no text at all and still runs: the filters are the query."""
    reads = FakeReads(rows=[_row("ENG-1")], backend="none")

    page = _service(reads).search(
        SearchQuery(text="", filters=SearchFilters(state="Done")), now=NOW
    )

    assert page.backend == "none"
    assert [hit.identifier for hit in page.hits] == ["ENG-1"]
    assert isinstance(reads.calls[0]["text"], TextPredicate)
    assert reads.calls[0]["text"].match is None
    assert reads.calls[0]["text"].like_terms == ()
    assert reads.calls[0]["text"].not_terms == ()


def test_the_service_hands_the_port_the_predicate_of_the_text() -> None:
    reads = FakeReads(rows=[])

    _service(reads).search(SearchQuery(text="deploy -bug"), now=NOW)

    assert reads.calls[0]["text"] == build_text_predicate("deploy -bug")


def test_the_extra_row_becomes_has_more_and_never_a_hit() -> None:
    reads = FakeReads(rows=[_row("ENG-1"), _row("ENG-2"), _row("ENG-3")], backend="fts")

    page = _service(reads).search(SearchQuery(text="deploy", limit=2), now=NOW)

    assert len(page.hits) == 2
    assert page.has_more is True
    assert reads.calls[0]["limit"] == 2


def test_a_page_shorter_than_the_limit_has_no_more() -> None:
    reads = FakeReads(rows=[_row("ENG-1")], backend="fts")

    page = _service(reads).search(SearchQuery(text="deploy", limit=50), now=NOW)

    assert page.has_more is False


def test_the_total_is_none_unless_the_caller_asked_for_it() -> None:
    reads = FakeReads(rows=[_row("ENG-1")], total=None)

    page = _service(reads).search(SearchQuery(text="deploy"), now=NOW)

    assert page.total is None
    assert reads.calls[0]["count"] is False


def test_the_total_is_the_filtered_set_and_not_the_page() -> None:
    """Twenty matches with `offset=100` is an empty page whose total is still 20."""
    reads = FakeReads(rows=[], total=20)

    page = _service(reads).search(SearchQuery(text="deploy", offset=100, count=True), now=NOW)

    assert page.hits == []
    assert page.has_more is False
    assert page.total == 20


def test_a_limit_outside_the_range_names_the_input_and_not_the_code() -> None:
    with pytest.raises(SearchError) as too_big:
        _service(FakeReads()).search(SearchQuery(text="x", limit=201), now=NOW)
    with pytest.raises(SearchError) as negative:
        _service(FakeReads()).search(SearchQuery(text="x", offset=-1), now=NOW)

    assert (too_big.value.code, too_big.value.field) == ("invalid_limit", "limit")
    assert (negative.value.code, negative.value.field) == ("invalid_limit", "offset")


def test_a_relative_due_window_reads_the_team_timezone_and_not_the_utc_day() -> None:
    reads = FakeReads(rows=[], zones={"ENG": "America/Santiago"})

    _service(reads).search(
        SearchQuery(text="", filters=SearchFilters(team="ENG", due="today")), now=NOW
    )

    assert reads.timezone_calls == ["ENG"]
    assert reads.calls[0]["today"] == "2026-11-19"


def test_a_relative_due_window_with_no_team_is_the_utc_day() -> None:
    reads = FakeReads(rows=[])

    _service(reads).search(SearchQuery(text="", filters=SearchFilters(due="today")), now=NOW)

    assert reads.timezone_calls == []
    assert reads.calls[0]["today"] == "2026-11-20"


def test_an_unknown_team_falls_back_to_utc() -> None:
    reads = FakeReads(rows=[], zones={})

    _service(reads).search(
        SearchQuery(text="", filters=SearchFilters(team="NOPE", due="week")), now=NOW
    )

    assert reads.timezone_calls == ["NOPE"]
    assert reads.calls[0]["today"] == "2026-11-20"


def test_an_absolute_due_date_reads_no_timezone_at_all() -> None:
    reads = FakeReads(rows=[])

    _service(reads).search(
        SearchQuery(text="", filters=SearchFilters(team="ENG", due="2026-12-01")), now=NOW
    )

    assert reads.timezone_calls == []
    assert reads.calls[0]["today"] == ""


def test_the_score_and_the_snippet_arrive_from_the_port_untouched() -> None:
    """The service does not escape, truncate or re-mark a snippet the adapter built."""
    reads = FakeReads(rows=[_row("ENG-1", snippet="a <mark>deploy</mark> b", score=1.5)])

    page = _service(reads).search(SearchQuery(text="deploy"), now=NOW)

    assert page.hits[0].snippet == "a <mark>deploy</mark> b"
    assert page.hits[0].score == 1.5


def test_the_page_is_the_five_keys_of_the_envelope() -> None:
    page = _service(FakeReads(rows=[])).search(SearchQuery(text="deploy"), now=NOW)

    assert isinstance(page, SearchPage)
    assert page.backend == "fts"
    assert page.took_ms >= 0.0
