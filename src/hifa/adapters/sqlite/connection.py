"""SQLite connection factory.

PRAGMAs are **per connection**: no connection in the system is opened without going
through here. No `.sql` in `migrations/` repeats a PRAGMA (journal_mode cannot be changed
inside a transaction).
"""

from __future__ import annotations

import sqlite3
import threading
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


def data_version(conn: sqlite3.Connection) -> int:
    """`PRAGMA data_version` of this connection: a counter of the *other* writers' commits.

    It reads no table, locks no row and opens no transaction, so between reads the connection
    holds no read lock. The number is opaque: compare it for equality, never order it, never do
    arithmetic on it and never show it to a person. A connection that cannot answer raises
    `HifaError(code="revision_unavailable")`.
    """
    try:
        row = conn.execute("PRAGMA data_version").fetchone()
    except sqlite3.Error as exc:
        raise HifaError(
            "the revision cannot be read",
            code="revision_unavailable",
            hint="restart the dashboard",
        ) from exc
    if row is None:  # pragma: no cover - defensive: the pragma always answers one row
        raise HifaError("the revision cannot be read", code="revision_unavailable")
    return int(row[0])


class RevisionWatch:
    """The one read connection that outlives a request, and the number it reports.

    `data_version` counts the commits of *other* connections to the file this connection
    opened, so a connection created for the request that reads it answers its own baseline
    forever and would never report a change. This one is opened once, lazily, read in
    autocommit, and closed by whoever owns the process. `read()` returns `None` instead of
    raising: a probe that cannot answer is a skipped tick, not a failed page.
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def read(self) -> int | None:
        """The current revision, or `None` when this connection cannot answer."""
        with self._lock:
            try:
                if self._connection is None:
                    self._connection = self._database.ro_connection()
                return data_version(self._connection)
            except (sqlite3.Error, HifaError):
                return None

    def close(self) -> None:
        """Close the connection. Idempotent, and a later `read()` opens a new one."""
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()


__all__ = [
    "PRAGMAS",
    "Database",
    "RevisionWatch",
    "connect",
    "data_version",
    "is_migrated",
    "open_db",
]
