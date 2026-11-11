"""Concurrency tests for state ordering with real processes (R25, D-019).

Every writer here is a separate `python -c` process running the real CLI against the same SQLite
file, because the failure mode this guards against lives in the transaction and the busy-timeout,
not in the Python call graph: two processes picking the same free slot, one of them losing, or an
`IntegrityError` escaping as a 500.

The suite has no browser: the drag gesture itself is only covered at the route and payload level
(`tests/e2e/web/test_board_reorder.py`). Playwright is not installed on this host and the browser
half stays a declared gap, not a simulated pass.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app

runner = CliRunner()
CANONICAL = ["Triage", "Todo", "Backlog", "In Progress", "Done", "Canceled"]

# Runs the real CLI in a fresh interpreter: `app()` reads sys.argv[1:].
BOOTSTRAP = "from hifa.cli.main import app; app()"


def _init(db: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HIFA_DB", str(db))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    result = runner.invoke(cli_app, ["init", "--team-key", "ENG"])
    assert result.exit_code == 0, result.output


def _ordered(db: Path) -> list[tuple[str, float]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT name, position FROM workflow_state ORDER BY position"
        ).fetchall()


@pytest.mark.slow
def test_eight_processes_moving_states_into_one_slot_keep_positions_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "concurrent.db"
    _init(db, tmp_path, monkeypatch)

    # Four different states all ask to sit right after Todo: the same computed slot, at once.
    moving = ["Triage", "In Progress", "Done", "Canceled"]
    env = {**os.environ, "HIFA_DB": str(db), "COLUMNS": "120"}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", BOOTSTRAP, "state", "update", name, "--after", "Todo"],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in moving * 2  # eight writers, two per state
    ]
    results = [(p.wait(timeout=120), p.communicate()) for p in processes]

    failures = [(code, out, err) for code, (out, err) in results if code != 0]
    assert failures == [], f"some writers failed: {failures}"

    rows = _ordered(db)
    positions = [position for _, position in rows]
    assert len(rows) == len(CANONICAL), f"states lost or duplicated: {rows}"
    assert len(set(positions)) == len(positions), f"duplicate positions: {rows}"

    names = [name for name, _ in rows]
    assert names.index("Todo") < names.index("Backlog"), f"Todo must stay left of Backlog: {names}"
    assert set(names) == set(CANONICAL)

    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.slow
def test_concurrent_creates_do_not_collide_on_a_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "concurrent-create.db"
    _init(db, tmp_path, monkeypatch)

    env = {**os.environ, "HIFA_DB": str(db), "COLUMNS": "120"}
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                BOOTSTRAP,
                "state",
                "create",
                f"Review {index}",
                "--type",
                "started",
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(6)
    ]
    results = [(p.wait(timeout=120), p.communicate()) for p in processes]

    failures = [(code, err) for code, (_, err) in results if code != 0]
    assert failures == [], f"some creates failed: {failures}"

    rows = _ordered(db)
    positions = [position for _, position in rows]
    assert len(rows) == len(CANONICAL) + 6
    assert len(set(positions)) == len(positions), f"duplicate positions: {rows}"
