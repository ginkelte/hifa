"""E2E tests for `hifa team create|list|show` (SPEC-050 §2.5.2, D-014, D-024).

They run the real Typer app over an isolated `HIFA_DB`: the canonical `--json` object, the
`KEY NAME ISSUES ARCHIVED` table of `list`, the key-value block of `show`, the `--states`
array of SPEC-050 §2.5.2 and the exit codes 0/2/3/4.

Every contract assertion here is a measured one: the header is compared token by token (the
previous revision asserted `ICON OPEN`, sealing the deviation), the archived filter is proven
by writing `archived_at` in SQL, and the counters are proven with an issue that is *closed*.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from hifa.cli.main import app
from hifa.config import CONFIG_FILENAME

runner = CliRunner()

#: SPEC-050 §2.4.1: `team list` columns, in this order.
TEAM_HEADER = ["KEY", "NAME", "ISSUES", "ARCHIVED"]

#: SPEC-050 §2.4.2 order of the canonical `Team` object (no key is ever omitted).
TEAM_KEYS = [
    "id",
    "key",
    "name",
    "icon",
    "color",
    "description",
    "timezone",
    "visibility",
    "parent_id",
    "cycles_enabled",
    "cycle_duration",
    "cycle_start_day",
    "issue_estimation_type",
    "default_issue_estimate",
    "triage_enabled",
    "auto_close_period",
    "archived_at",
    "created_at",
    "updated_at",
]

#: SPEC-050 §2.5.2: `team show --json` adds `progress` with these three keys, nothing else.
PROGRESS_KEYS = ["open_count", "closed_count", "urgent_open"]

#: SPEC-050 §2.4.2 order of the canonical `State` object.
STATE_KEYS = ["id", "name", "type", "color", "position", "is_default"]


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd inside a work tree + HIFA_DB (never the real environment) with one team."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()  # R13/D-041: the sandbox is a project, so `init` writes the config
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HIFA_ME_EMAIL", raising=False)
    monkeypatch.delenv("HIFA_TEAM", raising=False)
    init = run("init", "--team-key", "ENG")
    assert init.exit_code == 0, out(init)
    return SimpleNamespace(root=tmp_path, db=tmp_path / "hifa.db", config=project / CONFIG_FILENAME)


def run(*args: str) -> Any:
    return runner.invoke(app, list(args))


def out(result: Any) -> str:
    return result.stdout + result.stderr


def payload(result: Any) -> dict[str, Any]:
    """The JSON document of the invocation: data on `stdout`, errors on `stderr`."""
    text = result.stdout.strip() or result.stderr.strip()
    return json.loads(text)


def scalar(db: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def execute(db: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def write_config(sandbox: SimpleNamespace, body: str) -> None:
    """Rewrite `.hifa.toml` (the sandbox's `HIFA_DB` keeps winning the *path*)."""
    sandbox.config.write_text(body, encoding="utf-8")


def rows_by_key(result: Any) -> dict[str, list[str]]:
    """`team list` table → `{key: tokens}` (header excluded, blanks dropped)."""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {line.split()[0]: line.split() for line in lines[1:]}


# --- create -------------------------------------------------------------------------------


def test_create_exits_zero_and_emits_the_canonical_team(sandbox: SimpleNamespace) -> None:
    result = run("team", "create", "OPS", "Operations", "--json")

    assert result.exit_code == 0, out(result)
    team = payload(result)
    assert list(team) == TEAM_KEYS
    assert team["key"] == "OPS"
    assert team["name"] == "Operations"
    assert team["visibility"] == "public"
    assert team["created_at"].endswith("Z")


def test_create_normalizes_the_key_and_honors_the_flags(sandbox: SimpleNamespace) -> None:
    result = run(
        "team",
        "create",
        "ops",
        "Operations",
        "--icon",
        "🛠",
        "--color",
        "#123ABC",
        "--timezone",
        "UTC",
        "--json",
    )

    assert result.exit_code == 0, out(result)
    team = payload(result)
    assert (team["key"], team["icon"], team["color"], team["timezone"]) == (
        "OPS",
        "🛠",
        "#123ABC",
        "UTC",
    )


def test_create_persists_description_and_visibility(sandbox: SimpleNamespace) -> None:
    """B1: `--description` and `--visibility` must reach the `team` row (SPEC-050 §2.5.2).

    Asserted in SQL, not in the command's own output: reading back the JSON the edge built
    from the same object would pass even if nothing was written.
    """
    result = run(
        "team",
        "create",
        "OPS",
        "Operations",
        "--description",
        "Internal operations",
        "--visibility",
        "restricted",
    )

    assert result.exit_code == 0, out(result)
    stored = scalar(
        sandbox.db, "SELECT description || '|' || visibility FROM team WHERE key = 'OPS'"
    )
    assert stored == "Internal operations|restricted"


def test_create_defaults_description_to_null_and_visibility_to_public(
    sandbox: SimpleNamespace,
) -> None:
    assert run("team", "create", "OPS", "Operations").exit_code == 0

    stored = scalar(
        sandbox.db,
        "SELECT COALESCE(description, 'NULL') || '|' || visibility FROM team WHERE key = 'OPS'",
    )
    assert stored == "NULL|public"


@pytest.mark.parametrize("visibility", ["secret", "Public", ""])
def test_create_rejects_an_unknown_visibility(sandbox: SimpleNamespace, visibility: str) -> None:
    result = run("team", "create", "OPS", "Operations", "--visibility", visibility)

    assert result.exit_code == 2, out(result)
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM team WHERE key = 'OPS'") == 0


def test_create_seeds_the_six_states_with_todo_as_default(sandbox: SimpleNamespace) -> None:
    assert run("team", "create", "OPS", "Operations").exit_code == 0

    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workflow_state") == 12
    assert (
        scalar(
            sandbox.db,
            "SELECT COUNT(*) FROM workflow_state s JOIN team t ON t.id = s.team_id"
            " WHERE t.key = 'OPS'",
        )
        == 6
    )
    assert (
        scalar(
            sandbox.db,
            "SELECT COUNT(*) FROM workflow_state s JOIN team t ON t.id = s.team_id"
            " WHERE t.key = 'OPS' AND s.is_default = 1",
        )
        == 1
    )
    assert (
        scalar(
            sandbox.db,
            "SELECT s.name FROM workflow_state s JOIN team t ON t.id = s.team_id"
            " WHERE t.key = 'OPS' AND s.is_default = 1",
        )
        == "Todo"
    )


def test_create_with_an_invalid_key_exits_two(sandbox: SimpleNamespace) -> None:
    result = run("team", "create", "1BAD", "Bad", "--json")

    assert result.exit_code == 2, out(result)
    error = payload(result)["error"]
    assert error["code"] == "validation_error"
    assert error["field"] == "key"


def test_create_with_a_duplicate_key_exits_four(sandbox: SimpleNamespace) -> None:
    assert run("team", "create", "ENG2", "Team Two").exit_code == 0

    result = run("team", "create", "ENG2", "Team Two again", "--json")

    assert result.exit_code == 4, out(result)
    assert payload(result)["error"]["code"] == "conflict"
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM team") == 2


def test_create_human_output_is_one_row_with_the_list_header(sandbox: SimpleNamespace) -> None:
    result = run("team", "create", "OPS", "Operations")

    assert result.exit_code == 0, out(result)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].split() == TEAM_HEADER
    assert lines[1].split() == ["OPS", "Operations", "0", "no"]


# --- list ---------------------------------------------------------------------------------


def test_list_header_is_key_name_issues_archived(sandbox: SimpleNamespace) -> None:
    """B2: SPEC-050 §2.4.1/§2.5.2 fix `KEY  NAME  ISSUES  ARCHIVED` — nothing else."""
    assert run("team", "create", "ENG3", "Third").exit_code == 0
    assert run("issue", "create", "Fix login", "--team", "ENG3").exit_code == 0
    assert run("issue", "create", "Fix logout", "--team", "ENG3").exit_code == 0

    result = run("team", "list")

    assert result.exit_code == 0, out(result)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].split() == TEAM_HEADER
    table = rows_by_key(result)
    assert list(table) == ["ENG", "ENG3"]  # sorted by key ASC
    assert table["ENG"][2] == "0"
    assert table["ENG3"][2] == "2"
    assert table["ENG"][-1] == "no"


def test_list_issues_counts_a_closed_issue_too(sandbox: SimpleNamespace) -> None:
    """B2: `ISSUES` is `open_count + closed_count`, not the open count (SPEC-050 §2.5.2)."""
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    assert run("issue", "create", "Done one", "--team", "OPS", "-s", "Done").exit_code == 0

    result = run("team", "list")

    assert result.exit_code == 0, out(result)
    assert rows_by_key(result)["OPS"][2] == "1"


def test_list_hides_archived_teams_unless_all(sandbox: SimpleNamespace) -> None:
    """B3: the archived filter lives in the SQL, not in Python after `list()`.

    The team is archived by writing `archived_at` directly: no CLI command can produce that
    state yet, and the filter must be proven against the real column.
    """
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    execute(sandbox.db, "UPDATE team SET archived_at = '2026-01-01T00:00:00Z' WHERE key = 'OPS'")

    without_all = run("team", "list")
    with_all = run("team", "list", "--all")

    assert without_all.exit_code == 0, out(without_all)
    assert list(rows_by_key(without_all)) == ["ENG"]
    assert with_all.exit_code == 0, out(with_all)
    table = rows_by_key(with_all)
    assert list(table) == ["ENG", "OPS"]
    assert table["OPS"][-1] == "yes"
    assert table["ENG"][-1] == "no"


def test_list_all_json_includes_the_archived_team(sandbox: SimpleNamespace) -> None:
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    execute(sandbox.db, "UPDATE team SET archived_at = '2026-01-01T00:00:00Z' WHERE key = 'OPS'")

    without_all = json.loads(run("team", "list", "--json").stdout)
    with_all = json.loads(run("team", "list", "--all", "--json").stdout)

    assert [row["key"] for row in without_all["items"]] == ["ENG"]
    assert [row["key"] for row in with_all["items"]] == ["ENG", "OPS"]
    archived = with_all["items"][1]
    assert archived["archived"] is True
    assert archived["archived_at"].startswith("2026-01-01T00:00:00")


def test_list_json_carries_the_count_per_team(sandbox: SimpleNamespace) -> None:
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    assert run("issue", "create", "Fix login", "--team", "OPS").exit_code == 0

    result = run("team", "list", "--json")

    assert result.exit_code == 0, out(result)
    payload_ = json.loads(result.stdout)
    # SPEC-050 §2.4: a list command answers with the {items: [...]} envelope, never a bare array.
    assert set(payload_) == {"items"}, payload_
    rows = payload_["items"]
    assert [row["key"] for row in rows] == ["ENG", "OPS"]
    # `issues_count` is the live total (open + closed) of SPEC-050 §2.5.2 — the only additive
    # counter D-026 blesses: the bare `open` key was undeclared and is gone.
    assert [row["issues_count"] for row in rows] == [0, 1]
    assert "open" not in rows[1]
    assert all(set(TEAM_KEYS) <= set(row) for row in rows)


# --- show ---------------------------------------------------------------------------------


def test_show_is_case_insensitive(sandbox: SimpleNamespace) -> None:
    assert run("team", "create", "ENG2", "Team Two").exit_code == 0

    result = run("team", "show", "eng2")

    assert result.exit_code == 0, out(result)
    assert "ENG2" in result.stdout
    assert "Team Two" in result.stdout
    assert "states" in result.stdout


def test_show_json_adds_the_progress_counters_with_the_spec_keys(
    sandbox: SimpleNamespace,
) -> None:
    """B5: SPEC-050 §2.5.2 fixes `progress: {open_count, closed_count, urgent_open}`."""
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    assert run("issue", "create", "Fix login", "--team", "OPS").exit_code == 0
    assert run("issue", "create", "Fix logout", "--team", "OPS", "-s", "Done").exit_code == 0
    assert run("issue", "create", "Burn down", "--team", "OPS", "-p", "urgent").exit_code == 0

    result = run("team", "show", "OPS", "--json")

    assert result.exit_code == 0, out(result)
    team = payload(result)
    assert list(team) == [*TEAM_KEYS, "progress"]
    assert list(team["progress"]) == PROGRESS_KEYS
    assert team["progress"] == {"open_count": 2, "closed_count": 1, "urgent_open": 1}


def test_show_states_json_is_sorted_by_position(sandbox: SimpleNamespace) -> None:
    """B4: `--states` adds `states: State[]` ordered by `position` ASC (SPEC-050 §2.5.2)."""
    result = run("team", "show", "ENG", "--states", "--json")

    assert result.exit_code == 0, out(result)
    team = payload(result)
    assert list(team) == [*TEAM_KEYS, "progress", "states"]
    states = team["states"]
    assert [state["name"] for state in states] == [
        "Triage",
        "Todo",
        "Backlog",
        "In Progress",
        "Done",
        "Canceled",
    ]
    assert [state["position"] for state in states] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert all(list(state) == STATE_KEYS for state in states)
    assert [state["is_default"] for state in states].count(True) == 1


def test_show_without_states_does_not_emit_the_array(sandbox: SimpleNamespace) -> None:
    result = run("team", "show", "ENG", "--json")

    assert result.exit_code == 0, out(result)
    assert "states" not in payload(result)


def test_show_states_human_lists_the_names(sandbox: SimpleNamespace) -> None:
    result = run("team", "show", "ENG", "--states")

    assert result.exit_code == 0, out(result)
    assert "6 (Triage, Todo*, Backlog, In Progress, Done, Canceled)" in result.stdout


def test_show_with_an_unknown_key_exits_three(sandbox: SimpleNamespace) -> None:
    result = run("team", "show", "NOPE", "--json")

    assert result.exit_code == 3, out(result)
    assert payload(result)["error"]["code"] == "not_found"


def test_show_without_key_uses_the_configured_default_team(sandbox: SimpleNamespace) -> None:
    """B6: without `KEY` the team comes from `[cli].default_team` (SPEC-050 §2.2.2).

    Two teams exist, so the answer can only come from the config: picking "the first one"
    by key or by `created_at` would return ENG.
    """
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    write_config(sandbox, '[cli]\ndefault_team = "OPS"\n')

    result = run("team", "show")

    assert result.exit_code == 0, out(result)
    assert "OPS" in result.stdout
    assert "ENG" not in result.stdout


def test_show_without_key_and_without_a_resolvable_default_exits_two(
    sandbox: SimpleNamespace,
) -> None:
    """B6: an unresolvable implicit team is exit 2 `missing_context`, not a Typer parse error."""
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    write_config(sandbox, '[cli]\ndefault_team = "ZZZ"\n')

    result = run("team", "show")

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    assert "missing_context" in result.stderr
    assert "team required: pass KEY, set HIFA_TEAM or [cli].default_team in .hifa.toml" in (
        result.stderr
    )


def test_show_without_key_and_json_emits_the_missing_context_code(
    sandbox: SimpleNamespace,
) -> None:
    assert run("team", "create", "OPS", "Operations").exit_code == 0
    write_config(sandbox, '[cli]\ndefault_team = "ZZZ"\n')

    result = run("team", "show", "--json")

    assert result.exit_code == 2, out(result)
    assert payload(result)["error"]["code"] == "missing_context"


def test_show_without_key_falls_back_to_the_only_team(sandbox: SimpleNamespace) -> None:
    """Step 4 of 2.2.2: with a single non-archived team and no usable default, that one."""
    write_config(sandbox, '[team]\nkey = "ZZZ"\n')

    result = run("team", "show", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["key"] == "ENG"


# --- --quiet and --json --quiet (REQ-F050-05) ---------------------------------------------

#: One invocation per leaf command; `--quiet` promises the team **key** in all three.
QUIET_CASES = [
    ("create", ("team", "create", "OPS", "Operations"), "OPS"),
    ("list", ("team", "list"), "ENG"),
    ("show", ("team", "show", "ENG"), "ENG"),
]


@pytest.mark.parametrize(("argv", "expected"), [case[1:] for case in QUIET_CASES])
def test_quiet_prints_the_team_key(
    sandbox: SimpleNamespace, argv: tuple[str, ...], expected: str
) -> None:
    """N1q: `--quiet` is "Team key(s) only" — never the ULID, in all three commands."""
    result = run(*argv, "--quiet")

    assert result.exit_code == 0, out(result)
    assert result.stdout.strip() == expected
    assert len(expected) < 26  # a ULID is 26 characters; a key is at most 10


@pytest.mark.parametrize(("argv", "expected"), [case[1:] for case in QUIET_CASES])
def test_json_and_quiet_together_exit_two(
    sandbox: SimpleNamespace, argv: tuple[str, ...], expected: str
) -> None:
    """N2q: REQ-F050-05 — the pair is rejected with exit 2 `invalid_option` everywhere."""
    result = run(*argv, "--json", "--quiet")

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    assert "invalid_option" not in result.stdout
    error = payload(result)
    assert error["error"]["code"] == "invalid_option"
    assert error["error"]["field"] == "output"


# --- errors -------------------------------------------------------------------------------


def test_missing_database_message_does_not_repeat_the_code(
    sandbox: SimpleNamespace, tmp_path: Path
) -> None:
    """N8q: the human line said `hifa: database_not_found: database not found: /path`."""
    missing = tmp_path / "nope.db"

    result = run("team", "list", "--db", str(missing))

    assert result.exit_code == 3, out(result)
    assert result.stderr.strip() == (
        f"hifa: database not found at {missing}; run 'hifa init' (field: db)"
    )
    assert "database_not_found" not in result.stderr


def test_missing_database_json_keeps_the_code(sandbox: SimpleNamespace, tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"

    result = run("team", "list", "--db", str(missing), "--json")

    assert result.exit_code == 3, out(result)
    error = payload(result)["error"]
    assert error["code"] == "database_not_found"
    assert error["field"] == "db"
    assert error["message"] == f"database not found at {missing}; run 'hifa init'"
