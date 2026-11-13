---
id: SPEC-020
title: Domain core
component: domain
status: review
version: 1
spec_revision: 4
depends_on: [SPEC-000, SPEC-010]
implements:
  - src/hifa/domain/**
  - tests/unit/domain/**
acceptance: specs/acceptance/020-domain-core.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-13
---

# SPEC-020 — Domain core

## 1. Responsibility and boundaries

**Does:** define the vocabulary of the system and the rules that do not depend on IO: the enums with their
canonical values, the immutable entities that mirror the tables of `SPEC-010`, the algebra of
fractional ordering (`ranking`), the formatting/parsing of `identifier`, the pure validations (`rules`) and
the error hierarchy (`errors`). Everything that can be decided without touching disk, network or clock lives
here.

**Does NOT do (explicit):**

- **It performs no IO.** It does not open connections, does not read files, does not query the database, does not write logs, does not
  print. Zero `open`, zero `sqlite3`, zero `requests`.
- **It does not know time.** It does not call `datetime.now()`, `utcnow()`, `time.time()` nor `today()`.
  The instant enters as a `now: datetime` argument resolved by the `Clock` port in `app/`
  (see §4 R10). `datetime.date.fromisoformat` and `datetime.fromisoformat` are allowed:
  they are parsing, not clock reads.
- **It does not generate IDs.** It does not import `uuid`, `ulid`, `secrets` nor `random`. IDs enter from the
  `IdGen` port (implemented in `adapters/`), already materialized in the entity.
- **It does not decide transactions.** It does not know what a `BEGIN`, a commit or a rollback is (SPEC-030).
- **It does not read from the database to validate.** When a rule needs a fact from the world (how many open
  children does this issue have?), it receives it as an argument already counted/read by `app/`. This is what
  keeps `domain/` pure and testable without database fixtures.
- **It does not format output.** There is no `to_table()`, no `__str__` with padding, no ANSI colors.
  Presentation belongs to `cli/output.py` (SPEC-050) and `web/` (SPEC-070).
- **It does not import `hifa.ports`.** `ports.py` imports entities from `domain/` to type its Protocols;
  importing in the other direction would create a cycle (see §4 R11). `src/hifa/ports.py` belongs to
  SPEC-030 (D-017): here `Clock` and `IdGen` are only named as ports, not declared.

## 2. Public contract

> Normative. Exact signatures, no `...` and no "etc.". An implementer must be able to write the six
> modules from this section. Any symbol not listed here is private (`_prefix`) and cannot
> be used from `app/`, `cli/`, `api/`, `web/` nor `adapters/`.

| Symbol | Module | Type | Stable |
|---|---|---|---|
| `Priority`, `WorkflowStateType`, `RelationType`, `LabelGroupType`, `ProjectStatusType`, `EstimationType`, `TeamVisibility`, `MilestoneStatus` | `enums` | enums | yes |
| `Entity`, `Workspace`, `Team`, `TeamMembership`, `TeamPreviousKey`, `WorkflowState`, `Label`, `Member`, `Project`, `ProjectMilestone`, `Cycle`, `Issue`, `IssueView`, `Comment`, `IssueRelation`, `Attachment`, `IssueHistory` | `entities` | Pydantic v2 models | yes |
| `format`, `parse`, `validate_key`, `normalize_key`, `is_valid_key`, `KEY_PATTERN`, `IDENTIFIER_PATTERN` | `identifier` | functions / constants | yes |
| `between`, `needs_rebalance`, `rebalance`, `first`, `last` | `ranking` | functions | yes |
| `INITIAL_GAP`, `MIN_GAP` | `ranking` | stable constants | yes |
| `validate_title`, `validate_priority`, `validate_due_date`, `validate_date_range`, `validate_estimate`, `validate_state_team`, `validate_parent`, `validate_close`, `validate_transition`, `validate_relation`, `validate_label_scope`, `validate_archive_team`, `validate_comment_body` | `rules` | functions | yes |
| `MAX_TITLE`, `MAX_BODY`, `MAX_DESCRIPTION` | `rules` | stable constants | yes |
| `HifaError`, `NotFound`, `Conflict`, `Validation`, `InvalidTransition` | `errors` | exceptions | yes |

### 2.1 `domain/enums.py`

The eight enums are the literal translation of the `CHECK`s of `SPEC-010` §3. The `str`-enums inherit
from `enum.StrEnum` (Python ≥ 3.11) and serialize to their **exact value**; `Priority` inherits from
`enum.IntEnum` and serializes to **int**.

```python
# src/hifa/domain/enums.py
import enum


class Priority(enum.IntEnum):
    NO_PRIORITY = 0
    URGENT = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4

    @property
    def label(self) -> str:
        """'No priority' | 'Urgent' | 'High' | 'Medium' | 'Low'. Identical to v_priority."""

    @property
    def rank(self) -> int:
        """Ascending sort key: 0 -> 5, 1 -> 1, 2 -> 2, 3 -> 3, 4 -> 4."""

    @classmethod
    def from_label(cls, label: str) -> "Priority":
        """Case-sensitive over the five labels; ValueError if it does not match."""


class WorkflowStateType(enum.StrEnum):
    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @property
    def is_open(self) -> bool:
        """True for triage, backlog, unstarted, started."""

    @property
    def is_terminal(self) -> bool:
        """True for completed and canceled."""


class RelationType(enum.StrEnum):
    BLOCKS = "blocks"
    DUPLICATE = "duplicate"
    RELATED = "related"
    SIMILAR = "similar"

    @property
    def is_symmetric(self) -> bool:
        """False only for blocks; True for duplicate, related and similar."""

    @property
    def inverse_name(self) -> str:
        """blocks -> 'blocked_by'; the rest return their own value."""


class LabelGroupType(enum.StrEnum):
    MULTI_SELECT = "multiSelect"
    SINGLE_SELECT = "singleSelect"


class ProjectStatusType(enum.StrEnum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    STARTED = "started"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        """True for completed and canceled."""


class EstimationType(enum.StrEnum):
    NOT_USED = "notUsed"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"
    LINEAR = "linear"
    T_SHIRT = "tShirt"

    @property
    def allowed_values(self) -> frozenset[float]:
        """notUsed -> frozenset(); fibonacci -> {0,1,2,3,5,8,13,21};
        exponential -> {1,2,4,8,16}; linear -> {0..100}; tShirt -> {1,2,3,4,5}."""


class TeamVisibility(enum.StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class MilestoneStatus(enum.StrEnum):
    UNSTARTED = "unstarted"
    NEXT = "next"
    OVERDUE = "overdue"
    DONE = "done"
```

Serialization rules, verifiable:

| Enum | JSON | SQLite | Accepts in input |
|---|---|---|---|
| `Priority` | `2` | `INTEGER` 2 | `2`, `"2"`, `"High"` (CLI/API only, see SPEC-050/060) |
| `WorkflowStateType` | `"started"` | `TEXT 'started'` | exact `"started"` |
| `RelationType` | `"blocks"` | `TEXT 'blocks'` | exact `"blocks"` |
| `LabelGroupType` | `"multiSelect"` | `TEXT 'multiSelect'` | exact `"multiSelect"` |
| `ProjectStatusType` | `"planned"` | `TEXT 'planned'` | exact `"planned"` |
| `EstimationType` | `"tShirt"` | `TEXT 'tShirt'` | exact `"tShirt"` |
| `TeamVisibility` | `"public"` | `TEXT 'public'` | exact `"public"` |
| `MilestoneStatus` | `"unstarted"` | `TEXT 'unstarted'` | exact `"unstarted"` |

### 2.2 `domain/entities.py`

Common base:

```python
from datetime import date, datetime
from pydantic import AwareDatetime, BaseModel, ConfigDict


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
```

`frozen=True` ⇒ assigning an attribute raises `pydantic_core.ValidationError`; `extra="forbid"` ⇒ a
field that is not in the DDL is a construction error, not an ignored field. To "mutate", the
caller builds a copy: `issue.model_copy(update={"title": nuevo})`. The `INTEGER 0/1` of the
DDL are modeled as `bool`; the conversion is the adapter's responsibility (SPEC-030 §4).

```python
class Workspace(Entity):
    id: str
    name: str
    url_key: str
    created_at: AwareDatetime


class Team(Entity):
    id: str
    workspace_id: str
    key: str  # validated with identifier.validate_key
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

    @property
    def is_archived(self) -> bool: ...


class WorkflowState(Entity):
    id: str
    team_id: str
    name: str
    type: WorkflowStateType
    color: str = "#6B6F76"
    position: float
    is_default: bool = False
    archived_at: AwareDatetime | None = None


class Label(Entity):
    id: str
    workspace_id: str
    team_id: str | None = None  # None = workspace label
    parent_id: str | None = None  # group
    name: str
    color: str
    description: str | None = None
    is_group: bool = False
    group_type: LabelGroupType | None = None
    retired_at: AwareDatetime | None = None

    @property
    def is_workspace_wide(self) -> bool: ...
    @property
    def is_retired(self) -> bool: ...


class Member(Entity):
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


class Project(Entity):
    id: str
    workspace_id: str
    team_id: str | None = None  # None = cross-team
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
    id: str
    project_id: str
    name: str
    status: MilestoneStatus = MilestoneStatus.UNSTARTED
    target_date: date | None = None
    sort_order: float = 0.0


class Cycle(Entity):
    id: str
    team_id: str
    number: int
    name: str | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class Issue(Entity):
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
    def is_archived(self) -> bool: ...
    @property
    def is_trashed(self) -> bool: ...
    @property
    def is_visible(self) -> bool:
        """archived_at is None and not trashed."""


class IssueView(Issue):
    """Row of `v_issue`: `Issue` + the three derived columns of SPEC-010 §3.3.

    The field is called `visible` (not `is_visible`) because `Issue.is_visible` is a property:
    redeclaring it as a field shadowed the parent attribute and the same object returned two
    different values (`v.is_visible` = property, `v.model_dump()["is_visible"]` = column).
    The adapter maps the `v_issue.is_visible` column to this field (D-019).
    """

    priority_label: str
    is_open: bool
    visible: bool


class Comment(Entity):
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
    def is_resolved(self) -> bool: ...


class IssueRelation(Entity):
    id: str
    issue_id: str
    related_issue_id: str
    type: RelationType
    created_at: AwareDatetime


class Attachment(Entity):
    id: str
    issue_id: str
    title: str
    subtitle: str | None = None
    url: str
    source_type: str | None = None
    metadata: str | None = None  # serialized JSON, TEXT in the DDL
    created_at: AwareDatetime


class IssueHistory(Entity):
    id: int  # AUTOINCREMENT
    issue_id: str
    actor_id: str | None = None
    field: str
    old_value: str | None = None
    new_value: str | None = None
    created_at: AwareDatetime


class TeamMembership(Entity):
    """Row of `team_membership`: composite PK `(team_id, member_id)`, no id of its own."""

    team_id: str
    member_id: str
    is_owner: bool = False
    sort_order: float = 0.0


class TeamPreviousKey(Entity):
    """Row of `team_previous_key` (migration 003): historical key of a team.

    It is the materialization of `team.previous_keys` that SPEC-010 R3 names; it is not a field of
    `Team` (such a field would not exist as a column of `team`).
    """

    team_id: str
    key: str
    changed_at: AwareDatetime
```

There is no field in these classes that does not exist as a column in `SPEC-010` §3; `IssueView`
adds only the three columns of `v_issue` (`priority_label`, `is_open`, `visible`, the latter being an
alias of the homonymous column). The M2M fields (`labels`) do **not** live in `Issue`: they are read models of
SPEC-030. `schema_migration` is migrator infrastructure and has **no** domain model (§10).

### 2.3 `domain/identifier.py`

```python
import re

KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
IDENTIFIER_PATTERN = re.compile(r"^(?P<key>[A-Z][A-Z0-9]{1,9})-(?P<number>[1-9][0-9]{0,8})$")


def normalize_key(raw: str) -> str:
    """strip + upper; does not validate. ' eng ' -> 'ENG'."""


def is_valid_key(key: str) -> bool:
    """True if KEY_PATTERN.fullmatch(key). Does not normalize."""


def validate_key(key: str) -> str:
    """Returns the normalized key if valid; otherwise raise Validation(field='team.key')."""


def format(key: str, number: int) -> str:
    """'ENG', 123 -> 'ENG-123'. Validates key and number >= 1; otherwise Validation."""


def parse(identifier: str) -> tuple[str, int]:
    """'ENG-123' -> ('ENG', 123). Case-sensitive: 'eng-123' -> Validation."""
```

Regex rules, all verifiable: 2 to 10 characters; first letter `A-Z`; the rest `A-Z` or a digit;
no hyphens, no lowercase, no spaces, no accents. The number goes from 1 to 999,999,999, without leading
zeros (`ENG-007` is invalid: the canonical form is `ENG-7`).

### 2.4 `domain/ranking.py`

```python
INITIAL_GAP = 1024.0
MIN_GAP = 1e-6


def between(prev: float | None, next: float | None) -> float:
    """Position to insert between prev and next.

    (None, None) -> INITIAL_GAP
    (None, next) -> next - INITIAL_GAP
    (prev, None) -> prev + INITIAL_GAP
    (prev, next) -> (prev + next) / 2
    Raise Validation(rule='RANKING_INVALID_RANGE') if next <= prev.
    Raise Validation(rule='RANKING_EXHAUSTED') if (next - prev) < 2 * MIN_GAP.
    """


def first() -> float:
    """INITIAL_GAP."""


def last(values: Sequence[float]) -> float:
    """max(values) + INITIAL_GAP; empty list -> INITIAL_GAP."""


def needs_rebalance(values: Sequence[float], *, min_gap: float = MIN_GAP) -> bool:
    """True if some consecutive pair (after sorting) has separation < min_gap."""


def rebalance(values: Sequence[float], *, gap: float = INITIAL_GAP) -> list[float]:
    """Returns positions spaced by gap, 1..n, preserving the input order.
    Ties are resolved by the order of the received sequence."""
```

`between` does **not** renumber nor write: it only computes. If it returns `Validation(RANKING_EXHAUSTED)`,
`app/` calls `rebalance` and retries once (SPEC-040 §5).

### 2.5 `domain/rules.py`

All functions are pure, do not mutate their arguments and return the normalized value (or `None`
in the checking `validate_*` functions). All of them raise `Validation` with a stable `rule`.

```python
MAX_TITLE = 255
MAX_BODY = 50_000
MAX_DESCRIPTION = 100_000


def validate_title(title: str) -> str:
    """strip(); rejects empty (rule='TITLE_EMPTY') or len > MAX_TITLE (rule='TITLE_TOO_LONG')."""


def validate_comment_body(body: str) -> str:
    """strip(); rejects empty (rule='BODY_EMPTY'); len > MAX_BODY (rule='BODY_TOO_LONG')."""


def validate_priority(priority: int) -> Priority:
    """0..4 -> Priority; out of range -> Validation(rule='PRIORITY_OUT_OF_RANGE')."""


def validate_due_date(value: str | date | None) -> date | None:
    """None -> None. Strict 'YYYY-MM-DD' (regex + date.fromisoformat).
    '2026-13-45' and '2026-1-5' -> Validation(field='due_date', rule='DUE_DATE_FORMAT')."""


def validate_date_range(
    start: date | None,
    target: date | None,
    *,
    start_field: str = "start_date",
    target_field: str = "target_date",
) -> None:
    """target < start -> Validation(rule='DATE_RANGE_INVERTED')."""


def validate_estimate(value: float | None, estimation: EstimationType) -> float | None:
    """notUsed with a non-None value -> Validation(rule='ESTIMATE_NOT_USED').
    Otherwise: value outside estimation.allowed_values -> Validation(rule='ESTIMATE_NOT_IN_SCALE')."""


def validate_state_team(issue_team_id: str, state: WorkflowState) -> None:
    """state.team_id != issue_team_id -> Validation(rule='STATE_TEAM_MISMATCH', field='state_id')."""


def validate_parent(
    child_id: str, child_team_id: str, parent: Issue | None, ancestors: Mapping[str, str | None]
) -> None:
    """parent None -> ok.
    parent.id == child_id -> Validation(rule='PARENT_SELF').
    parent.team_id != child_team_id -> Validation(rule='PARENT_TEAM_MISMATCH').
    child_id appears in the ancestor chain of parent -> Validation(rule='PARENT_CYCLE').
    ancestors: mapping issue_id -> parent_id already read by the repo (SPEC-030)."""


def validate_close(target: WorkflowStateType, open_children: int) -> None:
    """target.is_terminal and open_children > 0 -> Validation(rule='CLOSE_WITH_OPEN_CHILDREN')."""


def validate_transition(current: WorkflowState, target: WorkflowState, open_children: int) -> None:
    """Validates team (validate_state_team), archived state and closing with open children.
    target.archived_at not None -> Validation(rule='STATE_ARCHIVED', field='state_id').
    target.type terminal and open_children > 0 -> Validation(rule='CLOSE_WITH_OPEN_CHILDREN').
    Any transition between non-archived types is allowed; there is no rigid state machine.
    Never raises InvalidTransition: the single producer is the use-case layer."""


def validate_relation(
    source_id: str,
    target_id: str,
    type: RelationType,
    existing: Sequence[tuple[str, str, RelationType]],
) -> None:
    """source_id == target_id -> Validation(rule='RELATION_SELF').
    (source_id, target_id, type) already exists -> Conflict(rule='RELATION_DUPLICATE').
    If type.is_symmetric and (target_id, source_id, type) exists -> Conflict(idem)."""


def validate_label_scope(issue_team_id: str, label: Label) -> None:
    """label.retired_at not None -> Validation(rule='LABEL_RETIRED').
    label.team_id not in (None, issue_team_id) -> Validation(rule='LABEL_TEAM_MISMATCH')."""


def validate_archive_team(team: Team, open_issues: int) -> None:
    """team.archived_at not None -> Conflict(rule='TEAM_ALREADY_ARCHIVED').
    open_issues > 0 -> Validation(rule='TEAM_HAS_OPEN_ISSUES')."""
```

### 2.6 `domain/errors.py`

```python
from typing import Any, Mapping


class HifaError(Exception):
    code: str = "HIFA_ERROR"
    default_message: str = "error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...

    @property
    def message(self) -> str: ...
    @property
    def details(self) -> dict[str, Any]: ...
    def to_dict(self) -> dict[str, Any]:
        """{'error': {'code': ..., 'message': ..., 'details': {...}}}."""


class NotFound(HifaError):
    code = "NOT_FOUND"

    def __init__(
        self,
        message: str | None = None,
        *,
        resource: str,
        key: str,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...

    @property
    def resource(self) -> str: ...
    @property
    def key(self) -> str: ...


class Conflict(HifaError):
    code = "CONFLICT"

    def __init__(
        self,
        message: str | None = None,
        *,
        rule: str,
        field: str | None = None,
        value: Any | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...

    @property
    def rule(self) -> str: ...
    @property
    def field(self) -> str | None: ...
    @property
    def value(self) -> Any | None: ...


class Validation(HifaError):
    code = "VALIDATION"

    def __init__(
        self,
        message: str | None = None,
        *,
        rule: str,
        field: str | None = None,
        value: Any | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...

    @property
    def rule(self) -> str: ...
    @property
    def field(self) -> str | None: ...
    @property
    def value(self) -> Any | None: ...


class InvalidTransition(HifaError):
    code = "INVALID_TRANSITION"

    def __init__(
        self,
        message: str | None = None,
        *,
        current: str,
        target: str,
        allowed: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> None: ...

    @property
    def current(self) -> str: ...
    @property
    def target(self) -> str: ...
    @property
    def allowed(self) -> tuple[str, ...]: ...
```

Each subclass fixes its attributes as read-only properties (`resource`/`key`, `rule`/`field`/
`value`, `current`/`target`/`allowed`) and includes them in `details` (explicit `details` are
merged with the derived ones, without overwriting them). All kwargs are **keyword-only** except `message`.
`code` is a stable `str` in `SCREAMING_SNAKE_CASE` that the edge uses to map to exit code /
HTTP (table in §6).

## 3. Data it touches

The domain **executes** no operation. This table declares the mirror: which DDL column
feeds which field, so the adapter does not invent conversions.

| Table (SPEC-010) | Domain operation | Invariant the model respects |
|---|---|---|
| `workspace` | none (in-memory mapping) | `url_key` unique at the app level |
| `team` | none | `key` matches `KEY_PATTERN`; `visibility`, `issue_estimation_type` within the `CHECK` |
| `team_previous_key` | none | historical `key`; `TeamPreviousKey` mirrors it (SPEC-010 R3, migration 003) |
| `workflow_state` | none | `type` one of the 6 values; `position` float |
| `label` | none | `is_group=1` ⇒ `parent_id IS NULL`; `group_type` NULL if not a group |
| `member` | none | `email` may be NULL; `is_admin`/`is_active` booleans |
| `team_membership` | none | PK `(team_id, member_id)`; `is_owner`/`sort_order` boolean and float |
| `project` | none | `status_type` one of 6; `priority` 0-4 |
| `project_milestone` | none | `status` one of 4 |
| `cycle` | none | `ends_at > starts_at` (checked by `validate_date_range` in `app/`) |
| `issue` | none | `priority` 0-4; `due_date` `YYYY-MM-DD`; `parent_id <> id`; `number >= 1` |
| `issue_label` | none (M2M outside the entity) | materialized in read models of SPEC-030 |
| `issue_relation` | none | `issue_id <> related_issue_id`; `type` one of 4 |
| `comment` | none | `body` not empty (`validate_comment_body`) |
| `attachment` | none | `metadata` is opaque TEXT/JSON: the domain does not parse it |
| `issue_history` | none | `id` autoincremental integer; `field` column name or logical key |

Mapping invariants (mandatory for SPEC-030 and SPEC-040):

- `INTEGER` 0/1 ⇄ `bool` in both directions; never raw `0`/`1` in an entity.
- `TEXT` ISO-8601 with `Z` ⇄ aware `datetime` in UTC; naive is a parse error, the local TZ is not assumed.
- `TEXT 'YYYY-MM-DD'` ⇄ `datetime.date`; never `datetime` for `due_date`, `start_date`,
  `target_date`.
- `REAL` ⇄ `float`; an `INTEGER` in a `REAL` column is accepted and normalized to `float`.
- `NULL` ⇄ `None`; an empty string is **not** `None` (an empty title is a `Validation`, not a NULL).

## 4. Business rules

### 4.1 Invariants (R)

- **R1 — Title.** `title.strip()` not empty and `len <= 255`. The value that is persisted is the
  `strip()`ped one; the original with spaces is not stored.
- **R2 — Priority.** Integer in `[0, 4]`; the label is derived, never stored. Ascending order by
  `Priority.rank` (Urgent, High, Medium, Low, No priority).
- **R3 — Calendar date.** `due_date`, `start_date`, `target_date` are pure `date`s. The only
  format accepted at the edge is `YYYY-MM-DD` with two-digit month and day. A nonexistent date
  (`2026-02-30`) is `Validation`, never "corrected" to the last day of the month.
- **R4 — Team state.** A `state_id` is valid only if `workflow_state.team_id == issue.team_id`.
  The composite FK of the DDL is the safety net; the domain rejects it earlier with a useful message.
- **R5 — Closing with children.** An issue **cannot** move to a state of type `completed` or
  `canceled` if it has at least one open child (`parent_id = issue.id`). `open_children` is counted
  by the repo; the domain only decides.
- **R6 — Hierarchy.** `parent_id != id`; parent and child share `team_id`; the ancestor chain cannot
  contain cycles (`A → B → A`).
- **R7 — Relations.** There is no self-relation (`issue_id != related_issue_id`). `(issue_id,
  related_issue_id, type)` is not duplicated. For symmetric types (`duplicate`, `related`, `similar`)
  the inverted pair is not duplicated either.
- **R8 — Labels.** A label is usable if `team_id` is `NULL` (workspace) or equals the issue's team,
  and if `retired_at IS NULL`.
- **R9 — Archiving a team.** A team with open issues is not archived; those issues are first closed, moved or
  archived.
- **R10 — Explicit time.** No function reads the clock. Every `*_at` that the domain needs to
  compute enters as `now: datetime` (aware, UTC) from `app/`, which obtains it from `Clock`.
- **R11 — Import graph.** `domain/` only imports: stdlib (`enum`, `re`, `datetime`, `typing`,
  `collections.abc`) and `pydantic`. Forbidden: `sqlite3`, `fastapi`, `typer`, `click`, `jinja2`,
  `httpx`, `os`, `time`, `random`, `uuid`, `ulid`, `hifa.ports`, `hifa.adapters.*`, `hifa.app.*`.
- **R12 — Immutability.** Entities are `frozen`; `rules` functions do not mutate the lists,
  dicts or models they receive. Every "modification" returns a new value (`model_copy`).
- **R13 — Errors with a code.** Every rejection is a subclass of `HifaError` with a stable `code`. The
  domain never raises `ValueError`, `KeyError`, `AssertionError` nor `pydantic.ValidationError`
  toward the caller: it wraps them in `Validation` with `rule` and `field`.
- **R14 — No human-facing text in the message.** `message` is a short phrase in technical English
  (`"priority must be between 0 and 4"`); the UI text is assembled by the edge.

### 4.2 Functional requirements (REQ-F)

Each one is verifiable by a scenario of §7 and by a unit test with the same tag.

| ID | Requirement | Verification |
|---|---|---|
| **REQ-F020-01** | The 8 enums expose exactly the values of the `CHECK` of SPEC-010; `Priority.label` returns the 5 exact labels and `Priority.rank` orders 1,2,3,4,0 | `list(Priority)` == 0..4; `[p.label for p in Priority]` == `["No priority","Urgent","High","Medium","Low"]`; `[p.rank for p in Priority]` == `[5,1,2,3,4]` |
| **REQ-F020-02** | `format`/`parse` are inverses for every `(valid key, number 1..999999999)`; `parse` rejects lowercase, extra hyphens and leading zeros | property test with 1000 cases + table of invalid ones |
| **REQ-F020-03** | `between` resolves the 4 cases (`None/None`, `None/next`, `prev/None`, `prev/next`) with the values of §2.4 | exact numeric asserts |
| **REQ-F020-04** | `between` raises `Validation(RANKING_INVALID_RANGE)` if `next <= prev` and `Validation(RANKING_EXHAUSTED)` if the gap is smaller than `2*MIN_GAP`; `needs_rebalance` detects the case; `rebalance` returns spaced positions in the same order | 3 scenarios + order test |
| **REQ-F020-05** | Every entity is `frozen` and `extra="forbid"`: assigning a field or passing a nonexistent field fails | `pytest.raises` in both cases |
| **REQ-F020-06** | `Issue` has 31 fields and each one corresponds to a column of `issue`; `IssueView` adds exactly `priority_label`, `is_open` and `visible`, and `Issue.is_visible` remains a property (not a field) with no name collision | test comparing `Issue.model_fields` with `PRAGMA table_info(issue)` + test verifying `IssueView.model_fields` and that there is no shadowing `UserWarning` |
| **REQ-F020-07** | `validate_title`, `validate_comment_body`, `validate_priority`, `validate_due_date`, `validate_estimate`, `validate_date_range` accept/reject according to R1-R3 and the scales of §2.1 | table of valid/invalid cases |
| **REQ-F020-08** | `validate_state_team` rejects a state from another team with `rule='STATE_TEAM_MISMATCH'` and `field='state_id'` | 1 test |
| **REQ-F020-09** | `validate_close`/`validate_transition` reject closing with `open_children > 0` with `rule='CLOSE_WITH_OPEN_CHILDREN'`; they allow closing with `open_children == 0` | 2 tests |
| **REQ-F020-10** | `validate_parent` rejects self-parent, parent from another team and a cycle in the ancestor chain (including a 3+ level cycle) | 3 tests |
| **REQ-F020-11** | `validate_relation` rejects self-relation (`Validation`), exact duplicate and symmetric duplicate (`Conflict`) | 3 tests |
| **REQ-F020-12** | `validate_label_scope` accepts workspace labels and own-team labels; rejects a label from another team and a retired label | 4 tests |
| **REQ-F020-13** | `validate_archive_team` rejects archiving a team with `open_issues > 0` and one already archived | 2 tests |
| **REQ-F020-14** | The 5 exceptions respect the hierarchy, expose their attributes and `to_dict()` returns `{"error": {"code", "message", "details"}}` | structure test + `isinstance` test |
| **REQ-F020-15** | No `domain/` module imports what R11 forbids nor calls `now`, `today`, `time`, `uuid4`, `random` | AST test that walks the 6 files |
| **REQ-F020-16** | No function of `rules`, `ranking` or `identifier` mutates its arguments or global state; two calls with the same input give the same output | double call + copy comparison |
| **REQ-F020-17** | `validate_date_range` rejects `target < start` in `project` and in `cycle` (`ends_at <= starts_at`) | 2 tests |
| **REQ-F020-18** | Every DDL table with a writer has a model: `team_membership` → `TeamMembership` and `team_previous_key` → `TeamPreviousKey`, with field names identical to the columns; `schema_migration` is left out by explicit decision (§10) | test comparing `model_fields` with `PRAGMA table_info` of both tables |
| **REQ-F020-19** | `validate_transition` rejects a target state with non-null `archived_at` with `Validation(rule='STATE_ARCHIVED', field='state_id')`, and accepts the same non-archived state type | 2 tests |

### 4.3 Non-functional requirements (REQ-N)

- **REQ-N020-01** — Line coverage of `src/hifa/domain/` ≥ **95 %**, and 100 % of the branches of
  `between`, `parse` and `validate_close` (they are the ones that decide money).
- **REQ-N020-02** — 100,000 calls to `ranking.between` in a loop < **100 ms** (p50 of the reference
  host); none allocates lists or creates Pydantic objects.
- **REQ-N020-03** — `import hifa.domain.entities` < **30 ms** warm and does not drag `pydantic`
  beyond what `pydantic.BaseModel` already imports.

## 5. Flow

The domain does not orchestrate; it is composed in the following order. Any use case of SPEC-040 performs
these steps inside its transaction, never before opening it.

```
1. Edge (CLI/API) normalizes strings: key -> identifier.normalize_key; priority "High" -> Priority.HIGH;
   due_date "2026-11-30" -> rules.validate_due_date -> date(2026,11,30).
2. app/ opens the transaction (SPEC-030) and reads what the rule needs:
   team, target workflow_state, label(s), parent issue, open_children, ancestors.
3. rules.validate_* in cheap->expensive order (no IO, so the order only matters for the
   error message the user sees):
     a. validate_title / validate_comment_body
     b. validate_priority / validate_estimate / validate_due_date / validate_date_range
     c. validate_state_team
     d. validate_label_scope (for each label)
     e. validate_parent (if parent_id comes in)
     f. validate_relation (if it is a relation)
     g. validate_close / validate_transition (if the target state is terminal)
4. identifier.format(team.key, number) with the number the repo already reserved (SPEC-030 §2.5 SQL-1).
5. The entity is built: Issue(..., created_at=now, updated_at=now) with now = clock.now().
6. The repo inserts; the domain does not participate again.
```

Sequence of a valid close, in domain terms:

```
Given issue ENG-42 in state.type=started, open_children=2
move_issue(-> completed)
  -> validate_transition(current=started, target=completed, open_children=2)
  -> Validation(rule='CLOSE_WITH_OPEN_CHILDREN', details={'open_children': 2})
  (nothing is written; app's transaction rolls back)
```

Sequence of a reorder:

```
between(prev=1024.0, next=2048.0) -> 1536.0
between(prev=1536.0, next=1536.0000005) -> Validation(RANKING_EXHAUSTED)
  -> app/ reads the group's positions, rebalance(...) and rewrites the group in the same transaction
```

## 6. Errors

`domain` defines the code; the mapping to exit code and HTTP status lives in `cli/errors.py` and
`api/errors.py` (rule A7) and **must** be exactly this table.

| Domain error | typical `code` | When | CLI | HTTP |
|---|---|---|---|---|
| `Validation` | `VALIDATION` | invalid input (empty title, priority 7, malformed `due_date`) | exit 2, stderr | 422 |
| `Validation` | `CLOSE_WITH_OPEN_CHILDREN` | closing an issue with open sub-issues | exit 2 | 422 |
| `Validation` | `STATE_TEAM_MISMATCH` / `LABEL_TEAM_MISMATCH` / `PARENT_TEAM_MISMATCH` | cross-team reference | exit 2 | 422 |
| `Validation` | `PARENT_CYCLE` / `RELATION_SELF` / `PARENT_SELF` | relation with itself or hierarchy cycle | exit 2 | 422 |
| `Validation` | `RANKING_EXHAUSTED` | no float gap between two positions | exit 2 | 422 |
| `Validation` | `STATE_ARCHIVED` | move/create against a `workflow_state` with `archived_at` | exit 2 | 422 |
| `NotFound` | `NOT_FOUND` | nonexistent id/identifier | exit 3 | 404 |
| `Conflict` | `CONFLICT` | uniqueness (`identifier`, `(team_id, number)`, duplicate label) | exit 4 | 409 |
| `Conflict` | `RELATION_DUPLICATE` / `TEAM_ALREADY_ARCHIVED` | logical duplicate | exit 4 | 409 |
| `InvalidTransition` | `INVALID_TRANSITION` | not raised by any function of this layer: the single producer is the use-case layer, which raises it for a write on an issue with `archived_at` not null (SPEC-040 §6, R20; D-042); the class exists so that condition maps to exit 5 of D-014 | exit 5 | 409 |
| `HifaError` (base) | `HIFA_ERROR` | bug: it should never escape; the edge treats it as 500 | exit 6 | 500 |

`exit 1` does **not** appear in this table: it is reserved for `hifa doctor` (warn) by D-001/D-014. Every mapping
bug or unclassified error is `6`.

Exact shape of the payload, identical in CLI `--json` and in the API:

```json
{"error": {"code": "CLOSE_WITH_OPEN_CHILDREN",
           "message": "cannot close an issue with open sub-issues",
           "details": {"issue": "ENG-42", "open_children": 2}}}
```

## 7. Acceptance Criteria

```gherkin
@SPEC-020
Feature: Pure domain core

  @REQ-F020-01
  Scenario: The enums expose the canonical values of the schema
    Given the hifa.domain.enums module imported
    When Priority, WorkflowStateType, RelationType, LabelGroupType, ProjectStatusType, EstimationType, TeamVisibility and MilestoneStatus are enumerated
    Then Priority has the values 0, 1, 2, 3 and 4 in that order
    And the labels of Priority are "No priority", "Urgent", "High", "Medium" and "Low"
    And the ranks of Priority are 5, 1, 2, 3 and 4
    And WorkflowStateType serializes to "triage", "backlog", "unstarted", "started", "completed", "canceled"
    And RelationType serializes to "blocks", "duplicate", "related", "similar"
    And EstimationType serializes to "notUsed", "exponential", "fibonacci", "linear", "tShirt"

  @REQ-F020-02
  Scenario: identifier format and parse are inverses
    Given the pair ("ENG", 123)
    When identifier.format("ENG", 123) is run
    Then the result is "ENG-123"
    And identifier.parse("ENG-123") returns ("ENG", 123)

  @REQ-F020-02
  Scenario: identifier rejects non-canonical forms
    Given the strings "eng-123", "ENG-0123", "ENG--1", "ENG_123", "1ENG-2" and "ENG-0"
    When identifier.parse is run on each one
    Then each call raises Validation with field "identifier"

  @REQ-F020-03
  Scenario: between resolves the four extremes
    Given the ranking with INITIAL_GAP 1024.0
    When between(None, None), between(None, 2048.0), between(1024.0, None) and between(1024.0, 2048.0) are called
    Then the results are 1024.0, 1024.0, 2048.0 and 1536.0

  @REQ-F020-04
  Scenario: between detects an invalid range
    Given the positions prev 2048.0 and next 1024.0
    When between(prev, next) is called
    Then it raises Validation with rule "RANKING_INVALID_RANGE"

  @REQ-F020-04
  Scenario: between detects an exhausted gap
    Given the positions prev 1536.0 and next 1536.0000005
    When between(prev, next) is called
    Then it raises Validation with rule "RANKING_EXHAUSTED"

  @REQ-F020-04
  Scenario: needs_rebalance detects the degraded group
    Given the positions [1024.0, 1024.0000001, 3072.0]
    When needs_rebalance is called with the group
    Then it returns True

  @REQ-F020-04
  Scenario: rebalance repairs the degraded group
    Given the positions [1024.0, 1024.0000001, 3072.0]
    When rebalance is called with the group
    Then it returns [1024.0, 2048.0, 3072.0]
    And needs_rebalance on the result returns False

  @REQ-F020-05
  Scenario: entities are immutable
    Given an Issue built with title "Fix login"
    When assigning issue.title = "Other" is attempted
    Then a Pydantic validation error is raised
    And the title is still "Fix login"

  @REQ-F020-05
  Scenario: a field outside the DDL is rejected
    Given a Team built with all its valid fields
    When the field story_points is added to the constructor
    Then construction fails because of extra="forbid"

  @REQ-F020-06
  Scenario: Issue mirrors exactly the columns of the issue table
    Given the database migrated to user_version 1
    When Issue.model_fields is compared with PRAGMA table_info(issue)
    Then the 31 names match one to one
    And IssueView adds exactly priority_label, is_open and visible
    And no attribute shadowing warning is emitted when importing entities

  @REQ-F020-07
  Scenario: an empty title is rejected
    Given the string "   "
    When rules.validate_title is called
    Then it raises Validation with field "title" and rule "TITLE_EMPTY"

  @REQ-F020-07
  Scenario: a nonexistent due_date is rejected without being corrected
    Given the string "2026-02-30"
    When rules.validate_due_date is called
    Then it raises Validation with field "due_date"
    And no substitute date is returned

  @REQ-F020-07
  Scenario: an estimate outside the fibonacci scale is rejected
    Given estimation fibonacci and the value 4
    When rules.validate_estimate(4, fibonacci) is called
    Then it raises Validation with rule "ESTIMATE_NOT_IN_SCALE"

  @REQ-F020-07
  Scenario: an estimate in a team with notUsed is rejected
    Given estimation notUsed and the value 3
    When rules.validate_estimate(3, notUsed) is called
    Then it raises Validation with rule "ESTIMATE_NOT_USED"

  @REQ-F020-08
  Scenario: a state from another team is rejected
    Given an issue of the ENG team and a workflow_state of the OPS team
    When rules.validate_state_team is called with that pair
    Then it raises Validation with rule "STATE_TEAM_MISMATCH" and field "state_id"

  @REQ-F020-09
  Scenario: an issue is not closed with open sub-issues
    Given an issue in state started with 2 open children
    When rules.validate_close(completed, 2) is called
    Then it raises Validation with rule "CLOSE_WITH_OPEN_CHILDREN"

  @REQ-F020-09
  Scenario: an issue is closed without open sub-issues
    Given an issue in state started with no open children
    When rules.validate_close(completed, 0) is called
    Then it raises no exception

  @REQ-F020-10
  Scenario: a hierarchy cycle is rejected
    Given the issues A, B and C with B.parent=A and C.parent=B
    When setting A.parent=C is attempted
    Then rules.validate_parent raises Validation with rule "PARENT_CYCLE"
    And the ancestor chain is not modified

  @REQ-F020-11
  Scenario: an issue is not related to itself
    Given the issue ENG-1
    When rules.validate_relation is called with equal issue_id and related_issue_id
    Then it raises Validation with rule "RELATION_SELF"

  @REQ-F020-11
  Scenario: a duplicated symmetric relation is a conflict
    Given the relation (ENG-1, ENG-2, related) already persisted
    When creating (ENG-2, ENG-1, related) is attempted
    Then it raises Conflict with rule "RELATION_DUPLICATE"

  @REQ-F020-12
  Scenario: a label from another team is rejected
    Given an issue of the ENG team and a label of the OPS team
    When rules.validate_label_scope is called
    Then it raises Validation with rule "LABEL_TEAM_MISMATCH"

  @REQ-F020-12
  Scenario: a workspace label is accepted in any team
    Given a label with team_id NULL
    When rules.validate_label_scope is called for the same issue
    Then it raises no exception

  @REQ-F020-13
  Scenario: a team with open issues is not archived
    Given the ENG team with 3 open issues
    When rules.validate_archive_team(team, 3) is called
    Then it raises Validation with rule "TEAM_HAS_OPEN_ISSUES"

  @REQ-F020-14
  Scenario: errors expose a code and serializable details
    Given a Validation with rule "TITLE_EMPTY", field "title" and value ""
    When to_dict() is called
    Then the result has the key "error"
    And error.code is "VALIDATION"
    And error.details.rule is "TITLE_EMPTY"
    And Validation is a subclass of HifaError

  @REQ-F020-15
  Scenario: the domain imports no infrastructure and reads no clock
    Given the six modules of src/hifa/domain
    When their AST is walked looking for forbidden imports and calls
    Then sqlite3, fastapi, typer, click, jinja2, httpx, os, time, random, uuid and ulid do not appear
    And no call to now, utcnow, today or time appears

  @REQ-F020-16
  Scenario: pure functions are deterministic and do not mutate inputs
    Given a list of positions [1024.0, 2048.0, 3072.0]
    When ranking.rebalance is called twice with the same list
    Then the two results are equal
    And the original list is still [1024.0, 2048.0, 3072.0]

  @REQ-F020-17
  Scenario: a cycle ending before it starts is rejected
    Given starts_at "2026-11-16" and ends_at "2026-11-09"
    When rules.validate_date_range is called with those dates
    Then it raises Validation with rule "DATE_RANGE_INVERTED"

  @REQ-F020-18
  Scenario: team_membership and team_previous_key have a model
    Given the database migrated to user_version 3
    When the model_fields of TeamMembership and TeamPreviousKey are compared with PRAGMA table_info
    Then the fields of TeamMembership are team_id, member_id, is_owner and sort_order
    And the fields of TeamPreviousKey are team_id, key and changed_at

  @REQ-F020-19
  Scenario: an archived state is not the target of a transition
    Given a workflow_state of the ENG team with non-null archived_at
    When rules.validate_transition is called with that state as target
    Then it raises Validation with rule "STATE_ARCHIVED" and field "state_id"

  @REQ-F020-19
  Scenario: a current state of the same type is a valid target
    Given a workflow_state of the ENG team of type started and archived_at NULL
    When rules.validate_transition is called with that state as target and 0 open children
    Then it raises no exception
```

## 8. Regeneration checklist

Executable from top to bottom; every step has a verification command.

1. Read `specs/000-system-architecture.md` (§2 A1-A8), `specs/010-data-model.md` (§3 DDL) and this
   spec in full. Do not read `src/hifa/adapters/**`: the domain must not depend on how data is stored.
2. Create the six empty files and verify that the package imports:
   ```bash
   mkdir -p src/hifa/domain tests/unit/domain
   for m in enums entities ranking identifier rules errors; do : > src/hifa/domain/$m.py; done
   printf '' > src/hifa/domain/__init__.py
   python -c "import hifa.domain"
   ```
3. Write `errors.py` first (everything else uses it), then `enums.py`, `identifier.py`,
   `ranking.py`, `entities.py` and finally `rules.py` (it depends on the previous five).
4. Verify the frozen contract before the tests:
   ```bash
   python scripts/spec_contract_check.py --spec specs/020-domain-core.md
   ```
   The check compares: names and values of the 8 enums, the 5 public functions of `identifier`,
   the 5 of `ranking`, the 13 of `rules`, the `errors` hierarchy and the `model_fields` of each
   entity against the tables of §2.
5. Write the tests with one file per module and one test per `REQ-F`:
   ```bash
   pytest tests/unit/domain -q
   pytest tests/unit/domain --cov=hifa.domain --cov-fail-under=95
   ```
6. Verify purity (R11, REQ-F020-15) with the AST check, which **cannot** be implemented in
   `domain/`:
   ```bash
   python scripts/spec_contract_check.py --spec specs/020-domain-core.md --check imports
   ```
7. Verify the layer graph: `lint-imports` green with the `domain` contract of `.importlinter`.
8. Repeat the cycle by deleting the component, to prove the spec is sufficient:
   ```bash
   git rm -r src/hifa/domain tests/unit/domain && pytest tests/unit/domain -q
   ```
   (the second command must fail because the module is absent; if it passes, there are tests coupled to another
   component and they are misplaced).

## 9. Provenance

- **`Priority` values (0-4) and their labels**: the `Priority` enum plus the textual descriptions
  ("No priority", "Urgent", "High", "Medium", "Low") the CLI prints for each value.
- **`state.type` with 6 values**: the type is the fixed semantic bucket that makes a custom workflow
  safe — a team renames and reorders columns, and `is_open` still has an answer.
- **`RelationType` (blocks | duplicate | related | similar) and `blocked_by` as a non-stored
  inverse**: `issue_relation.type` of SPEC-010; one row per relation, the inverse is read backwards.
- **`LabelGroupType`, `ProjectStatusType`, `EstimationType`, `TeamVisibility`, `MilestoneStatus`**:
  the enums of SPEC-010 §3 (`issue_label.group_type`, `project_status.type`,
  `team.issue_estimation_type`, `team.visibility`, `project_milestone.status`).
- **Fractional ranking with floats (`INITIAL_GAP=1024`, average on insert)**: the concrete values
  (1024 / `1e-6`) are the author's decision, 2026-11-08, so that ~20 successive insertions fit
  between two neighbours before any rebalancing.
- **Rule "do not close with open children"**: completing a parent that still has open sub-issues
  is refused; the author's decision to make it a hard validation instead of a warning, 2026-11-08.
- **Estimation scales** (`fibonacci` 0,1,2,3,5,8,13,21; `exponential` 1,2,4,8,16; `linear`
  0-100; `tShirt` 1-5): author's decision, 2026-11-08 — four scales, because a team that estimates in
  t-shirts and a team that estimates in story points never agree on a conversion.
- **Domain purity and `Clock` as a port**: rule A2 of SPEC-000 and the author's decision that
  time enters as an argument instead of monkeypatching `datetime`, 2026-11-08.

## 10. Out of scope

- Persistence, SQL and transactions (SPEC-030).
- Composition of use cases and invariants that require reading the database (SPEC-040).
- Parsing of user input (flags, JSON, forms): each edge validates its syntax and calls
  `rules` for semantics (SPEC-050/060/070).
- Markdown rendering of `description` and `body` (SPEC-070).
- Generation of ULIDs (`IdGen` port of `src/hifa/ports.py`, SPEC-030 §2.7) and of `branch_name`
  (rule of `app/issues.py`, SPEC-040 §4 R17).
- Rigid state machine: the domain allows any transition between types except R5.
- Migrations and schema evolution (SPEC-010).
- **Modeling `schema_migration`**: it is the migrator's internal ledger (`version`, `name`, `applied_at`,
  `checksum`), infrastructure and not domain. It has no entity, repo or use case, by design.

## Notes

- spec_revision 4 (2026-11-13): **`InvalidTransition` has a producer.** §6 stops calling the class
  reserved: no function of this layer raises it, and the single producer is the use-case layer, which
  raises it for a write on an issue with `archived_at` not null (SPEC-040 §6, R20; D-042). The class,
  its `code` and the exit 5 of D-014 stay as declared in §2.6.
- spec_revision 3 (2026-11-11): translated to English (repo-wide language convention).

`spec_revision 2` — gaps of Spec Review 02 applied to this spec (the rest are applied in 030/040):

| Gap | Change |
|---|---|
| 4 (blocking) | `IssueView.visible` instead of `is_visible`; `Issue.is_visible` stays as a property; mapping invariant declared (D-019) |
| 10 | `status: review` (matches `INDEX.md`) |
| 11 | IDs to the canonical format `REQ-F020-NN` / `REQ-N020-NN` (D-004/P-02), outside the range of SPEC-001 |
| 12 | `INITIAL_GAP`, `MIN_GAP`, `MAX_TITLE`, `MAX_BODY`, `MAX_DESCRIPTION` declared in the table of §2 |
| 13 | Exact signatures of `NotFound`, `Conflict`, `Validation`, `InvalidTransition` (§2.6) |
| 14 | `InvalidTransition` declared **reserved** (no producer); the base bug moves to exit 6 (D-014) |
| 15 | `validate_transition` documents and raises `Validation(rule='STATE_ARCHIVED', field='state_id')` |
| 16 | `TeamMembership` and `TeamPreviousKey` modeled; `schema_migration` declared outside the domain (§10) |
| 17 | Cross reference fixed: `SPEC-030 §2.5 (SQL-1)` |
| 27 | Five scenarios split in two; `Given` steps after `Then` removed |
| 28 | `branch_name` moves from §10 to SPEC-040 §4 R17 |

New requirements: `REQ-F020-18` (model coverage for `team_membership`/`team_previous_key`) and
`REQ-F020-19` (`STATE_ARCHIVED`).


