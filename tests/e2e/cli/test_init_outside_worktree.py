"""`hifa init` outside a git work tree writes no config and uses the global database (R13, D-041).

These run the real Typer app through `CliRunner` in a scratch directory whose ancestors hold no
`.git`: the guard of R13 must suppress the file, keep exit `0`, leave the resolution of R1 intact
and say `config: none (outside a git work tree)` in the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from conftest import CLI_ENV
from hifa.cli.main import app
from hifa.config import CONFIG_FILENAME

runner = CliRunner()

NO_CONFIG = "none (outside a git work tree)"


def git_ancestor(directory: Path) -> Path | None:
    """The nearest ancestor containing `.git`, or `None`."""
    current = directory.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


@pytest.fixture
def outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Scratch cwd with no `.git` anywhere above it; `HIFA_DB` unset, XDG and HOME sandboxed."""
    if git_ancestor(tmp_path) is not None:
        pytest.skip("the temporary root of this machine lives inside a git work tree")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
    monkeypatch.delenv("HIFA_DB", raising=False)
    monkeypatch.delenv("HIFA_TEAM", raising=False)
    monkeypatch.delenv("HIFA_ME_EMAIL", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return SimpleNamespace(
        root=tmp_path,
        cwd=scratch,
        global_db=tmp_path / "xdg" / "hifa" / "hifa.db",
        config=scratch / CONFIG_FILENAME,
    )


def run(*args: str) -> Any:
    return runner.invoke(app, list(args), env=CLI_ENV)


def out(result: Any) -> str:
    return result.stdout + result.stderr


def config_files_between(directory: Path, stop: Path) -> list[Path]:
    """Every `.hifa.toml` from `directory` up to (excluding) `stop` — the ancestors it feeds."""
    found: list[Path] = []
    current = directory.resolve()
    ceiling = stop.resolve()
    while current != ceiling:
        candidate = current / CONFIG_FILENAME
        if candidate.exists():
            found.append(candidate)
        if current.parent == current:
            break
        current = current.parent
    return found


def test_init_outside_a_work_tree_writes_no_config(outside: SimpleNamespace) -> None:
    result = run("init")

    assert result.exit_code == 0, out(result)
    assert not outside.config.exists()
    assert config_files_between(outside.cwd, outside.root) == []


def test_init_outside_a_work_tree_seeds_the_global_database(outside: SimpleNamespace) -> None:
    result = run("init", "--json")

    assert result.exit_code == 0, out(result)
    payload = json.loads(result.stdout)
    assert payload["db_path"] == str(outside.global_db)
    assert outside.global_db.is_file()


def test_init_outside_a_work_tree_reports_that_it_wrote_no_config(
    outside: SimpleNamespace,
) -> None:
    result = run("init")

    assert result.exit_code == 0, out(result)
    config_lines = [
        line for line in result.stdout.splitlines() if line.strip().startswith("config")
    ]
    assert len(config_lines) == 1, result.stdout
    assert NO_CONFIG in config_lines[0]
    assert str(outside.cwd / CONFIG_FILENAME) not in result.stdout


def test_force_outside_a_work_tree_still_writes_no_config(outside: SimpleNamespace) -> None:
    first = run("init")
    forced = run("init", "--force")

    assert first.exit_code == 0 and forced.exit_code == 0, out(forced)
    assert not outside.config.exists()
    assert not outside.config.with_name(f"{CONFIG_FILENAME}.bak").exists()
    # The first run reports the config state; the second one short-circuits on "already
    # initialized", where there is no config to report and nothing to force.
    assert NO_CONFIG in first.stdout
    assert str(outside.config) not in forced.stdout


def test_hifa_db_still_wins_outside_a_work_tree(
    outside: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = outside.root / "env.db"
    monkeypatch.setenv("HIFA_DB", str(chosen))

    result = run("init", "--json")

    assert result.exit_code == 0, out(result)
    assert json.loads(result.stdout)["db_path"] == str(chosen)
    assert chosen.is_file()
    assert not outside.config.exists()
    assert not outside.global_db.exists()


def test_db_flag_still_wins_outside_a_work_tree(outside: SimpleNamespace) -> None:
    chosen = outside.root / "flag.db"

    result = run("--db", str(chosen), "init", "--json")

    assert result.exit_code == 0, out(result)
    assert json.loads(result.stdout)["db_path"] == str(chosen)
    assert chosen.is_file()
    assert not outside.config.exists()
