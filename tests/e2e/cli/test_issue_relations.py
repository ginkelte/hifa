"""E2E tests of `hifa issue relate|relations|blocked`.

Every test runs the real Typer app against a database seeded by `hifa init` and reads the
stored edge back, so the assertions are about what reached the file and not about what the
command printed. The exit codes follow the shared map (0 ok, 2 validation, 3 not found,
4 conflict).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import CLI_ENV
from hifa.cli.main import app

runner = CliRunner()


def run(*args: str):
    return runner.invoke(app, list(args), env=CLI_ENV)


def out(result) -> str:
    return result.output + (result.stderr if result.stderr_bytes else "")


def body(result) -> dict:
    """The data envelope, which every successful command writes to `stdout`."""
    return json.loads(result.stdout)


def error_body(result) -> dict:
    """The error envelope, which goes to `stderr`: `stdout` stays empty when a command fails."""
    return json.loads(result.stderr)


@pytest.fixture
def workspace(cli) -> Path:
    """The seeded workspace with three issues: a blocker, a blocked one and a free one."""
    for title in ("Blocker", "Blocked one", "Free"):
        created = cli.run("issue", "create", title)
        assert created.exit_code == 0, out(created)
    return cli.db


def edges(db: Path) -> list[tuple[str, str, str]]:
    """Every stored edge, as the two ends and the type."""
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return list(
            connection.execute(
                "SELECT issue_id, related_issue_id, type FROM issue_relation ORDER BY rowid"
            )
        )
    finally:
        connection.close()


# --- relate --------------------------------------------------------------------------


def test_relate_writes_one_edge_and_prints_the_row(workspace: Path) -> None:
    result = run("issue", "relate", "ENG-1", "blocks", "ENG-2", "--json")

    assert result.exit_code == 0, out(result)
    payload = body(result)
    assert set(payload) == {"id", "issue_id", "related_issue_id", "type", "created_at"}
    assert payload["type"] == "blocks"
    assert len(edges(workspace)) == 1


def test_the_inverse_is_not_written(workspace: Path) -> None:
    """`blocked_by` names the same row from the other end; it never adds a second one."""
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    blocked_by = run("issue", "relate", "ENG-2", "blocked_by", "ENG-1", "--json")

    assert blocked_by.exit_code == 4, out(blocked_by)
    assert error_body(blocked_by)["error"]["details"]["rule"] == "RELATION_DUPLICATE"
    assert len(edges(workspace)) == 1


def test_a_symmetric_edge_written_from_the_other_end_is_a_duplicate(workspace: Path) -> None:
    assert run("issue", "relate", "ENG-1", "related", "ENG-2").exit_code == 0

    again = run("issue", "relate", "ENG-2", "related", "ENG-1", "--json")

    assert again.exit_code == 4, out(again)
    assert error_body(again)["error"]["details"]["rule"] == "RELATION_DUPLICATE"
    assert len(edges(workspace)) == 1


def test_an_unknown_type_is_a_validation_error(workspace: Path) -> None:
    result = run("issue", "relate", "ENG-1", "nope", "ENG-2", "--json")

    assert result.exit_code == 2, out(result)
    assert error_body(result)["error"]["field"] == "type"
    assert edges(workspace) == []


def test_an_issue_related_to_itself_is_refused(workspace: Path) -> None:
    result = run("issue", "relate", "ENG-1", "related", "ENG-1", "--json")

    assert result.exit_code == 2, out(result)
    assert error_body(result)["error"]["details"]["rule"] == "RELATION_SELF"
    assert edges(workspace) == []


def test_undo_removes_the_edge_and_writes_null(workspace: Path) -> None:
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    result = run("issue", "relate", "ENG-1", "blocks", "ENG-2", "--undo", "--json")

    assert result.exit_code == 0, out(result)
    assert result.stdout.strip() == "null"
    assert edges(workspace) == []


def test_undo_of_an_edge_that_does_not_exist_is_not_found(workspace: Path) -> None:
    result = run("issue", "relate", "ENG-1", "blocks", "ENG-3", "--undo", "--json")

    assert result.exit_code == 3, out(result)
    assert error_body(result)["error"]["code"] == "not_found"


# --- relations -----------------------------------------------------------------------


def test_relations_reads_the_type_from_each_end(workspace: Path) -> None:
    """One row, two readings: `blocks` from the blocker and `blocked_by` from the blocked."""
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    from_blocker = run("issue", "relations", "ENG-1")
    from_blocked = run("issue", "relations", "ENG-2")

    assert from_blocker.exit_code == 0, out(from_blocker)
    assert from_blocked.exit_code == 0, out(from_blocked)
    assert from_blocker.stdout.splitlines()[0].split() == [
        "READS",
        "AS",
        "IDENTIFIER",
        "TITLE",
        "STATE",
    ]
    assert from_blocker.stdout.splitlines()[1].split()[0] == "blocks"
    assert from_blocker.stdout.splitlines()[1].split()[1] == "ENG-2"
    assert from_blocked.stdout.splitlines()[1].split()[0] == "blocked_by"
    assert from_blocked.stdout.splitlines()[1].split()[1] == "ENG-1"


def test_relations_json_carries_the_stored_row_only(workspace: Path) -> None:
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    result = run("issue", "relations", "ENG-2", "--json")

    assert result.exit_code == 0, out(result)
    items = body(result)["items"]
    assert len(items) == 1
    assert set(items[0]) == {"id", "issue_id", "related_issue_id", "type", "created_at"}
    assert items[0]["type"] == "blocks"


def test_an_issue_without_edges_says_zero(workspace: Path) -> None:
    result = run("issue", "relations", "ENG-3")

    assert result.exit_code == 0, out(result)
    assert result.stdout.strip() == ""
    assert "0 relations" in result.stderr


def test_relations_of_an_unknown_issue_is_not_found(workspace: Path) -> None:
    result = run("issue", "relations", "ENG-999", "--json")

    assert result.exit_code == 3, out(result)
    assert error_body(result)["error"]["code"] == "not_found"


# --- blocked -------------------------------------------------------------------------


def test_blocked_lists_the_issue_held_by_a_live_blocker(workspace: Path) -> None:
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    result = run("issue", "blocked", "--json")

    assert result.exit_code == 0, out(result)
    assert [item["identifier"] for item in body(result)["items"]] == ["ENG-2"]


def test_a_finished_blocker_stops_blocking(workspace: Path) -> None:
    """The edge stays; what changes is the state of one of its ends."""
    assert run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0
    assert run("issue", "move", "ENG-1", "-s", "Done").exit_code == 0

    result = run("issue", "blocked", "--json")

    assert result.exit_code == 0, out(result)
    assert body(result)["items"] == []
    assert len(edges(workspace)) == 1


def test_blocked_without_rows_says_zero(workspace: Path) -> None:
    result = run("issue", "blocked")

    assert result.exit_code == 0, out(result)
    assert "0 issues" in result.stderr
