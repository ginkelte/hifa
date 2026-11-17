"""The one builder that turns a refusal into the line the reader sees.

A refused write puts exactly one sentence in front of the reader, and the sentence names the
issue the route resolved — never the value the error happens to carry. That distinction is the
whole point of these tests: the three rules that fill `value` fill it with three different kinds
of thing, so a sentence built from it would name an internal id or a workflow-state type where
the reader expects the identifier they typed.

`None` is not an error here. It is the one refusal that no use case produces: a form that asked
for confirmation and did not get it.
"""

from __future__ import annotations

import pytest

from hifa.domain.errors import InvalidTransition, Validation
from hifa.web.app import refusal_sentence

IDENTIFIER = "ENG-13"
ULID = "01J8ZQ7K3M4N5P6Q7R8S9T0V1W"


def test_an_archived_issue_is_named_by_the_identifier() -> None:
    """The transition refusal carries the identifier in `value`; the sentence still uses ours."""
    refusal = InvalidTransition(
        "issue is archived",
        rule="ISSUE_ARCHIVED",
        field="state",
        value=IDENTIFIER,
    )

    assert (
        refusal_sentence(refusal, verb="edit", identifier=IDENTIFIER)
        == "ENG-13 is archived; restore it first"
    )


def test_a_trashed_issue_is_named_by_the_identifier_and_not_the_internal_id() -> None:
    """`ISSUE_TRASHED` carries the internal ULID, so reading `value` names the wrong subject."""
    refusal = Validation(
        "issue is in the trash",
        rule="ISSUE_TRASHED",
        field="trashed",
        value=ULID,
    )

    sentence = refusal_sentence(refusal, verb="edit", identifier=IDENTIFIER)

    assert sentence == "ENG-13 is in the trash; restore it first"
    assert ULID not in sentence


@pytest.mark.parametrize("verb", ["archive", "move"])
def test_closing_with_open_children_counts_them_under_the_route_s_verb(verb: str) -> None:
    """Two routes close a parent, and each says its own word; the count comes from the details."""
    refusal = Validation(
        "cannot close an issue with open sub-issues",
        rule="CLOSE_WITH_OPEN_CHILDREN",
        field="state",
        details={"open_children": 2},
    )

    assert (
        refusal_sentence(refusal, verb=verb, identifier=IDENTIFIER)
        == f"cannot {verb} ENG-13: it has 2 open sub-issues"
    )


def test_a_verb_outside_the_closing_set_borrows_no_sentence() -> None:
    """A write that did not close a parent must not claim it did: it falls back to the domain."""
    refusal = Validation(
        "cannot close an issue with open sub-issues",
        rule="CLOSE_WITH_OPEN_CHILDREN",
        field="title",
        details={"open_children": 2},
    )

    assert (
        refusal_sentence(refusal, verb="edit", identifier=IDENTIFIER)
        == "invalid value: cannot close an issue with open sub-issues"
    )


def test_a_missing_confirmation_asks_for_the_form_s_own_verb() -> None:
    """No use case produced this one, so there is no error to read a rule from."""
    assert (
        refusal_sentence(None, verb="archive", identifier=IDENTIFIER)
        == "confirm the archive of ENG-13"
    )


def test_a_rule_outside_the_catalogue_quotes_the_domain() -> None:
    """The fallback is the only sentence of the edge that repeats what the core said."""
    refusal = Validation("estimate must be a number", rule="INVALID_ESTIMATE", field="estimate")

    assert (
        refusal_sentence(refusal, verb="edit", identifier=IDENTIFIER)
        == "invalid value: estimate must be a number"
    )
