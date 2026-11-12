"""`domain.identifier` — regex exactos, `format`/`parse` inversas."""

from __future__ import annotations

import pytest

from hifa.domain import identifier
from hifa.domain.errors import Validation


def test_patterns_are_the_spec_ones() -> None:
    assert identifier.KEY_PATTERN.pattern == r"^[A-Z][A-Z0-9]{1,9}$"
    assert (
        identifier.IDENTIFIER_PATTERN.pattern
        == r"^(?P<key>[A-Z][A-Z0-9]{1,9})-(?P<number>[1-9][0-9]{0,8})$"
    )


def test_normalize_key() -> None:
    assert identifier.normalize_key(" eng ") == "ENG"
    assert identifier.normalize_key("eng7") == "ENG7"


@pytest.mark.parametrize("key", ["ENG", "AB", "A1", "OPS1", "ABCDEFGHIJ", "X9Y8Z7W6V5"])
def test_validate_key_accepts_valid_keys(key: str) -> None:
    assert identifier.validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    ["A", "e", "ENG1ENG1ENG1", "EN-G", "EN G", "1NG", "ÉNG", "", "EN G!"],
)
def test_validate_key_rejects_invalid_keys(key: str) -> None:
    with pytest.raises(Validation) as excinfo:
        identifier.validate_key(key)
    assert excinfo.value.field == "team.key"
    assert excinfo.value.rule == "KEY_INVALID"


def test_is_valid_key_does_not_normalize() -> None:
    assert identifier.is_valid_key("ENG") is True
    assert identifier.is_valid_key("eng") is False
    assert identifier.is_valid_key("ENG-1") is False


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "ENG-1"), (7, "ENG-7"), (123, "ENG-123"), (999_999_999, "ENG-999999999")],
)
def test_format_valid_numbers(number: int, expected: str) -> None:
    assert identifier.format("ENG", number) == expected


@pytest.mark.parametrize("number", [0, -1, 1_000_000_000])
def test_format_rejects_numbers_out_of_range(number: int) -> None:
    with pytest.raises(Validation) as excinfo:
        identifier.format("ENG", number)
    assert excinfo.value.rule == "IDENTIFIER_NUMBER_INVALID"
    assert excinfo.value.field == "identifier"


def test_format_rejects_invalid_key() -> None:
    with pytest.raises(Validation) as excinfo:
        identifier.format("e-1", 2)
    assert excinfo.value.field == "team.key"


def test_format_rejects_bool_as_number() -> None:
    with pytest.raises(Validation):
        identifier.format("ENG", True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ENG-1", ("ENG", 1)),
        ("ENG-123", ("ENG", 123)),
        ("AB-9", ("AB", 9)),
        ("ABCDEFGHIJ-999999999", ("ABCDEFGHIJ", 999_999_999)),
    ],
)
def test_parse_valid_strings(text: str, expected: tuple[str, int]) -> None:
    assert identifier.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "eng-123",
        "ENG-0123",
        "ENG--1",
        "ENG_123",
        "1ENG-2",
        "ENG-0",
        "ENG-",
        "-1",
        "",
        "E-1",
        "ABCDEFGHIJK-1",
        "ENG-1234567890",
        " ENG-1",
        "ENG-1 ",
        "ENG-1a",
    ],
)
def test_parse_rejects_non_canonical_forms(text: str) -> None:
    with pytest.raises(Validation) as excinfo:
        identifier.parse(text)
    assert excinfo.value.field == "identifier"
    assert excinfo.value.rule == "IDENTIFIER_FORMAT"


@pytest.mark.parametrize("key", ["ENG", "OPS", "AB", "A1B2C3D4E5"])
@pytest.mark.parametrize("number", [1, 2, 42, 1024, 999_999_999])
def test_format_and_parse_are_inverses(key: str, number: int) -> None:
    assert identifier.parse(identifier.format(key, number)) == (key, number)


def test_parse_returns_int_not_str() -> None:
    _, number = identifier.parse("ENG-42")
    assert isinstance(number, int)
