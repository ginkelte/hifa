"""`domain.errors` — hierarchy, attributes, `to_dict()` and the D-014 mapping."""

from __future__ import annotations

import pytest

from hifa.domain.errors import (
    EXIT_CONFLICT,
    EXIT_INTERNAL,
    EXIT_INVALID_TRANSITION,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    Conflict,
    HifaError,
    InvalidTransition,
    NotFound,
    Validation,
)


def test_full_hierarchy() -> None:
    for cls in (NotFound, Conflict, Validation, InvalidTransition):
        assert issubclass(cls, HifaError)
    assert issubclass(HifaError, Exception)


def test_codes_are_stable() -> None:
    assert HifaError.code == "HIFA_ERROR"
    assert NotFound.code == "NOT_FOUND"
    assert Conflict.code == "CONFLICT"
    assert Validation.code == "VALIDATION"
    assert InvalidTransition.code == "INVALID_TRANSITION"


def test_exit_codes_from_d014() -> None:
    assert Validation.exit_code == EXIT_VALIDATION == 2
    assert NotFound.exit_code == EXIT_NOT_FOUND == 3
    assert Conflict.exit_code == EXIT_CONFLICT == 4
    assert InvalidTransition.exit_code == EXIT_INVALID_TRANSITION == 5
    assert HifaError.exit_code == EXIT_INTERNAL == 6


def test_validation_exposes_rule_field_value_and_details() -> None:
    err = Validation("title must not be empty", rule="TITLE_EMPTY", field="title", value="")
    assert err.rule == "TITLE_EMPTY"
    assert err.field == "title"
    assert err.value == ""
    assert err.message == "title must not be empty"
    assert err.details == {"rule": "TITLE_EMPTY", "field": "title", "value": ""}


def test_explicit_details_do_not_override_derived_ones() -> None:
    err = Validation(
        rule="CLOSE_WITH_OPEN_CHILDREN",
        field="state_id",
        details={"rule": "OTRA", "open_children": 2},
    )
    assert err.details["rule"] == "CLOSE_WITH_OPEN_CHILDREN"
    assert err.details["open_children"] == 2


def test_details_is_a_copy() -> None:
    err = Validation(rule="TITLE_EMPTY")
    err.details["rule"] = "MUTADO"
    assert err.rule == "TITLE_EMPTY"
    assert err.to_dict()["error"]["details"]["rule"] == "TITLE_EMPTY"


def test_to_dict_payload_structure() -> None:
    payload = Validation(rule="TITLE_EMPTY", field="title", value="").to_dict()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == "VALIDATION"
    assert payload["error"]["message"]
    assert payload["error"]["details"]["rule"] == "TITLE_EMPTY"


def test_code_override_for_the_boundary() -> None:
    err = Validation(rule="CLOSE_WITH_OPEN_CHILDREN", code="CLOSE_WITH_OPEN_CHILDREN")
    assert err.code == "CLOSE_WITH_OPEN_CHILDREN"
    assert err.to_dict()["error"]["code"] == "CLOSE_WITH_OPEN_CHILDREN"


def test_default_message_when_none_is_passed() -> None:
    assert HifaError().message == "error"
    assert HifaError().code == "HIFA_ERROR"
    assert str(HifaError()) == "error"


def test_not_found_derives_message_and_details() -> None:
    err = NotFound("no existe", resource="issue", key="ENG-404")
    assert err.resource == "issue"
    assert err.key == "ENG-404"
    assert err.details == {"resource": "issue", "key": "ENG-404"}
    assert err.message == "no existe"
    assert NotFound(resource="team", key="ENG").message == "team not found: ENG"


def test_conflict_exposes_rule_field_value() -> None:
    err = Conflict(rule="RELATION_DUPLICATE", field="related_issue_id", value="ENG-2")
    assert (err.rule, err.field, err.value) == ("RELATION_DUPLICATE", "related_issue_id", "ENG-2")
    assert err.code == "CONFLICT"


def test_invalid_transition_exposes_current_target_allowed() -> None:
    err = InvalidTransition(current="started", target="done", allowed=("completed", "canceled"))
    assert err.current == "started"
    assert err.target == "done"
    assert err.allowed == ("completed", "canceled")
    assert err.details["allowed"] == ["completed", "canceled"]


def test_attributes_are_read_only() -> None:
    err = Validation(rule="TITLE_EMPTY")
    with pytest.raises(AttributeError):
        err.rule = "OTRA"  # type: ignore[misc]


def test_kwargs_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        Validation("msg", "TITLE_EMPTY")  # type: ignore[misc]
    with pytest.raises(TypeError):
        NotFound("msg", "issue", "ENG-1")  # type: ignore[misc]


def test_repr_includes_code_and_message() -> None:
    assert "VALIDATION" in repr(Validation(rule="TITLE_EMPTY"))


def test_migrations_keep_their_lowercase_codes() -> None:
    """SPEC-010 §6: migration codes are not the SPEC-020 ones."""
    from hifa.domain.errors import MigrationChecksumMismatch, SchemaTooNew

    assert MigrationChecksumMismatch("x").code == "migration_checksum_mismatch"
    too_new = SchemaTooNew("x")
    assert too_new.code == "schema_too_new"
    assert too_new.exit_code == 4


def test_exit_code_and_hint_can_be_overridden() -> None:
    """The boundary can force another exit code without changing the class (A7)."""
    err = HifaError("boom", code="DB_ERROR", exit_code=6, hint="check the file")
    assert err.code == "DB_ERROR"
    assert err.exit_code == 6
    assert err.hint == "check the file"
