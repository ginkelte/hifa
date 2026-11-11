"""Format and parsing of `identifier` (`ENG-123`) and validation of `team.key` (SPEC-020 §2.3).

Pure: `re` and stdlib, no clock and no IO.
"""

from __future__ import annotations

import re

from hifa.domain.errors import Validation

KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
IDENTIFIER_PATTERN = re.compile(r"^(?P<key>[A-Z][A-Z0-9]{1,9})-(?P<number>[1-9][0-9]{0,8})$")

MAX_NUMBER = 999_999_999


def normalize_key(raw: str) -> str:
    """strip + upper; it does not validate. ' eng ' -> 'ENG'."""
    return raw.strip().upper()


def is_valid_key(key: str) -> bool:
    """True if `KEY_PATTERN.fullmatch(key)`. Does not normalize."""
    return KEY_PATTERN.fullmatch(key) is not None


def validate_key(key: str) -> str:
    """Return the normalized `key` when valid; otherwise `Validation(field='team.key')`."""
    normalized = normalize_key(key)
    if not is_valid_key(normalized):
        raise Validation(
            "team key must be 2-10 chars of A-Z and 0-9, starting with a letter",
            rule="KEY_INVALID",
            field="team.key",
            value=key,
            code="KEY_INVALID",
        )
    return normalized


def format(key: str, number: int) -> str:
    """`'ENG', 123` -> `'ENG-123'`. Validates the key and `1 <= number <= 999999999`."""
    normalized = validate_key(key)
    if isinstance(number, bool) or not isinstance(number, int):
        raise Validation(
            "issue number must be an integer",
            rule="IDENTIFIER_NUMBER_INVALID",
            field="identifier",
            value=number,
            code="IDENTIFIER_NUMBER_INVALID",
        )
    if number < 1 or number > MAX_NUMBER:
        raise Validation(
            "issue number must be between 1 and 999999999",
            rule="IDENTIFIER_NUMBER_INVALID",
            field="identifier",
            value=number,
            code="IDENTIFIER_NUMBER_INVALID",
        )
    return f"{normalized}-{number}"


def parse(identifier: str) -> tuple[str, int]:
    """`'ENG-123'` -> `('ENG', 123)`. Case-sensitive: `'eng-123'` -> `Validation`."""
    match = IDENTIFIER_PATTERN.fullmatch(identifier) if isinstance(identifier, str) else None
    if match is None:
        raise Validation(
            "identifier must look like ENG-123",
            rule="IDENTIFIER_FORMAT",
            field="identifier",
            value=identifier,
            code="IDENTIFIER_FORMAT",
        )
    return match.group("key"), int(match.group("number"))


__all__ = [
    "IDENTIFIER_PATTERN",
    "KEY_PATTERN",
    "MAX_NUMBER",
    "format",
    "is_valid_key",
    "normalize_key",
    "parse",
    "validate_key",
]
