"""Pure rules and invariants (SPEC-020 §2.5).

No function does IO or reads the clock: whatever the rule needs from the world
arrives as an argument already read by `app/`. Every violation is a `Validation`/`Conflict` with a
stable `rule`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date

from hifa.domain.entities import Issue, Label, Team, WorkflowState
from hifa.domain.enums import EstimationType, Priority, RelationType, WorkflowStateType
from hifa.domain.errors import Conflict, Validation

MAX_TITLE = 255
MAX_BODY = 50_000
MAX_DESCRIPTION = 100_000

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _reject(message: str, rule: str, field: str, value: object, **extra: object) -> Validation:
    """`Validation` with `code == rule`: the edge shows the specific code (§6)."""
    details = dict(extra)
    return Validation(
        message,
        rule=rule,
        field=field,
        value=value,
        details=details,
        code=rule,
    )


def validate_title(title: str) -> str:
    """`strip()`; rejects empty (`TITLE_EMPTY`) or len > MAX_TITLE (`TITLE_TOO_LONG`)."""
    cleaned = title.strip()
    if not cleaned:
        raise _reject("title must not be empty", "TITLE_EMPTY", "title", title)
    if len(cleaned) > MAX_TITLE:
        raise _reject(
            f"title must be at most {MAX_TITLE} characters",
            "TITLE_TOO_LONG",
            "title",
            title,
            max_length=MAX_TITLE,
            length=len(cleaned),
        )
    return cleaned


def validate_comment_body(body: str) -> str:
    """`strip()`; rejects empty (`BODY_EMPTY`) or len > MAX_BODY (`BODY_TOO_LONG`)."""
    cleaned = body.strip()
    if not cleaned:
        raise _reject("comment body must not be empty", "BODY_EMPTY", "body", body)
    if len(cleaned) > MAX_BODY:
        raise _reject(
            f"comment body must be at most {MAX_BODY} characters",
            "BODY_TOO_LONG",
            "body",
            body,
            max_length=MAX_BODY,
            length=len(cleaned),
        )
    return cleaned


def validate_priority(priority: int) -> Priority:
    """0..4 -> Priority; out of range -> `PRIORITY_OUT_OF_RANGE`."""
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise _reject(
            "priority must be an integer between 0 and 4",
            "PRIORITY_OUT_OF_RANGE",
            "priority",
            priority,
        )
    if not 0 <= priority <= 4:
        raise _reject(
            "priority must be between 0 and 4",
            "PRIORITY_OUT_OF_RANGE",
            "priority",
            priority,
        )
    return Priority(priority)


def validate_due_date(value: str | date | None) -> date | None:
    """None -> None. Strict 'YYYY-MM-DD' (regex + `date.fromisoformat`)."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, bool):
        # `datetime` is a subclass of `date`: it is rejected rather than silently dropping the time.
        if type(value) is not date:
            raise _reject(
                "due_date must be a calendar date, not a datetime",
                "DUE_DATE_FORMAT",
                "due_date",
                value,
            )
        return value
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise _reject(
            "due_date must use the YYYY-MM-DD format",
            "DUE_DATE_FORMAT",
            "due_date",
            value,
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _reject(
            "due_date is not a real calendar date",
            "DUE_DATE_FORMAT",
            "due_date",
            value,
        ) from None


def validate_date_range(
    start: date | None,
    target: date | None,
    *,
    start_field: str = "start_date",
    target_field: str = "target_date",
) -> None:
    """`target < start` -> `DATE_RANGE_INVERTED`."""
    if start is None or target is None:
        return
    if target < start:
        raise _reject(
            f"{target_field} must not be before {start_field}",
            "DATE_RANGE_INVERTED",
            target_field,
            target,
            start_field=start_field,
            start=start.isoformat(),
            target=target.isoformat(),
        )


def validate_estimate(value: float | None, estimation: EstimationType) -> float | None:
    """`notUsed` with a non-None value -> `ESTIMATE_NOT_USED`; out of scale -> `NOT_IN_SCALE`."""
    if estimation is EstimationType.NOT_USED:
        if value is None:
            return None
        raise _reject(
            "estimates are disabled for this team",
            "ESTIMATE_NOT_USED",
            "estimate",
            value,
        )
    if value is None:
        return None
    if value not in estimation.allowed_values:
        raise _reject(
            f"estimate is not part of the {estimation.value} scale",
            "ESTIMATE_NOT_IN_SCALE",
            "estimate",
            value,
            estimation=estimation.value,
        )
    return float(value)


def validate_state_team(issue_team_id: str, state: WorkflowState) -> None:
    """`state.team_id != issue_team_id` -> `STATE_TEAM_MISMATCH` with `field='state_id'`."""
    if state.team_id != issue_team_id:
        raise _reject(
            "workflow state belongs to another team",
            "STATE_TEAM_MISMATCH",
            "state_id",
            state.id,
            issue_team_id=issue_team_id,
            state_team_id=state.team_id,
        )


def validate_parent(
    child_id: str,
    child_team_id: str,
    parent: Issue | None,
    ancestors: Mapping[str, str | None],
) -> None:
    """Missing parent = ok; rejects self-parent, parent from another team and hierarchy cycles."""
    if parent is None:
        return
    if parent.id == child_id:
        raise _reject("an issue cannot be its own parent", "PARENT_SELF", "parent_id", child_id)
    if parent.team_id != child_team_id:
        raise _reject(
            "parent issue belongs to another team",
            "PARENT_TEAM_MISMATCH",
            "parent_id",
            parent.id,
            issue_team_id=child_team_id,
            parent_team_id=parent.team_id,
        )
    seen: set[str] = set()
    cursor: str | None = parent.id
    while cursor is not None and cursor not in seen:
        if cursor == child_id:
            raise _reject(
                "parent chain would create a cycle",
                "PARENT_CYCLE",
                "parent_id",
                parent.id,
                child_id=child_id,
                parent_id=parent.id,
            )
        seen.add(cursor)
        cursor = ancestors.get(cursor)


def validate_close(target: WorkflowStateType, open_children: int) -> None:
    """`target.is_terminal` with open children -> `CLOSE_WITH_OPEN_CHILDREN`."""
    if target.is_terminal and open_children > 0:
        raise _reject(
            "cannot close an issue with open sub-issues",
            "CLOSE_WITH_OPEN_CHILDREN",
            "state_id",
            target.value,
            open_children=open_children,
        )


def validate_transition(current: WorkflowState, target: WorkflowState, open_children: int) -> None:
    """Validates the team, an archived state and closing with open children.

    Never raises `InvalidTransition` (§6: reserved class, no producer in the domain).
    """
    validate_state_team(current.team_id, target)
    if target.archived_at is not None:
        raise _reject(
            "target workflow state is archived",
            "STATE_ARCHIVED",
            "state_id",
            target.id,
            state=target.id,
        )
    validate_close(target.type, open_children)


def validate_relation(
    source_id: str,
    target_id: str,
    type: RelationType,
    existing: Sequence[tuple[str, str, RelationType]],
) -> None:
    """Rejects self-relation (`Validation`) and exact or symmetric duplicates (`Conflict`)."""
    if source_id == target_id:
        raise _reject(
            "an issue cannot be related to itself", "RELATION_SELF", "related_issue_id", target_id
        )
    pairs = {
        (issue_id, related_id, relation_type) for issue_id, related_id, relation_type in existing
    }
    if (source_id, target_id, type) in pairs:
        raise Conflict(
            "relation already exists",
            rule="RELATION_DUPLICATE",
            field="related_issue_id",
            value=target_id,
            details={"issue_id": source_id, "related_issue_id": target_id, "type": type.value},
            code="RELATION_DUPLICATE",
        )
    if type.is_symmetric and (target_id, source_id, type) in pairs:
        raise Conflict(
            "symmetric relation already exists",
            rule="RELATION_DUPLICATE",
            field="related_issue_id",
            value=target_id,
            details={"issue_id": source_id, "related_issue_id": target_id, "type": type.value},
            code="RELATION_DUPLICATE",
        )


def validate_label_scope(issue_team_id: str, label: Label) -> None:
    """Rejects retired labels (`LABEL_RETIRED`) and cross-team labels (`LABEL_TEAM_MISMATCH`)."""
    if label.retired_at is not None:
        raise _reject(
            "label is retired",
            "LABEL_RETIRED",
            "label_id",
            label.id,
            retired_at=label.retired_at.isoformat(),
        )
    if label.team_id not in (None, issue_team_id):
        raise _reject(
            "label belongs to another team",
            "LABEL_TEAM_MISMATCH",
            "label_id",
            label.id,
            issue_team_id=issue_team_id,
            label_team_id=label.team_id,
        )


def validate_archive_team(team: Team, open_issues: int) -> None:
    """Team already archived -> `Conflict`; with open issues -> `Validation`."""
    if team.archived_at is not None:
        raise Conflict(
            "team is already archived",
            rule="TEAM_ALREADY_ARCHIVED",
            field="archived_at",
            value=team.archived_at.isoformat(),
            details={"team_id": team.id},
            code="TEAM_ALREADY_ARCHIVED",
        )
    if open_issues > 0:
        raise _reject(
            "cannot archive a team with open issues",
            "TEAM_HAS_OPEN_ISSUES",
            "team_id",
            team.id,
            open_issues=open_issues,
        )


__all__ = [
    "MAX_BODY",
    "MAX_DESCRIPTION",
    "MAX_TITLE",
    "validate_archive_team",
    "validate_close",
    "validate_comment_body",
    "validate_date_range",
    "validate_due_date",
    "validate_estimate",
    "validate_label_scope",
    "validate_parent",
    "validate_priority",
    "validate_relation",
    "validate_state_team",
    "validate_title",
    "validate_transition",
]
