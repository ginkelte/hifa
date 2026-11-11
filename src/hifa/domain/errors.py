"""Domain errors: `HifaError` and its subclasses (SPEC-020 §2.6, D-014).

`code` is a stable `str` in `SCREAMING_SNAKE_CASE` (or the lowercase code of the
migrations, SPEC-010 §6) that the edges use to map to exit code / HTTP (A7).
Pure module: stdlib only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

EXIT_VALIDATION: Final[int] = 2
EXIT_NOT_FOUND: Final[int] = 3
EXIT_CONFLICT: Final[int] = 4
EXIT_INVALID_TRANSITION: Final[int] = 5
EXIT_INTERNAL: Final[int] = 6


class HifaError(Exception):
    """Domain error with a stable `code`, a short `message` and serializable `details`."""

    code: str = "HIFA_ERROR"
    default_message: str = "error"
    exit_code: int = EXIT_INTERNAL

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        hint: str | None = None,
        field: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        self._message = self.default_message if message is None else message
        self._details: dict[str, Any] = dict(details or {})
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code
        self.hint = hint
        self._field = field
        super().__init__(self._message)

    @property
    def message(self) -> str:
        """Short message in technical English (R14)."""
        return self._message

    @property
    def details(self) -> dict[str, Any]:
        """Copy of the details (never the internal dict)."""
        return dict(self._details)

    @property
    def field(self) -> str | None:
        """Input field that caused the rejection, when applicable."""
        return self._field

    def to_dict(self) -> dict[str, Any]:
        """Error payload shared by CLI `--json` and the API (SPEC-020 §6)."""
        return {
            "error": {
                "code": self.code,
                "message": self._message,
                "details": dict(self._details),
            }
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self._message!r})"


class NotFound(HifaError):
    """Resource that does not exist by id or identifier. CLI exit 3, HTTP 404."""

    code = "NOT_FOUND"
    exit_code = EXIT_NOT_FOUND

    def __init__(
        self,
        message: str | None = None,
        *,
        resource: str,
        key: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._resource = resource
        self._key = key
        merged: dict[str, Any] = dict(details or {})
        merged["resource"] = resource
        merged["key"] = key
        super().__init__(
            f"{resource} not found: {key}" if message is None else message,
            details=merged,
        )

    @property
    def resource(self) -> str:
        return self._resource

    @property
    def key(self) -> str:
        return self._key


class Conflict(HifaError):
    """Uniqueness violation or logical duplicate. CLI exit 4, HTTP 409."""

    code = "CONFLICT"
    exit_code = EXIT_CONFLICT

    def __init__(
        self,
        message: str | None = None,
        *,
        rule: str,
        field: str | None = None,
        value: Any | None = None,
        details: Mapping[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self._rule = rule
        self._field = field
        self._value = value
        merged: dict[str, Any] = dict(details or {})
        merged["rule"] = rule
        if field is not None:
            merged["field"] = field
        if value is not None:
            merged["value"] = value
        super().__init__(
            f"conflict: {rule}" if message is None else message,
            code=code,
            details=merged,
            field=field,
        )

    @property
    def rule(self) -> str:
        return self._rule

    @property
    def value(self) -> Any | None:
        return self._value


class Validation(HifaError):
    """Semantically invalid input. CLI exit 2, HTTP 422."""

    code = "VALIDATION"
    exit_code = EXIT_VALIDATION

    def __init__(
        self,
        message: str | None = None,
        *,
        rule: str,
        field: str | None = None,
        value: Any | None = None,
        details: Mapping[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self._rule = rule
        self._field = field
        self._value = value
        merged: dict[str, Any] = dict(details or {})
        merged["rule"] = rule
        if field is not None:
            merged["field"] = field
        if value is not None:
            merged["value"] = value
        super().__init__(
            f"invalid input: {rule}" if message is None else message,
            code=code,
            details=merged,
            field=field,
        )

    @property
    def rule(self) -> str:
        return self._rule

    @property
    def value(self) -> Any | None:
        return self._value


class InvalidTransition(HifaError):
    """Invalid state transition. **Reserved**: no rule in 020/040 raises it.

    It exists so an edge with its own constraints can map to exit 5 of D-014.
    """

    code = "INVALID_TRANSITION"
    exit_code = EXIT_INVALID_TRANSITION

    def __init__(
        self,
        message: str | None = None,
        *,
        current: str,
        target: str,
        allowed: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._current = current
        self._target = target
        self._allowed = tuple(allowed)
        merged: dict[str, Any] = dict(details or {})
        merged["current"] = current
        merged["target"] = target
        merged["allowed"] = list(self._allowed)
        super().__init__(
            f"invalid transition: {current} -> {target}" if message is None else message,
            details=merged,
        )

    @property
    def current(self) -> str:
        return self._current

    @property
    def target(self) -> str:
        return self._target

    @property
    def allowed(self) -> tuple[str, ...]:
        return self._allowed


# --- Migrations and persistence (SPEC-010 §6, SPEC-030 §6) -----------------------------
# Lowercase codes, fixed by SPEC-010 §6; they are not part of SPEC-020 §2.


class MigrationChecksumMismatch(HifaError):
    """An already applied `.sql` changed its checksum: applied migrations are never rewritten."""

    code = "migration_checksum_mismatch"
    default_message = "migration checksum mismatch"


class SchemaTooNew(HifaError):
    """`user_version` greater than `MAX_VERSION`: the database comes from a newer binary."""

    code = "schema_too_new"
    default_message = "database schema is newer than this binary"
    exit_code = EXIT_CONFLICT


class BackupFailed(HifaError):
    """`VACUUM INTO` could not write the backup: nothing is migrated (fail closed)."""

    code = "backup_failed"
    default_message = "backup failed"


class MigrationFailed(HifaError):
    """A migration's DDL failed; the transaction for that version is rolled back."""

    code = "migration_failed"
    default_message = "migration failed"


class CorruptDatabase(HifaError):
    """`SQLITE_NOTADB` / `SQLITE_CORRUPT` while opening or migrating."""

    code = "corrupt_database"
    default_message = "database is corrupt"


class Fts5Unavailable(HifaError):
    """`PRAGMA compile_options` without `ENABLE_FTS5`: SQLite built without search support."""

    code = "fts5_unavailable"
    default_message = "sqlite was built without fts5"


__all__ = [
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_INVALID_TRANSITION",
    "EXIT_NOT_FOUND",
    "EXIT_VALIDATION",
    "BackupFailed",
    "Conflict",
    "CorruptDatabase",
    "Fts5Unavailable",
    "HifaError",
    "InvalidTransition",
    "MigrationChecksumMismatch",
    "MigrationFailed",
    "NotFound",
    "SchemaTooNew",
    "Validation",
]
