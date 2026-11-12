#!/usr/bin/env python3
"""Verify that the real database schema matches the DDL declared in the specs.

The code is derived from the spec, so drift between `specs/010-data-model.md` (the source of
truth for the DDL) and `sqlite_master` is a bug. This script is the check.

Usage:

    python scripts/spec_contract_check.py --spec specs/010-data-model.md --db /tmp/hifa.db
    python scripts/spec_contract_check.py --all            # every check, temporary database
    python scripts/spec_contract_check.py --all --db path  # every check against that file

No external dependencies: stdlib only (it works before any domain code exists).
Exit 0 without drift, 1 with drift or a read error.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SQL_BLOCK_RE = re.compile(r"^```sql\s*$(?P<body>.*?)^```\s*$", re.MULTILINE | re.DOTALL)
CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)
CREATE_VIEW_RE = re.compile(
    r"^\s*CREATE\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)
CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)

# SQLite internal tables and FTS5 shadow tables: they are not declared in the spec.
SYSTEM_TABLE_PREFIXES = ("sqlite_", "issue_fts_")


@dataclass
class Schema:
    """Set of objects declared (by a spec) or existing (by `sqlite_master`)."""

    tables: set[str] = field(default_factory=set)
    views: set[str] = field(default_factory=set)
    indexes: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.tables or self.views or self.indexes)


def parse_spec(path: Path) -> Schema:
    """Extract tables, views and indexes from the ```sql blocks of a spec."""
    text = path.read_text(encoding="utf-8")
    schema = Schema()
    for block in SQL_BLOCK_RE.finditer(text):
        body = block.group("body")
        schema.tables.update(m.group(1) for m in CREATE_TABLE_RE.finditer(body))
        schema.views.update(m.group(1) for m in CREATE_VIEW_RE.finditer(body))
        schema.indexes.update(m.group(1) for m in CREATE_INDEX_RE.finditer(body))
    return schema


def read_db(db_path: Path) -> Schema:
    """Read the real objects of the database (excludes SQLite internals and shadow tables)."""
    if not db_path.exists():
        raise FileNotFoundError(f"database does not exist: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()

    schema = Schema()
    for obj_type, name in rows:
        if name.lower().startswith(SYSTEM_TABLE_PREFIXES) and name != "issue_fts":
            continue
        if obj_type == "table":
            schema.tables.add(name)
        elif obj_type == "view":
            schema.views.add(name)
        elif obj_type == "index":
            schema.indexes.add(name)
    return schema


def diff(expected: Schema, actual: Schema) -> list[str]:
    """Compare two schemas and return the drift lines (empty = no drift)."""
    problems: list[str] = []
    for kind in ("tables", "views", "indexes"):
        want: set[str] = getattr(expected, kind)
        got: set[str] = getattr(actual, kind)
        if not want:
            continue
        missing = sorted(want - got)
        extra = sorted(got - want)
        if missing:
            problems.append(f"  missing in the database ({kind}): {', '.join(missing)}")
        if extra:
            problems.append(f"  extra in the database ({kind}): {', '.join(extra)}")
    return problems


def migrate_temp_db(tmp: Path) -> Path | None:
    """Migrate a temporary database with the project migrations.

    Return the path, or None when the migrator does not exist yet (T-01 before T-02).
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from hifa.adapters.sqlite.connection import open_db
        from hifa.adapters.sqlite.migrate import apply_migrations
    except ImportError as exc:  # pragma: no cover - depends on the state of the repo
        print(f"SKIP: migrator not available ({exc})")
        return None
    db_path = tmp / "spec_contract_check.db"
    conn = open_db(db_path)
    try:
        apply_migrations(conn, backup=False)
    finally:
        conn.close()
    return db_path


def check_spec(spec_path: Path, db_path: Path) -> list[str]:
    """Return the drift lines between the spec and the database."""
    expected = parse_spec(spec_path)
    if expected.is_empty():
        return [f"  the spec declares no DDL: {spec_path}"]
    actual = read_db(db_path)
    return diff(expected, actual)


def check_entrypoint() -> list[str]:
    """`pyproject.toml` declares the entrypoint and the base dependencies."""
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return ["  pyproject.toml is missing"]
    text = pyproject.read_text(encoding="utf-8")
    problems: list[str] = []
    for needle, label in (
        ('hifa = "hifa.cli.main:app"', "entrypoint hifa = hifa.cli.main:app"),
        ('requires-python = ">=3.11"', "requires-python >= 3.11"),
        ('"typer', "base dependency typer"),
        ('"pydantic', "base dependency pydantic"),
        ('"markdown-it-py', "base dependency markdown-it-py"),
        ('"python-ulid', "base dependency python-ulid"),
    ):
        if needle not in text:
            problems.append(f"  missing in pyproject.toml: {label}")
    for bad in ("fastapi", "jinja2"):
        base = text.split("[project.optional-dependencies]")[0]
        if re.search(rf'^\s*"{bad}', base, re.MULTILINE):
            problems.append(f"  {bad} cannot be a base dependency ")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the DDL of the specs against the real schema of a SQLite database."
    )
    parser.add_argument("--spec", action="append", default=[], help="spec with DDL (repeatable)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite database to compare")
    parser.add_argument("--all", action="store_true", help="run every available check")
    parser.add_argument("--quiet", action="store_true", help="only report the outcome")
    args = parser.parse_args(argv)

    specs = [Path(p) for p in args.spec]
    if args.all and not specs:
        specs = [REPO_ROOT / "specs" / "010-data-model.md"]

    failures = 0
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    db_path = args.db

    try:
        if db_path is None and specs:
            tmp_dir = tempfile.TemporaryDirectory(prefix="hifa-contract-")
            db_path = migrate_temp_db(Path(tmp_dir.name))

        for spec in specs:
            if not spec.exists():
                print(f"FAIL: spec does not exist: {spec}")
                failures += 1
                continue
            if db_path is None:
                print(f"SKIP: {spec.name} (no database: use --db or a repo with a migrator)")
                continue
            problems = check_spec(spec, db_path)
            if problems:
                failures += 1
                print(f"FAIL: drift between {spec.name} and {db_path}")
                for line in problems:
                    print(line)
            elif not args.quiet:
                print(f"OK: {spec.name} matches {db_path}")

        if args.all:
            problems = check_entrypoint()
            if problems:
                failures += 1
                print("FAIL: pyproject.toml does not declare the entrypoint and base deps")
                for line in problems:
                    print(line)
            elif not args.quiet:
                print("OK: pyproject.toml (entrypoint + base deps)")
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()

    if not specs and not args.all:
        parser.error("pass --spec, --db or --all")
    if failures:
        print(f"\n{failures} check(s) with drift")
        return 1
    if not args.quiet:
        print("\nno drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
