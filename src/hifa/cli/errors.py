"""Mapping of errors to CLI exit codes and messages.

The only place at the edge where an error becomes an exit code and text. No command
builds error messages on its own.

Map: `0` ok · `1` `doctor` only · `2` validation/config · `3` not found ·
`4` conflict · `5` transition · `6` internal. `130` interruption.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import typer

from hifa.cli import output
from hifa.config import ConfigError
from hifa.domain.errors import (
    CorruptDatabase,
    DatabaseNotFound,
    Fts5Unavailable,
    HifaError,
    NotAHifaDatabase,
)

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_INVALID_TRANSITION = 5
EXIT_INTERNAL = 6
EXIT_INTERRUPTED = 130

#: Domain codes (`SCREAMING_SNAKE`) → error envelope code.
_JSON_CODES: dict[str, str] = {
    "VALIDATION": "validation_error",
    "NOT_FOUND": "not_found",
    "CONFLICT": "conflict",
    "INVALID_TRANSITION": "invalid_transition",
    "HIFA_ERROR": "internal_error",
    "READ_ONLY_UOW": "conflict",
    "DB_READONLY": "DB_READONLY",
    "DB_BUSY": "DB_BUSY",
    "KEY_INVALID": "validation_error",
    "TEAM_KEY_INVALID": "validation_error",
    "TEAM_NAME_INVALID": "validation_error",
    "TEAM_ICON_INVALID": "validation_error",
    "TEAM_COLOR_INVALID": "validation_error",
    "TEAM_TIMEZONE_INVALID": "validation_error",
    "TEAM_WITHOUT_STATES": "validation_error",
    "UNIQUE_TEAM_KEY": "conflict",
    "TEAM_ARCHIVED": "validation_error",
    "STATE_TYPE_INVALID": "validation_error",
    "STATE_NAME_EMPTY": "validation_error",
    "STATE_NAME_INVALID": "validation_error",
    "STATE_NAME_AMBIGUOUS": "validation_error",
    "STATE_NEIGHBOR_TEAM_MISMATCH": "validation_error",
    "STATE_POSITION_SELF": "validation_error",
    "STATE_POSITION_BODY_INVALID": "validation_error",
    "COLOR_FORMAT": "validation_error",
    "UNIQUE_STATE_NAME": "conflict",
    "STATE_DEFAULT_INVARIANT": "conflict",
}


#: CLI codes whose message is already the whole sentence: the code is not repeated on
#: `stderr` (it stays in the `--json` envelope and in the exit code).
#: Codes whose message already names what is missing, so no `(field: …)` suffix is added.
_NO_FIELD_SUFFIX_CODES: frozenset[str] = frozenset({"missing_context"})

#: The classes whose message *is* the sentence of the catalog: the code is not appended to it.
#: They are the verdicts of the database layer, where the code and the sentence say one thing.
_SELF_SPELLED_CLASSES: tuple[type[HifaError], ...] = (
    DatabaseNotFound,
    CorruptDatabase,
    NotAHifaDatabase,
    Fts5Unavailable,
)


_UNPREFIXED_CLI_CODES: frozenset[str] = frozenset(
    {
        "invalid_option",
        "database_not_found",
        "no_changes",
        "confirmation_required",
        "not_found",
        "missing_context",
    }
)


class CliError(Exception):
    """CLI-edge error (contradictory flags, missing context)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_option",
        field: str | None = None,
        exit_code: int = EXIT_VALIDATION,
    ) -> None:
        self.code = code
        self.message = message
        self.field = field
        self.exit_code = exit_code
        super().__init__(message)


def exit_code_for(exc: BaseException) -> int:
    """Translate the exception to its definitive exit code."""
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    if isinstance(exc, ConfigError):
        return EXIT_VALIDATION
    if isinstance(exc, CliError):
        return exc.exit_code
    if isinstance(exc, HifaError):
        return getattr(exc, "exit_code", EXIT_INTERNAL)
    return EXIT_INTERNAL


def error_code_for(exc: BaseException) -> str:
    """Stable code for the `--json` envelope.

    A domain code is either an envelope code already (`invalid_config`, `corrupt_database`) or
    the name of a rule (`TITLE_EMPTY`, `STATE_TEAM_MISMATCH`). A rule that the map does not
    know is not an envelope code: the exception class decides and the rule stays in `details`.
    """
    if isinstance(exc, (ConfigError, CliError)):
        return exc.code
    if not isinstance(exc, HifaError):
        return "internal_error"
    code = getattr(exc, "code", "")
    if code and code != code.upper():
        return code
    mapped = _JSON_CODES.get(code)
    if mapped is not None:
        return mapped
    class_code = getattr(type(exc), "code", "HIFA_ERROR")
    return _JSON_CODES.get(class_code, "internal_error")


def error_message_for(exc: BaseException) -> str:
    """Human message without the `hifa: ` prefix."""
    if isinstance(exc, (ConfigError, CliError)):
        message = exc.message
        # Config codes are part of the observable contract: they are shown. A CLI code
        # whose message already reads as a sentence (`invalid_option`, `database_not_found`)
        # is *not* prefixed — `hifa: database_not_found: database not found` said it twice.
        prefix_code = isinstance(exc, ConfigError) or exc.code not in _UNPREFIXED_CLI_CODES
        if prefix_code and not message.startswith(exc.code):
            message = f"{exc.code}: {message}"
        return message
    if isinstance(exc, HifaError):
        message = exc.message
        code = getattr(exc, "code", "")
        if code and code != code.upper() and not isinstance(exc, _SELF_SPELLED_CLASSES):
            message = f"{message} [{code}]"
        return message
    return f"internal error: {type(exc).__name__}: {exc}"


def error_field_for(exc: BaseException) -> str | None:
    return getattr(exc, "field", None)


def error_details_for(exc: BaseException) -> list[dict[str, object]] | None:
    """`details` of the envelope: the multi-error list, or `null`.

    The envelope gives `details` exactly two shapes and no third — `null`, or the list
    `[{"field": ..., "message": ...}]` that a refusal publishes when it reports **several**
    validation errors, with `field` repeating the first one. The mapping an exception *carries*
    is its accessor (`exc.details`): `rule`/`field`/`value` for a `Validation` or a
    `Conflict`, `resource`/`key` for a `NotFound`, `labels`/`teams` for an ambiguous name. No
    edge publishes that mapping — the rule travels in `message`, the 404's noun in `field`, the
    ambiguous spellings in `message` — so this renderer forwards a list and answers `null` for
    everything else. `LABEL_AMBIGUOUS` is one of those: its mapping is an accessor, and
    `error.details` is `null` while `message` names the spellings and the levels.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, (list, tuple)):
        return None
    errors: list[dict[str, object]] = []
    for item in details:
        if not isinstance(item, Mapping) or not {"field", "message"} <= set(item):
            return None
        errors.append({"field": item["field"], "message": item["message"]})
    return errors or None


def error_payload(exc: BaseException) -> dict[str, object]:
    """`--json` error object: the four keys of the envelope, none of them optional."""
    payload: dict[str, object] = {
        "code": error_code_for(exc),
        "message": error_message_for(exc),
        "field": error_field_for(exc),
        "details": error_details_for(exc),
    }
    hint = getattr(exc, "hint", None)
    if hint:
        payload["hint"] = hint
    return {"error": payload}


def format_human(exc: BaseException) -> str:
    """`hifa: <message> (field: <field>)` line — the prefix and the `field` are contractual.

    Two exceptions, both declared by the message catalog: a code whose message already reads
    as a sentence is not prefixed, and a message that names what is missing carries no field.
    """
    message = error_message_for(exc)
    code = getattr(exc, "code", "")
    field = None if code in _NO_FIELD_SUFFIX_CODES else error_field_for(exc)
    return f"hifa: {message}" + (f" (field: {field})" if field else "")


def emit_error(exc: BaseException) -> None:
    """Report the error in the current format: human `stderr` or JSON (never `stdout`)."""
    if output.options().json:
        typer.echo(json.dumps(error_payload(exc), indent=2, ensure_ascii=False), err=True)
    else:
        typer.echo(format_human(exc), err=True)


def fail(exc: BaseException) -> typer.Exit:
    """Emit the error and return the `typer.Exit` with the mapped exit code."""
    emit_error(exc)
    return typer.Exit(code=exit_code_for(exc))


__all__ = [
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_INTERRUPTED",
    "EXIT_INVALID_TRANSITION",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_VALIDATION",
    "CliError",
    "emit_error",
    "error_code_for",
    "error_payload",
    "exit_code_for",
    "fail",
    "format_human",
]
