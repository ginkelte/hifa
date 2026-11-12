"""SQLite connection factory.

PRAGMAs are **per connection**: no connection in the system is opened without going
through here. No `.sql` in `migrations/` repeats a PRAGMA (journal_mode cannot be changed
inside a transaction).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final
from urllib.parse import quote

from hifa.domain.errors import CorruptDatabase, HifaError

PRAGMAS: Final[tuple[tuple[str, str], ...]] = (
    ("journal_mode", "WAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
    ("synchronous", "NORMAL"),
    ("temp_store", "MEMORY"),
    ("cache_size", "-8000"),  # 8 MiB of page cache per connection
)

SQLITE_MIN_VERSION: Final[tuple[int, int, int]] = (3, 35, 0)  # RETURNING


def _is_corrupt(exc: sqlite3.DatabaseError) -> bool:
    """`SQLITE_NOTADB` / `SQLITE_CORRUPT` are translated to `CorruptDatabase`."""
    text = str(exc).lower()
    return "not a database" in text or "malformed" in text


def open_db(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the database and apply EVERY PRAGMA on this connection.

    `readonly=True` opens through the URI `file:<path>?mode=ro` and never writes. A missing
    path in read mode raises `sqlite3.OperationalError('unable to open database file')`,
    which the caller translates to `NotFound(resource='database')`.
    """
    target = Path(path).expanduser()
    if sqlite3.sqlite_version_info < SQLITE_MIN_VERSION:
        raise HifaError(
            f"SQLite {sqlite3.sqlite_version} is older than 3.35 (no RETURNING)",
            code="sqlite_too_old",
            hint="use a Python whose SQLite is >= 3.35",
        )

    if readonly:
        uri = f"file:{quote(str(target.resolve()))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(target), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    try:
        for name, value in PRAGMAS:
            try:
                conn.execute(f"PRAGMA {name} = {value}")
            except sqlite3.OperationalError:
                if not readonly:
                    raise
                # Under `mode=ro` write PRAGMAs do not apply; the rest is already pinned.
    except sqlite3.DatabaseError as exc:
        conn.close()
        if _is_corrupt(exc):
            raise CorruptDatabase(
                f"not a valid SQLite database file: {target}",
                hint="hifa doctor",
                details={"path": str(target)},
            ) from exc
        raise
    return conn


#: Canonical name; `connect` is the historical alias.
connect = open_db


def is_migrated(conn: sqlite3.Connection, *, expected: int) -> bool:
    """`PRAGMA user_version == expected` and the `issue` table exists."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version != expected:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'issue'"
    ).fetchone()
    return row is not None


class Database:
    """Connection factory for one path. Keeps no pool and no open connection."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        return open_db(self._path, readonly=readonly)

    def ro_connection(self) -> sqlite3.Connection:
        return open_db(self._path, readonly=True)


__all__ = ["PRAGMAS", "Database", "connect", "is_migrated", "open_db"]
