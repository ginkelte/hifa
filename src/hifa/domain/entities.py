"""Immutable domain entities: a row-by-row mirror of the database schema.

`frozen=True` + `extra="forbid"`: to "mutate" you build a copy with
`model_copy(update={...})`. The DDL's `INTEGER 0/1` are modelled as `bool`, the
timestamps as aware (UTC) `datetime` and calendar dates as `date`.
"""

from __future__ import annotations

from datetime import date

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from hifa.domain.enums import (
    EstimationType,
    LabelGroupType,
    MilestoneStatus,
    Priority,
    ProjectStatusType,
    RelationType,
    TeamVisibility,
    WorkflowStateType,
)
from hifa.domain.identifier import validate_key


class Entity(BaseModel):
    """Base for every entity: immutable, no extra fields, validated defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class Workspace(Entity):
    """`workspace` row."""

    id: str
    name: str
    url_key: str
    created_at: AwareDatetime


class Team(Entity):
    """`team` row."""

    id: str
    workspace_id: str
    key: str
    name: str
    icon: str | None = None  # emoji
    color: str | None = None
    description: str | None = None
    timezone: str = "America/Santiago"
    visibility: TeamVisibility = TeamVisibility.PUBLIC
    parent_id: str | None = None
    cycles_enabled: bool = False
    cycle_duration: int = 2
    cycle_start_day: int = 1  # 0 = Sunday
    issue_estimation_type: EstimationType = EstimationType.FIBONACCI
    default_issue_estimate: float | None = None
    triage_enabled: bool = False
    auto_close_period: int | None = None
    archived_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        """`key` matches `identifier.KEY_PATTERN` (normalizes to uppercase)."""
        return validate_key(value)

    @property
    def is_archived(self) -> bool:
        """True if the team has `archived_at`."""
        return self.archived_at is not None


class WorkflowState(Entity):
    """`workflow_state` row."""

    id: str
    team_id: str
    name: str
    type: WorkflowStateType
    color: str = "#6B6F76"
    position: float
    is_default: bool = False
    archived_at: AwareDatetime | None = None


class Label(Entity):
    """`label` row. `team_id is None` = workspace label."""

    id: str
    workspace_id: str
    team_id: str | None = None
    parent_id: str | None = None  # group
    name: str
    color: str
    description: str | None = None
    is_group: bool = False
    group_type: LabelGroupType | None = None
    retired_at: AwareDatetime | None = None

    @property
    def is_workspace_wide(self) -> bool:
        """True if the label does not belong to a team."""
        return self.team_id is None

    @property
    def is_retired(self) -> bool:
        """True if the label has `retired_at`."""
        return self.retired_at is not None


class Member(Entity):
    """`member` row."""

    id: str
    workspace_id: str
    name: str
    display_name: str
    email: str | None = None
    initials: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None
    is_admin: bool = False
    is_active: bool = True
    created_at: AwareDatetime


class TeamMembership(Entity):
    """`team_membership` row: composite PK `(team_id, member_id)`, no id of its own."""

    team_id: str
    member_id: str
    is_owner: bool = False
    sort_order: float = 0.0


class TeamPreviousKey(Entity):
    """`team_previous_key` row (migration 003): historical key of a team."""

    team_id: str
    key: str
    changed_at: AwareDatetime


class Project(Entity):
    """`project` row. `team_id is None` = cross-team project."""

    id: str
    workspace_id: str
    team_id: str | None = None
    name: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    status_type: ProjectStatusType = ProjectStatusType.PLANNED
    priority: Priority = Priority.NO_PRIORITY
    lead_id: str | None = None
    start_date: date | None = None
    target_date: date | None = None
    sort_order: float = 0.0
    archived_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProjectMilestone(Entity):
    """`project_milestone` row."""

    id: str
    project_id: str
    name: str
    status: MilestoneStatus = MilestoneStatus.UNSTARTED
    target_date: date | None = None
    sort_order: float = 0.0


class Cycle(Entity):
    """`cycle` row. `ends_at > starts_at` is validated by `rules.validate_date_range`."""

    id: str
    team_id: str
    number: int
    name: str | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class Issue(Entity):
    """`issue` row: 31 columns, not one more."""

    id: str
    workspace_id: str
    team_id: str
    number: int
    identifier: str
    title: str
    description: str | None = None
    priority: Priority = Priority.NO_PRIORITY
    priority_sort_order: float = 0.0
    estimate: float | None = None
    due_date: date | None = None
    state_id: str
    assignee_id: str | None = None
    creator_id: str | None = None
    project_id: str | None = None
    milestone_id: str | None = None
    cycle_id: str | None = None
    parent_id: str | None = None
    sort_order: float = 0.0
    board_order: float = 0.0
    sub_issue_sort_order: float = 0.0
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    canceled_at: AwareDatetime | None = None
    snoozed_until_at: AwareDatetime | None = None
    archived_at: AwareDatetime | None = None
    auto_archived_at: AwareDatetime | None = None
    trashed: bool = False
    branch_name: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @property
    def is_archived(self) -> bool:
        """True if the issue has `archived_at`."""
        return self.archived_at is not None

    @property
    def is_trashed(self) -> bool:
        """True if the issue is in the trash (`trashed`)."""
        return self.trashed

    @property
    def is_visible(self) -> bool:
        """`archived_at is None and not trashed` (column `v_issue.visible`)."""
        return self.archived_at is None and not self.trashed


class IssueView(Issue):
    """`v_issue` row: `Issue` plus the three derived columns of the view.

    The field is named `visible` (not `is_visible`) because `Issue.is_visible` is a
    property: redeclaring it as a field shadowed the parent attribute.
    """

    priority_label: str
    is_open: bool
    visible: bool


class Comment(Entity):
    """`comment` row."""

    id: str
    issue_id: str
    parent_id: str | None = None
    author_id: str | None = None
    body: str
    resolved_at: AwareDatetime | None = None
    edited_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @property
    def is_resolved(self) -> bool:
        """True if the comment has `resolved_at`."""
        return self.resolved_at is not None


class IssueRelation(Entity):
    """`issue_relation` row."""

    id: str
    issue_id: str
    related_issue_id: str
    type: RelationType
    created_at: AwareDatetime


class Attachment(Entity):
    """`attachment` row. `metadata` is opaque TEXT/JSON: the domain does not parse it."""

    id: str
    issue_id: str
    title: str
    subtitle: str | None = None
    url: str
    source_type: str | None = None
    metadata: str | None = None
    created_at: AwareDatetime


class IssueHistory(Entity):
    """`issue_history` row (`id` is an AUTOINCREMENT integer)."""

    id: int
    issue_id: str
    actor_id: str | None = None
    field: str
    old_value: str | None = None
    new_value: str | None = None
    created_at: AwareDatetime


__all__ = [
    "Attachment",
    "Comment",
    "Cycle",
    "Entity",
    "Issue",
    "IssueHistory",
    "IssueRelation",
    "IssueView",
    "Label",
    "Member",
    "Project",
    "ProjectMilestone",
    "Team",
    "TeamMembership",
    "TeamPreviousKey",
    "WorkflowState",
    "Workspace",
]
