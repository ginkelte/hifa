#!/usr/bin/env python3
"""Check that the applied DDL matches the one declared in the data-model spec.

CI usage: create a temporary database, apply the migrations and compare `sqlite_master`
against the `CREATE TABLE`/`CREATE INDEX`/`CREATE VIEW` statements of the spec.
Exit 0 without drift, 1 with drift.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hifa.adapters.sqlite.connection import open_db  # noqa: E402
from hifa.adapters.sqlite.migrate import apply_migrations  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "drift.db"
        conn = open_db(db)
        apply_migrations(conn)
        conn.close()
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "spec_contract_check.py"),
                "--db",
                str(db),
                "--spec",
                str(ROOT / "specs" / "010-data-model.md"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
