"""The `--json` error envelope: the four keys and the two shapes of `details`.

SPEC-060 §6.3 declares `details` as `null` or a list of `{field, message}` and no third shape;
SPEC-050 §2.4.5 renders the same four keys. The mapping an exception carries is its accessor
(`exc.details`, SPEC-020 §6), which a domain consumer reads and no edge publishes — so a refusal
that carries `rule`/`field`/`value` (`LABEL_AMBIGUOUS` and its `labels`/`teams` included) answers
`details: null` and says everything in `message`. These tests drive the renderer directly: the
object it returns is what both edges publish.
"""

from __future__ import annotations

from typing import Any, cast

from hifa.cli.errors import error_details_for, error_payload, format_human
from hifa.domain.errors import Conflict, NotFound, Validation

FOUR_KEYS = ("code", "message", "field", "details")


def _envelope(exc: BaseException) -> dict[str, Any]:
    """The `error` object of the envelope, as the client parses it."""
    return cast(dict[str, Any], error_payload(exc)["error"])


class AggregatedValidation(Validation):
    """A refusal that reports several field errors, which is the one `details` a wire may carry.

    The tree has no producer of this shape today — the edge that aggregates is the API's request
    validation and `src/hifa/api/` holds no edge yet — so the stand-in subclasses the real class
    and changes only the accessor, which is the one thing the renderer reads.
    """

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("two fields are invalid", rule="MULTI_ERROR", field=errors[0]["field"])
        self._errors = list(errors)

    @property
    def details(self) -> Any:  # type: ignore[override]
        return [dict(item) for item in self._errors]


def test_a_single_refusal_publishes_four_keys_and_a_null_details() -> None:
    """A `Validation` carries `rule`/`field`/`value`; none of it reaches the envelope."""
    exc = Validation(
        "estimate is not part of the fibonacci scale",
        rule="ESTIMATE_NOT_IN_SCALE",
        field="estimate",
        value=99,
    )

    error = _envelope(exc)

    assert tuple(error) == FOUR_KEYS
    assert error["details"] is None
    assert error["code"] == "validation_error"
    assert error["field"] == "estimate"


def test_the_ambiguity_mapping_stays_an_accessor() -> None:
    """`labels`/`teams` are readable in process and absent from the envelope, which names them."""
    exc = Validation(
        "label Bug is ambiguous: 'Bug', 'bug' — it exists in ENG, OPS, the workspace",
        rule="LABEL_AMBIGUOUS",
        field="ref",
        value="Bug",
        details={"labels": ["Bug", "bug"], "teams": ["ENG", "OPS", "the workspace"]},
        code="LABEL_AMBIGUOUS",
    )

    error = _envelope(exc)

    assert tuple(error) == FOUR_KEYS
    assert error["details"] is None
    assert (error["code"], error["field"]) == ("validation_error", "ref")
    assert exc.details["teams"] == ["ENG", "OPS", "the workspace"]
    assert "Bug" in error["message"] and "OPS" in error["message"]


def test_a_conflict_and_a_404_publish_a_null_details_too() -> None:
    """The rule is the shape of the accessor, not the class of the refusal."""
    conflict = Conflict(
        "label bug already exists at this level as Bug",
        rule="UNIQUE_LABEL_NAME",
        field="name",
        value="bug",
        code="UNIQUE_LABEL_NAME",
    )
    missing = NotFound(resource="label", key="infra")

    assert _envelope(conflict)["details"] is None
    assert _envelope(missing)["details"] is None
    assert tuple(_envelope(missing)) == FOUR_KEYS


def test_several_field_errors_keep_their_list() -> None:
    """An aggregate is the shape `details` exists for; it is published, one object per field."""
    exc = AggregatedValidation(
        [
            {"field": "title", "message": "title cannot be blank", "rule": "TITLE_EMPTY"},
            {"field": "estimate", "message": "estimate is not part of the scale"},
        ]
    )

    assert error_details_for(exc) == [
        {"field": "title", "message": "title cannot be blank"},
        {"field": "estimate", "message": "estimate is not part of the scale"},
    ]
    assert _envelope(exc)["details"] == error_details_for(exc)


def test_a_list_that_is_not_the_aggregate_publishes_null() -> None:
    """A list of anything else is not the declared shape: the envelope gains no third one."""
    exc = AggregatedValidation([{"field": "title", "message": "blank"}])
    exc._errors = [{"rule": "TITLE_EMPTY"}]

    assert error_details_for(exc) is None


def test_the_human_line_is_unchanged_by_the_envelope_rule() -> None:
    """Without `--json` a refusal is one sentence and the `(field: …)` suffix of §2.4.5."""
    exc = Validation(
        "label not found: infra",
        rule="LABEL_NOT_FOUND",
        field="label",
        value="infra",
        code="LABEL_NOT_FOUND",
    )

    assert format_human(exc) == "hifa: label not found: infra (field: label)"
