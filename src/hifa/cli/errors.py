"""Mapping of errors to CLI exit codes and messages.

The only place at the edge where an error becomes an exit code and text. No command
builds error messages on its own.

Map: `0` ok · `1` `doctor` only · `2` validation/config · `3` not found ·
`4` conflict · `5` transition · `6` internal. `130` interruption.
"""

from __future__ import annotations

import json

import typer

from hifa.cli import output
from hifa.config import ConfigError
from hifa.domain.errors import HifaError

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
_UNPREFIXED_CLI_CODES: frozenset[str] = frozenset(
    {"invalid_option", "database_not_found", "no_changes", "confirmation_required", "not_found"}
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
        if code and code != code.upper():
            message = f"{message} [{code}]"
        return message
    return f"internal error: {type(exc).__name__}: {exc}"


def error_field_for(exc: BaseException) -> str | None:
    return getattr(exc, "field", None)


def error_details_for(exc: BaseException) -> dict[str, object]:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        return dict(details)
    return {}


def error_payload(exc: BaseException) -> dict[str, object]:
    """`--json` error object."""
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
    """`hifa: <message> (field: <field>)` line — the prefix and the `field` are contractual."""
    message = error_message_for(exc)
    field = error_field_for(exc)
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
