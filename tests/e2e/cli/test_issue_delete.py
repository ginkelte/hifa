"""E2E tests for `hifa issue delete`.

`delete` is soft: it sets `trashed` and moves `updated_at`, keeps the row and leaves the
listing by default. It asks for confirmation before the use case runs, so a non-interactive
session without `--yes` is refused with `confirmation_required` and nothing is written.
Error paths: 2 for the refusal, the silent refusal and the physical-deletion flag; 3 for an
unknown issue.
"""

from __future__ import annotations

from conftest import CANONICAL_KEYS, ISSUE_HEADER, Cli, error_json, json_stdout, plain

CATALOG_REFUSAL = "hifa: operation canceled"


def test_delete_sets_trashed_and_keeps_the_row(cli: Cli) -> None:
    cli.create("Trashable")
    assert cli.run("issue", "move", "ENG-1", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "delete", "ENG-1", "--yes", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["trashed"] is True
    assert payload["is_visible"] is False
    assert payload["state"]["name"] == "In Progress"
    assert payload["archived_at"] is None
    assert tuple(payload) == CANONICAL_KEYS
    # Soft: the row is still there and the change is one history entry.
    assert cli.issue("ENG-1")["trashed"] == 1
    assert cli.history_fields("ENG-1")[0] == "trashed"


def test_delete_writes_one_history_row_and_touches_no_other_column(cli: Cli) -> None:
    cli.create("Stamped")
    created_at = str(cli.issue("ENG-1")["created_at"])
    result = cli.run("issue", "delete", "ENG-1", "--yes", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(cli.history("ENG-1")) == 1
    row = cli.history("ENG-1")[0]
    assert (row["field"], row["old_value"], row["new_value"]) == ("trashed", "0", "1")
    stored = cli.issue("ENG-1")
    assert stored["title"] == "Stamped"
    assert stored["archived_at"] is None
    assert str(stored["created_at"]) == created_at


def test_delete_prints_one_table_row(cli: Cli) -> None:
    cli.create("Delete row")
    result = cli.run("issue", "delete", "ENG-1", "--yes")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == ISSUE_HEADER.split()


def test_delete_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Delete quiet")
    result = cli.run("issue", "delete", "ENG-1", "--yes", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_delete_without_yes_in_a_non_interactive_session_is_refused(cli: Cli) -> None:
    cli.create("Needs yes")
    result = cli.run("issue", "delete", "ENG-1")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert plain(result.stderr) == CATALOG_REFUSAL
    assert cli.issue("ENG-1")["trashed"] == 0
    assert cli.history("ENG-1") == []


def test_delete_json_refusal_keeps_stdout_empty(cli: Cli) -> None:
    cli.create("Needs yes json")
    result = cli.run("issue", "delete", "ENG-1", "--json")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert error_json(result)["code"] == "confirmation_required"
    assert cli.issue("ENG-1")["trashed"] == 0


def test_delete_with_no_input_never_prompts(cli: Cli) -> None:
    cli.create("No input")
    result = cli.run("issue", "delete", "ENG-1", "--no-input")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert plain(result.stderr) == CATALOG_REFUSAL
    assert "Send" not in result.stderr
    assert cli.issue("ENG-1")["trashed"] == 0


def test_delete_of_an_archived_issue_is_allowed(cli: Cli) -> None:
    cli.create("Archived first")
    cli.archive("ENG-1")
    result = cli.run("issue", "delete", "ENG-1", "--yes", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["trashed"] is True
    assert payload["archived_at"] is not None


def test_delete_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "delete", "ENG-999", "--yes", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"


def test_delete_rejects_the_physical_deletion_flag(cli: Cli) -> None:
    cli.create("No hard delete")
    result = cli.run("issue", "delete", "ENG-1", "--hard", "--yes", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"
    assert "--hard" in plain(result.stderr)
    assert cli.issue("ENG-1")["trashed"] == 0
