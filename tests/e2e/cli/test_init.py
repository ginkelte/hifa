"""E2E for `hifa init` (SPEC-050 §2.5.1, SPEC-100 §2.5/§3.1/§4.2).

Runs the real Typer binary through `CliRunner` against an `HIFA_DB` in `tmp_path` and checks the
seeded database, permissions, idempotency, `--force`, `--no-labels`, `--json` and the validation
errors (exit 2).
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

EXPECTED_STATES = [
    ("Triage", "triage"),
    ("Todo", "unstarted"),
    ("Backlog", "backlog"),
    ("In Progress", "started"),
    ("Done", "completed"),
    ("Canceled", "canceled"),
]
EXPECTED_LABELS = ["Bug", "Feature", "Improvement", "Docs"]
JSON_KEYS = [
    "created",
    "skipped",
    "workspace_id",
    "team_id",
    "member_id",
    "db_path",
    "already_initialized",
]


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd inside a work tree, with HIFA_DB/XDG/HOME in tmp_path."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()  # R13/D-041: `init` writes `.hifa.toml` only inside a work tree
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HIFA_ME_EMAIL", raising=False)
    return SimpleNamespace(
        root=tmp_path,
        cwd=project,
        db=tmp_path / "hifa.db",
        config=project / CONFIG_FILENAME,
    )


def run(*args: str) -> Any:
    return runner.invoke(app, list(args))


def query(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def scalar(db: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    rows = query(db, sql, params)
    return rows[0][0]


def out(result: Any) -> str:
    return result.stdout + result.stderr


# --- siembra ---------------------------------------------------------------------------


def test_init_exits_zero(sandbox: SimpleNamespace) -> None:
    result = run("init")
    assert result.exit_code == 0, out(result)


def test_init_creates_database_and_config(sandbox: SimpleNamespace) -> None:
    result = run("init")
    assert result.exit_code == 0, out(result)
    assert sandbox.db.is_file()
    assert sandbox.config.is_file()


def test_init_human_output_mentions_initialized(sandbox: SimpleNamespace) -> None:
    result = run("init")
    assert "initialized hifa at" in result.stdout
    assert "next: hifa issue create" in result.stdout


def test_init_seeds_the_six_canonical_states(sandbox: SimpleNamespace) -> None:
    run("init")
    rows = query(sandbox.db, "SELECT name, type FROM workflow_state ORDER BY position")
    assert [(row["name"], row["type"]) for row in rows] == EXPECTED_STATES


def test_init_has_exactly_one_default_state_and_it_is_todo(sandbox: SimpleNamespace) -> None:
    run("init")
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workflow_state WHERE is_default = 1") == 1
    assert scalar(sandbox.db, "SELECT name FROM workflow_state WHERE is_default = 1") == "Todo"
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workflow_state") == 6


def test_init_seeds_four_workspace_labels(sandbox: SimpleNamespace) -> None:
    run("init")
    rows = query(sandbox.db, "SELECT name, team_id FROM label ORDER BY name")
    assert [row["name"] for row in rows] == sorted(EXPECTED_LABELS)
    assert all(row["team_id"] is None for row in rows)


def test_init_seeds_member_and_membership(sandbox: SimpleNamespace) -> None:
    run("init")
    member = query(sandbox.db, "SELECT * FROM member")
    assert len(member) == 1
    assert member[0]["is_admin"] == 1
    assert member[0]["is_active"] == 1
    assert member[0]["initials"]
    membership = query(sandbox.db, "SELECT * FROM team_membership")
    assert len(membership) == 1
    assert membership[0]["is_owner"] == 1


def test_init_leaves_user_version_at_three(sandbox: SimpleNamespace) -> None:
    run("init")
    assert scalar(sandbox.db, "PRAGMA user_version") == 3


def test_init_seeds_exactly_one_workspace_and_team(sandbox: SimpleNamespace) -> None:
    run("init")
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workspace") == 1
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM team") == 1
    assert scalar(sandbox.db, "SELECT key FROM team") == "ENG"


def test_init_applies_the_mode_0600_to_the_database(sandbox: SimpleNamespace) -> None:
    run("init")
    assert sandbox.db.stat().st_mode & 0o777 == 0o600


def test_init_mode_0700_for_database_directory(sandbox: SimpleNamespace) -> None:
    db = sandbox.root / "nested" / "hifa.db"
    result = run("init", "--db", str(db))
    assert result.exit_code == 0, out(result)
    assert db.parent.stat().st_mode & 0o777 == 0o700
    assert db.stat().st_mode & 0o777 == 0o600


def test_init_config_file_mode_0600_and_contents(sandbox: SimpleNamespace) -> None:
    run("init", "--name", "Ginkelte", "--team-key", "ENG", "--team-name", "Engineering")
    assert sandbox.config.stat().st_mode & 0o777 == 0o600
    text = sandbox.config.read_text(encoding="utf-8")
    assert 'name = "Ginkelte"' in text
    assert 'url_key = "ginkelte"' in text
    assert 'key = "ENG"' in text
    assert f'db = "{sandbox.db}"' in text


# --- idempotencia y flags ---------------------------------------------------------------


def test_init_is_idempotent(sandbox: SimpleNamespace) -> None:
    first = run("init")
    second = run("init")
    assert first.exit_code == 0 and second.exit_code == 0
    assert "already initialized" in second.stdout


def test_init_second_run_keeps_counts(sandbox: SimpleNamespace) -> None:
    run("init")
    run("init")
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workspace") == 1
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM team") == 1
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM workflow_state") == 6
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM label") == 4
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM member") == 1


def test_init_second_run_reports_empty_created(sandbox: SimpleNamespace) -> None:
    run("init")
    second = run("init", "--json")
    payload = json.loads(second.stdout)
    assert payload["created"] == []
    assert payload["already_initialized"] is True


def test_no_labels_seeds_no_labels(sandbox: SimpleNamespace) -> None:
    result = run("init", "--no-labels")
    assert result.exit_code == 0, out(result)
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM label") == 0


def test_force_adds_the_missing_labels(sandbox: SimpleNamespace) -> None:
    run("init", "--no-labels")
    forced = run("init", "--force")
    assert forced.exit_code == 0, out(forced)
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM label") == 4
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM issue") == 0


def test_force_rewrites_config_and_keeps_a_backup(sandbox: SimpleNamespace) -> None:
    run("init")
    original = sandbox.config.read_text(encoding="utf-8")
    result = run("init", "--force", "--team-name", "Platform")
    assert result.exit_code == 0, out(result)
    assert (sandbox.cwd / f"{CONFIG_FILENAME}.bak").read_text(encoding="utf-8") == original
    assert 'name = "Platform"' in sandbox.config.read_text(encoding="utf-8")


def test_no_member_skips_member_and_membership(sandbox: SimpleNamespace) -> None:
    result = run("init", "--no-member")
    assert result.exit_code == 0, out(result)
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM member") == 0
    assert scalar(sandbox.db, "SELECT COUNT(*) FROM team_membership") == 0


def test_team_key_is_normalized_to_uppercase(sandbox: SimpleNamespace) -> None:
    result = run("init", "--team-key", "eng")
    assert result.exit_code == 0, out(result)
    assert scalar(sandbox.db, "SELECT key FROM team") == "ENG"


def test_workspace_name_and_url_key(sandbox: SimpleNamespace) -> None:
    run("init", "--name", "Hifa Dev")
    row = query(sandbox.db, "SELECT name, url_key FROM workspace")[0]
    assert row["name"] == "Hifa Dev"
    assert row["url_key"] == "hifa-dev"


# --- salida ----------------------------------------------------------------------------


def test_json_envelope_is_exact(sandbox: SimpleNamespace) -> None:
    result = run("init", "--json")
    assert result.exit_code == 0, out(result)
    payload = json.loads(result.stdout)
    assert list(payload) == JSON_KEYS
    assert payload["created"][:2] == ["workspace", "team"]
    assert "workflow_state:Todo" in payload["created"]
    assert "label:Bug" in payload["created"]
    assert payload["created"][-2:] == ["member", "team_membership"]
    assert payload["member_id"]
    assert payload["db_path"] == str(sandbox.db)
    assert payload["already_initialized"] is False


def test_json_global_flag_before_subcommand(sandbox: SimpleNamespace) -> None:
    result = run("--json", "init")
    assert result.exit_code == 0, out(result)
    assert list(json.loads(result.stdout)) == JSON_KEYS


def test_quiet_prints_only_ids(sandbox: SimpleNamespace) -> None:
    result = run("init", "--quiet")
    assert result.exit_code == 0, out(result)
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 3
    assert all(line.startswith("01") for line in lines)


def test_json_and_quiet_are_mutually_exclusive(sandbox: SimpleNamespace) -> None:
    result = run("init", "--json", "--quiet")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "invalid_option" in result.stderr


# --- errores ---------------------------------------------------------------------------


def test_invalid_team_key_exits_two(sandbox: SimpleNamespace) -> None:
    result = run("init", "--team-key", "1bad")
    assert result.exit_code == 2
    assert "config_invalid_value" in result.stderr
    assert not sandbox.db.exists()


def test_invalid_timezone_exits_two_before_touching_the_database(
    sandbox: SimpleNamespace,
) -> None:
    result = run("init", "--timezone", "Mars/Olympus")
    assert result.exit_code == 2
    assert "config_invalid_value" in result.stderr
    assert "team.timezone" in result.stderr
    assert not sandbox.db.exists()


def test_invalid_icon_exits_two(sandbox: SimpleNamespace) -> None:
    result = run("init", "--icon", "dos palabras")
    assert result.exit_code == 2
    assert "team.icon" in result.stderr


def test_foreign_database_is_rejected_untouched(sandbox: SimpleNamespace) -> None:
    foreign = sandbox.root / "foreign.db"
    conn = sqlite3.connect(foreign)
    try:
        conn.execute("CREATE TABLE foo (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    before = foreign.read_bytes()

    result = run("init", "--db", str(foreign))

    assert result.exit_code == 2
    assert "not_a_hifa_database" in result.stderr
    assert foreign.read_bytes() == before


def test_memory_database_is_rejected(sandbox: SimpleNamespace) -> None:
    result = run("init", "--db", ":memory:")
    assert result.exit_code == 2
    assert "in_memory_not_supported" in result.stderr


def test_broken_toml_exits_two_with_line(sandbox: SimpleNamespace) -> None:
    sandbox.config.write_text("[web]\nport = = 7777\n", encoding="utf-8")
    result = run("init")
    assert result.exit_code == 2
    assert "config_invalid_toml" in result.stderr
    assert "line" in result.stderr.lower()


def test_flag_db_wins_over_env(sandbox: SimpleNamespace) -> None:
    other = sandbox.root / "flag.db"
    result = run("--db", str(other), "init")
    assert result.exit_code == 0, out(result)
    assert other.is_file()
    assert not sandbox.db.exists()
