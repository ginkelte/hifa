"""E2E tests for `hifa issue edit`.

Covers the field edit (one history row per changed column, the derived branch, the no-op),
the state flag as a move, the clear flags, the canonical `--json` envelope and the error
paths with their exit codes: 2 for a blank title, an empty invocation, a foreign state, an
unknown flag or a board anchor; 3 for an unknown issue; 5 for an archived one.
"""

from __future__ import annotations

import pytest

from conftest import CANONICAL_KEYS, ISSUE_HEADER, Cli, error_json, json_stdout


def test_edit_writes_one_history_row_per_changed_column(cli: Cli) -> None:
    cli.create("Old")
    result = cli.run(
        "issue", "edit", "ENG-1", "--priority", "2", "--title", "New", "--estimate", "3"
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    row = cli.issue("ENG-1")
    assert (row["title"], row["priority"], row["estimate"]) == ("New", 2, 3.0)
    assert sorted(cli.history_fields("ENG-1")) == ["branch_name", "estimate", "priority", "title"]
    assert len(cli.history("ENG-1")) == 4


def test_edit_of_one_field_rewrites_the_derived_branch(cli: Cli) -> None:
    cli.create("Old")
    assert cli.issue("ENG-1")["branch_name"] == "eng-1-old"
    result = cli.run("issue", "edit", "ENG-1", "--title", "Renamed", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["title"] == "Renamed"
    assert payload["branch_name"] == "eng-1-renamed"
    rows = {row["field"]: (row["old_value"], row["new_value"]) for row in cli.history("ENG-1")}
    assert rows["title"] == ("Old", "Renamed")
    assert rows["branch_name"] == ("eng-1-old", "eng-1-renamed")


def test_edit_with_an_identical_value_writes_nothing(cli: Cli) -> None:
    cli.create("Stable", "--priority", "2")
    before_updated = cli.updated_at("ENG-1")
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "edit", "ENG-1", "--priority", "2")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(cli.history("ENG-1")) == before_rows
    assert cli.updated_at("ENG-1") == before_updated


def test_edit_with_a_blank_title_is_rejected_without_writing(cli: Cli) -> None:
    cli.create("Old")
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "edit", "ENG-1", "--title", "   ", "--json")
    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "validation_error"
    assert error["field"] == "title"
    assert cli.issue("ENG-1")["title"] == "Old"
    assert len(cli.history("ENG-1")) == before_rows


def test_edit_without_flags_is_no_changes(cli: Cli) -> None:
    cli.create("Old")
    before_updated = cli.updated_at("ENG-1")
    result = cli.run("issue", "edit", "ENG-1", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "no_changes"
    assert cli.updated_at("ENG-1") == before_updated


def test_edit_without_flags_prints_the_catalog_sentence(cli: Cli) -> None:
    cli.create("Old")
    result = cli.run("issue", "edit", "ENG-1")
    assert result.exit_code == 2
    assert result.stderr.strip() == "hifa: nothing to change"
    assert result.stdout == ""


def test_edit_json_is_the_canonical_issue(cli: Cli) -> None:
    cli.create("Canonical")
    result = cli.run("issue", "edit", "ENG-1", "--priority", "1", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert tuple(payload) == CANONICAL_KEYS
    assert "items" not in payload
    assert payload["identifier"] == "ENG-1"
    assert payload["priority_label"] == "Urgent"


def test_edit_prints_one_table_row(cli: Cli) -> None:
    cli.create("Table")
    result = cli.run("issue", "edit", "ENG-1", "--priority", "1")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == ISSUE_HEADER.split()
    assert "ENG-1" in lines[1]


def test_edit_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Quiet")
    result = cli.run("issue", "edit", "ENG-1", "--priority", "1", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_edit_state_appends_the_issue_to_the_end_of_the_column(cli: Cli) -> None:
    cli.create("First")
    cli.create("Second")
    assert cli.run("issue", "edit", "ENG-2", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "edit", "ENG-1", "--state", "In Progress", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["state"]["name"] == "In Progress"
    assert payload["started_at"] is not None
    assert payload["board_order"] > cli.issue("ENG-2")["board_order"]
    fields = cli.history_fields("ENG-1")
    assert fields.count("state_id") == 1
    assert "board_order" in fields and "started_at" in fields


def test_edit_with_a_state_of_another_team_exits_2(cli: Cli, other_team: str) -> None:
    cli.create("Foreign state")
    before_state = cli.issue("ENG-1")["state_id"]
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "edit", "ENG-1", "--state", other_team, "--json")
    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "validation_error"
    assert "STATE_TEAM_MISMATCH" in result.stderr
    assert cli.issue("ENG-1")["state_id"] == before_state
    assert len(cli.history("ENG-1")) == before_rows


def test_edit_clears_description_and_assignee(cli: Cli) -> None:
    cli.create("Cleared", "--description", "detail", "--assignee", "ME")
    assert cli.issue("ENG-1")["assignee_id"] is not None
    result = cli.run("issue", "edit", "ENG-1", "--clear-description", "--clear-assignee", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["description"] is None
    assert payload["assignee"] is None
    assert sorted(cli.history_fields("ENG-1")) == ["assignee_id", "description"]


def test_edit_clears_and_sets_the_due_date(cli: Cli) -> None:
    cli.create("Dated", "--due", "2026-11-20")
    cleared = cli.run("issue", "edit", "ENG-1", "--clear-due", "--json")
    assert cleared.exit_code == 0, cleared.stdout + cleared.stderr
    assert json_stdout(cleared)["due_date"] is None
    dated = cli.run("issue", "edit", "ENG-1", "--due", "2026-12-01", "--json")
    assert dated.exit_code == 0, dated.stdout + dated.stderr
    assert json_stdout(dated)["due_date"] == "2026-12-01"


def test_edit_reads_the_description_from_stdin_after_a_dash(cli: Cli) -> None:
    cli.create("Body")
    result = cli.run("issue", "edit", "ENG-1", "-d", "-", "--json", input="from stdin\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["description"] == "from stdin\n"
    assert payload["description"] != "-"
    assert cli.issue("ENG-1")["description"] == "from stdin\n"


def test_edit_reads_the_description_from_a_file_after_a_dash(cli: Cli) -> None:
    cli.create("Body")
    result = cli.run(
        "issue", "edit", "ENG-1", "--description-file", "-", "--json", input="from a file\n"
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json_stdout(result)["description"] == "from a file\n"


@pytest.mark.parametrize(
    ("flag", "clear_flag", "field"),
    [
        ("--project", "--clear-project", "project_id"),
        ("--milestone", "--clear-milestone", "milestone_id"),
        ("--cycle", "--clear-cycle", "cycle_id"),
    ],
)
def test_edit_with_a_value_and_its_clear_flag_names_both_flags(
    cli: Cli, flag: str, clear_flag: str, field: str
) -> None:
    cli.create("Contradiction")
    before_updated = cli.updated_at("ENG-1")
    result = cli.run("issue", "edit", "ENG-1", flag, "X", clear_flag, "--json")
    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_option"
    assert error["message"] == f"use {flag} or {clear_flag}, not both"
    assert error["field"] == field
    assert cli.updated_at("ENG-1") == before_updated


def test_edit_on_an_archived_issue_exits_5(cli: Cli) -> None:
    cli.create("Archived")
    cli.archive("ENG-1")
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "edit", "ENG-1", "--title", "New", "--json")
    assert result.exit_code == 5
    assert error_json(result)["code"] == "invalid_transition"
    assert cli.issue("ENG-1")["title"] == "Archived"
    assert len(cli.history("ENG-1")) == before_rows


def test_edit_with_an_unknown_flag_aborts_without_writing(cli: Cli) -> None:
    cli.create("Unknown flag")
    before_updated = cli.updated_at("ENG-1")
    result = cli.run("issue", "edit", "ENG-1", "--sprint", "5")
    assert result.exit_code == 2
    assert "--sprint" in result.stderr
    assert cli.updated_at("ENG-1") == before_updated


def test_edit_does_not_take_a_board_anchor(cli: Cli, other_team: str) -> None:
    cli.create("Anchored")
    cli.create("Anchor")
    before_state = cli.issue("ENG-1")["state_id"]
    result = cli.run(
        "issue", "edit", "ENG-1", "--state", "In Progress", "--before", "ENG-2", "--json"
    )
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"
    assert cli.issue("ENG-1")["state_id"] == before_state


def test_edit_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "edit", "ENG-999", "--priority", "2", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"
