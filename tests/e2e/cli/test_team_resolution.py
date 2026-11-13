"""E2E tests for the team an invocation works on (`--team`, `HIFA_TEAM`, `.hifa.toml`, workspace).

Every `issue`, `state` and `team` verb resolves the team in one place, in the declared order:
the flag, the environment, the `[cli].default_team` the file names, and finally the workspace
itself when it holds exactly one non-archived team. Zero or two or more candidates are a
`missing_context` exit 2, never a team chosen by key, name or `created_at`: a listing that
answers `0 issues` and a `create` that lands in another team are the same defect, and the
case with several teams and no declaration is its measured form.

The workspace is built with `hifa init`, which writes `.hifa.toml` with `[cli].default_team`
set; the scenarios below rewrite that file without the section, which is what a hand-written
file and a board without `.hifa.toml` look like. `HIFA_DB` keeps winning the database path,
so the rewritten file never moves the data.
"""

from __future__ import annotations

import pytest
from click.testing import Result

from conftest import Cli, error_json, json_stdout

#: The one sentence the contract declares for a team that cannot be resolved.
TEAM_REQUIRED = "team required: use --team, HIFA_TEAM or [cli].default_team in .hifa.toml"

#: A `.hifa.toml` that names no default team: the next sources decide.
NO_DECLARED_TEAM = '[workspace]\nname = "Demo"\n'


def write_config(cli: Cli, body: str) -> None:
    """Rewrite `.hifa.toml`; `HIFA_DB` of the fixture still wins the database path."""
    (cli.cwd / ".hifa.toml").write_text(body, encoding="utf-8")


def without_declared_team(cli: Cli) -> None:
    write_config(cli, NO_DECLARED_TEAM)


def add_team(cli: Cli, key: str, name: str) -> None:
    created = cli.run("team", "create", key, name)
    assert created.exit_code == 0, created.stdout + created.stderr


def archive_team(cli: Cli, key: str) -> None:
    """Archive a team through SQL: `team archive` is not a command the contract declares."""
    cli.execute("UPDATE team SET archived_at = ? WHERE key = ?", ("2026-01-01T00:00:00Z", key))


def identifiers(result: Result) -> list[str]:
    """Identifiers of a `--json` listing, in the order the command printed them."""
    payload = json_stdout(result)
    return [str(item["identifier"]) for item in payload["items"]]


def assert_missing_context(result: Result) -> None:
    """Exit 2, the declared sentence on `stderr` and nothing on `stdout`."""
    assert result.exit_code == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert TEAM_REQUIRED in result.stderr
    if not result.stderr.lstrip().startswith("{"):
        # The human rendering is the catalog sentence; only the JSON envelope carries the code.
        assert "missing_context" not in result.stderr


# --- no team at all ------------------------------------------------------------------------


def test_a_workspace_without_teams_exits_two(cli: Cli) -> None:
    """Zero candidates: nothing to list, so the command says what is missing (exit 2)."""
    cli.execute("DELETE FROM team")
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all")

    assert_missing_context(result)


def test_a_workspace_without_teams_reports_missing_context_in_json(cli: Cli) -> None:
    """`--json` moves the envelope to `stderr` and keeps the exit code (A7)."""
    cli.execute("DELETE FROM team")
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all", "--json")

    assert result.exit_code == 2, result.stdout + result.stderr
    assert result.stdout == ""
    error = error_json(result)
    assert error["code"] == "missing_context"
    assert error["field"] == "team"
    assert error["message"].endswith(TEAM_REQUIRED)


def test_a_workspace_without_teams_does_not_create_an_issue(cli: Cli) -> None:
    """The same hole on the write side: no team, no row, no silent guess."""
    cli.execute("DELETE FROM team")
    without_declared_team(cli)

    result = cli.run("issue", "create", "Orphan")

    assert_missing_context(result)
    assert cli.rows("SELECT id FROM issue") == []


# --- exactly one non-archived team ---------------------------------------------------------


def test_the_only_team_of_the_workspace_is_used(cli: Cli) -> None:
    """Step 4: one non-archived team, so that team, even if `[team]` names another key."""
    created = cli.create("Only one team")
    write_config(cli, '[team]\nkey = "ZZZ"\n')

    result = cli.run("issue", "list", "--all", "--json")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert identifiers(result) == [created]


def test_an_archived_team_does_not_count_as_a_candidate(cli: Cli) -> None:
    """Step 4 counts non-archived teams only: ENG archived leaves OPS as the one team."""
    eng_issue = cli.create("Belongs to ENG")
    add_team(cli, "OPS", "Operations")
    ops = cli.run("issue", "create", "Belongs to OPS", "--team", "OPS", "--json")
    assert ops.exit_code == 0, ops.stdout + ops.stderr
    ops_issue = str(json_stdout(ops)["identifier"])
    archive_team(cli, "ENG")
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all", "--json")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert identifiers(result) == [ops_issue]
    assert eng_issue not in result.stdout


# --- two or more teams ---------------------------------------------------------------------


def test_two_teams_without_a_declaration_exit_two(cli: Cli) -> None:
    """Two candidates and no choice made: exit 2 with the declared sentence."""
    add_team(cli, "OPS", "Operations")
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all")

    assert_missing_context(result)


def test_two_teams_and_an_empty_board_never_answer_zero_issues(cli: Cli) -> None:
    """The measured defect: `0 issues` and exit 0 from a workspace full of work.

    The team the old resolution picked (the `[team]` key) has no issues, so a listing that
    guesses answers *empty* instead of *ambiguous*. `stdout` stays empty: an empty page and
    an unresolved team are different answers and only one of them is exit 0.
    """
    add_team(cli, "OPS", "Operations")
    first = cli.run("issue", "create", "OPS work", "--team", "OPS", "--json")
    assert first.exit_code == 0, first.stdout + first.stderr
    second = cli.run("issue", "create", "More OPS work", "--team", "OPS", "--json")
    assert second.exit_code == 0, second.stdout + second.stderr
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all", "--json")

    assert_missing_context(result)
    assert "items" not in result.stdout


def test_creating_in_an_ambiguous_workspace_writes_nothing(cli: Cli) -> None:
    """A write must not land in a team the invocation never named."""
    add_team(cli, "OPS", "Operations")
    without_declared_team(cli)

    result = cli.run("issue", "create", "Into the void")

    assert_missing_context(result)
    assert cli.rows("SELECT id FROM issue") == []


def test_state_list_uses_the_same_resolution(cli: Cli) -> None:
    """One resolver for every verb: `state` fails exactly like `issue`."""
    add_team(cli, "OPS", "Operations")
    without_declared_team(cli)

    result = cli.run("state", "list")

    assert_missing_context(result)


def test_team_show_without_key_uses_the_same_resolution(cli: Cli) -> None:
    """`team show` without `KEY` resolves the team and fails with the same sentence."""
    add_team(cli, "OPS", "Operations")
    without_declared_team(cli)

    result = cli.run("team", "show")

    assert_missing_context(result)


# --- the sources that win over the workspace -----------------------------------------------


def test_the_environment_names_the_team(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 2: `HIFA_TEAM` beats the workspace, which has two teams and no declaration."""
    add_team(cli, "OPS", "Operations")
    created = cli.run("issue", "create", "OPS task", "--team", "OPS", "--json")
    assert created.exit_code == 0, created.stdout + created.stderr
    ops_issue = str(json_stdout(created)["identifier"])
    without_declared_team(cli)
    monkeypatch.setenv("HIFA_TEAM", "OPS")

    result = cli.run("issue", "list", "--all", "--json")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert identifiers(result) == [ops_issue]


def test_the_config_default_names_the_team(cli: Cli) -> None:
    """Step 3: `[cli].default_team` beats the workspace ambiguity."""
    add_team(cli, "OPS", "Operations")
    created = cli.run("issue", "create", "OPS task", "--team", "OPS", "--json")
    assert created.exit_code == 0, created.stdout + created.stderr
    ops_issue = str(json_stdout(created)["identifier"])
    write_config(cli, '[cli]\ndefault_team = "OPS"\n')

    result = cli.run("issue", "list", "--all", "--json")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert identifiers(result) == [ops_issue]


def test_the_flag_names_the_team_over_environment_and_config(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1: `--team` wins over `HIFA_TEAM` and over `[cli].default_team` together."""
    add_team(cli, "OPS", "Operations")
    eng_issue = cli.create("ENG task")
    write_config(cli, '[cli]\ndefault_team = "OPS"\n')
    monkeypatch.setenv("HIFA_TEAM", "OPS")

    result = cli.run("issue", "list", "--all", "--team", "ENG", "--json")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert identifiers(result) == [eng_issue]


def test_an_explicit_unknown_team_is_not_found(cli: Cli) -> None:
    """A name the invocation writes itself is looked up: unknown is exit 3, not exit 2."""
    add_team(cli, "OPS", "Operations")
    without_declared_team(cli)

    result = cli.run("issue", "list", "--all", "--team", "NOPE")

    assert result.exit_code == 3, result.stdout + result.stderr
    assert result.stdout == ""
    assert "team" in result.stderr
