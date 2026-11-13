"""E2E tests for `hifa issue create`.

Runs the real Typer app with `CliRunner` over an isolated `HIFA_DB`: human output, the
canonical `--json` object (key order included) and exit codes 0/2/3/4.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from conftest import CLI_ENV, plain
from hifa.cli.main import app
from hifa.config import CONFIG_FILENAME

runner = CliRunner()

ISSUE_KEYS = [
    "id",
    "identifier",
    "number",
    "team",
    "title",
    "description",
    "priority",
    "priority_label",
    "estimate",
    "due_date",
    "state",
    "assignee",
    "creator",
    "project",
    "milestone",
    "cycle",
    "parent",
    "labels",
    "sort_order",
    "board_order",
    "sub_issue_sort_order",
    "started_at",
    "completed_at",
    "canceled_at",
    "snoozed_until_at",
    "archived_at",
    "auto_archived_at",
    "trashed",
    "branch_name",
    "is_open",
    "is_visible",
    "created_at",
    "updated_at",
]


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()  # R13/D-041: `init` writes `.hifa.toml` only inside a work tree
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HIFA_TEAM", raising=False)
    monkeypatch.delenv("HIFA_MEMBER", raising=False)
    monkeypatch.delenv("HIFA_ME_EMAIL", raising=False)
    return SimpleNamespace(root=tmp_path, cwd=project, db=tmp_path / "hifa.db")


@pytest.fixture
def initialized(sandbox: SimpleNamespace) -> SimpleNamespace:
    result = runner.invoke(app, ["init", "--team-key", "ENG"], env=CLI_ENV)
    assert result.exit_code == 0, result.stdout + result.stderr
    return sandbox


def run(*args: str) -> Any:
    return runner.invoke(app, list(args), env=CLI_ENV)


def test_create_prints_table_row(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Fix the login", "--priority", "P1", "--label", "Bug")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ENG-1" in result.stdout
    assert "Fix the login" in result.stdout
    assert "Bug" in result.stdout
    assert "BUG" not in result.stdout.splitlines()[0].split()[2]


def test_create_json_envelope_has_stable_key_order(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Second issue", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert list(payload) == ISSUE_KEYS
    assert payload["identifier"] == "ENG-1"
    assert payload["priority"] == 0
    assert payload["priority_label"] == "No priority"
    assert payload["team"]["key"] == "ENG"
    assert payload["state"]["name"] == "Todo"
    assert payload["labels"] == []
    assert payload["is_open"] is True and payload["is_visible"] is True
    assert payload["trashed"] is False


def test_create_json_includes_labels_and_due(initialized: SimpleNamespace) -> None:
    result = run(
        "issue", "create", "Labeled", "--label", "Bug,Docs", "--due", "2026-11-20", "--json"
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert [label["name"] for label in payload["labels"]] == ["Bug", "Docs"]
    assert payload["due_date"] == "2026-11-20"


def test_create_with_estimate_carries_it_in_the_envelope(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "With estimate", "-e", "3", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert list(payload) == ISSUE_KEYS
    assert payload["estimate"] == 3.0


def test_create_long_estimate_flag_is_the_same_field(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Long flag", "--estimate", "5", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["estimate"] == 5.0


def test_create_without_estimate_leaves_it_null(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "No estimate", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["estimate"] is None


def test_create_with_an_estimate_outside_the_scale_exits_2(
    initialized: SimpleNamespace,
) -> None:
    result = run("issue", "create", "Bad", "-e", "99", "--json")
    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "validation_error"
    assert error["field"] == "estimate"
    assert error["details"]["rule"] == "ESTIMATE_NOT_IN_SCALE"
    written = run("issue", "create", "After the rejection", "--json")
    assert written.exit_code == 0, written.stdout + written.stderr
    assert json.loads(written.stdout)["identifier"] == "ENG-1"


def test_create_with_a_non_numeric_estimate_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Not a number", "-e", "three")
    assert result.exit_code == 2
    assert "estimate" in result.stderr.lower()


def run_help(*args: str) -> str:
    """`--help` output for `args`, without ANSI escapes and with the layout flattened.

    Rich reflows the option panel to the width `CLI_ENV` pins and styles the option names it
    prints, so the raw text carries escapes between the words (`\\x1b[1m--estimate\\x1b[0m`).
    Stripping them before collapsing the whitespace makes the match about the option — long
    name, short flag, metavariable — rather than about the styling or the columns.
    """
    result = runner.invoke(app, [*args, "--help"], env=CLI_ENV)
    assert result.exit_code == 0, result.stdout + result.stderr
    return plain(result.stdout)


def test_create_help_lists_the_estimate_option(initialized: SimpleNamespace) -> None:
    assert "--estimate -e <float>" in run_help("issue", "create")


def test_create_quiet_prints_identifier(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Quiet", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_missing_title_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create")
    assert result.exit_code == 2
    assert "title" in (result.stdout + result.stderr)


def test_title_in_both_forms_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "One", "--title", "Two")
    assert result.exit_code == 2


def test_invalid_priority_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Bad priority", "--priority", "P9")
    assert result.exit_code == 2


def test_empty_title_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "   ")
    assert result.exit_code == 2


def test_bad_due_date_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Bad due", "--due", "20-11-2026")
    assert result.exit_code == 2


def test_unknown_team_exits_3(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Nope", "--team", "OPS")
    assert result.exit_code == 3


def test_unknown_label_exits_2(initialized: SimpleNamespace) -> None:
    result = run("issue", "create", "Nope label", "--label", "ghost")
    assert result.exit_code == 2


def test_missing_database_exits_3(sandbox: SimpleNamespace) -> None:
    result = run("issue", "create", "No db")
    assert result.exit_code == 3
    assert "database not found" in (result.stdout + result.stderr)


def test_duplicate_identifier_exits_4(
    initialized: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hifa.adapters.sqlite.issue_repo.IssueRepo.next_number", lambda self, team_id: 1
    )
    monkeypatch.setattr("hifa.app.retry.time.sleep", lambda _: None)
    first = run("issue", "create", "First")
    assert first.exit_code == 0, first.stdout + first.stderr
    second = run("issue", "create", "Second")
    assert second.exit_code == 4


def test_create_from_config_team(initialized: SimpleNamespace) -> None:
    assert (initialized.cwd / CONFIG_FILENAME).is_file()
    result = run("issue", "create", "Team from config")
    assert result.exit_code == 0, result.stdout + result.stderr
