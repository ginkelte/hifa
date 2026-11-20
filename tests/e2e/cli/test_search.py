"""`hifa search`: the flags, the two output formats and the exit codes of the verb.

Every scenario of `specs/acceptance/001-mvp-v1/10-search.feature` lives here or in
`test_db_reindex.py`, and the shapes are the ones the contract publishes: the five keys of the
envelope, the eleven fields of a hit, the six columns of the table and `0, 2, 6` as the exit
set.
"""

from __future__ import annotations

import json

import pytest

from conftest import Cli, error_json, json_stdout, plain

ENVELOPE_KEYS = ("hits", "total", "has_more", "backend", "took_ms")
HIT_KEYS = (
    "issue_id",
    "identifier",
    "title",
    "state_name",
    "state_type",
    "priority",
    "due_date",
    "assignee_display_name",
    "updated_at",
    "snippet",
    "score",
)
SEARCH_HEADER = "IDENTIFIER P STATE UPDATED TITLE MATCH"


def test_search_finds_by_title_and_returns_a_snippet(cli: Cli) -> None:
    identifier = cli.create("Broken login on Safari", "--description", "no entra nadie")

    result = cli.run("search", "login", "--count", "--json")

    assert result.exit_code == 0
    payload = json_stdout(result)
    assert tuple(payload) == ENVELOPE_KEYS
    assert payload["total"] == 1
    assert payload["backend"] == "fts"
    assert payload["has_more"] is False
    hit = payload["hits"][0]
    assert tuple(hit) == HIT_KEYS
    assert hit["identifier"] == identifier
    assert "login" in hit["snippet"]
    assert hit["snippet"] == "Broken <mark>login</mark> on Safari"


def test_an_issue_in_trash_does_not_appear_in_search(cli: Cli) -> None:
    cli.create("Broken login on Safari")

    deleted = cli.run("issue", "delete", "ENG-1", "--yes")
    assert deleted.exit_code == 0, deleted.stderr
    payload = json_stdout(cli.run("search", "login", "--count", "--json"))

    assert payload["total"] == 0
    assert payload["hits"] == []


def test_trash_and_restore_keep_the_hit_without_reindexing(cli: Cli) -> None:
    cli.create("Broken login on Safari")
    assert cli.run("issue", "delete", "ENG-1", "--yes").exit_code == 0
    restored = cli.run("issue", "restore", "ENG-1")
    assert restored.exit_code == 0, restored.stderr

    payload = json_stdout(cli.run("search", "login", "--count", "--json"))

    assert payload["total"] == 1
    # The index answered: the triggers of the trash and the restore kept it in sync, and no
    # rebuild ran between them.
    assert payload["backend"] == "fts"


def test_the_table_prints_its_six_columns_and_never_a_marker(cli: Cli) -> None:
    cli.create("Fix login", "--priority", "2")

    result = cli.run("search", "login")

    assert result.exit_code == 0
    text = plain(result.stdout)
    assert text.startswith(SEARCH_HEADER)
    assert "ENG-1" in text
    assert "<mark>" not in text
    assert "</mark>" not in text


def test_an_empty_result_is_an_empty_stdout_and_a_note_on_stderr(cli: Cli) -> None:
    cli.create("Fix login")

    result = cli.run("search", "zzz")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert plain(result.stderr) == "0 issues"


def test_quiet_prints_one_identifier_per_hit(cli: Cli) -> None:
    cli.create("Fix login")
    cli.create("Login page")

    result = cli.run("search", "login", "--quiet")

    assert result.exit_code == 0
    # Two hits of the same weight, so the rank between them is the tiebreak's and not a
    # property of this verb: the set is what `--quiet` promises, one identifier per line.
    assert sorted(result.stdout.split()) == ["ENG-1", "ENG-2"]
    assert len(result.stdout.splitlines()) == 2


def test_only_count_produces_a_total(cli: Cli) -> None:
    cli.create("Deploy roto")
    cli.create("Arreglar deploy")

    without = json_stdout(cli.run("search", "deploy", "--json"))
    with_count = json_stdout(cli.run("search", "deploy", "--json", "--count"))

    assert without["total"] is None
    assert with_count["total"] == 2


def test_a_limit_outside_the_range_is_a_validation_error(cli: Cli) -> None:
    result = cli.run("search", "deploy", "--limit", "201", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_limit"
    assert error["field"] == "limit"


def test_a_negative_offset_is_a_validation_error(cli: Cli) -> None:
    result = cli.run("search", "deploy", "--offset", "-1", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_limit"
    assert error["field"] == "offset"


def test_an_offset_past_the_end_is_an_empty_page_and_not_an_error(cli: Cli) -> None:
    cli.create("Deploy roto")
    cli.create("Arreglar deploy")

    result = cli.run("search", "deploy", "--offset", "100", "--count", "--json")

    assert result.exit_code == 0
    payload = json_stdout(result)
    assert payload["hits"] == []
    assert payload["has_more"] is False
    assert payload["total"] == 2


def test_an_empty_invocation_is_a_refusal_of_the_command(cli: Cli) -> None:
    result = cli.run("search", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "query_empty"
    assert error["field"] == "q"


def test_a_filters_only_search_adds_no_text_predicate(cli: Cli) -> None:
    cli.create("Uno", "--state", "Done")
    cli.create("Dos", "--state", "Done")
    cli.create("Tres")

    payload = json_stdout(cli.run("search", "--state", "Done", "--json"))

    assert payload["backend"] == "none"
    assert sorted(hit["identifier"] for hit in payload["hits"]) == ["ENG-1", "ENG-2"]


def test_a_filters_only_search_prints_the_empty_match_cell(cli: Cli) -> None:
    cli.create("Uno", "--state", "Done")

    result = cli.run("search", "--state", "Done")

    assert result.exit_code == 0
    text = plain(result.stdout)
    assert text.startswith("IDENTIFIER P STATE UPDATED TITLE MATCH ENG-1")
    assert text.endswith("Uno \u2013")


def test_a_flag_overrides_the_filter_the_text_carried(cli: Cli) -> None:
    cli.create("Deploy roto", "--state", "Done")
    cli.create("Deploy en curso", "--state", "In Progress")

    payload = json_stdout(
        cli.run("search", "deploy state:Done", "--state", "In Progress", "--json")
    )

    assert [hit["identifier"] for hit in payload["hits"]] == ["ENG-2"]
    assert payload["hits"][0]["state_name"] == "In Progress"


def test_all_includes_the_archived_and_the_trashed(cli: Cli) -> None:
    cli.create("Deploy vigente")
    archived = cli.create("Deploy archivado")
    trashed = cli.create("Deploy borrado")
    assert cli.run("issue", "archive", archived, "--yes").exit_code == 0
    assert cli.run("issue", "delete", trashed, "--yes").exit_code == 0

    default = json_stdout(cli.run("search", "deploy", "--json"))
    everything = json_stdout(cli.run("search", "deploy", "--is", "all", "--json"))

    assert [hit["identifier"] for hit in default["hits"]] == ["ENG-1"]
    assert sorted(hit["identifier"] for hit in everything["hits"]) == [
        "ENG-1",
        "ENG-2",
        "ENG-3",
    ]


def test_pagination_asks_for_one_more_row(cli: Cli) -> None:
    for index in range(120):
        cli.create(f"pago {index}")

    first = json_stdout(cli.run("search", "pago", "--limit", "50", "--json"))
    last = json_stdout(
        cli.run("search", "pago", "--limit", "50", "--offset", "100", "--json", "--count")
    )

    assert len(first["hits"]) == 50
    assert first["has_more"] is True
    assert len(last["hits"]) == 20
    assert last["has_more"] is False
    assert last["total"] == 120


def test_the_backend_is_like_when_the_index_is_dropped(cli: Cli) -> None:
    cli.create("Deploy roto en producción")
    cli.execute("DROP TABLE issue_fts")

    result = cli.run("search", "deploy", "--json")

    assert result.exit_code == 0
    payload = json_stdout(result)
    assert payload["backend"] == "like"
    assert payload["hits"][0]["identifier"] == "ENG-1"
    assert payload["hits"][0]["score"] == 0.0


def test_a_query_the_component_refuses_is_exit_two_with_its_code(cli: Cli) -> None:
    result = cli.run("search", "deploy", "--due", "ayer", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_filter"
    assert error["field"] == "due"


def test_an_unknown_sort_is_a_validation_error(cli: Cli) -> None:
    result = cli.run("search", "deploy", "--sort", "magic", "--json")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"


def test_the_snippet_of_the_json_envelope_keeps_the_markers(cli: Cli) -> None:
    cli.create("Deploy the <form> tag")

    payload = json_stdout(cli.run("search", "deploy", "--json"))

    assert "<mark>" in payload["hits"][0]["snippet"]
    assert "<form>" in payload["hits"][0]["snippet"]


@pytest.mark.parametrize("query", ['"*" OR 1=1', "NEAR(login)", "-login", "title:eng"])
def test_a_hostile_query_is_treated_as_literal_text(cli: Cli, query: str) -> None:
    cli.create("Broken login")

    result = cli.run("search", "--count", "--json", "--", query)

    assert result.exit_code == 0, result.stderr
    payload = json_stdout(result)
    assert payload["total"] == 0
    assert payload["hits"] == []


def test_a_negative_query_only_excludes(cli: Cli) -> None:
    cli.create("Bug del deploy")
    cli.create("Otra cosa")
    with_description = cli.create("Deploy limpio")
    cli.execute("UPDATE issue SET description = NULL WHERE identifier = ?", (with_description,))

    payload = json_stdout(cli.run("search", "--json", "--", "-bug"))

    assert payload["backend"] == "like"
    assert sorted(hit["identifier"] for hit in payload["hits"]) == ["ENG-2", "ENG-3"]
    assert all(hit["score"] == 0.0 for hit in payload["hits"])


def test_json_and_quiet_are_mutually_exclusive(cli: Cli) -> None:
    result = cli.run("search", "deploy", "--json", "--quiet")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"


def test_the_json_output_is_one_document_and_stderr_stays_empty(cli: Cli) -> None:
    cli.create("Deploy roto")

    result = cli.run("search", "deploy", "--json")

    assert result.exit_code == 0
    assert json.loads(result.stdout)["backend"] == "fts"
    assert result.stderr == ""
