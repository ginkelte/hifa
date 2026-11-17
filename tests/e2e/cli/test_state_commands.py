"""E2E tests of `hifa state list|create|update`.

Every test runs the real Typer app against a database seeded by `hifa init`, and checks the
human table, the `--json` envelope and the D-014 exit codes (0 ok, 2 validation, 3 not found,
4 conflict).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import CLI_ENV
from hifa.cli.main import app

runner = CliRunner()
CANONICAL = ["Triage", "Todo", "Backlog", "In Progress", "In Review", "Done", "Canceled"]


def run(*args: str):
    return runner.invoke(app, list(args), env=CLI_ENV)


def out(result) -> str:
    return result.output + (result.stderr if result.stderr_bytes else "")


def body(result) -> dict:
    return json.loads(result.stdout)


def rows(result) -> list[list[str]]:
    """Data rows of a printed table, split on the two-space gutter the renderer leaves."""
    return [re.split(r"\s{2,}", line.strip()) for line in result.stdout.splitlines()[1:]]


def stored_positions(db: Path, *names: str) -> dict[str, float]:
    """`workflow_state.position` as the database holds it, key for key of `names`."""
    with sqlite3.connect(db) as conn:
        return {
            name: position
            for name, position in conn.execute("SELECT name, position FROM workflow_state")
            if name in names
        }


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated cwd + `HIFA_DB` with one team (ENG) and its seven canonical states."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "tester@example.com")
    result = run("init", "--team-key", "ENG")
    assert result.exit_code == 0, out(result)
    return tmp_path / "hifa.db"


def test_list_prints_the_canonical_order(sandbox: Path) -> None:
    result = run("state", "list")

    assert result.exit_code == 0, out(result)
    header = result.stdout.splitlines()[0]
    assert header.split() == ["POSITION", "NAME", "TYPE", "COLOR", "DEFAULT", "ISSUES"]
    rows = [re.split(r"\s{2,}", line.strip()) for line in result.stdout.splitlines()[1:]]
    assert [row[1] for row in rows] == CANONICAL
    assert [row[0] for row in rows] == ["1.0", "2.0", "3.0", "4.0", "4.5", "5.0", "6.0"]
    # DEFAULT is a marker, not a sentence: `*` for the default column, `-` for the rest.
    assert [row[4] for row in rows] == ["–", "*", "–", "–", "–", "–", "–"]
    # One concrete row, cell by cell, so a formatting regression cannot hide behind the header.
    assert rows[1] == ["2.0", "Todo", "unstarted", "#E2E2E2", "*", "0"]


def test_list_prints_the_position_the_value_needs(sandbox: Path) -> None:
    """HIFA-56: a stored `4.75` prints `4.75`, not the `4.8` one decimal produced.

    `--before Done` on the seven-state seed lands in the `In Review`(4.5)–`Done`(5.0) slot, so
    the stored value is exactly `4.75`. The table and the column are asserted, and the database
    is read beside the CLI answer: the printed number is the stored one, not a rounding of it.
    """
    created = run("state", "create", "QA", "--type", "started", "--before", "Done")

    assert created.exit_code == 0, out(created)
    assert created.stdout.splitlines()[1].split()[0] == "4.75"
    assert stored_positions(sandbox, "QA") == {"QA": 4.75}

    listed = run("state", "list")
    assert listed.exit_code == 0, out(listed)
    printed = {row[1]: row[0] for row in rows(listed)}
    assert printed["QA"] == "4.75"
    # The neighbours keep the one decimal the value needs: no trailing zero, never a bare `4`.
    assert [printed[name] for name in CANONICAL] == [
        "1.0",
        "2.0",
        "3.0",
        "4.0",
        "4.5",
        "5.0",
        "6.0",
    ]


def test_three_inserts_after_a_column_print_three_distinct_positions(sandbox: Path) -> None:
    """Two positions that read differently must never print the same number.

    Three inserts after `In Progress` halve the same slot: the database holds `4.25 / 4.125 /
    4.0625` and the one-decimal formatter printed `4.2 / 4.1 / 4.1` — two orders, one number.
    The printed column is what is asserted, with the stored value read beside it.
    """
    for name in ("QA", "Blocked", "Reviewing"):
        created = run("state", "create", name, "--type", "started", "--after", "In Progress")
        assert created.exit_code == 0, out(created)

    listed = run("state", "list")
    assert listed.exit_code == 0, out(listed)
    printed = {row[1]: row[0] for row in rows(listed)}
    names = ("QA", "Blocked", "Reviewing")
    assert [printed[name] for name in names] == ["4.25", "4.125", "4.0625"]
    assert len({printed[name] for name in names}) == 3, "two positions printed one number"
    # And they are the stored values, in the order the board is read in.
    assert stored_positions(sandbox, *names) == {
        "QA": 4.25,
        "Blocked": 4.125,
        "Reviewing": 4.0625,
    }
    assert [row[0] for row in rows(listed)][3:6] == ["4.0", "4.0625", "4.125"]


def test_list_json_uses_the_items_envelope(sandbox: Path) -> None:
    result = run("state", "list", "--json")

    assert result.exit_code == 0, out(result)
    payload = body(result)
    assert list(payload) == ["items"]
    items = payload["items"]
    assert [item["name"] for item in items] == CANONICAL
    assert [item["position"] for item in items] == [1.0, 2.0, 3.0, 4.0, 4.5, 5.0, 6.0]
    assert list(items[0]) == [
        "id",
        "name",
        "type",
        "color",
        "position",
        "is_default",
        "issues_count",
    ]
    assert [item["is_default"] for item in items].count(True) == 1


def test_create_after_a_neighbour_lands_between_the_two_columns(sandbox: Path) -> None:
    created = run("state", "create", "QA", "--type", "started", "--after", "In Progress", "--json")

    assert created.exit_code == 0, out(created)
    assert body(created)["position"] == 4.25

    listed = body(run("state", "list", "--json"))["items"]
    assert [item["name"] for item in listed] == [
        "Triage",
        "Todo",
        "Backlog",
        "In Progress",
        "QA",
        "In Review",
        "Done",
        "Canceled",
    ]


def test_create_without_anchors_appends_at_the_end(sandbox: Path) -> None:
    created = run("state", "create", "Shipped", "--type", "completed", "--color", "#00FF00")

    assert created.exit_code == 0, out(created)
    listed = body(run("state", "list", "--json"))["items"]
    assert listed[-1]["name"] == "Shipped"
    assert listed[-1]["color"] == "#00FF00"


def test_create_a_duplicate_name_exits_four(sandbox: Path) -> None:
    result = run("state", "create", "todo", "--type", "unstarted")

    assert result.exit_code == 4, out(result)
    assert "UNIQUE_STATE_NAME" in out(result) or "already exists" in out(result)


def test_create_with_an_invalid_type_exits_two(sandbox: Path) -> None:
    result = run("state", "create", "Blocked", "--type", "blocked")

    assert result.exit_code == 2, out(result)


def test_create_with_an_invalid_color_exits_two(sandbox: Path) -> None:
    result = run("state", "create", "Blocked", "--type", "started", "--color", "red")

    assert result.exit_code == 2, out(result)
    assert "expected: #RRGGBB" in out(result)


def test_create_for_an_unknown_team_exits_three(sandbox: Path) -> None:
    result = run("state", "create", "Blocked", "--type", "started", "--team", "OPS")

    assert result.exit_code == 3, out(result)


def test_create_with_both_anchors_exits_two(sandbox: Path) -> None:
    result = run(
        "state", "create", "Blocked", "--type", "started", "--before", "Todo", "--after", "Done"
    )

    assert result.exit_code == 2, out(result)


def test_update_moves_a_state_before_another_one(sandbox: Path) -> None:
    result = run("state", "update", "Canceled", "--before", "Backlog", "--json")

    assert result.exit_code == 0, out(result)
    assert body(result)["position"] == 2.5
    listed = [item["name"] for item in body(run("state", "list", "--json"))["items"]]
    assert listed == [
        "Triage",
        "Todo",
        "Canceled",
        "Backlog",
        "In Progress",
        "In Review",
        "Done",
    ]


def test_update_renames_and_recolors(sandbox: Path) -> None:
    result = run("state", "update", "Backlog", "--name", "Icebox", "--color", "#FFFFFF", "--json")

    assert result.exit_code == 0, out(result)
    payload = body(result)
    assert (payload["name"], payload["color"]) == ("Icebox", "#FFFFFF")
    assert payload["position"] == 3.0


def test_update_to_a_duplicate_name_exits_four(sandbox: Path) -> None:
    result = run("state", "update", "Backlog", "--name", "done")

    assert result.exit_code == 4, out(result)


def test_update_an_unknown_state_exits_three(sandbox: Path) -> None:
    result = run("state", "update", "Nope", "--name", "Whatever")

    assert result.exit_code == 3, out(result)
    assert "not found" in out(result)


def test_update_with_json_and_quiet_exits_two(sandbox: Path) -> None:
    result = run("state", "update", "Todo", "--name", "Next", "--json", "--quiet")

    assert result.exit_code == 2, out(result)


def test_update_reports_the_real_issue_count_not_zero(sandbox: Path) -> None:
    """Found on the live teams: `state update` printed ISSUES 0 for a state holding 28 issues.

    The column exists in the output, so it has to be the same number `state list` prints.
    """
    assert run("issue", "create", "Primero").exit_code == 0
    assert run("issue", "create", "Segundo").exit_code == 0

    listed = run("state", "list")
    assert listed.exit_code == 0, out(listed)
    list_row = next(
        re.split(r"\s{2,}", line.strip()) for line in listed.stdout.splitlines() if "Todo" in line
    )
    assert list_row[-1] == "2"

    updated = run("state", "update", "Todo", "--name", "Todo")
    assert updated.exit_code == 0, out(updated)
    rows = [re.split(r"\s{2,}", line.strip()) for line in updated.stdout.splitlines()[1:]]
    assert rows[0][0] == "2.0"
    assert rows[0][-1] == "2", "the row must carry the real count, not a hardcoded 0"


def test_update_json_carries_the_same_count_as_list(sandbox: Path) -> None:
    assert run("issue", "create", "Primero").exit_code == 0

    updated = run("state", "update", "Todo", "--json")
    assert updated.exit_code == 0, out(updated)
    payload = body(updated)
    assert payload["issues_count"] == 1
    assert payload["name"] == "Todo"

    listed = body(run("state", "list", "--json"))
    todo = next(item for item in listed["items"] if item["name"] == "Todo")
    assert todo["issues_count"] == payload["issues_count"]


def test_create_reports_zero_issues_without_assuming_it(sandbox: Path) -> None:
    created = run("state", "create", "QA", "--type", "started")
    assert created.exit_code == 0, out(created)
    payload = body(run("state", "create", "Blocked", "--type", "started", "--json"))
    assert payload["issues_count"] == 0
