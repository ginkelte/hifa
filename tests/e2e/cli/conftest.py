"""Shared fixtures for the CLI end-to-end tests.

Every test gets its own workspace: a project directory holding a `.git` entry (so `hifa init`
writes `.hifa.toml` there), its own database and its own `HOME`/`XDG_DATA_HOME`, so nothing
touches the real `~/.local/share/hifa`. The `Cli` helper wraps `CliRunner` and reads the same
database the subprocess-free invocation wrote, which is how the tests assert on history rows.

The rendered text is pinned too: every invocation runs under `CLI_ENV`, and `plain()` removes
ANSI escapes from whatever the assertion reads, so a test about an option or a click message
does not depend on the terminal (or the CI runner) that happens to execute the suite.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result
from typer import rich_utils as typer_rich_utils
from typer.testing import CliRunner

from hifa.cli.main import app

runner = CliRunner()

#: Environment every CLI invocation runs under. `NO_COLOR`, `FORCE_COLOR`, `PY_COLORS` and
#: `TERM` decide whether Rich/Typer emit ANSI escapes, and `COLUMNS` decides where they wrap;
#: pinning all of them makes help screens and click error panels render the same text on a
#: developer machine, on a CI runner and with no terminal. The empty values are deliberate:
#: `FORCE_COLOR=""` is how Rich reads "do not force a terminal" (`FORCE_COLOR=0` would force).
CLI_ENV: dict[str, str] = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "COLUMNS": "80",
    "LINES": "24",
    "PY_COLORS": "",
    "FORCE_COLOR": "",
    "CLICOLOR": "0",
    "CLICOLOR_FORCE": "0",
}

#: An ANSI escape sequence: `ESC[`, its parameters and one final byte.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """`text` with ANSI escapes removed and every run of whitespace collapsed to one space."""
    return " ".join(ANSI_ESCAPE.sub("", text).split())


@pytest.fixture(autouse=True)
def pinned_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the one colour switch that `runner.invoke(env=...)` reaches too late.

    `typer.rich_utils` resolves `GITHUB_ACTIONS` / `FORCE_COLOR` / `PY_COLORS` once, when the
    module is imported, and keeps the answer in `FORCE_TERMINAL`; the env of an invocation is
    applied afterwards, so a suite started on a CI runner would render styled text no matter
    what `CLI_ENV` says. Resetting the attribute makes in-process help and click errors render
    without colour whatever environment started the process.
    """
    monkeypatch.setattr(typer_rich_utils, "FORCE_TERMINAL", None)


#: Keys of the canonical `Issue` object, in order.
CANONICAL_KEYS: tuple[str, ...] = (
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
)

#: Header of the row every issue mutation prints.
ISSUE_HEADER = "IDENTIFIER  P  STATE  ASSIGNEE  TITLE  LABELS  DUE"


def json_stdout(result: Result) -> Any:
    """`stdout` parsed as one JSON document (no other text is allowed there)."""
    return json.loads(result.stdout)


def error_json(result: Result) -> dict[str, Any]:
    """Error envelope of a `--json` invocation, read from `stderr`."""
    payload = json.loads(result.stderr)
    assert "error" in payload, payload
    return payload["error"]


@dataclass
class Cli:
    """A workspace plus the helpers to invoke the CLI and read the database back."""

    db: Path
    cwd: Path

    def run(self, *args: str, input: str | None = None) -> Result:
        """Invoke the CLI; `input` is what `stdin` yields (a `-` flag reads it).

        Every invocation carries `CLI_ENV`, so the text it renders does not depend on the
        environment of the process running the suite.
        """
        return runner.invoke(app, list(args), input=input, env=CLI_ENV)

    def rows(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def issue(self, identifier: str) -> sqlite3.Row:
        rows = self.rows("SELECT * FROM issue WHERE identifier = ?", (identifier,))
        assert rows, f"issue {identifier} is not in the database"
        return rows[0]

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        """A committed write used to build a state the CLI cannot reach yet."""
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def history(self, identifier: str) -> list[sqlite3.Row]:
        """History rows of one issue, newest first, without the row `create` wrote."""
        return self.rows(
            """
            SELECT field, old_value, new_value FROM issue_history
            WHERE issue_id = (SELECT id FROM issue WHERE identifier = ?) AND field <> 'created'
            ORDER BY rowid DESC
            """,
            (identifier,),
        )

    def history_fields(self, identifier: str) -> list[str]:
        return [row["field"] for row in self.history(identifier)]

    def updated_at(self, identifier: str) -> str:
        return str(self.issue(identifier)["updated_at"])

    def create(self, title: str, *args: str) -> str:
        """Create an issue and return its identifier."""
        result = self.run("issue", "create", title, *args, "--json")
        assert result.exit_code == 0, result.stdout + result.stderr
        return str(json_stdout(result)["identifier"])

    def archive(self, identifier: str) -> None:
        result = self.run("issue", "archive", identifier, "--yes")
        assert result.exit_code == 0, result.stdout + result.stderr

    def state_id(self, name: str, team: str = "ENG") -> str:
        rows = self.rows(
            """
            SELECT workflow_state.id AS id FROM workflow_state
            JOIN team ON team.id = workflow_state.team_id
            WHERE workflow_state.name = ? AND team.key = ?
            """,
            (name, team),
        )
        assert rows, f"state {name} of team {team} is not in the database"
        return str(rows[0]["id"])


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Cli:
    """Initialized workspace with team ENG and its six default workflow states."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HIFA_TEAM", raising=False)
    monkeypatch.delenv("HIFA_MEMBER", raising=False)
    result = runner.invoke(app, ["init", "--team-key", "ENG"], env=CLI_ENV)
    assert result.exit_code == 0, result.stdout + result.stderr
    return Cli(db=tmp_path / "hifa.db", cwd=project)


@pytest.fixture
def other_team(cli: Cli) -> str:
    """Second team OPS with one state Review; returns the ULID of that state."""
    created = cli.run("team", "create", "OPS", "Operations")
    assert created.exit_code == 0, created.stdout + created.stderr
    state = cli.run("state", "create", "Review", "--type", "unstarted", "--team", "OPS")
    assert state.exit_code == 0, state.stdout + state.stderr
    return cli.state_id("Review", "OPS")
