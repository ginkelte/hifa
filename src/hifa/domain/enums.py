"""Data model enums.

Each value is the literal translation of a `CHECK` constraint of the schema. `StrEnum`s
serialize to their exact value; `Priority` is an `IntEnum` and serializes to `int`.
"""

from __future__ import annotations

import enum
from typing import Literal

from hifa.domain.errors import Validation


class Priority(enum.IntEnum):
    """Issue/project priority: `0..4`, same as `CHECK (priority BETWEEN 0 AND 4)`."""

    NO_PRIORITY = 0
    URGENT = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4

    @property
    def label(self) -> str:
        """'No priority' | 'Urgent' | 'High' | 'Medium' | 'Low'. Identical to `v_priority`."""
        return _PRIORITY_LABELS[self.value]

    @property
    def rank(self) -> int:
        """Ascending sort key: 0 -> 5, 1 -> 1, 2 -> 2, 3 -> 3, 4 -> 4."""
        return _PRIORITY_RANKS[self.value]

    @classmethod
    def from_label(cls, label: str) -> Priority:
        """Case-sensitive over the five labels; `ValueError` otherwise."""
        try:
            return _PRIORITY_BY_LABEL[label]
        except KeyError:
            raise ValueError(f"unknown priority label: {label!r}") from None


_PRIORITY_LABELS: dict[int, str] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}

_PRIORITY_RANKS: dict[int, int] = {0: 5, 1: 1, 2: 2, 3: 3, 4: 4}

_PRIORITY_BY_LABEL: dict[str, Priority] = {
    label: Priority(value) for value, label in _PRIORITY_LABELS.items()
}


class WorkflowStateType(enum.StrEnum):
    """Semantic bucket of `workflow_state.type`: enables per-team workflows."""

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @property
    def is_open(self) -> bool:
        """True for triage, backlog, unstarted and started."""
        return self in _OPEN_STATE_TYPES

    @property
    def is_terminal(self) -> bool:
        """True for completed and canceled."""
        return self in _TERMINAL_STATE_TYPES


_OPEN_STATE_TYPES = frozenset(
    {
        WorkflowStateType.TRIAGE,
        WorkflowStateType.BACKLOG,
        WorkflowStateType.UNSTARTED,
        WorkflowStateType.STARTED,
    }
)

_TERMINAL_STATE_TYPES = frozenset(
    {
        WorkflowStateType.COMPLETED,
        WorkflowStateType.CANCELED,
    }
)


class RelationType(enum.StrEnum):
    """Type of `issue_relation.type`. `blocks` is the only asymmetric one."""

    BLOCKS = "blocks"
    DUPLICATE = "duplicate"
    RELATED = "related"
    SIMILAR = "similar"

    @property
    def is_symmetric(self) -> bool:
        """False only for blocks; True for duplicate, related and similar."""
        return self is not RelationType.BLOCKS

    @property
    def is_hard(self) -> bool:
        """True only for blocks: the one type that gates the work of the far end."""
        return self is RelationType.BLOCKS

    @property
    def inverse_name(self) -> str:
        """`blocks` -> 'blocked_by'; the rest return their own value (not stored)."""
        return "blocked_by" if self is RelationType.BLOCKS else self.value

    def name_as_seen(self, direction: Literal["out", "in"]) -> str:
        """Name of the type read from one end of its row.

        'out' is the row's own `issue_id` -> the type's value; 'in' is its
        `related_issue_id` -> the inverse name, so a `blocks` row reads as `blocked_by`
        for the blocked issue and the symmetric types read the same from both ends.
        """
        if direction == "out":
            return self.value
        if direction == "in":
            return self.inverse_name
        raise ValueError(f"unknown relation direction: {direction!r}")

    def canonical_pair(self, source_id: str, target_id: str) -> tuple[str, str]:
        """The `(issue_id, related_issue_id)` order this type is stored in.

        `blocks` keeps the direction the caller gave: the source is the blocker, and that
        order is the meaning of the row. The symmetric types collapse both spellings of
        one edge into a single pair sorted by identifier, so the `UNIQUE` of the schema
        sees one key per edge.
        """
        if not self.is_symmetric:
            return source_id, target_id
        if target_id < source_id:
            return target_id, source_id
        return source_id, target_id

    @classmethod
    def parse(cls, raw: str) -> tuple[RelationType, bool]:
        """Input vocabulary -> `(type, swap)`.

        The four stored tokens are returned unchanged. `blocked_by` is the input alias of
        the inverse direction: `(BLOCKS, True)` tells the caller to swap the pair before
        storing it, because `A blocked_by B` is the row `B blocks A`. Any other name is
        `Validation(rule='COMMAND_FIELD')`.
        """
        if raw == cls.BLOCKS.inverse_name:
            return cls.BLOCKS, True
        try:
            return cls(raw), False
        except ValueError:
            raise Validation(
                "unknown relation type",
                rule="COMMAND_FIELD",
                field="type",
                value=raw,
                code="COMMAND_FIELD",
            ) from None


class LabelGroupType(enum.StrEnum):
    """Label group type (`label.group_type`)."""

    MULTI_SELECT = "multiSelect"
    SINGLE_SELECT = "singleSelect"


class ProjectStatusType(enum.StrEnum):
    """Status of `project.status_type`."""

    BACKLOG = "backlog"
    PLANNED = "planned"
    STARTED = "started"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        """True for completed and canceled."""
        return self in _TERMINAL_PROJECT_STATUSES


_TERMINAL_PROJECT_STATUSES = frozenset(
    {
        ProjectStatusType.COMPLETED,
        ProjectStatusType.CANCELED,
    }
)


class EstimationType(enum.StrEnum):
    """Team estimation scale (`team.issue_estimation_type`)."""

    NOT_USED = "notUsed"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"
    LINEAR = "linear"
    T_SHIRT = "tShirt"

    @property
    def allowed_values(self) -> frozenset[float]:
        """Admitted values of `issue.estimate` for this scale."""
        return _ESTIMATE_SCALES[self]


_ESTIMATE_SCALES: dict[EstimationType, frozenset[float]] = {
    EstimationType.NOT_USED: frozenset(),
    EstimationType.EXPONENTIAL: frozenset({1.0, 2.0, 4.0, 8.0, 16.0}),
    EstimationType.FIBONACCI: frozenset({0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0}),
    EstimationType.LINEAR: frozenset(float(v) for v in range(0, 101)),
    EstimationType.T_SHIRT: frozenset({1.0, 2.0, 3.0, 4.0, 5.0}),
}


class TeamVisibility(enum.StrEnum):
    """Visibility of `team.visibility` (no enforcement in v1: it is local)."""

    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class MilestoneStatus(enum.StrEnum):
    """Status of `project_milestone.status`."""

    UNSTARTED = "unstarted"
    NEXT = "next"
    OVERDUE = "overdue"
    DONE = "done"


__all__ = [
    "EstimationType",
    "LabelGroupType",
    "MilestoneStatus",
    "Priority",
    "ProjectStatusType",
    "RelationType",
    "TeamVisibility",
    "WorkflowStateType",
]
