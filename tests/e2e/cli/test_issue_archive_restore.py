"""E2E tests for `hifa issue archive` and `hifa issue restore`.

`archive` stamps `archived_at` without touching the state, is a no-op the second time, asks
for confirmation before the use case runs (so a non-interactive session without `--yes` is
refused with `confirmation_required`) and `--undo` is the reversible path. `restore` never
prompts and is a no-op on a visible issue. Error paths: 2 for the refusal, an issue in trash
and the physical-deletion flag; 3 for an unknown issue.
"""

from __future__ import annotations

from conftest import CANONICAL_KEYS, ISSUE_HEADER, Cli, error_json, json_stdout


def test_archive_sets_archived_at_and_keeps_the_state(cli: Cli) -> None:
    cli.create("Archivable")
    assert cli.run("issue", "move", "ENG-1", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is not None
    assert payload["state"]["name"] == "In Progress"
    assert payload["is_visible"] is False
    assert tuple(payload) == CANONICAL_KEYS
    assert cli.history_fields("ENG-1")[0] == "archived_at"


def test_archive_keeps_updated_at_and_the_second_one_writes_nothing(cli: Cli) -> None:
    cli.create("Twice")
    first = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert first.exit_code == 0, first.stdout + first.stderr
    archived_at = json_stdout(first)["archived_at"]
    updated_at = cli.updated_at("ENG-1")
    rows = len(cli.history("ENG-1"))
    second = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert second.exit_code == 0, second.stdout + second.stderr
    assert json_stdout(second)["archived_at"] == archived_at
    assert cli.updated_at("ENG-1") == updated_at
    assert len(cli.history("ENG-1")) == rows


def test_archive_prints_one_table_row(cli: Cli) -> None:
    cli.create("Archive row")
    result = cli.run("issue", "archive", "ENG-1", "--yes")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == ISSUE_HEADER.split()


def test_archive_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Archive quiet")
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_archive_without_yes_in_a_non_interactive_session_is_refused(cli: Cli) -> None:
    cli.create("Needs yes")
    result = cli.run("issue", "archive", "ENG-1", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "confirmation_required"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_with_no_input_is_refused(cli: Cli) -> None:
    cli.create("No input")
    result = cli.run("issue", "archive", "ENG-1", "--no-input", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "confirmation_required"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_refusal_prints_the_catalog_sentence(cli: Cli) -> None:
    cli.create("Needs yes")
    result = cli.run("issue", "archive", "ENG-1")
    assert result.exit_code == 2
    assert result.stderr.strip() == "hifa: operation canceled"
    assert result.stdout == ""
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_undo_restores_the_issue(cli: Cli) -> None:
    cli.create("Undo")
    cli.archive("ENG-1")
    result = cli.run("issue", "archive", "ENG-1", "--undo", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is None
    assert payload["is_visible"] is True
    last = cli.history("ENG-1")[0]
    assert last["field"] == "archived_at"
    assert last["new_value"] is None


def test_archive_of_an_issue_in_trash_exits_2(cli: Cli) -> None:
    cli.create("Trashed")
    cli.execute("UPDATE issue SET trashed = 1 WHERE identifier = 'ENG-1'")
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "archive", "ENG-999", "--yes", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"


def test_archive_rejects_the_physical_deletion_flag(cli: Cli) -> None:
    cli.create("No hard delete")
    result = cli.run("issue", "archive", "ENG-1", "--hard", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"
    assert "--hard" in result.stderr
    assert cli.issue("ENG-1")["archived_at"] is None


def test_restore_of_an_archived_issue_brings_it_back(cli: Cli) -> None:
    cli.create("Restorable")
    cli.archive("ENG-1")
    result = cli.run("issue", "restore", "ENG-1", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is None
    assert tuple(payload) == CANONICAL_KEYS


def test_restore_of_a_visible_issue_is_a_noop(cli: Cli) -> None:
    cli.create("Visible")
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "restore", "ENG-1")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(cli.history("ENG-1")) == before_rows
    assert cli.issue("ENG-1")["trashed"] == 0


def test_restore_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Restore quiet")
    cli.archive("ENG-1")
    result = cli.run("issue", "restore", "ENG-1", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_restore_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "restore", "ENG-999", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"


def test_restore_rejects_the_physical_deletion_flag(cli: Cli) -> None:
    cli.create("No hard restore")
    result = cli.run("issue", "restore", "ENG-1", "--hard", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"
