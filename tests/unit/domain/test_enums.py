"""`domain.enums` — canonical values, labels, ranks and scales."""

from __future__ import annotations

import enum

import pytest

from hifa.domain import enums
from hifa.domain.errors import Validation


def test_priority_is_intenum_with_values_0_to_4() -> None:
    assert issubclass(enums.Priority, enum.IntEnum)
    assert [int(p) for p in enums.Priority] == [0, 1, 2, 3, 4]


def test_priority_labels_are_exact() -> None:
    assert [p.label for p in enums.Priority] == [
        "No priority",
        "Urgent",
        "High",
        "Medium",
        "Low",
    ]


def test_priority_ranks() -> None:
    assert [p.rank for p in enums.Priority] == [5, 1, 2, 3, 4]


def test_priority_from_label_case_sensitive() -> None:
    assert enums.Priority.from_label("High") is enums.Priority.HIGH
    assert enums.Priority.from_label("No priority") is enums.Priority.NO_PRIORITY
    for bad in ("high", "HIGH", "", "blocker"):
        with pytest.raises(ValueError, match="unknown priority label"):
            enums.Priority.from_label(bad)


def test_priority_serializes_to_int() -> None:
    assert enums.Priority.HIGH == 2
    assert isinstance(enums.Priority.HIGH.value, int)


def test_workflow_state_type_values() -> None:
    assert [s.value for s in enums.WorkflowStateType] == [
        "triage",
        "backlog",
        "unstarted",
        "started",
        "completed",
        "canceled",
    ]


@pytest.mark.parametrize(
    ("state_type", "open_", "terminal"),
    [
        (enums.WorkflowStateType.TRIAGE, True, False),
        (enums.WorkflowStateType.BACKLOG, True, False),
        (enums.WorkflowStateType.UNSTARTED, True, False),
        (enums.WorkflowStateType.STARTED, True, False),
        (enums.WorkflowStateType.COMPLETED, False, True),
        (enums.WorkflowStateType.CANCELED, False, True),
    ],
)
def test_workflow_state_is_open_and_is_terminal(
    state_type: enums.WorkflowStateType, open_: bool, terminal: bool
) -> None:
    assert state_type.is_open is open_
    assert state_type.is_terminal is terminal


def test_relation_type_values() -> None:
    assert [r.value for r in enums.RelationType] == ["blocks", "duplicate", "related", "similar"]


@pytest.mark.parametrize(
    ("relation", "symmetric", "inverse"),
    [
        (enums.RelationType.BLOCKS, False, "blocked_by"),
        (enums.RelationType.DUPLICATE, True, "duplicate"),
        (enums.RelationType.RELATED, True, "related"),
        (enums.RelationType.SIMILAR, True, "similar"),
    ],
)
def test_relation_symmetry_and_inverse(
    relation: enums.RelationType, symmetric: bool, inverse: str
) -> None:
    assert relation.is_symmetric is symmetric
    assert relation.inverse_name == inverse


#: The four facts every writer and every read of an edge depends on, one row per type.
RELATION_FACTS = (
    (enums.RelationType.BLOCKS, False, True, "blocks", "blocked_by"),
    (enums.RelationType.DUPLICATE, True, False, "duplicate", "duplicate"),
    (enums.RelationType.RELATED, True, False, "related", "related"),
    (enums.RelationType.SIMILAR, True, False, "similar", "similar"),
)


@pytest.mark.parametrize(("relation", "symmetric", "hard", "seen_out", "seen_in"), RELATION_FACTS)
def test_relation_hardness(
    relation: enums.RelationType, symmetric: bool, hard: bool, seen_out: str, seen_in: str
) -> None:
    assert relation.is_hard is hard


@pytest.mark.parametrize(("relation", "symmetric", "hard", "seen_out", "seen_in"), RELATION_FACTS)
def test_relation_name_as_seen_from_both_ends(
    relation: enums.RelationType, symmetric: bool, hard: bool, seen_out: str, seen_in: str
) -> None:
    assert relation.name_as_seen("out") == seen_out
    assert relation.name_as_seen("in") == seen_in


@pytest.mark.parametrize(
    ("relation", "source", "target", "stored"),
    [
        (enums.RelationType.BLOCKS, "01B", "01A", ("01B", "01A")),
        (enums.RelationType.BLOCKS, "01A", "01B", ("01A", "01B")),
        (enums.RelationType.RELATED, "01B", "01A", ("01A", "01B")),
        (enums.RelationType.RELATED, "01A", "01B", ("01A", "01B")),
        (enums.RelationType.SIMILAR, "01Z", "01A", ("01A", "01Z")),
        (enums.RelationType.DUPLICATE, "01B", "01A", ("01A", "01B")),
    ],
)
def test_relation_canonical_pair(
    relation: enums.RelationType, source: str, target: str, stored: tuple[str, str]
) -> None:
    assert relation.canonical_pair(source, target) == stored


@pytest.mark.parametrize(
    "relation",
    [enums.RelationType.DUPLICATE, enums.RelationType.RELATED, enums.RelationType.SIMILAR],
)
def test_relation_canonical_pair_is_the_same_from_either_end(relation: enums.RelationType) -> None:
    """The whole point of the canonical order: both spellings land on one storable pair."""
    assert relation.canonical_pair("01B", "01A") == relation.canonical_pair("01A", "01B")


@pytest.mark.parametrize(("raw", "parsed"), [(r.value, r) for r in enums.RelationType])
def test_relation_parse_accepts_the_stored_tokens_without_swapping(
    raw: str, parsed: enums.RelationType
) -> None:
    assert enums.RelationType.parse(raw) == (parsed, False)


def test_relation_parse_accepts_blocked_by_as_the_inverted_alias() -> None:
    assert enums.RelationType.parse("blocked_by") == (enums.RelationType.BLOCKS, True)


@pytest.mark.parametrize("raw", ["blocked-by", "BLOCKS", "Blocked_by", "blocked", "inverse", ""])
def test_relation_parse_rejects_any_other_name(raw: str) -> None:
    with pytest.raises(Validation) as excinfo:
        enums.RelationType.parse(raw)
    assert excinfo.value.rule == "COMMAND_FIELD"
    assert excinfo.value.field == "type"
    assert excinfo.value.code == "COMMAND_FIELD"


def test_label_group_type_values() -> None:
    assert [g.value for g in enums.LabelGroupType] == ["multiSelect", "singleSelect"]


def test_project_status_type_values_and_terminal_flags() -> None:
    assert [s.value for s in enums.ProjectStatusType] == [
        "backlog",
        "planned",
        "started",
        "paused",
        "completed",
        "canceled",
    ]
    terminal = [s for s in enums.ProjectStatusType if s.is_terminal]
    assert terminal == [enums.ProjectStatusType.COMPLETED, enums.ProjectStatusType.CANCELED]


def test_team_visibility_values() -> None:
    assert [v.value for v in enums.TeamVisibility] == ["public", "private", "restricted"]


def test_milestone_status_values() -> None:
    assert [m.value for m in enums.MilestoneStatus] == ["unstarted", "next", "overdue", "done"]


def test_estimation_type_values() -> None:
    assert [e.value for e in enums.EstimationType] == [
        "notUsed",
        "exponential",
        "fibonacci",
        "linear",
        "tShirt",
    ]


@pytest.mark.parametrize(
    ("estimation", "expected"),
    [
        (enums.EstimationType.NOT_USED, frozenset()),
        (enums.EstimationType.FIBONACCI, frozenset({0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0})),
        (enums.EstimationType.EXPONENTIAL, frozenset({1.0, 2.0, 4.0, 8.0, 16.0})),
        (enums.EstimationType.LINEAR, frozenset(float(v) for v in range(101))),
        (enums.EstimationType.T_SHIRT, frozenset({1.0, 2.0, 3.0, 4.0, 5.0})),
    ],
)
def test_estimation_allowed_values(
    estimation: enums.EstimationType, expected: frozenset[float]
) -> None:
    assert estimation.allowed_values == expected


def test_str_enums_serialize_to_their_value() -> None:
    assert enums.WorkflowStateType.STARTED == "started"
    assert f"{enums.EstimationType.T_SHIRT}" == "tShirt"
    assert enums.LabelGroupType.MULTI_SELECT == "multiSelect"
