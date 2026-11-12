"""Forward-only migrator.

Public contract: `open_db`, `schema_version`, `apply_migrations`, `MAX_VERSION`,
`MigrationResult`. One file = one version = one transaction; `PRAGMA user_version` is the
cursor and `schema_migration` the auditable ledger with sha256 checksums.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Final

from hifa.adapters.sqlite.connection import open_db
from hifa.domain.errors import (
    BackupFailed,
    CorruptDatabase,
    Fts5Unavailable,
    HifaError,
    MigrationChecksumMismatch,
    MigrationFailed,
    SchemaTooNew,
)

MIGRATIONS_DIR: Final[str] = "migrations"
MAX_VERSION: Final[int] = 3  # 001_init, 002_schema_migration, 003_team_previous_key

MIGRATION_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class MigrationResult:
    """Result of one `apply_migrations` run (it is the `--json` of `hifa db migrate`)."""

    applied: list[int]  # versions applied in this run (empty = already up to date)
    from_version: int
    to_version: int
    backup_path: str | None  # None when there was nothing to apply


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    path: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def _migrations_path(migrations_dir: Path | None) -> Path:
    if migrations_dir is not None:
        return Path(migrations_dir)
    # Package resource: the installed build does not depend on the repo.
    return Path(str(resources.files("hifa.adapters.sqlite").joinpath(MIGRATIONS_DIR)))


def discover(migrations_dir: Path | None = None) -> list[_Migration]:
    """Discover the `NNN_*.sql` files ordered by version; an invalid name is `MigrationFailed`."""
    directory = _migrations_path(migrations_dir)
    if not directory.is_dir():
        raise MigrationFailed(
            f"migrations directory does not exist: {directory}",
            details={"directory": str(directory)},
        )

    found: dict[int, _Migration] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        match = MIGRATION_RE.match(path.name)
        if match is None:
            raise MigrationFailed(
                f"invalid migration name: {path.name}",
                hint="the pattern is NNN_name.sql (lowercase letters and _ only)",
                details={"file": path.name},
            )
        version = int(match.group("version"))
        if version in found:
            raise MigrationFailed(
                f"duplicate migration version: {version}",
                details={"version": version},
            )
        found[version] = _Migration(version=version, name=path.name, path=path)

    migrations = [found[v] for v in sorted(found)]
    expected = list(range(1, len(migrations) + 1))
    if [m.version for m in migrations] != expected:
        raise MigrationFailed(
            "the migration sequence has gaps",
            hint="versions must be 001, 002, ... with no gaps",
            details={"versions": [m.version for m in migrations]},
        )
    return migrations


def schema_version(conn: sqlite3.Connection) -> int:
    """`PRAGMA user_version`; `0` on a virgin database. Does not read `schema_migration`."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    return int(version)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _sqlite_version(conn: sqlite3.Connection) -> tuple[int, ...]:
    row = conn.execute("SELECT sqlite_version()").fetchone()
    return tuple(int(part) for part in str(row[0]).split("."))


def _database_file(conn: sqlite3.Connection) -> Path | None:
    for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
        if name == "main" and file:
            return Path(file)
    return None


def _statements(sql: str) -> list[str]:
    """Split a script into complete statements (honours the `BEGIN ... END` of triggers)."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if buffer.strip() and sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip() and not buffer.strip().startswith("--"):
        statements.append(buffer.strip())
    return statements


def _ensure_fts5(conn: sqlite3.Connection) -> None:
    options = {row[0] for row in conn.execute("PRAGMA compile_options")}
    if "ENABLE_FTS5" not in options:
        raise Fts5Unavailable(
            "SQLite without FTS5: the issue_fts search index cannot be created",
            hint="use a Python build whose SQLite is compiled with ENABLE_FTS5",
        )


def backup_database(conn: sqlite3.Connection, *, version: int) -> str:
    """`VACUUM INTO` backup of the current database; `BackupFailed` if it cannot be written."""
    source = _database_file(conn)
    if source is None:
        raise BackupFailed(
            "could not resolve the database file to back up",
            details={"version": version},
        )
    destination = Path(f"{source}.bak-{version}")
    try:
        conn.execute("VACUUM INTO ?", (str(destination),))
    except sqlite3.Error as exc:
        raise BackupFailed(
            f"could not write the backup: {destination}",
            hint="check disk space and directory permissions",
            details={"path": str(destination), "version": version},
        ) from exc
    if not destination.exists() or destination.stat().st_size == 0:
        raise BackupFailed(
            f"the backup came out empty: {destination}",
            details={"path": str(destination)},
        )
    return str(destination)


def _applied_checksums(conn: sqlite3.Connection) -> dict[int, str]:
    if not _table_exists(conn, "schema_migration"):
        return {}
    rows = conn.execute("SELECT version, checksum FROM schema_migration").fetchall()
    return {int(version): str(checksum) for version, checksum in rows}


def _apply_one(
    conn: sqlite3.Connection,
    migration: _Migration,
    *,
    backfill: list[_Migration],
) -> None:
    """Apply one migration and update the ledger in the SAME transaction (all or nothing)."""
    applied_at = _utc_now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _statements(migration.path.read_text(encoding="utf-8")):
            conn.execute(statement)
        if _table_exists(conn, "schema_migration"):
            # When the ledger is created, the already-applied versions are recorded.
            for done in backfill:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migration"
                    " (version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
                    (done.version, done.name, applied_at, done.checksum),
                )
        conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        conn.execute("COMMIT")
    except HifaError:
        conn.execute("ROLLBACK")
        raise
    except (sqlite3.Error, UnicodeDecodeError) as exc:
        conn.execute("ROLLBACK")
        if isinstance(exc, sqlite3.DatabaseError) and (
            "not a database" in str(exc).lower() or "malformed" in str(exc).lower()
        ):
            raise CorruptDatabase(f"corrupt database while applying {migration.name}") from exc
        raise MigrationFailed(
            f"migration {migration.name} failed",
            hint="check the migration DDL and try again",
            details={"file": migration.name, "statement": str(exc)},
        ) from exc


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    migrations_dir: Path | None = None,
    backup: bool = True,
) -> MigrationResult:
    """Apply the pending versions in order; idempotent."""
    current = schema_version(conn)
    migrations = discover(migrations_dir)

    if current > MAX_VERSION:
        raise SchemaTooNew(
            f"the database is at version {current} while this binary reaches {MAX_VERSION}",
            hint="upgrade hifa",
            details={"user_version": current, "max_version": MAX_VERSION},
        )

    # An already applied migration whose `.sql` changed aborts the whole run,
    # even when there is nothing pending (AC "changed checksum of an applied migration").
    ledger = _applied_checksums(conn)
    for migration in migrations:
        recorded = ledger.get(migration.version)
        if recorded is None:
            continue
        actual = migration.checksum
        if recorded != actual:
            raise MigrationChecksumMismatch(
                f"migration {migration.name} changed after being applied",
                hint="an applied migration is never rewritten",
                details={
                    "file": migration.name,
                    "version": migration.version,
                    "expected": recorded,
                    "actual": actual,
                },
            )

    plan = [m for m in migrations if m.version > current]
    if not plan:
        return MigrationResult([], current, current, None)

    _ensure_fts5(conn)

    # Back up before touching anything; a virgin database has nothing to back up.
    backup_path: str | None = None
    if backup and current > 0:
        backup_path = backup_database(conn, version=current)

    applied: list[int] = []
    for migration in plan:
        if migration.version in ledger:
            continue  # already applied: it is not re-executed
        backfill = [m for m in migrations if m.version <= migration.version]
        _apply_one(conn, migration, backfill=backfill)
        applied.append(migration.version)
        ledger = _applied_checksums(conn)

    return MigrationResult(
        applied=applied,
        from_version=current,
        to_version=schema_version(conn),
        backup_path=backup_path,
    )


__all__ = [
    "MAX_VERSION",
    "MIGRATIONS_DIR",
    "MigrationResult",
    "apply_migrations",
    "backup_database",
    "discover",
    "open_db",
    "schema_version",
]
