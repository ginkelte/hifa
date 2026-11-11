"""Data model enums (SPEC-020 §2.1).

Each value is the literal translation of a `CHECK` in SPEC-010 §3. `StrEnum`s
serialize to their exact value; `Priority` is an `IntEnum` and serializes to `int`.
"""

from __future__ import annotations

import enum


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
        """Case-sensitive over the five labels; `ValueError` otherwise (SPEC-020 §2.1)."""
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
    def inverse_name(self) -> str:
        """`blocks` -> 'blocked_by'; the rest return their own value (not stored)."""
        return "blocked_by" if self is RelationType.BLOCKS else self.value


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
