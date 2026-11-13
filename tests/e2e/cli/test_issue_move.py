"""E2E tests for `hifa issue move`.

The verb carries a state and an optional neighbor anchor; it prints one row of the issue
columns, the canonical `Issue` under `--json` (with no `rebalanced` key) and the identifier
under `--quiet`. Error paths: 2 for a contradictory anchor or a foreign state, 3 for an
unknown issue or state name, 5 for an archived issue.
"""

from __future__ import annotations

from conftest import CANONICAL_KEYS, ISSUE_HEADER, Cli, error_json, json_stdout


def test_move_changes_the_state_and_records_it(cli: Cli) -> None:
    cli.create("Movable")
    result = cli.run("issue", "move", "ENG-1", "--state", "In Progress", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["state"]["name"] == "In Progress"
    assert payload["started_at"] is not None
    assert "rebalanced" not in payload
    assert tuple(payload) == CANONICAL_KEYS
    fields = cli.history_fields("ENG-1")
    assert {"state_id", "started_at"} <= set(fields)


def test_move_prints_one_row_of_the_issue_columns(cli: Cli) -> None:
    cli.create("Row")
    result = cli.run("issue", "move", "ENG-1", "--state", "In Progress")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == ISSUE_HEADER.split()
    assert "In Progress" in lines[1]


def test_move_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Quiet move")
    result = cli.run("issue", "move", "ENG-1", "--state", "Done", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_move_to_the_same_state_twice_is_idempotent(cli: Cli) -> None:
    cli.create("Idempotent")
    first = cli.run("issue", "move", "ENG-1", "--state", "Done")
    second = cli.run("issue", "move", "ENG-1", "--state", "Done")
    assert (first.exit_code, second.exit_code) == (0, 0)
    row = cli.issue("ENG-1")
    assert row["completed_at"] is not None


def test_move_before_an_anchor_puts_the_issue_first(cli: Cli) -> None:
    cli.create("One")
    cli.create("Two")
    assert cli.run("issue", "move", "ENG-2", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "move", "ENG-1", "--state", "In Progress", "--before", "ENG-2")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert cli.issue("ENG-1")["board_order"] < cli.issue("ENG-2")["board_order"]


def test_move_after_an_anchor_puts_the_issue_last(cli: Cli) -> None:
    cli.create("One")
    cli.create("Two")
    assert cli.run("issue", "move", "ENG-2", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "move", "ENG-1", "--state", "In Progress", "--after", "ENG-2")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert cli.issue("ENG-1")["board_order"] > cli.issue("ENG-2")["board_order"]


def test_move_with_before_and_after_exits_2(cli: Cli) -> None:
    cli.create("Both anchors")
    cli.create("Neighbor")
    result = cli.run(
        "issue",
        "move",
        "ENG-1",
        "-s",
        "In Progress",
        "--before",
        "ENG-2",
        "--after",
        "ENG-2",
        "--json",
    )
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"


def test_move_with_a_state_of_another_team_exits_2(cli: Cli, other_team: str) -> None:
    cli.create("Foreign")
    before_state = cli.issue("ENG-1")["state_id"]
    result = cli.run("issue", "move", "ENG-1", "--state", other_team, "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"
    assert "STATE_TEAM_MISMATCH" in result.stderr
    assert cli.issue("ENG-1")["state_id"] == before_state


def test_move_of_an_archived_issue_exits_5(cli: Cli) -> None:
    cli.create("Archived move")
    cli.archive("ENG-1")
    before_state = cli.issue("ENG-1")["state_id"]
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "move", "ENG-1", "--state", "In Progress", "--json")
    assert result.exit_code == 5
    assert error_json(result)["code"] == "invalid_transition"
    assert cli.issue("ENG-1")["state_id"] == before_state
    assert len(cli.history("ENG-1")) == before_rows


def test_move_to_an_unknown_state_exits_3(cli: Cli) -> None:
    cli.create("Unknown state")
    result = cli.run("issue", "move", "ENG-1", "--state", "Nope", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"
    assert "Nope" in result.stderr


def test_move_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "move", "ENG-999", "--state", "Todo", "--json")
    assert result.exit_code == 3


def test_move_requires_the_state_flag(cli: Cli) -> None:
    cli.create("No state")
    result = cli.run("issue", "move", "ENG-1")
    assert result.exit_code == 2
    assert "--state" in result.stderr
