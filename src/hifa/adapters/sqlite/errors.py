"""Translation of `sqlite3.Error` into domain errors (single function).

It lives in `connection.py` because it is the adapter exception boundary:
no `sqlite3.Error` crosses into `app/`.
"""

from __future__ import annotations

import logging
import sqlite3

from hifa.domain.errors import Conflict, CorruptDatabase, HifaError, NotFound, Validation

logger = logging.getLogger(__name__)

_NOT_MIGRATED_MARKERS = ("no such table", "no such view")
_CORRUPT_MARKERS = ("file is not a database", "database disk image is malformed")
_BUSY_MARKERS = ("database is locked", "database table is locked", "database is busy")
_READONLY_MARKERS = ("attempt to write a readonly database", "readonly database")


def _integrity(exc: sqlite3.IntegrityError) -> HifaError:
    message = str(exc)
    low = message.lower()
    column = message.split(":", 1)[1].strip() if ":" in message else ""
    fields = [part.strip() for part in column.split(",") if part.strip()]

    if "unique constraint failed" in low:
        if "issue.identifier" in column:
            return Conflict(
                "identifier already exists", rule="UNIQUE_IDENTIFIER", field="identifier"
            )
        if "issue.team_id" in column and "issue.number" in column:
            return Conflict("issue number already used", rule="DUPLICATE_NUMBER", field="number")
        if column.startswith("issue_relation."):
            return Conflict("relation already exists", rule="RELATION_DUPLICATE")
        if column.startswith("label."):
            return Conflict("label already exists", rule="DUPLICATE_LABEL", field="name")
        last = fields[-1].split(".")[-1] if fields else None
        return Conflict(
            "duplicate value", rule="UNIQUE", field=last, details={"constraint": column}
        )
    if "foreign key constraint failed" in low:
        return Validation("referenced row does not exist", rule="FOREIGN_KEY")
    if "check constraint failed" in low:
        return Validation("check constraint failed", rule="CHECK_CONSTRAINT", field=column or None)
    if "not null constraint failed" in low:
        field = column.split(".")[-1] if column else None
        return Validation("missing required field", rule="NOT_NULL", field=field)
    return Validation("constraint violated", rule="CONSTRAINT", details={"constraint": column})


def translate_sqlite_error(exc: sqlite3.Error) -> HifaError:
    """Convert any `sqlite3.Error` into the matching `HifaError`."""
    if isinstance(exc, HifaError):  # pragma: no cover - defensive
        return exc

    message = str(exc)
    low = message.lower()
    logger.debug("translated sqlite error: %s", message)

    if isinstance(exc, sqlite3.IntegrityError):
        return _integrity(exc)

    if isinstance(exc, sqlite3.OperationalError):
        if any(marker in low for marker in _BUSY_MARKERS):
            return Conflict("database is busy", rule="DB_BUSY", code="DB_BUSY")
        if any(marker in low for marker in _READONLY_MARKERS):
            return Conflict("database is read-only", rule="DB_READONLY", code="DB_READONLY")
        if any(marker in low for marker in _NOT_MIGRATED_MARKERS):
            return HifaError(
                "database is not migrated", code="DB_NOT_MIGRATED", hint="hifa db migrate"
            )
        if "unable to open database file" in low:
            return NotFound("database not found", resource="database", key=message)
        if any(marker in low for marker in _CORRUPT_MARKERS):
            return CorruptDatabase("database is corrupt", code="DB_CORRUPT")

    if isinstance(exc, sqlite3.DatabaseError) and any(marker in low for marker in _CORRUPT_MARKERS):
        return CorruptDatabase("database is corrupt", code="DB_CORRUPT")

    return HifaError("sqlite error", code="DB_ERROR", details={"sqlite": type(exc).__name__})


__all__ = ["translate_sqlite_error"]
