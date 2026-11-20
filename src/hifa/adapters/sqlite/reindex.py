"""Rebuild of the FTS index of one database.

`reindex(conn)` is the only place besides the migration that writes `issue_fts`: it recreates
the virtual table when it is gone, drops and recreates the three triggers that keep it
synchronized (they belong to `issue`, so dropping the table leaves them behind) and asks FTS5
to rebuild the whole index in one transaction. It opens nothing, probes nothing and validates
nothing: the caller hands it a connection to a database it has already identified as a hifa
one, and gets back the number of rows it indexed.
"""

from __future__ import annotations

import sqlite3

#: The eight statements between `BEGIN IMMEDIATE` and `COMMIT`, in this order.
REINDEX_STATEMENTS: tuple[str, ...] = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS issue_fts USING fts5(
      title, description, identifier UNINDEXED,
      content='issue', content_rowid='rowid',
      tokenize='unicode61 remove_diacritics 2'
    )
    """,
    "DROP TRIGGER IF EXISTS issue_fts_ai",
    "DROP TRIGGER IF EXISTS issue_fts_ad",
    "DROP TRIGGER IF EXISTS issue_fts_au",
    """
    CREATE TRIGGER issue_fts_ai AFTER INSERT ON issue BEGIN
      INSERT INTO issue_fts(rowid, title, description, identifier)
      VALUES (new.rowid, new.title, new.description, new.identifier);
    END
    """,
    """
    CREATE TRIGGER issue_fts_ad AFTER DELETE ON issue BEGIN
      INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
      VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
    END
    """,
    """
    CREATE TRIGGER issue_fts_au AFTER UPDATE OF title, description, identifier ON issue BEGIN
      INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
      VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
      INSERT INTO issue_fts(rowid, title, description, identifier)
      VALUES (new.rowid, new.title, new.description, new.identifier);
    END
    """,
    "INSERT INTO issue_fts(issue_fts) VALUES('rebuild')",
)


def reindex(conn: sqlite3.Connection) -> int:
    """Rebuilds the index and its triggers in one transaction; returns the count of `issue` rows.

    A failure rolls the whole sequence back, so the previous index (or its absence) survives
    instead of half of a new one; the count is every row of `issue`, trashed and archived
    included, because visibility is a predicate of the query and never of the index.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in REINDEX_STATEMENTS:
            conn.execute(statement)
        row = conn.execute("SELECT COUNT(*) FROM issue").fetchone()
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return int(row[0]) if row is not None else 0


__all__ = ["REINDEX_STATEMENTS", "reindex"]
