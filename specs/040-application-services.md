---
id: SPEC-040
title: Application services
component: application
status: review
version: 2
spec_revision: 7
depends_on: [SPEC-020, SPEC-030]
implements:
  - src/hifa/app/**
  - tests/integration/app/**
acceptance: specs/acceptance/040-application-services.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
---

# SPEC-040 — Application services

## 1. Responsibility and boundaries

**Does:** composes the use cases. It receives an already parsed command, opens **one** transaction, reads what
the rule needs, calls the domain validations, writes entity + relations + history
in that same transaction and returns the resulting entity. It is the layer where the **composite
invariants** live: the ones that need to combine a pure rule (SPEC-020) with a read (SPEC-030).

**Does NOT do (explicit):**

- **It knows nothing about HTTP or the terminal.** Zero `fastapi`, `typer`, `click`, `argparse`, `print`,
  `sys.stdout`, exit codes, HTTP status, ASCII tables or colors. The use cases return
  objects; formatting them belongs to `cli/output.py` (SPEC-050) and `api/schemas.py` (SPEC-060).
- **It does not write SQL.** Not a single statement: everything goes through repos and read models of SPEC-030. Forbidden:
  `sqlite3` and strings with `SELECT`/`INSERT`.
- **It does not validate input syntax.** It does not parse flags nor JSON: it receives `CreateIssue` already built. It does
  validate **semantics** (ranges, membership, cycles) by delegating to `domain.rules`.
- **It does not implement pure rules.** If a rule does not need to read anything, it lives in `domain/rules.py` and here
  it is only invoked. Duplicating a rule in `app/` is a bug.
- **It does not decide the ID format nor the clock.** `idgen.new()` and `clock.now()` enter through
  the `UnitOfWork` constructor; `app/` uses them, does not define them.
- **It does not open nested transactions.** One use case = one `uow.write()`; if it needs another, it is badly
  designed.
- **It does not cache nor remember state between calls.** Every use case is a stateless function.
- **It does not implement search nor import/export.** It declares the **signature** of `search_issues` (the port that
  060 and 070 consume) but its semantics, its envelope and its filters belong to SPEC-090 §2 (D-016); here they are neither
  redefined nor given extra parameters. `app/transfer.py` (import/export) belongs to SPEC-080 and does not appear in
  this contract.

## 2. Public contract

> Normative. Modules: `commands.py`, `issues.py`, `teams.py`, `labels.py`, `members.py`,
> `projects.py`, `cycles.py`, `boards.py`. Exact signatures; no `...` and no "etc.".
>
> **This §2 is the complete application port** (D-012). The three edges —CLI (SPEC-050), API
> (SPEC-060) and web (SPEC-070)— consume **these** functions and these dataclasses; no edge declares
> its own signatures nor writes two functions for the same mutation. A name that an edge uses and that is not
> here is edge drift (§2.3), unless it is a presentation helper, which by definition is not part of the
> port.

**Two forms of call, and only two:**

| Family | Signature | Transaction |
|---|---|---|
| Write | `def f(uow: UnitOfWork, cmd: X, actor: Actor) -> R` | exactly one `uow.write()` (R1) |
| Read | `def f(db: Database, ...) -> R` | none; `db.ro_connection()` or `uow.read()` |

No read receives `actor`: it does not write history nor need identity. No write receives
a `dict` of changes: the typed command is the only transport (R4).

| Symbol | Module | Type | Stable |
|---|---|---|---|
| `Actor`, `UNSET`, `Unset` | `commands` | dataclasses / sentinel | yes |
| `CreateIssue`, `UpdateIssue`, `AssignIssue`, `SetPriority`, `SetDueDate`, `MoveIssue`, `ReorderIssue`, `RebalanceBoard`, `ArchiveIssue`, `TrashIssue`, `RestoreIssue` | `commands` | dataclasses | yes |
| `AddLabel`, `RemoveLabel`, `RelateIssues`, `UnrelateIssues`, `CommentOnIssue`, `UpdateComment`, `ResolveComment`, `AddAttachment` | `commands` | dataclasses | yes |
| `CreateTeam`, `UpdateTeam`, `ArchiveTeam`, `CreateState`, `UpdateState` | `commands` | dataclasses | yes |
| `CreateLabel`, `UpdateLabel`, `RetireLabel` | `commands` | dataclasses | yes |
| `CreateMember`, `UpdateMember`, `DeactivateMember` | `commands` | dataclasses | yes |
| `CreateProject`, `UpdateProject`, `ArchiveProject`, `CreateMilestone` | `commands` | dataclasses | yes |
| `CreateCycle`, `CompleteCycle`, `AddIssuesToCycle` | `commands` | dataclasses | yes |
| `IssuePage` | `boards` (re-exported from `reads`) | return dataclass, **single owner: SPEC-030 §2.6** | yes |
| `Board`, `BoardColumn` | `boards` | return dataclasses | yes |
| `create_issue`, `update_issue`, `assign_issue`, `set_priority`, `set_due_date`, `move_issue`, `reorder_issue`, `rebalance_board`, `archive_issue`, `trash_issue`, `restore_issue`, `relate_issues`, `unrelate_issues`, `comment_on_issue`, `update_comment`, `resolve_comment`, `add_attachment` | `issues` | write functions | yes |
| `add_label`, `remove_label` | `labels` | write functions | yes |
| `create_team`, `update_team`, `archive_team`, `create_state`, `update_state` | `teams` | write functions | yes |
| `create_label`, `update_label`, `retire_label` | `labels` | write functions | yes |
| `create_member`, `update_member`, `deactivate_member` | `members` | write functions | yes |
| `create_project`, `update_project`, `archive_project`, `add_milestone` | `projects` | write functions | yes |
| `create_cycle`, `complete_cycle`, `add_issues_to_cycle` | `cycles` | write functions | yes |
| `issue_detail`, `list_issues`, `list_board`, `agenda`, `search_issues`, `list_comments`, `list_attachments`, `list_members`, `member_detail`, `list_teams`, `team_detail`, `list_states`, `list_labels`, `label_detail`, `list_projects`, `project_detail`, `list_cycles`, `cycle_detail` | `boards` / `teams` / `labels` / `members` / `projects` / `cycles` | read functions | yes |

Read vocabulary, without exceptions: `list_<plural>` returns a tuple of summary rows;
`<singular>_detail` returns the detail read model. `issue_detail` is the only one with a name of its
own because D-019 fixes that name for the read model of SPEC-030.

### 2.1 `app/commands.py`

```python
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Literal, TypeVar

from hifa.domain.enums import EstimationType, Priority, ProjectStatusType, RelationType


class _Unset:
    """Sentinel for 'do not touch this field'. It is not None: None clears."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


Unset = _Unset
UNSET: Final = _Unset()
Maybe = TypeVar("Maybe")  # documentary alias: T | Unset


@dataclass(frozen=True, slots=True)
class Actor:
    member_id: str | None = None
    source: Literal["cli", "api", "web", "import"] = "cli"
    label: str | None = None  # display name for history; never persisted in issue

    def __post_init__(self) -> None:
        """source outside the Literal -> Validation(rule='ACTOR_SOURCE')."""


@dataclass(frozen=True, slots=True)
class CreateIssue:
    team: str  # ULID or key ("ENG")
    title: str
    description: str | None = None
    priority: Priority | int = Priority.NO_PRIORITY
    state: str | None = None  # name or ULID; None -> team's default/triage
    assignee_id: str | None = None
    project_id: str | None = None
    milestone_id: str | None = None
    cycle_id: str | None = None
    parent: str | None = None  # identifier or ULID
    labels: tuple[str, ...] = ()  # ULIDs or names, without duplicates
    estimate: float | None = None
    due_date: date | str | None = None
    board_order: float | None = None  # None -> last() of the board
    sort_order: float | None = None  # None -> last() of the backlog
    creator_id: str | None = None  # None -> actor.member_id


@dataclass(frozen=True, slots=True)
class UpdateIssue:
    issue: str
    title: str | Unset = UNSET
    description: str | None | Unset = UNSET  # None clears
    priority: Priority | int | Unset = UNSET
    state: str | Unset = UNSET  # goes through the same path as move_issue
    assignee_id: str | None | Unset = UNSET
    project_id: str | None | Unset = UNSET
    milestone_id: str | None | Unset = UNSET
    cycle_id: str | None | Unset = UNSET
    parent: str | None | Unset = UNSET
    estimate: float | None | Unset = UNSET
    due_date: date | str | None | Unset = UNSET


@dataclass(frozen=True, slots=True)
class MoveIssue:
    issue: str
    target_state: str | None = None  # None -> same state (reorder only)
    position: Literal["first", "last", "before", "after"] = "last"
    before_id: str | None = None  # mandatory if position == "before"
    after_id: str | None = None  # mandatory if position == "after"
    sort_order: float | None = None  # web edge shortcut: it is validated, not trusted (R8)
    rebalance: bool = False  # forces a column rebalance before positioning


@dataclass(frozen=True, slots=True)
class RebalanceBoard:
    team: str
    scope: Literal["board", "backlog", "sub"] = "board"
    state: str | None = None  # only with scope="board"; None -> all columns


@dataclass(frozen=True, slots=True)
class ReorderIssue:
    issue: str
    before: str | None = None  # identifier/ULID of the previous neighbor
    after: str | None = None  # identifier/ULID of the next neighbor
    scope: Literal["backlog", "board", "sub"] = "backlog"


@dataclass(frozen=True, slots=True)
class ArchiveIssue:
    issue: str


@dataclass(frozen=True, slots=True)
class TrashIssue:
    issue: str


@dataclass(frozen=True, slots=True)
class RestoreIssue:
    issue: str


@dataclass(frozen=True, slots=True)
class AddLabel:
    issue: str
    label: str  # ULID or name
    create_if_missing: bool = False
    color: str | None = None  # only if create_if_missing


@dataclass(frozen=True, slots=True)
class RemoveLabel:
    issue: str
    label: str


@dataclass(frozen=True, slots=True)
class RelateIssues:
    issue: str
    related: str
    type: str | RelationType = RelationType.RELATED  # accepts 'blocked_by' and inverts it


@dataclass(frozen=True, slots=True)
class UnrelateIssues:
    issue: str
    related: str
    type: str | RelationType | None = None  # None -> any between the pair


@dataclass(frozen=True, slots=True)
class CommentOnIssue:
    issue: str
    body: str
    parent_id: str | None = None
    author_id: str | None = None  # None -> actor.member_id


@dataclass(frozen=True, slots=True)
class ResolveComment:
    comment_id: str


@dataclass(frozen=True, slots=True)
class CreateTeam:
    key: str
    name: str
    icon: str | None = None
    color: str | None = None
    description: str | None = None
    timezone: str = "America/Santiago"
    visibility: str = "public"
    estimation_type: EstimationType | str = EstimationType.FIBONACCI
    cycles_enabled: bool = False
    cycle_duration: int = 2
    cycle_start_day: int = 1
    triage_enabled: bool = False
    default_state: str | None = None  # name; None -> "Backlog"


@dataclass(frozen=True, slots=True)
class UpdateTeam:
    team: str
    name: str | Unset = UNSET
    icon: str | None | Unset = UNSET
    color: str | None | Unset = UNSET
    description: str | None | Unset = UNSET
    timezone: str | Unset = UNSET
    estimation_type: EstimationType | str | Unset = UNSET
    cycles_enabled: bool | Unset = UNSET
    cycle_duration: int | Unset = UNSET
    cycle_start_day: int | Unset = UNSET


@dataclass(frozen=True, slots=True)
class ArchiveTeam:
    team: str


@dataclass(frozen=True, slots=True)
class CreateProject:
    name: str
    team: str | None = None  # None -> cross-team project
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    status_type: ProjectStatusType | str = ProjectStatusType.PLANNED
    priority: Priority | int = Priority.NO_PRIORITY
    lead_id: str | None = None
    start_date: date | str | None = None
    target_date: date | str | None = None


@dataclass(frozen=True, slots=True)
class UpdateProject:
    project: str
    name: str | Unset = UNSET
    description: str | None | Unset = UNSET
    status_type: ProjectStatusType | str | Unset = UNSET
    priority: Priority | int | Unset = UNSET
    lead_id: str | None | Unset = UNSET
    start_date: date | str | None | Unset = UNSET
    target_date: date | str | None | Unset = UNSET


@dataclass(frozen=True, slots=True)
class ArchiveProject:
    project: str


@dataclass(frozen=True, slots=True)
class CreateMilestone:
    project: str
    name: str
    target_date: date | str | None = None


@dataclass(frozen=True, slots=True)
class CreateCycle:
    team: str
    starts_at: datetime | str  # ISO-8601; normalized to UTC
    ends_at: datetime | str | None = None
    duration_weeks: int | None = None  # alternative to ends_at; exactly one of the two
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteCycle:
    cycle: str


@dataclass(frozen=True, slots=True)
class AddIssuesToCycle:
    cycle: str
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssignIssue:
    issue: str
    member_id: str | None  # None -> unassign (NULL, never "")


@dataclass(frozen=True, slots=True)
class SetPriority:
    issue: str
    priority: Priority | int


@dataclass(frozen=True, slots=True)
class SetDueDate:
    issue: str
    due_date: date | str | None  # None -> no date


@dataclass(frozen=True, slots=True)
class UpdateComment:
    comment_id: str
    body: str


@dataclass(frozen=True, slots=True)
class AddAttachment:
    issue: str
    url: str
    title: str | None = None
    metadata: dict[str, object] | None = None  # serialized as JSON in `attachment.metadata`


@dataclass(frozen=True, slots=True)
class CreateMember:
    name: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class UpdateMember:
    member: str
    name: str | Unset = UNSET
    email: str | Unset = UNSET
    display_name: str | None | Unset = UNSET
    avatar_url: str | None | Unset = UNSET


@dataclass(frozen=True, slots=True)
class DeactivateMember:
    member: str


@dataclass(frozen=True, slots=True)
class CreateLabel:
    name: str
    team: str | None = None  # None -> workspace label (visible in all)
    color: str | None = None
    description: str | None = None
    parent: str | None = None  # ULID or name of the group label
    is_group: bool = False


@dataclass(frozen=True, slots=True)
class UpdateLabel:
    label: str
    name: str | Unset = UNSET
    color: str | None | Unset = UNSET
    description: str | None | Unset = UNSET


@dataclass(frozen=True, slots=True)
class RetireLabel:
    label: str  # `retired_at = now`; it does not delete the row (D-015)


@dataclass(frozen=True, slots=True)
class CreateState:
    team: str
    name: str
    state_type: str  # triage|backlog|unstarted|started|completed|canceled
    color: str | None = None
    position: float | None = None  # None -> last of the team


@dataclass(frozen=True, slots=True)
class UpdateState:
    state: str
    name: str | Unset = UNSET
    color: str | None | Unset = UNSET
    position: float | None | Unset = UNSET
```

Command validation (`__post_init__`, identical in all):

1. It accepts the declared type **or its primitive**: `Priority | int`, `EstimationType | str`,
   `ProjectStatusType | str`, `RelationType | str`, `date | "YYYY-MM-DD"`, `datetime | ISO-8601`.
   It normalizes with `object.__setattr__` (the dataclass is frozen) and leaves the strong type in the field.
2. A primitive that does not convert ⇒ `Validation(rule='COMMAND_FIELD', field=<field name>)`.
3. It does not validate membership in the database (that needs the transaction). It only validates shape and local
   combinations: `MoveIssue` requires `before_id` if `position == "before"` and `after_id` if
   `position == "after"`, and rejects both anchors with `first`/`last`; `CreateCycle` requires exactly
   one of `ends_at`/`duration_weeks`; `AddLabel` requires `color` if `create_if_missing`;
   `CreateLabel` requires `parent` if `is_group`; `AddAttachment.url` must be `http`/`https`/`file`;
   `SetDueDate.due_date` accepts `YYYY-MM-DD` or `None`; `RebalanceBoard.state` is only admitted with
   `scope="board"`.
4. `title`/`body`/`name`/`url` are validated with `rules.validate_title` / `validate_comment_body` /
   `validate_attachment_url` in the **use case**, not in the command, so that the message includes the
   resource.

### 2.2 Use cases — signatures

Every **write** has the form `(uow: UnitOfWork, cmd: X, actor: Actor) -> R` and opens
**exactly one** `uow.write()` (R1). Every **read** has the form `(db: Database, ...) -> R`, does not
receive `actor` and does not open a write transaction: it uses `db.ro_connection()` and, if it chains several
queries that must see the same snapshot, `uow.read()`.

In the signatures, `# raises:` enumerates the domain errors the function can raise (the classes and
their `rule`/`code` are in §6). What is not listed is not raised: an undeclared `Validation` is a
bug of the implementation, not a variant.

**Status of the team writes.** `create_team` is implemented (`src/hifa/app/teams.py`). `update_team`
and `archive_team` are **declared, not implemented**: they are deferred together (D-031) because they
are the same "mutate an existing team" family and because `--undo` at the edge cannot be built before
`update_team` exists. The signatures below are the contract for the revision that lands them, and the
`# raises:` line of `archive_team` stays normative *for that function* — which is why the
`TEAM_HAS_OPEN_ISSUES` (exit 2) and `TEAM_ALREADY_ARCHIVED` (exit 4) rows of §6 are untouched. Those
codes were never the contract of an existing command: SPEC-050 §2.5.2 declares no `team archive` row
until the use case exists (D-031), so no edge publishes a behaviour that contradicts this table.

```python
# src/hifa/app/issues.py  -- writes


def create_issue(uow: UnitOfWork, cmd: CreateIssue, actor: Actor) -> Issue: ...


# raises: NotFound(team|parent|label|project|milestone|cycle), Validation, Conflict(UNIQUE)


def update_issue(uow: UnitOfWork, cmd: UpdateIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue|state|member|project|milestone|cycle|parent), Validation, InvalidTransition


def assign_issue(uow: UnitOfWork, cmd: AssignIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue|member), Validation(MEMBER_INACTIVE|ISSUE_TRASHED)


def set_priority(uow: UnitOfWork, cmd: SetPriority, actor: Actor) -> Issue: ...


# raises: NotFound(issue), Validation(PRIORITY_RANGE)


def set_due_date(uow: UnitOfWork, cmd: SetDueDate, actor: Actor) -> Issue: ...


# raises: NotFound(issue), Validation(DUE_DATE_FORMAT)


def move_issue(uow: UnitOfWork, cmd: MoveIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue|workflow_state|neighbor), Validation, InvalidTransition


def reorder_issue(uow: UnitOfWork, cmd: ReorderIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue|neighbor), Validation(POSITION_NEIGHBOR|POSITION_OUT_OF_ORDER)


def rebalance_board(uow: UnitOfWork, cmd: RebalanceBoard, actor: Actor) -> RebalanceReport: ...


# raises: NotFound(team|workflow_state), Validation(RANKING_DEGRADED)


def archive_issue(uow: UnitOfWork, cmd: ArchiveIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue), Validation(CLOSE_WITH_OPEN_CHILDREN|ISSUE_TRASHED)


def trash_issue(uow: UnitOfWork, cmd: TrashIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue)


def restore_issue(uow: UnitOfWork, cmd: RestoreIssue, actor: Actor) -> Issue: ...


# raises: NotFound(issue)


def add_label(uow: UnitOfWork, cmd: AddLabel, actor: Actor) -> Issue: ...


# raises: NotFound(issue|label), Validation(LABEL_TEAM_MISMATCH|LABEL_RETIRED)


def remove_label(uow: UnitOfWork, cmd: RemoveLabel, actor: Actor) -> Issue: ...


# raises: NotFound(issue|label|label_link)


def relate_issues(uow: UnitOfWork, cmd: RelateIssues, actor: Actor) -> IssueRelation: ...


# raises: NotFound(issue), Validation(RELATION_SELF), Conflict(RELATION_DUPLICATE)


def unrelate_issues(uow: UnitOfWork, cmd: UnrelateIssues, actor: Actor) -> None: ...


# raises: NotFound(issue|relation)


def comment_on_issue(uow: UnitOfWork, cmd: CommentOnIssue, actor: Actor) -> Comment: ...


# raises: NotFound(issue|comment), Validation(BODY_EMPTY|COMMENT_PARENT_MISMATCH)


def update_comment(uow: UnitOfWork, cmd: UpdateComment, actor: Actor) -> Comment: ...


# raises: NotFound(comment), Validation(BODY_EMPTY)


def resolve_comment(uow: UnitOfWork, cmd: ResolveComment, actor: Actor) -> Comment: ...


# raises: NotFound(comment)


def add_attachment(uow: UnitOfWork, cmd: AddAttachment, actor: Actor) -> Attachment: ...


# raises: NotFound(issue), Validation(ATTACHMENT_URL_INVALID|ATTACHMENT_URL_SCHEME)

# src/hifa/app/teams.py  -- writes


def create_team(uow: UnitOfWork, cmd: CreateTeam, actor: Actor) -> Team: ...


# raises: Validation(TEAM_KEY_INVALID|TEAM_WITHOUT_STATES), Conflict(UNIQUE_TEAM_KEY)


def update_team(uow: UnitOfWork, cmd: UpdateTeam, actor: Actor) -> Team: ...


# raises: NotFound(team), Validation(TEAM_KEY_INVALID), Conflict(UNIQUE_TEAM_KEY)


def archive_team(uow: UnitOfWork, cmd: ArchiveTeam, actor: Actor) -> Team: ...


# raises: NotFound(team), Validation(TEAM_HAS_OPEN_ISSUES), Conflict(TEAM_ALREADY_ARCHIVED)
# status: declared, not implemented — deferred with update_team (D-031)


def create_state(uow: UnitOfWork, cmd: CreateState, actor: Actor) -> WorkflowState: ...


# raises: NotFound(team), Validation(STATE_TYPE_INVALID|STATE_NAME_EMPTY), Conflict(UNIQUE_STATE_NAME)


def update_state(uow: UnitOfWork, cmd: UpdateState, actor: Actor) -> WorkflowState: ...


# raises: NotFound(state), Validation(STATE_TYPE_INVALID), Conflict(UNIQUE_STATE_NAME)

# src/hifa/app/labels.py  -- writes


def create_label(uow: UnitOfWork, cmd: CreateLabel, actor: Actor) -> Label: ...


# raises: NotFound(team|label), Validation(LABEL_GROUP_INVALID|COLOR_FORMAT), Conflict(UNIQUE_LABEL_NAME)


def update_label(uow: UnitOfWork, cmd: UpdateLabel, actor: Actor) -> Label: ...


# raises: NotFound(label), Validation(COLOR_FORMAT), Conflict(UNIQUE_LABEL_NAME)


def retire_label(uow: UnitOfWork, cmd: RetireLabel, actor: Actor) -> Label: ...


# raises: NotFound(label), Conflict(LABEL_RETIRED)

# src/hifa/app/members.py  -- writes


def create_member(uow: UnitOfWork, cmd: CreateMember, actor: Actor) -> Member: ...


# raises: Validation(MEMBER_EMAIL_INVALID|MEMBER_NAME_EMPTY), Conflict(UNIQUE_MEMBER_EMAIL)


def update_member(uow: UnitOfWork, cmd: UpdateMember, actor: Actor) -> Member: ...


# raises: NotFound(member), Validation(MEMBER_EMAIL_INVALID), Conflict(UNIQUE_MEMBER_EMAIL)


def deactivate_member(uow: UnitOfWork, cmd: DeactivateMember, actor: Actor) -> Member: ...


# raises: NotFound(member), Conflict(MEMBER_ALREADY_INACTIVE)

# src/hifa/app/projects.py  -- writes


def create_project(uow: UnitOfWork, cmd: CreateProject, actor: Actor) -> Project: ...


# raises: NotFound(team|member), Validation(PROJECT_TEAM_MISMATCH|DATE_RANGE_INVERTED)


def update_project(uow: UnitOfWork, cmd: UpdateProject, actor: Actor) -> Project: ...


# raises: NotFound(project|team|member), Validation(DATE_RANGE_INVERTED)


def archive_project(uow: UnitOfWork, cmd: ArchiveProject, actor: Actor) -> Project: ...


# raises: NotFound(project)


def add_milestone(uow: UnitOfWork, cmd: CreateMilestone, actor: Actor) -> ProjectMilestone: ...


# raises: NotFound(project), Validation(MILESTONE_PROJECT_MISMATCH|DATE_RANGE_INVERTED), Conflict(DUPLICATE_MILESTONE)

# src/hifa/app/cycles.py  -- writes


def create_cycle(uow: UnitOfWork, cmd: CreateCycle, actor: Actor) -> Cycle: ...


# raises: NotFound(team), Validation(DATE_RANGE_INVERTED), Conflict(DUPLICATE_CYCLE_NUMBER)


def complete_cycle(uow: UnitOfWork, cmd: CompleteCycle, actor: Actor) -> Cycle: ...


# raises: NotFound(cycle), Conflict(CYCLE_ALREADY_COMPLETED)


def add_issues_to_cycle(
    uow: UnitOfWork, cmd: AddIssuesToCycle, actor: Actor
) -> tuple[Issue, ...]: ...


# raises: NotFound(cycle|issue), Validation(CYCLE_COMPLETED|CYCLE_TEAM_MISMATCH)

# src/hifa/app/boards.py  -- reads, no writes

# What a full detail page shows. A public constant, NOT the default of `include`: the row/card
# fragments call `issue_detail(db, ref)` with no `include` and must not pay for them; the detail page
# and the panel pass this constant, because both render the comments (D-027, D-029).
DETAIL_INCLUDE: frozenset[str] = frozenset({"comments", "attachments", "history"})


def require_team(db: Database, ref: str) -> Team: ...


# raises: NotFound(team, key=ref). Accepts key (case-insensitive) or ULID.


def resolve_filters(
    db: Database,
    team: str,
    *,
    state: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
) -> dict[str, str | None]: ...


# returns {"state_id", "assignee_id", "label_id"}: name/key/ULID -> id. An unknown value resolves
# to None (filter dropped); it does NOT raise, so a stale bookmark renders an unfiltered view (D-027).
# raises: NotFound(team).


def board_counts(db: Database, team: str) -> dict[str, int]: ...


# {"open", "urgent", "total"} of one team, one aggregate query; raises: NotFound(team).


def issue_detail(
    db: Database, ref: str, *, include: frozenset[str] = frozenset()
) -> IssueDetail: ...


# raises: NotFound(issue); include ⊆ {comments, relations, attachments, history, children} -> Validation(INCLUDE_INVALID)


def list_issues(db: Database, filters: ListFilters) -> IssuePage: ...


# raises: NotFound(team), Validation(INVALID_CURSOR|LIMIT_RANGE|ORDER_INVALID)


def list_board(
    db: Database,
    *,
    team: str,
    filters: ListFilters | None = None,
    group_by: Literal["state", "assignee", "priority", "project"] = "state",
    limit_per_group: int = 50,
) -> Board: ...


# raises: NotFound(team), Validation(LIMIT_RANGE|ORDER_INVALID)
#
# This signature is the contract (D-027). Filters — `q` included (D-025) — travel inside ONE
# `ListFilters`; six loose kwargs (`state`, `assignee`, `label`, `cycle`, `project`, `q`) are NOT
# the contract and no edge may reintroduce them. `order`, `cursor` and `offset` are ignored by the
# board: the column order is `board_order`, not the listing's sort key (D-011).


def agenda(
    db: Database,
    *,
    start: date,
    days: int = 7,
    team: str | None = None,
    include_overdue: bool = True,
) -> tuple[AgendaEntry, ...]: ...


# raises: NotFound(team), Validation(DATE_RANGE_INVERTED)


def search_issues(db: Database, query: SearchQuery) -> SearchPage: ...


# raises: Validation(QUERY_EMPTY); semantics from SPEC-090 §2 (D-016), not redefined here


def list_comments(
    db: Database, ref: str, *, include_resolved: bool = True
) -> tuple[Comment, ...]: ...


# raises: NotFound(issue)


def list_attachments(db: Database, ref: str) -> tuple[Attachment, ...]: ...


# raises: NotFound(issue)

# src/hifa/app/teams.py  -- reads


def list_teams(db: Database, *, include_archived: bool = False) -> tuple[Team, ...]: ...
def team_detail(db: Database, ref: str) -> TeamDetail: ...  # raises: NotFound(team)
def list_states(
    db: Database, *, team: str, include_archived: bool = False
) -> tuple[WorkflowState, ...]: ...


# raises: NotFound(team)

# src/hifa/app/labels.py  -- reads


def list_labels(
    db: Database, *, team: str | None = None, include_retired: bool = False
) -> tuple[Label, ...]: ...
# raises: NotFound(team)
def label_detail(db: Database, ref: str) -> Label: ...  # raises: NotFound(label)


# src/hifa/app/members.py  -- reads


def list_members(db: Database, *, include_inactive: bool = False) -> tuple[Member, ...]: ...
def member_detail(db: Database, ref: str) -> Member: ...  # raises: NotFound(member)


# src/hifa/app/projects.py  -- reads


def list_projects(
    db: Database,
    *,
    team: str | None = None,
    status_type: str | None = None,
    include_archived: bool = False,
) -> tuple[Project, ...]: ...
# raises: NotFound(team)
def project_detail(db: Database, ref: str) -> Project: ...  # raises: NotFound(project)


# src/hifa/app/cycles.py  -- reads


def list_cycles(
    db: Database, *, team: str | None = None, include_completed: bool = True
) -> tuple[Cycle, ...]: ...
# raises: NotFound(team)
def cycle_detail(db: Database, ref: str) -> Cycle: ...  # raises: NotFound(cycle)
```

`Database` is the connection source of SPEC-030 §2.1; `UnitOfWork` and `Clock`/`IdGen` are from
SPEC-030 §2.2/§2.7 (D-017: `ports.py` is not declared here). `ListFilters`, `IssueDetail`,
`IssueListItem`, `AgendaEntry`, `IssuePage`… the read models are from SPEC-030 §2.6; the return
types owned by this layer are only the **two** below.

`IssuePage` has **a single owner** (SPEC-030 §2.6, where it lives next to `ListFilters` and `list_view`).
This layer imports it and returns it; it does **not** redefine it: two dataclasses with the same shape are two
contracts and they diverge at the first edit.

**Pure helpers that this layer owns.** Every symbol that §5 invokes and that is not part of the SPEC-020 port
is declared here, with owner and signature; none is delegated to SPEC-020 (R17: the formula lives in the owner, not
in the consumer).

| Symbol | Module | Signature | Rule |
|---|---|---|---|
| `with_write_retry` | `app/retry.py` | `(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 0.05, retry_on: frozenset[str] = frozenset({'DB_BUSY'})) -> T` | D-018: the system's only retry; it re-executes the complete transaction |
| `slugify` | `app/rules.py` | `(text: str) -> str` | NFKD→ASCII, `[a-z0-9]` joined by `-`, capped at 60 (R17) |
| `branch_name` | `app/rules.py` | `(identifier: str, title: str) -> str` | R17: `f"{identifier.lower()}-{slugify(title)}"` |
| `apply_milestones` | `app/rules.py` | `(*, current: str, target: str, issue: Issue, now: datetime) -> dict[str, datetime \| None]` | R7bis; returns only the milestone columns that change |
| `exhausted` | `app/ranking.py` | `(column: Sequence[float]) -> bool` | `True` if the largest gap between neighbors is `< 1e-6` |
| `validate_attachment_url` | `app/validation.py` | `(url: str) -> str` | `http`/`https`/`file`; otherwise `Validation(rule='ATTACHMENT_URL_SCHEME')` or `ATTACHMENT_URL_INVALID` |

`with_write_retry` is the **only** retry loop (D-018). `UnitOfWork.write()` of SPEC-030 does not
retry —it cannot: the body of the `with` already ran— so the owner of the retry is the use
case, which can call it again:

```python
# src/hifa/app/retry.py
def with_write_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.05,
    retry_on: frozenset[str] = frozenset({"DB_BUSY"}),
) -> T:
    """Runs `fn` (which opens its own `uow.write()`) up to `attempts` times.
    It only retries a `Conflict` whose `code`/`rule` is in `retry_on`; every attempt is a
    new transaction (the `ROLLBACK` of the previous one already happened). Once the attempts are exhausted, it propagates."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Conflict as exc:
            if (exc.code or exc.rule) not in retry_on or attempt == attempts:
                raise
            time.sleep(base_delay * 2 ** (attempt - 1))  # 50/100/200 ms
    raise AssertionError("unreachable")
```

All write use cases are invoked wrapped (`return with_write_retry(lambda: _impl(...))`);
`create_issue` additionally passes `retry_on={'DB_BUSY', 'DUPLICATE_NUMBER'}` (SPEC-030 SQL-1). No use
case writes its own retry `for`.

```python
# src/hifa/app/boards.py  -- return dataclasses owned by the application layer
from hifa.adapters.sqlite.reads import IssuePage  # single owner: SPEC-030 §2.6


@dataclass(frozen=True, slots=True)
class BoardColumn:
    state_id: str
    name: str
    type: str
    position: float
    items: tuple[IssueListItem, ...]
    count: int  # group total, not len(items)


@dataclass(frozen=True, slots=True)
class Board:
    team_id: str
    group_by: str
    columns: tuple[BoardColumn, ...]
    has_more: bool  # some group was trimmed by limit_per_group
```

Return conventions, mandatory:

- Writes return the **final state** of the affected entity (after the commit), not the
  command nor a boolean. `unrelate_issues` returns `None`; if the relation did not exist,
  `NotFound(resource='relation')`.
- Reads return tuples (immutable) and **never** generators: the edge can iterate them twice
  (count and render) without consuming them.
- `issue_detail` accepts `ENG-123` or a ULID; `team_detail`, `project_detail`, `cycle_detail`,
  `label_detail` and `member_detail` accept a ULID or name/key depending on the resource, and raise
  `NotFound(resource=..., key=...)` with the `key` as it arrived.
- `list_issues` resolves `filters.team_id` by ULID **or** key: if it is not a 26-character ULID, it
  treats it as a key, resolves it with `TeamRepo.get_by_key` and replaces the filter with the ULID before
  calling `reads.list_view` (which only compares against `issue.team_id`).
- `list_board` and `agenda` resolve `team` by ULID or key with the same rule.
- No read sorts by `set`/`dict` without an explicit key (R16) nor does `COUNT(*)` to paginate.

### 2.3 Reconciliation with the edges

This spec is the source of truth for `hifa.app` (D-012): the edge **builds the command** and calls the
function. The following table is the record of the names the edges used in the previous round and
their real contract. A name from the left column that survives in 050/060/070 is edge drift:
it is fixed there, not here.

| Name in the edge | 040 contract | Resolution |
|---|---|---|
| `get_issue(db, ref)` | `issue_detail(db, ref, *, include=...)` | rename in the edge |
| `get_board(...)` | `list_board(db, *, team, filters, group_by, limit_per_group)` | rename in the edge |
| `get_agenda(...)` | `agenda(db, *, start, days, team, include_overdue)` | rename in the edge |
| `assign_issue(uow, *, ref, member)` | `assign_issue(uow, AssignIssue(issue=ref, member_id=member), actor)` | aligned |
| `set_priority` / `set_due_date` | `set_priority` / `set_due_date` with `SetPriority` / `SetDueDate` | aligned |
| `rebalance` / `rebalance_board(team, state)` | `rebalance_board(uow, RebalanceBoard(team=..., scope=..., state=...), actor)` | aligned |
| `move_issue(uow, *, ref, state, after, before, sort_order)` | `move_issue(uow, MoveIssue(issue=ref, target_state=state, position="before"\|"after", before_id=..., after_id=..., sort_order=...), actor)` | map in the edge (§5.2) |
| `create_relation` / `delete_relation` | `relate_issues` / `unrelate_issues` | aliases are **not** added: two write functions for the same table would duplicate validations (R1) |
| `add_comment` | `comment_on_issue(uow, CommentOnIssue(issue=..., body=..., parent_id=...), actor)` | rename in the edge |
| `update_comment` | `update_comment(uow, UpdateComment(comment_id=..., body=...), actor)` | aligned |
| `add_labels` / `remove_labels` (plural) | `add_label` / `remove_label`, one call per label; the edge iterates (GAP-05) | rename in the edge |
| `delete_issue` / `--hard` | does not exist: `trash_issue` (soft) and `archive_issue` (D-015) | remove from the edge |
| `list_issues(uow, *, filters, sort, cursor) -> Page[Issue]` | `list_issues(db, ListFilters(limit, cursor, order, include_archived)) -> IssuePage` | aligned (D-011) |
| `search_issues(q, ...)` | `search_issues(db, SearchQuery)`; the envelope belongs to SPEC-090 (D-016) | aligned |
| `bootstrap_workspace(...)` | not from this spec: `app/teams.py::bootstrap(...)` is from SPEC-100 §2.5 | no change |

**Edge names that are NOT part of the port.** They appear as `fn(` in 050/060/070 and are not added to
§2.2 because they are not use cases: they are presentation, infrastructure or stdlib helpers.
Adding them here would be moving edge logic into the application layer.

| Name | Where | Why it is not part of the port |
|---|---|---|
| `base64url` | 050, 060 | cursor codec, not an operation: `encode_cursor`/`decode_cursor` live in the keyset of SPEC-030 (D-011); 040 only transports the opaque string |
| `render`, `to_json`, `board_vm`, `snippet` | 050, 060, 070 | formatting and view models: `cli/output.py`, `api/schemas.py`, `web/readmodels.py` (D-028) |
| `basename`, `parse_query`, `fetch` | 050, 070 | CLI/JS helpers (paths and query string), they do not touch the database |
| `create_app` | 060 | ASGI factory of SPEC-060 |
| `bootstrap` | 050 | workspace startup, SPEC-100 §2.5 |
| `doctor`, `migrate`, `reindex`, `resolve_config` | 050 | `doctor`/`reindex` are from SPEC-100 §2.6, `migrate` from SPEC-030 §2.3, `resolve_config` from SPEC-100 §2.1 |
| `min`, `len`, `default`, `fractional`, `http`, `write`, `alert`, `select`, `trim`, `length` | 070, 050, 060 | stdlib, Jinja filters, UI JS and `CHECK` fragments quoted from the schema |
| `build_board_vm`, `build_list_vm`, `build_issue_vm`, `render_markdown` | 070 | view models and web markdown (`web/readmodels.py`, `web/markdown.py`); `app/` does not format |
| `slug` | 060 | schema comment (`display_name ⇒ slug(name)`): the pure function is `domain.rules.slugify`, not a use case |
| `get_uow`, `connect` | 060 | FastAPI dependencies and connection opening (`api/deps.py`), edge infrastructure |
| `_one_anchor`, `model_validator`, `model_dump` | 060 | Pydantic internals in the input schema |
| `issue_history` | 050 | **table** name quoted in prose (`issue_history(field="state_id")`), not a function |
| `use_case` | 050 | generic marker in an example (`result = use_case(...)`), not a real symbol |

Closing rule: `scripts/spec_contract_check.py` (SPEC-000 §6) compares the `def`s of §2.2 against the
names invoked by the three edges and fails if a port name does not exist here or if an edge
declares its own signature.

## 3. Data it touches

### 3.1 Transactional unit per use case

Every row is **one** transaction (`uow.write()`). What is in "writes" is committed together or nothing is
committed at all. What is in "reads" happens inside the same transaction, after
the write lock of the UoW (SPEC-030 §2.2), so that the decision and the write see the same state.

| Use case | Writes (same tx) | Reads before writing |
|---|---|---|
| `create_issue` | `issue` (1 INSERT), `issue_label` (N), `issue_history` (`created`) | `team`, `workspace`, `workflow_state`, `label`×N, `project`, `project_milestone`, `cycle`, `issue` (parent), board/backlog positions |
| `update_issue` | `issue` (1 UPDATE), `issue_history` (1 row per distinct field) | `issue`, `team`, `workflow_state`, `member`, `project`, `cycle`, `issue` (parent) + `ancestor_chain` |
| `move_issue` | `issue` (`state_id` + milestones + `updated_at`), `issue_history` | `issue`, target `workflow_state`, `count_open_children`, positions of the target state |
| `reorder_issue` | `issue` (1 position UPDATE) or `issue`×N if there is a rebalance, `issue_history` | `positions` of the scope, neighbors |
| `archive_issue` / `trash_issue` / `restore_issue` | `issue` (`archived_at`/`trashed`), `issue_history` | `issue`, `count_open_children` (only to archive a parent) |
| `add_label` / `remove_label` | `issue_label` (1 INSERT OR IGNORE / DELETE), `issue_history` | `issue`, `label` |
| `relate_issues` | `issue_relation` (1 INSERT), `issue_history` (`relation`) | both `issue`s, existing relations of the pair |
| `unrelate_issues` | `issue_relation` (1 DELETE), `issue_history` | both `issue`s, existing relation |
| `comment_on_issue` | `comment` (1 INSERT) | `issue`, `comment` (parent) |
| `resolve_comment` | `comment` (`resolved_at`), `issue_history`? **no** | `comment`, `issue` |
| `create_team` | `workspace` (if it does not exist), `team`, `workflow_state`×6 | `workspace`, `team` (key collision) |
| `update_team` | `team` (1 UPDATE) | `team`, `workspace` |
| `archive_team` | `team` (`archived_at`) | `team`, `count_open_issues` |
| `create_project` | `project` (1 INSERT) | `team` (if `team` is not None), `workspace` |
| `update_project` | `project` (1 UPDATE) | `project`, `team` |
| `archive_project` | `project` (`archived_at`) | `project` |
| `add_milestone` | `project_milestone` (1 INSERT) | `project` |
| `create_cycle` | `cycle` (1 INSERT) | `team`, `next_number`, last cycle |
| `complete_cycle` | `cycle` (`completed_at`) | `cycle`, open issues of the cycle (only for the message) |
| `add_issues_to_cycle` | `issue`×N (`cycle_id`), `issue_history`×N | `cycle`, `issue`×N |
| `assign_issue` / `set_priority` / `set_due_date` | `issue` (1 UPDATE), `issue_history` (1 row) | `issue`, `member` (only `assign_issue`) |
| `rebalance_board` | `issue`×N (`board_order` or `sort_order`), `issue_history`×N | `team`, `workflow_state`, `positions` of the scope |
| `update_comment` | `comment` (`body`, `edited_at`) | `comment`, `issue` |
| `add_attachment` | `attachment` (1 INSERT) | `issue` |
| `create_member` | `member` (1 INSERT), `team_membership` (N) | `workspace`, `member` (email collision) |
| `update_member` / `deactivate_member` | `member` (1 UPDATE) | `member`, `workspace` |
| `create_label` / `update_label` / `retire_label` | `label` (1 INSERT/UPDATE) | `team`, `label` (parent group), team name |
| `create_state` / `update_state` | `workflow_state` (1 INSERT/UPDATE) | `team`, `workflow_state` of the team |

None of this writes `issue_fts` (the triggers of SPEC-090/010 do) nor `v_*` (they are views). The only
use case that writes `attachment` is `add_attachment`, and only INSERT: there is no UPDATE nor DELETE of
attachments in v1.

### 3.2 Invariants: where each one lives

The rule is explicit because it is the source of most duplications: **if it does not need to read,
it lives in `domain`; if it needs to read, it lives in `app` and uses the domain function for the judgment.**

| # | Invariant | Lives in | Why |
|---|---|---|---|
| I1 | Title not empty, priority 0-4, valid `due_date`, estimate scale | `domain.rules` | needs no IO |
| I2 | The state belongs to the issue's team | `domain.rules.validate_state_team` | the judge is pure; `app` reads the `state` |
| I3 | Do not close with open children | `domain.rules.validate_close` (judgment) + `app` (`count_open_children`) | the domain cannot count |
| I4 | No hierarchy cycles | `domain.rules.validate_parent` (judgment) + `app` (`ancestor_chain`) | the graph is read by the repo |
| I5 | Label from the correct team and not retired | `domain.rules.validate_label_scope` | the `label` is read by `app` |
| I6 | Relation without self-reference nor symmetric duplicate | `domain.rules.validate_relation` | existing relations are read by `app` |
| I7 | `project`/`milestone` compatible with the issue's team | **`app` only** | requires reading `project` and `project_milestone`; there is no pure rule that fits |
| I8 | `cycle` from the same team and not completed | **`app` only** | requires reading `cycle` |
| I9 | Unique `number`/`identifier` | `adapters` (SPEC-030 R2/SQL-1) | requires the write lock |
| I10 | Default state when none is specified | **`app` only** (`triage` if `triage_enabled`, else `is_default`, else `Backlog`) | requires reading the team's states |
| I11 | Rebalance when the fractional gap is exhausted | `domain.ranking.needs_rebalance/rebalance` (calculation) + `app` (decision and retry) | the group of positions is read by the repo |
| I12 | Field diff for history | **`app` only** | compares the before and after of the use case |
| I13 | A team with open issues is not archived | `domain.rules.validate_archive_team` | the count is read by `app` |
| I14 | `milestone` belongs to the issue's `project` | **`app` only** | requires reading the milestone |

## 4. Business rules

### 4.1 Invariants (R)

- **R1 — One transaction per use case.** Exactly one `with uow.write()`. If the use case
  needs two, it is redesigned or split into two commands.
- **R2 — Read inside, not before.** Every read a decision depends on happens inside the
  transaction. A `get` before the `with` and a `write` after is a race condition.
- **R3 — Mandatory history.** Every mutation of `issue` that changes a column records a row in
  `issue_history` per column, with `old_value`/`new_value` serialized by the repo (SPEC-030 §2.4) and
  `actor_id = actor.member_id`. Exception: `comment` does not generate history (the comment **is** the
  record) and neither does `resolve_comment`.
- **R4 — `UNSET` vs `None`.** In `Update*`, `UNSET` means "do not touch" and `None` means "set
  NULL". An absent field and a field at `None` are different operations and the history reflects it.
- **R5 — Default state.** Without `state`: if `team.triage_enabled` → the state of type `triage`;
  else the `is_default`; if there is none, the first `unstarted` state by `position`; if the team
  has no states, `Validation(rule='TEAM_WITHOUT_STATES')`.
- **R6 — Closing a parent (composite invariant, lives here).** `move_issue` and `update_issue` toward a
  terminal type, and `archive_issue` of an issue with children, execute: (1) `count_open_children(id)` in
  the same transaction, (2) `rules.validate_close(target_type, open_children)`. If there are open children,
  `Validation(rule='CLOSE_WITH_OPEN_CHILDREN')` and **nothing is written**. The judgment is pure (SPEC-020),
  the count is a read (SPEC-030) and the composition belongs to this layer: `issues.set_state` of SPEC-030
  validates nothing, it only persists the columns it receives.
- **R7 — Temporal milestones.** On entering `started`: `started_at = now` only if it was `NULL`.
  On entering `completed`: `completed_at = now`, `canceled_at = NULL`.
  On entering `canceled`: `canceled_at = now`, `completed_at = NULL`.
  On returning to an open type: `completed_at = NULL`, `canceled_at = NULL`, `started_at` is kept
  (it is a historical milestone, not the current state).
- **R8 — Position.** `position="first"` ⇒ `between(None, first)`; `"last"` ⇒
  `between(last, None)`; `"before"` ⇒ `between(previous_to(before_id), position(before_id))`;
  `"after"` ⇒ `between(position(after_id), next_to(after_id))`. The anchor (`before_id`/`after_id`)
  must be in the same team and in the target state, otherwise `Validation(rule='POSITION_NEIGHBOR')`.
  `cmd.sort_order` (web edge shortcut) is **not written as is**: it is accepted only if it is strictly
  greater than the previous neighbor's position and strictly less than the next one's; otherwise,
  `Validation(rule='POSITION_OUT_OF_ORDER')`. `rebalance=True` forces R9 before computing the position.
  The `between` calculation belongs to `domain.ranking`; this layer only reads the neighbors and decides.
- **R9 — Rebalance (the decision lives here).** Three triggers, a single mechanism:
  1. `between` returns a gap `< 1e-6` (the float no longer distinguishes the neighbors) ⇒ **automatic**
     rebalance, without error: the scope's positions are read, `ranking.rebalance` rewrites them
     with multiples of 1024 (1024, 2048, …) in the **same** transaction and `between` is recomputed.
  2. `between` raises `RANKING_EXHAUSTED` ⇒ same rebalance and one retry.
  3. `cmd.rebalance=True` (web edge, REQ-F070-06) ⇒ rebalance before computing the position.
  If after the rebalance the gap is still `< 1e-6`, or the second `between` fails again, it is
  `HifaError(code='RANKING_DEGRADED')`: a column with more than ~2²⁰ elements is not a use case, it is
  a caller bug (D-014 ⇒ 6/500). A duplicated or `NULL` position is never written.
- **R7bis — Milestones on create.** `create_issue` applies R7 with `current=None`: if the resolved state is of
  type `started`, `started_at = now`; if it is `completed`/`canceled`, the corresponding milestone. It is the
  same milestone table, not a second rule.
- **R10 — Idempotent labels.** `add_label` of an already present label is not an error and generates no history;
  `remove_label` of an absent one is `NotFound(resource='label_link')`.
- **R11 — Directed relations.** `blocks` is persisted **once**, in the direction
  `(issue blocks related)`. `blocked_by` is accepted as input and inverted before writing. The
  symmetric types (`related`, `similar`, `duplicate`) are persisted in the order they arrive and are
  checked in both directions (SPEC-020 REQ-F020-11).
- **R12 — Archiving does not delete.** `archive_issue` sets `archived_at`; `trash_issue` sets `trashed=1`;
  neither deletes rows. `restore_issue` clears both.
- **R13 — Actor.** `actor.member_id` is written to `issue_history.actor_id` and to `comment.author_id`
  when the command does not bring one. `actor` is never written to `issue`.
- **R14 — No formatting.** No use case prints, formats or serializes. Forbidden: `print`,
  `json.dumps`, `str.format` for output and access to `sys.stdout`.
- **R15 — Domain errors only.** `app/` raises `NotFound`, `Conflict`, `Validation`,
  `InvalidTransition` or `HifaError`; never `ValueError`, `KeyError` nor `sqlite3.Error` (SPEC-030 §6
  translates them first).
- **R16 — Determinism per command.** The same command over the same state produces the same
  result (except IDs and `now`). Sorting by `set` or by `dict` without an explicit key is forbidden.
- **R17 — `branch_name` (the formula lives here; SPEC-020 §10 **consumes** it).**
  `branch_name = f"{identifier.lower()}-{rules.slugify(title)}"` where `rules.slugify` is a pure function
  of **`app/rules.py`, owner of the formula** (SPEC-020 §10 consumes it, does not define it: delegating it there would
  make it circular) = NFKD → ASCII, lowercase, everything
  that is not `[a-z0-9]` collapsed to `-`, trimmed to **60** characters at the last `-` and without a trailing `-`. It is computed in `create_issue` (always) and in `update_issue` **only** when `title` changes
  **and** the current value is `NULL` or equal to what the formula would produce with the previous title (that is:
  nobody edited it by hand). A manual `branch_name` is never regenerated, and the change is recorded in
  `issue_history` as one more column. `identifier` is stable, so the branch does not change when the
  issue moves state.
- **R18 — Paginated reads (D-011).** `list_issues` and `list_board` are the **only** paginated
  reads. `order ∈ {manual, created, updated, priority, due}` (default `manual`), with `sort_key`
  per order and final tie-break by `issue.id`; `limit` 1-200 (default 50) is validated here and not in the
  edge; `has_more` comes from asking for `limit + 1` (never `COUNT(*)`); the cursor is
  `base64url(sort_key + "|" + id)`, opaque, and is decoded with the codec of SPEC-030 —`app/` does not do
  `base64`—; an unreadable cursor or one from another `order` is `Validation(rule='INVALID_CURSOR')`. `NULL` goes to the
  end in both directions. `offset` does not exist in this layer: web pagination uses a cursor, and the cap
  500 of D-011 is applied in SPEC-070 before calling.
- **R19 — Read models without shadowing (D-019).** `IssueDetail`, `IssueListItem` and `AgendaEntry` are
  consumed as SPEC-030 declares them, including `visible` (not `is_visible`) and `is_open`; `app/` does not
  rename nor recompute those fields. `IssueRepo.positions` is invoked with `state_id` to read only the
  target column (drag & drop) and with `scope` for the backlog.

### 4.2 Functional requirements (REQ-F)

| ID | Requirement |
|---|---|
| **REQ-F040-01** | `create_issue` writes `issue` + `issue_label` + `issue_history` in a single transaction; if the history fails, the issue does not remain |
| **REQ-F040-02** | `create_issue` resolves the default state according to R5 and uses it when `cmd.state is None` |
| **REQ-F040-03** | `create_issue` validates team, state, labels, parent, project, milestone and cycle **before** inserting and writes nothing if any of them fails |
| **REQ-F040-04** | `update_issue` writes one history row per field actually changed and respects `UNSET` vs `None` |
| **REQ-F040-05** | `move_issue` applies R7 (milestones) and records `state_id` in the history with the previous state name and the new one |
| **REQ-F040-06** | `move_issue` computes the position with `ranking.between` for `first`/`last`/`before`/`after` and validates the neighbor |
| **REQ-F040-07** | `move_issue` toward a terminal state with open children raises `Validation(rule='CLOSE_WITH_OPEN_CHILDREN')` and does not modify the issue |
| **REQ-F040-08** | `reorder_issue` uses `between`; on `RANKING_EXHAUSTED` it rebalances the scope and retries once |
| **REQ-F040-09** | `add_label`/`remove_label` validate scope with `rules.validate_label_scope`, are idempotent on add and write history |
| **REQ-F040-10** | `relate_issues` normalizes `blocked_by` to inverted `blocks`, rejects self-relation and symmetric duplicate |
| **REQ-F040-11** | `unrelate_issues` deletes the relation and writes history; if it does not exist, `NotFound(resource='relation')` |
| **REQ-F040-12** | `comment_on_issue` validates `body`, requires `parent_id` to be a comment of the same issue and sets `author_id` from the actor |
| **REQ-F040-13** | `resolve_comment` sets `resolved_at` only once (idempotent) and returns the comment |
| **REQ-F040-14** | `create_team` creates the workspace if missing, inserts the team and its 6 default states, all in one transaction |
| **REQ-F040-15** | `archive_team` rejects with `Validation(rule='TEAM_HAS_OPEN_ISSUES')` if there are open issues |
| **REQ-F040-16** | `create_project`/`update_project`/`archive_project` write only `project` and validate `team` and the date range |
| **REQ-F040-17** | `add_milestone` requires the project to exist and validates `target_date`; the name is unique per project |
| **REQ-F040-18** | `create_cycle` computes `number = MAX+1`, derives `ends_at` from `duration_weeks` if needed and requires `ends_at > starts_at` |
| **REQ-F040-19** | `complete_cycle` sets `completed_at` once; `add_issues_to_cycle` rejects completed cycles and cycles from another team |
| **REQ-F040-20** | `archive_issue`/`trash_issue`/`restore_issue` are UPDATEs (SPEC-030 REQ-F030-21) and write history |
| **REQ-F040-21** | `actor.member_id` reaches `issue_history.actor_id` and `comment.author_id`; an `Actor` without a member leaves `NULL` |
| **REQ-F040-22** | Every `app/` error is a subclass of `HifaError` with a stable `code`; no `sqlite3.Error` nor `ValueError` escapes |
| **REQ-F040-23** | No use case prints nor serializes: there is no `print`, `sys.stdout`, `json.dumps` in `src/hifa/app/` |
| **REQ-F040-24** | `issue_detail`/`list_issues`/`list_board`/`agenda` resolve the team by ULID or key and do not open a write transaction |
| **REQ-F040-25** | `create_member`/`update_member`/`deactivate_member` write only `member` (plus `team_membership` on create) and map the duplicate email to `Conflict(rule='UNIQUE_MEMBER_EMAIL')`, not to `sqlite3.IntegrityError` |
| **REQ-F040-26** | `create_label`/`update_label`/`retire_label` and `create_state`/`update_state` validate scope (team, parent group, state type) and are the only creation paths: `add_label` does not create labels unless `create_if_missing=True` |
| **REQ-F040-27** | `add_attachment` validates the URL scheme (`http`/`https`/`file`) and serializes `metadata` as JSON; there is no use case for deleting attachments (D-015) |
| **REQ-F040-28** | `update_comment` changes `body` and `edited_at`, does not touch `resolved_at` nor `parent_id`, and does **not** write `issue_history` (R3); an empty body is `Validation(rule='BODY_EMPTY')`. `comment` has no soft delete in SPEC-010, so there is no "deleted comment" case to reject |
| **REQ-F040-29** | `list_issues` returns `IssuePage` with `has_more` computed by `limit + 1` and an opaque `next_cursor`; with `limit=200` and 10,000 issues it executes no `COUNT(*)` nor `OFFSET` |
| **REQ-F040-30** | `branch_name` is computed with R17 in `create_issue` and is regenerated only if the title changes and the previous value was derived; a manual `branch_name` survives any edit |

### 4.3 Non-functional requirements (REQ-N)

- **REQ-N040-01** — Full `create_issue` (resolve team + state + insert + labels + history) in
  **< 5 ms p95** on a database with 10,000 issues.
- **REQ-N040-02** — `move_issue` in **< 3 ms p95** (includes counting open children).
- **REQ-N040-03** — `update_issue` with 5 changed fields executes **≤ 6 statements** (1 read of the
  issue, 1 UPDATE, 1 history `executemany` and at most 3 reference reads).
- **REQ-N040-04** — No use case opens more than **1** write transaction; verified with the connection's
  `set_trace_callback` counting openings.

## 5. Flow

Three flows are normative because all three edges depend on their exact order: creation, movement
with fractional reordering and keyset listing. The other use cases follow the pattern of §5.1
(read → validate with `rules` → write → history → commit).

### 5.1 `create_issue` — step by step

```
 1. cmd already validated by its __post_init__ (normalized types; title not yet validated).
 2. with uow.write() as tx:                       # a single write transaction
 3.     ws = tx.teams.workspace()                    # 030 §2.4: single row of `workspace`
                                                     -> None => HifaError(code='NO_WORKSPACE')
        team = tx.teams.get_by_key(ws.id, cmd.team) or tx.teams.get(cmd.team)
        -> None => NotFound(resource='team', key=cmd.team)
        -> team.archived_at != None => Validation(rule='TEAM_ARCHIVED')
 4.     title = rules.validate_title(cmd.title)            # R1 of SPEC-020
 5.     priority = rules.validate_priority(cmd.priority)
 6.     due = rules.validate_due_date(cmd.due_date)
 7.     state = <R5: triage | is_default | first unstarted>
        rules.validate_state_team(team.id, state)          # defense in depth
 8.     estimate = rules.validate_estimate(cmd.estimate, team.issue_estimation_type)
 9.     parent = tx.issues.get_by_identifier(cmd.parent) or tx.issues.get(cmd.parent) if given
        rules.validate_parent(child_id=<new id>, child_team_id=team.id,
                              parent=parent, ancestors=tx.issues.ancestor_chain(parent.id))
10.     for each label: label = get_by_name / get  -> Validation(rule='LABEL_NOT_FOUND') if missing
        rules.validate_label_scope(team.id, label)          # includes label.retired_at
11.     project / milestone / cycle: same matrix as §3.1 (PROJECT_TEAM_MISMATCH,
        MILESTONE_PROJECT_MISMATCH, CYCLE_TEAM_MISMATCH, CYCLE_COMPLETED)
12.     number = tx.issues.next_number(team.id)             # SQL-1, under the lock; retries UNIQUE
13.     now = uow.clock.now(); new_id = uow.idgen.new()
        identifier = identifier.format(team.key, number)
        branch = rules.branch_name(identifier, title)       # R17
        backlog_positions = tx.issues.positions(team_id=team.id, scope="backlog")
        board_positions   = (tx.issues.positions(team_id=team.id, scope="board",
                                                 state_id=state.id) if board else backlog)
        issue = Issue(id=new_id, workspace_id=team.workspace_id, team_id=team.id,
                      number=number, identifier=identifier, title=title, description=cmd.description,
                      priority=priority, estimate=estimate, due_date=due, state_id=state.id,
                      assignee_id=cmd.assignee_id, creator_id=cmd.creator_id or actor.member_id,
                      project_id=..., milestone_id=..., cycle_id=..., parent_id=...,
                      sort_order=(cmd.sort_order if cmd.sort_order is not None
                                  else ranking.last(backlog_positions)),
                      board_order=(cmd.board_order if cmd.board_order is not None
                                  else ranking.last(board_positions)),
                      sub_issue_sort_order=ranking.last(parent positions) if parent,
                      branch_name=branch,
                      milestones per R7bis, created_at=now, updated_at=now)
14.     saved = tx.issues.insert(issue)                     # RETURNING *
15.     tx.issues.set_labels(saved.id, [l.id for l in labels])
16.     tx.history.record(saved.id, [("created", None, saved.identifier)], actor_id=actor.member_id,
                           now=now)
    # COMMIT on leaving the with; any exception up to here => total ROLLBACK
17. return saved
```

Details that are not obvious and are part of the contract:

- `idgen.new()` is called **once** per issue; the `number` does not come from the idgen.
- `sub_issue_sort_order` is computed only if there is a `parent_id`; otherwise it stays at its default `0`.
- Step 3 tolerates `cmd.team` being a ULID or a key: key is tried first and then id (never the other way around, because
  a key is never a valid ULID).
- If `cmd.labels` brings duplicates, they are deduplicated preserving the order before step 15 (the
  `INSERT OR IGNORE` does not fail, but the history and the label count would be incorrect).
- `assignee_id` is validated against `member.is_active` (step 11): an inactive member is
  `Validation(rule='MEMBER_INACTIVE')`, not a broken FK.
- The resolved state is read **inside** the transaction: two concurrent creations in the same team cannot
  compute the same `number` (serialized by the write lock + `UNIQUE(team_id, number)`).

### 5.2 `move_issue` — step by step (fractional position + edge payloads)

Payload that each edge builds, translated into the single command:

| Edge | Request | `MoveIssue` |
|---|---|---|
| CLI `issue move` | `--first` / `--last` / `--before ENG-2` / `--after ENG-2` | `position="first"\|"last"\|"before"\|"after"` + `before_id`/`after_id` |
| API `POST /issues/{ref}/move` | `{state, after?, before?, sort_order?}` (060 `IssueMove`) | `target_state=state`, `position="after"/"before"` according to the anchor, `sort_order` as is |
| Web `POST …/move` | `{state_id, sort_order, rebalance?}` (070 R9/R10) | `position="last"` + `sort_order`, `rebalance=true` if the client's gap was `< 1e-4` |

`before_id` and `after_id` are **reference anchors** (`ENG-123` or ULID), never indexes nor
numeric positions: the client does not send the whole column (070 R9) and the server does not trust a
`sort_order` without neighbors to validate it against.

```
 1. with uow.write() as tx:
 2.     issue = tx.issues.require(cmd.issue)         # ULID or ENG-123; NotFound if missing
        issue.trashed => Validation(rule='ISSUE_TRASHED')
 3.     target = cmd.target_state  and (tx.teams.get_state_by_name(issue.team_id, cmd.target_state)
                                       or tx.teams.get_state(cmd.target_state))
        target is None and cmd.target_state is not None
                => NotFound(resource='workflow_state', key=cmd.target_state)
        target is None and cmd.target_state is None => target = current state (reorder only)
 4.     rules.validate_state_team(issue.team_id, target)
 5.     open_children = tx.issues.count_open_children(issue.id)      # SQL-5, only if target is terminal
 6.     rules.validate_close(target.type, open_children)
                -> Validation(rule='CLOSE_WITH_OPEN_CHILDREN')  # R6; nothing is written
 7.     column = tx.issues.positions(team_id=issue.team_id, scope="board", state_id=target.id)
        # positions sorted by board_order; the issue itself is excluded from the computation
        if cmd.rebalance or (cmd.sort_order is None and ranking.exhausted(column)):
            column = _rebalance_column(tx, issue.team_id, target.id)      # R9, same transaction
 8.     according to cmd.position:
          first  -> between(None, column[0].board_order)
          last   -> between(column[-1].board_order, None)
          before -> neighbor = require(before_id); neighbor.state_id == target.id or POSITION_NEIGHBOR
                    between(previous_to(neighbor), neighbor.board_order)
          after  -> neighbor = require(after_id);  neighbor.state_id == target.id or POSITION_NEIGHBOR
                    between(neighbor.board_order, next_to(neighbor))
 9.     if cmd.sort_order is not None:                     # web edge shortcut: it is validated (R8)
            prev, nxt = neighbors_of(column, cmd.sort_order)
            not (prev.board_order < cmd.sort_order < nxt.board_order)
                    => Validation(rule='POSITION_OUT_OF_ORDER')
            new_pos = cmd.sort_order
        else:
            new_pos = between(...)                       # step 8
        if new_pos - lo < 1e-6 or hi - new_pos < 1e-6:  # float gap exhausted
            column = _rebalance_column(tx, issue.team_id, target.id); recompute new_pos (once)
            if the gap is still < 1e-6 => HifaError(code='RANKING_DEGRADED')   # 6/500
10.     now = uow.clock.now()
        milestones = rules.apply_milestones(current=issue.state_type, target=target.type, issue=issue, now=now)
        # 030 §2.4: `set_state` touches ONLY state_id + updated_at; the milestones go through `update(issue)`
        moved = tx.issues.set_state(issue.id, target.id, now=now)
        if milestones:
            moved = tx.issues.update(replace(moved, **milestones))
        tx.issues.set_positions(issue.id, board_order=new_pos, now=now)
11.     changes = [("state_id", issue.state_id, target.id)] if changed
                + [("board_order", issue.board_order, new_pos)] if changed
                + [("started_at"|"completed_at"|"canceled_at", old, new)] for each milestone
        tx.history.record(issue.id, changes, actor_id=actor.member_id, now=now)
12. return moved      # COMMIT on exit
```

Flow invariants:

- Steps 2-11 are **a single** transaction. Two concurrent `move_issue` calls on the same issue are
  serialized by the UoW write lock: the second reads the already updated `state_id` and its history
  records the real transition, not an invented one.
- `_rebalance_column` is a private function of `issues.py`: it reads the column, calls
  `ranking.rebalance(len)` and rewrites `board_order` with multiples of 1024 in the same transaction.
  It does not open its own transaction nor is it called from another module (`rebalance_board` is its public
  use case, for the web edge).
- The rebalance is **not** a visible error: if the gap was exhausted, the user sees the issue where they
  dropped it. It is only an error if after rebalancing there is still no gap (`RANKING_DEGRADED`, bug).
- Milestones are applied **after** validating the close and **before** writing: a rejected `move`
  does not leave a half-written `completed_at`.

### 5.3 `list_issues` — keyset with opaque cursor (D-011)

```
 1. f = filters                       # ListFilters from SPEC-030 §2.6: limit, cursor, order, include_archived
    limit = f.limit or 50; 1 <= limit <= 200 otherwise => Validation(rule='LIMIT_RANGE')
    order = f.order or "manual"; order not in {manual,created,updated,priority,due}
            => Validation(rule='ORDER_INVALID')
 2. tx = uow.read()                                # readonly repos, no BEGIN IMMEDIATE
    team_id = f.team_id
    ws_id = tx.teams.workspace().id                  # 030 §2.4
    if team_id is not a 26-char ULID: team = tx.teams.get_by_key(ws_id, team_id)
            -> None => NotFound(resource='team', key=team_id); team_id = team.id
 3. if f.cursor:
        (sort_key, last_id) = keyset.decode_cursor(f.cursor)     # base64url(sort_key|id), SPEC-030
        invalid shape or sort_key from another `order` => Validation(rule='INVALID_CURSOR')
 4. rows = reads.list_view(tx.connection,
                           ListFilters(team_id=team_id, state=..., assignee=..., label=...,
                                       priority=..., project=..., cycle=..., parent=...,
                                       include_archived=f.include_archived, order=order,
                                       cursor=..., limit=limit + 1))
        # exact signature from 030 §2.6: list_view(conn, filters: ListFilters) -> IssuePage
        # q is a title filter inside ListFilters (D-025): LIKE '%term%' over title, ESCAPE
        # SQL-4: ORDER BY <sort_key> <dir>, issue.id <dir>  with NULL at the end;
        # WHERE (sort_key, id) > (?, ?) — tuple comparison, no OFFSET
 5. has_more = len(rows) > limit; items = rows[:limit]
 6. next_cursor = keyset.encode_cursor(items[-1].sort_key, items[-1].id) if has_more else None
 7. return IssuePage(items=tuple(items), next_cursor=next_cursor, has_more=has_more)
```

Flow rules:

- The cursor is **opaque**: the edge transports it as a string and does not interpret it; `app/` does not do
  `base64` (the codec belongs to SPEC-030) and never returns the `sort_key` in clear.
- `limit + 1` is requested and then trimmed: `has_more` costs no `COUNT(*)` (D-011).
- The order is **total** (`sort_key`, `id`): without the `id` tie-break, two issues with the same priority
  can repeat or be lost between pages.
- `order="manual"` uses `sort_order` (backlog) —the same column as `reorder_issue`—, not
  `board_order`: the listing and the board are two different orders of the same data.
- `q` is a **title filter, not search**: workspace search is SPEC-090 / `search_issues` (D-025).
  `ListFilters.q` is passed through untouched to `reads.list_view`, which translates it to a
  `LIKE '%term%'` over `title` with the term's `%`/`_`/`\` escaped; this layer neither reinterprets it
  nor rejects it. The search grammar (`field:value`, negation, ranking) never enters this path.
- `list_board` reuses the same pipeline per column (`state_id`) and the same `has_more` per group.

## 6. Errors

`app/` does not translate to exit codes nor to HTTP (A7): it raises domain errors with stable `code` and `rule`.
Each edge maps them **by type**, not by message. The five buckets are those of D-014; `1` **does not
exist** in this layer (it is reserved for `hifa doctor`):

| Class | CLI exit | HTTP | Meaning |
|---|---|---|---|
| `Validation` | 2 | 422 | semantically invalid input; the user can fix it |
| `NotFound` | 3 | 404 | the referenced resource does not exist (or is not visible) |
| `Conflict` | 4 | 409 | uniqueness, state already applied, database busy or read-only |
| `InvalidTransition` | 5 | 409 | the state transition is not allowed |
| `HifaError` (internal) | 6 | 500 | broken program invariant, corrupt database or unusable environment |

`InvalidTransition` **has a single producer: this layer**. SPEC-020 exports it reserved —no domain
function raises it— and it is `move_issue`/`update_issue` that build it when the team's state
machine forbids the step. No other module raises it nor catches it as control flow.

Codes owned by the application layer (those of SPEC-020 §6 remain valid and are not repeated):

| `rule` / `code` | Class | When | Use case | CLI | HTTP |
|---|---|---|---|---|---|
| `TEAM_ARCHIVED` | `Validation` | operating on a team with `archived_at` | `create_issue`, `update_issue`, `create_state`, `create_label` | 2 | 422 |
| `TEAM_WITHOUT_STATES` | `Validation` | team without `workflow_state` and without a requested state | `create_issue`, `create_team` | 2 | 422 |
| `TEAM_HAS_OPEN_ISSUES` | `Validation` | archiving a team with open issues | `archive_team` | 2 | 422 |
| `TEAM_ALREADY_ARCHIVED` | `Conflict` | archiving twice | `archive_team` | 4 | 409 |
| `TEAM_KEY_INVALID` | `Validation` | key that does not match `identifier.KEY_PATTERN` | `create_team`, `update_team` | 2 | 422 |
| `ISSUE_TRASHED` | `Validation` | operating on an issue with `trashed = 1` | `update_issue`, `move_issue`, `assign_issue`, `add_label` | 2 | 422 |
| `CLOSE_WITH_OPEN_CHILDREN` | `Validation` | closing/canceling/archiving a parent with open sub-issues | `move_issue`, `update_issue`, `archive_issue` | 2 | 422 |
| `INVALID_TRANSITION` | `InvalidTransition` | transition that the team's state machine does not allow | `move_issue`, `update_issue` | 5 | 409 |
| `POSITION_NEIGHBOR` | `Validation` | nonexistent anchor, from another team or from another state | `move_issue`, `reorder_issue` | 2 | 422 |
| `POSITION_OUT_OF_ORDER` | `Validation` | edge `sort_order` does not fall strictly between its neighbors | `move_issue` | 2 | 422 |
| `RANKING_EXHAUSTED` | `Validation` | no float gap left even after rebalancing once | `move_issue`, `reorder_issue` | 2 | 422 |
| `RANKING_DEGRADED` | `HifaError` | second consecutive failure after rebalancing: bug | `move_issue`, `rebalance_board` | **6** | 500 |
| `INVALID_CURSOR` | `Validation` | unreadable, truncated cursor or one from another `order` | `list_issues`, `list_board` | 2 | 422 |
| `LIMIT_RANGE` | `Validation` | `limit` outside 1-200 | `list_issues`, `list_board` | 2 | 422 |
| `ORDER_INVALID` | `Validation` | `order` outside the D-011 vocabulary | `list_issues`, `list_board` | 2 | 422 |
| `INCLUDE_INVALID` | `Validation` | `include` with a key outside `{comments,relations,attachments,history,children}` | `issue_detail` | 2 | 422 |
| `LABEL_NOT_FOUND` | `Validation` | `add_label` with a nonexistent name and `create_if_missing=False` | `add_label` | 2 | 422 |
| `LABEL_TEAM_MISMATCH` | `Validation` | label from another team | `add_label`, `create_label` | 2 | 422 |
| `LABEL_RETIRED` | `Conflict` | using or retiring an already retired label | `add_label`, `retire_label` | 4 | 409 |
| `LABEL_GROUP_INVALID` | `Validation` | group with a parent, or `parent` that is not a group | `create_label` | 2 | 422 |
| `UNIQUE_LABEL_NAME` | `Conflict` | repeated `(workspace_id, team_id, name)` | `create_label`, `update_label` | 4 | 409 |
| `COLOR_FORMAT` | `Validation` | color outside `#RRGGBB` | `create_label`, `update_label` | 2 | 422 |
| `UNIQUE_STATE_NAME` | `Conflict` | repeated state `(team_id, name)` | `create_state`, `update_state` | 4 | 409 |
| `STATE_TYPE_INVALID` | `Validation` | `state_type` outside the six types | `create_state`, `update_state` | 2 | 422 |
| `STATE_NAME_EMPTY` | `Validation` | blank state name | `create_state` | 2 | 422 |
| `STATE_TEAM_MISMATCH` | `Validation` | state from another team | `create_issue`, `move_issue`, `update_issue` | 2 | 422 |
| `UNIQUE_MEMBER_EMAIL` | `Conflict` | email already registered | `create_member`, `update_member` | 4 | 409 |
| `MEMBER_EMAIL_INVALID` / `MEMBER_NAME_EMPTY` | `Validation` | email without `@` or blank name | `create_member`, `update_member` | 2 | 422 |
| `MEMBER_INACTIVE` | `Validation` | assigning to a member with `is_active = 0` | `assign_issue`, `create_issue` | 2 | 422 |
| `MEMBER_ALREADY_INACTIVE` | `Conflict` | deactivating twice | `deactivate_member` | 4 | 409 |
| `PRIORITY_RANGE` / `DUE_DATE_FORMAT` / `ESTIMATE_*` | `Validation` | range and format (SPEC-020) | `set_priority`, `set_due_date`, `create_issue`, `update_issue` | 2 | 422 |
| `COMMENT_PARENT_MISMATCH` | `Validation` | `parent_id` from another issue | `comment_on_issue` | 2 | 422 |
| `BODY_EMPTY` | `Validation` | empty comment or only spaces | `comment_on_issue`, `update_comment` | 2 | 422 |
| `ATTACHMENT_URL_INVALID` / `ATTACHMENT_URL_SCHEME` | `Validation` | empty URL or scheme outside `http/https/file` | `add_attachment` | 2 | 422 |
| `PROJECT_TEAM_MISMATCH` | `Validation` | project from another team (and not cross-team) | `create_issue`, `update_issue`, `create_project` | 2 | 422 |
| `MILESTONE_PROJECT_MISMATCH` | `Validation` | milestone from another project | `create_issue`, `update_issue`, `add_milestone` | 2 | 422 |
| `CYCLE_TEAM_MISMATCH` / `CYCLE_COMPLETED` | `Validation` | cycle from another team or already completed | `create_issue`, `update_issue`, `add_issues_to_cycle` | 2 | 422 |
| `CYCLE_ALREADY_COMPLETED` | `Conflict` | closing a cycle twice | `complete_cycle` | 4 | 409 |
| `DATE_RANGE_INVERTED` | `Validation` | `target < start`, `ends_at <= starts_at` | `create_project`, `add_milestone`, `create_cycle`, `agenda` | 2 | 422 |
| `DUPLICATE_MILESTONE` / `DUPLICATE_CYCLE_NUMBER` | `Conflict` | repeated milestone name or `(team_id, number)` | `add_milestone`, `create_cycle` | 4 | 409 |
| `RELATION_SELF` / `PARENT_CYCLE` / `PARENT_SELF` | `Validation` | invalid relation or hierarchy (SPEC-020) | `relate_issues`, `create_issue`, `update_issue` | 2 | 422 |
| `RELATION_DUPLICATE` | `Conflict` | relation already existing (includes the inverted pair) | `relate_issues` | 4 | 409 |
| `UNIQUE_IDENTIFIER` / `DUPLICATE_NUMBER` | `Conflict` | `identifier` / `(team_id, number)` collision | `create_issue` | 4 | 409 |
| `COMMAND_FIELD` / `ACTOR_SOURCE` | `Validation` | a command field or the actor's `source` does not convert | any use case (in `__post_init__`) | 2 | 422 |
| `DB_BUSY` / `DB_READONLY` | `Conflict` | SQLite busy or opened read-only (SPEC-030 §6) | any use case | 4 | 409 |
| `READ_ONLY_UOW` | `Conflict` | write on a read UoW (edge bug) | any write | 4 | 409 |
| `UOW_NESTED` / `HIFA_ERROR` | `HifaError` | nested transaction or broken invariant | any use case | **6** | 500 |
| `NOT_FOUND` | `NotFound` | nonexistent issue, team, state, label, label_link, member, project, milestone, cycle, comment, relation, attachment or neighbor | all | 3 | 404 |

**`q` is a title filter, not search: workspace search is SPEC-090 / `search_issues` (D-025).** With
`q` declared inside `ListFilters` (SPEC-030 §2.6) this layer has no filter left that it refuses to
translate, so the former rule that rejected `q` as an unsupported filter is **removed** from the
table and from the matrix below (D-025 records its name and its removal). A parameter that is not a
`ListFilters` field is not an application error: it is a shape error of the edge (unknown query string
/ CLI flag), rejected at the boundary with 422 / exit 2 before any use case runs (§5 step 1).

Use case → expected errors matrix (contract for the tests of §7):

| Use case | `NotFound` | `Validation` | `Conflict` | `InvalidTransition` |
|---|---|---|---|---|
| `create_issue` | team, parent, label, project, milestone, cycle, member | `TEAM_ARCHIVED`, `STATE_TEAM_MISMATCH`, `LABEL_TEAM_MISMATCH`, `PROJECT_TEAM_MISMATCH`, `MILESTONE_PROJECT_MISMATCH`, `CYCLE_COMPLETED`, `PARENT_CYCLE`, `MEMBER_INACTIVE`, `ESTIMATE_*` | `UNIQUE_IDENTIFIER`, `DUPLICATE_NUMBER` | — |
| `update_issue` | issue, state, project, milestone, cycle, parent, member | those of `create_issue` + `CLOSE_WITH_OPEN_CHILDREN`, `ISSUE_TRASHED` | — | `INVALID_TRANSITION` |
| `assign_issue` / `set_priority` / `set_due_date` | issue, member | `MEMBER_INACTIVE`, `ISSUE_TRASHED`, `PRIORITY_RANGE`, `DUE_DATE_FORMAT` | — | — |
| `move_issue` | issue, workflow_state, neighbor | `STATE_TEAM_MISMATCH`, `CLOSE_WITH_OPEN_CHILDREN`, `POSITION_NEIGHBOR`, `POSITION_OUT_OF_ORDER`, `RANKING_EXHAUSTED`, `ISSUE_TRASHED` | — | `INVALID_TRANSITION` |
| `reorder_issue` | issue, neighbor | `POSITION_NEIGHBOR`, `POSITION_OUT_OF_ORDER`, `RANKING_EXHAUSTED` | — | — |
| `rebalance_board` | team, workflow_state | — | — | — |
| `archive_issue` | issue | `CLOSE_WITH_OPEN_CHILDREN`, `ISSUE_TRASHED` | — | — |
| `trash_issue` / `restore_issue` | issue | — | — | — |
| `add_label` / `remove_label` | issue, label, label_link | `LABEL_TEAM_MISMATCH`, `LABEL_NOT_FOUND` | `LABEL_RETIRED` | — |
| `relate_issues` | issue (×2) | `RELATION_SELF` | `RELATION_DUPLICATE` | — |
| `unrelate_issues` | issue, relation | — | — | — |
| `comment_on_issue` | issue, comment (parent) | `BODY_EMPTY`, `COMMENT_PARENT_MISMATCH` | — | — |
| `update_comment` / `resolve_comment` | comment | `BODY_EMPTY` | — | — |
| `add_attachment` | issue | `ATTACHMENT_URL_INVALID`, `ATTACHMENT_URL_SCHEME` | — | — |
| `create_team` | — | `TEAM_KEY_INVALID` | `UNIQUE_TEAM_KEY` | — |
| `update_team` | team | `TEAM_KEY_INVALID` | `UNIQUE_TEAM_KEY` | — |
| `archive_team` | team | `TEAM_HAS_OPEN_ISSUES` | `TEAM_ALREADY_ARCHIVED` | — |
| `create_state` / `update_state` | team, state | `STATE_TYPE_INVALID`, `STATE_NAME_EMPTY`, `TEAM_ARCHIVED` | `UNIQUE_STATE_NAME` | — |
| `create_label` / `update_label` / `retire_label` | label, team | `LABEL_GROUP_INVALID`, `COLOR_FORMAT`, `TEAM_ARCHIVED` | `UNIQUE_LABEL_NAME`, `LABEL_RETIRED` | — |
| `create_member` / `update_member` / `deactivate_member` | member | `MEMBER_EMAIL_INVALID`, `MEMBER_NAME_EMPTY` | `UNIQUE_MEMBER_EMAIL`, `MEMBER_ALREADY_INACTIVE` | — |
| `create_project` | team, member | `DATE_RANGE_INVERTED`, `PROJECT_TEAM_MISMATCH` | — | — |
| `update_project` / `archive_project` | project, team, member | `DATE_RANGE_INVERTED` | — | — |
| `add_milestone` | project | `DATE_RANGE_INVERTED` | `DUPLICATE_MILESTONE` | — |
| `create_cycle` | team | `DATE_RANGE_INVERTED` | `DUPLICATE_CYCLE_NUMBER` | — |
| `complete_cycle` | cycle | — | `CYCLE_ALREADY_COMPLETED` | — |
| `add_issues_to_cycle` | cycle, issue | `CYCLE_COMPLETED`, `CYCLE_TEAM_MISMATCH` | — | — |
| `issue_detail` / `list_*` / `agenda` / `search_issues` | issue, team, project, cycle, label, member | `INVALID_CURSOR`, `LIMIT_RANGE`, `ORDER_INVALID`, `INCLUDE_INVALID`, `DATE_RANGE_INVERTED`, `QUERY_EMPTY` | — | — |

Payload shape: the one of SPEC-020 §6 (`{"error": {"code", "message", "details"}}`). `details` always
includes `resource`/`key` in `NotFound`, `rule`/`field` in `Validation` and `rule` in `Conflict`. The
errors of this layer carry no SQL, table names nor paths: that is REQ-S060-03 and it is verified with a
forced-500 test.

## 7. Acceptance Criteria

```gherkin
@SPEC-040
Feature: Application use cases

  @REQ-F040-01
  Scenario: creating an issue writes issue, labels and history in one transaction
    Given the ENG team with a default state "Backlog" and the label "bug"
    When create_issue is run with title "Fix login" and labels ("bug",)
    Then an issue exists with identifier "ENG-1" and the label "bug"
    And a row exists in issue_history with field "created" and new_value "ENG-1"
    Given a forced failure in history.record
    When the same create_issue is run
    Then the issue does not exist
    And no row is left in issue_label for that issue

  @REQ-F040-02
  Scenario: the default state depends on the team configuration
    Given the ENG team with triage_enabled true and a "Triage" state of type triage
    When create_issue is run without state
    Then the issue stays in the "Triage" state
    Given the OPS team with triage_enabled false and "Backlog" as is_default
    When create_issue is run without state
    Then the issue stays in "Backlog"

  @REQ-F040-03
  Scenario: an invalid reference leaves no trace
    Given the ENG team and a label of the OPS team
    When create_issue is run with labels ("ops-only",)
    Then Validation is raised with rule "LABEL_TEAM_MISMATCH"
    And the ENG team still has 0 issues

  @REQ-F040-04
  Scenario: update distinguishes not touching from clearing
    Given an issue ENG-1 with description "hello" and assignee "mo"
    When update_issue is run with description UNSET and assignee_id None
    Then the description is still "hello"
    And the assignee_id is NULL
    And issue_history has exactly 1 row with field "assignee_id" and new_value NULL

  @REQ-F040-05
  Scenario: moving to started sets started_at and records the transition
    Given an issue ENG-1 in "Backlog" without started_at
    When move_issue is run toward "In Progress" of type started
    Then started_at is not NULL and completed_at is NULL
    And issue_history records state_id from "Backlog" to "In Progress"
    When move_issue is run toward "Done" of type completed
    Then completed_at is not NULL and canceled_at is NULL

  @REQ-F040-06
  Scenario: moving with position before respects the neighbors
    Given the state "In Progress" with ENG-1 at 1024.0 and ENG-2 at 2048.0
    When move_issue of ENG-3 toward "In Progress" is run with position before and before_id ENG-2
    Then the board_order of ENG-3 is 1536.0
    And the order of the state is ENG-1, ENG-3, ENG-2

  @REQ-F040-07
  Scenario: a parent with open children cannot be completed
    Given ENG-1 with two open sub-issues
    When move_issue of ENG-1 toward "Done" is run
    Then Validation is raised with rule "CLOSE_WITH_OPEN_CHILDREN"
    And the state_id of ENG-1 did not change
    And no row was added to issue_history

  @REQ-F040-08
  Scenario: reordering rebalances when there is no gap
    Given three issues with board_order 1024.0, 1024.0000005 and 3072.0
    When reorder_issue is run inserting between the first two
    Then the three (plus the new one) have positions spaced by 1024.0
    And the previous relative order is kept

  @REQ-F040-09
  Scenario: adding a label twice is idempotent
    Given ENG-1 with the label "bug"
    When add_label is run with "bug" twice
    Then the issue has a single label "bug"
    And issue_history has a single row with field "label"

  @REQ-F040-10
  Scenario: blocked_by is persisted inverted
    Given the issues ENG-1 and ENG-2
    When relate_issues(issue=ENG-1, related=ENG-2, type="blocked_by") is run
    Then a relation exists with issue_id ENG-2, related_issue_id ENG-1 and type "blocks"
    And the inverse relation does not exist

  @REQ-F040-11
  Scenario: unrelating something nonexistent is NotFound
    Given the issues ENG-1 and ENG-2 without a relation
    When unrelate_issues is run between them
    Then NotFound is raised with resource "relation"

  @REQ-F040-12
  Scenario: a comment cannot reply to an hifa from another issue
    Given the comment C1 of ENG-1
    When comment_on_issue is run on ENG-2 with parent_id C1
    Then Validation is raised with rule "COMMENT_PARENT_MISMATCH"
    And no comment is inserted

  @REQ-F040-13
  Scenario: resolving a comment is idempotent
    Given an unresolved comment of ENG-1
    When resolve_comment is run twice
    Then resolved_at is not NULL
    And resolved_at did not change between the first and the second call

  @REQ-F040-14
  Scenario: creating a team creates the workspace and the six states
    Given a migrated database without workspace or teams
    When create_team is run with key "ENG" and name "Engineering"
    Then a workspace exists
    And the ENG team has 6 workflow_state, one per type
    And exactly one has is_default 1

  @REQ-F040-15
  Scenario: a team with open work is not archived
    Given the ENG team with 2 open issues
    When archive_team is run
    Then Validation is raised with rule "TEAM_HAS_OPEN_ISSUES"
    And team.archived_at is still NULL

  @REQ-F040-16
  Scenario: a project with inverted dates is rejected
    Given the ENG team
    When create_project is run with start_date 2026-12-01 and target_date 2026-11-01
    Then Validation is raised with rule "DATE_RANGE_INVERTED"
    And no project is inserted

  @REQ-F040-17
  Scenario: a milestone from another project is rejected
    Given the projects P1 and P2
    When add_milestone is run on P1 with a valid target_date and then it is assigned to an issue of P2
    Then the assignment raises Validation with rule "MILESTONE_PROJECT_MISMATCH"

  @REQ-F040-18
  Scenario: the cycle derives ends_at from duration_weeks
    Given the ENG team without cycles
    When create_cycle is run with starts_at 2026-11-09T00:00:00Z and duration_weeks 2
    Then the cycle has number 1
    And ends_at is 2026-11-23T00:00:00Z
    And ends_at is greater than starts_at

  @REQ-F040-19
  Scenario: issues are not added to a completed cycle
    Given a completed cycle of the ENG team
    When add_issues_to_cycle is run with an open issue
    Then Validation is raised with rule "CYCLE_COMPLETED"
    And the cycle_id of the issue is still NULL

  @REQ-F040-20
  Scenario: archiving and trashing do not delete
    Given an issue ENG-1
    When archive_issue and then trash_issue are run
    Then the row still exists with archived_at not NULL and trashed 1
    When restore_issue is run
    Then archived_at is NULL and trashed is 0
    And issue_history has 3 new rows

  @REQ-F040-21
  Scenario: the actor reaches the history and the comment
    Given an Actor with member_id "U1"
    When create_issue and comment_on_issue are run with that actor
    Then the "created" row of issue_history has actor_id "U1"
    And the comment has author_id "U1"
    Given an Actor without member_id
    When create_issue is run
    Then the "created" row has actor_id NULL

  @REQ-F040-22
  Scenario: application errors are domain errors
    Given a create_issue command with a nonexistent team
    When the use case is run
    Then NotFound is raised and not sqlite3.Error
    And the error is an instance of HifaError
    And its to_dict has code "NOT_FOUND"

  @REQ-F040-23
  Scenario: use cases do not print
    Given the modules of src/hifa/app
    When a static test looks for print, sys.stdout and json.dumps
    Then it finds no match
    And importing hifa.app writes nothing to stdout

  @REQ-F040-24
  Scenario: reads resolve the team by key
    Given the ENG team with key "ENG" and 3 issues
    When list_board is run with team "ENG"
    Then the Board has a team_id equal to the team's ULID
    And list_issues with a filter for "ENG" resolves the ULID and returns 3 items
    And neither of them opens a write transaction

  @REQ-F040-29
  Scenario: list_issues paginates with an opaque cursor and limit + 1
    Given the ENG team with 3 issues and a limit of 2
    When list_issues is run with limit 2 and order "created"
    Then the page returns 2 items and has_more is true
    And next_cursor does not contain "|" nor the ULID in clear
    When list_issues is run with that next_cursor
    Then the page returns 1 item and has_more is false
    And next_cursor is null

  @REQ-F040-29
  Scenario: a cursor from another order is Validation
    Given a cursor issued with order "created"
    When list_issues is run with order "priority" and that cursor
    Then Validation is raised with rule "INVALID_CURSOR"
    And the edge maps it to exit 2 and HTTP 422

  @REQ-F040-08
  Scenario: a gap smaller than 1e-6 rebalances without error
    Given two consecutive issues with board_order 1024.0 and 1024.0000001
    When move_issue is run with position before and the before_id of the second one
    Then the column is rewritten with multiples of 1024 in the same transaction
    And the issue lands between the two
    And no error is raised

  @REQ-F040-06
  Scenario: a sort_order out of range is rejected
    Given the state "Todo" with ENG-1 at 1024.0 and ENG-2 at 2048.0
    When move_issue is run with sort_order 4096.0
    Then Validation is raised with rule "POSITION_OUT_OF_ORDER"
    And the board_order of no issue is modified

  @REQ-F040-25
  Scenario: two members with the same email are Conflict
    Given a member with email "ana@example.com"
    When create_member is run with the same email
    Then Conflict is raised with rule "UNIQUE_MEMBER_EMAIL"
    And the edge maps it to exit 4 and HTTP 409

  @REQ-F040-30
  Scenario: branch_name comes from the identifier and the title
    When create_issue is run in the ENG team with title "Fix login flow"
    Then branch_name is "eng-1-fix-login-flow"
    When update_issue is run with a different title
    Then branch_name is regenerated with the new title
    And if the branch_name had been edited by hand, it is not touched

  @REQ-F040-27
  Scenario: an attachment with a disallowed scheme is Validation
    When add_attachment is run with url "javascript:alert(1)"
    Then Validation is raised with rule "ATTACHMENT_URL_SCHEME"
    And no row is inserted in attachment

  @REQ-F040-26
  Scenario: create_label rejects a parent group from another team
    Given the ENG team with the group label "area" and the OPS team
    When create_label is run in the "OPS" team with parent "area" from the ENG team
    Then Validation is raised with rule "LABEL_SCOPE"
    And no row is inserted in label

  @REQ-F040-26
  Scenario: create_state rejects a type outside the state machine
    Given the ENG team
    When create_state is run with name "Blocked" and type "blocked"
    Then Validation is raised with rule "STATE_TYPE_INVALID"
    And no row is inserted in workflow_state

  @REQ-F040-26
  Scenario: add_label does not create labels unless create_if_missing
    Given the ENG team without the label "infra"
    When add_label is run on ENG-1 with name "infra" and create_if_missing false
    Then Validation is raised with rule "LABEL_NOT_FOUND"
    And no row is inserted in label

  @REQ-F040-28
  Scenario: update_comment does not reopen the hifa nor write history
    Given a resolved comment on ENG-1 with body "ok" and non-null resolved_at
    When update_comment is run with body "ok, fixed"
    Then comment.body is "ok, fixed" and comment.edited_at is not null
    And comment.resolved_at and comment.parent_id did not change
    And no row was added to issue_history for that issue
```

## 8. Regeneration checklist

1. Read `SPEC-000` (§2 A1-A8), `SPEC-020` (§2.1-§2.6 in full) and `SPEC-030` (§2.2 `UnitOfWork`,
   §2.4 repos, §2.6 read models). Without SPEC-030 you cannot know the repo methods; without SPEC-020
   you cannot know what to validate.
2. Create the package and the seven empty modules; verify that it imports and that it does **not** drag
   in edge dependencies:
   ```bash
   mkdir -p src/hifa/app tests/integration/app
   for m in commands issues teams labels projects cycles boards; do : > src/hifa/app/$m.py; done
   printf '' > src/hifa/app/__init__.py
   python -c "import hifa.app, sys; assert 'fastapi' not in sys.modules and 'typer' not in sys.modules"
   ```
3. Implement in this order: `commands.py` (dataclasses + normalization) → `issues.py`
   (`create_issue` first; it is the one that defines the transaction pattern) → `labels.py` → `teams.py` →
   `projects.py` → `cycles.py` → `boards.py` (reads at the end, they hold no mystery).
4. Write `tests/integration/app/conftest.py` with three fixtures: `db` (migrated database in `tmp_path`),
   `uow` (with deterministic `FrozenClock` and `SeqIdGen`) and `actor`. Without a fixed clock, the asserts on
   `completed_at` are flaky.
   ```bash
   pytest tests/integration/app -q
   ```
5. Verify the atomicity of each use case with a wrapped repo that fails on the last write
   (`monkeypatch` on `uow.history.record`) and checking that the entity does not exist afterwards. It is the
   test that proves REQ-F040-01 and it cannot be simulated by reading the code.
6. Verify R1/REQ-N040-04 with `set_trace_callback` on the connection: the transaction opening counter
   must be exactly 1 per use case (the `BEGIN` literal belongs to SPEC-030, not
   to this layer: here only how many times it is opened is counted).
7. Verify R14/REQ-F040-23 with a static test (AST) over `src/hifa/app/**`: zero `print`, zero
   `sys.stdout`, zero `json.dumps`, zero strings starting with `SELECT`/`INSERT`/`UPDATE`.
8. Verify the graph and the contract:
   ```bash
   lint-imports     # app -> domain and app -> adapters (ports), never app -> cli/api/web
   python scripts/spec_contract_check.py --spec specs/040-application-services.md
   ```
   The check compares the signatures of §2.2, the fields of the dataclasses of §2.1 and the mandatory
   use case list against the symbols exported by each module.
9. Regeneration proof: `git rm -r src/hifa/app tests/integration/app`, reimplement only with
   SPEC-000 + SPEC-010 + SPEC-020 + SPEC-030 + this spec, and run
   `pytest tests/integration/app -q`. If you need to read `cli/` to understand a use case, the spec
   is incomplete.

## 9. Provenance

- **Command / use case / entity separation**: "command object → service function" pattern chosen
  by Ginkelte, 2026-11-08, so that the CLI, the API and the dashboard call exactly the same function
  and there are no two write paths (rule A6/A7 of SPEC-000).
- **`UNSET` vs `None` in update commands**: PATCH semantics (`null` clears, absent does not touch)
  carried over to the command; author's decision, 2026-11-08, so that `hifa issue edit`
  and `PATCH /issues/{id}` do not need two different DTOs.
- **Default state with `triage` first**: the order triage → default → first `unstarted` is
  Ginkelte's decision, 2026-11-08, so an incoming issue lands in the triage column when the team
  has one.
- **`started_at`/`completed_at`/`canceled_at` milestones independent of the state**: the temporal
  milestones are stored apart from the current state, so a report can answer "when did it start"
  without replaying history. Keeping `started_at` when reopening is the author's decision, 2026-11-08.
- **Directional `blocks` with `blocked_by` as a non-stored inverse**: `issue_relation.type` of
  SPEC-010, stored once and read backwards for the inverse.
- **Default state of a new team: 6 workflow_state (Backlog, Todo, In Progress, In Review,
  Done, Canceled)**: the exact names are Ginkelte's decision, 2026-11-08.
- **History per column instead of a change blob**: `issue_history` table of SPEC-010 §3.1;
  the `(field, old_value, new_value)` format is what makes "who changed what" queryable without
  parsing JSON.
- **One transaction per use case**: `SPEC-010` R1 and `SPEC-030` R3; the number of statements per
  use case (REQ-N040-03) comes from counting the operations of §3.1.
- **`comment` does not generate history**: author's decision, 2026-11-08: the comment is its own
  record; duplicating it in `issue_history` inflates the table without adding information.

## 10. Out of scope

- Parsing of flags, JSON or forms: each edge builds the command (SPEC-050/060/070).
- Output format, tables, colors, `--json` (SPEC-050) and HTTP schemas (SPEC-060).
- FTS search (`app/search.py`, SPEC-090) and import/export (`app/transfer.py`, SPEC-080).
- Authentication, permissions and multi-user: `Actor` only identifies, it does not authorize.
- Undo/redo, navigable history as a feature, notifications, webhooks, subscriptions.
- Batch operations beyond `add_issues_to_cycle` (bulk edit, bulk move).
- Automatic rollover of issues when completing a cycle (`complete_cycle` only marks `completed_at`).
- Moving an issue across teams (it would change `identifier`; it requires its own use case with a product
  decision, nonexistent today).
- Retries at the level of an **individual use case** (per-function decorators, queues, custom backoff): the
  only retry is `with_write_retry` (§2.2), and it re-executes the complete transaction; `UnitOfWork.write()`
  of SPEC-030 does not retry (D-018).

## Notes

- spec_revision 7 (2026-11-11): **`update_team` and `archive_team` are declared, not implemented
  (D-031).** §2.2 gains a status paragraph and an inline `# status:` comment on `archive_team`; the
  signature, its `# raises:` line, §3.1 and the §6 error rows are unchanged, because they are the
  contract of the function for the revision that implements it. The decision exists because SPEC-050
  declared a `team archive` command for this use case while this spec declared the opposite error
  classes for the same edge (`Validation` ⇒ 2 vs `Conflict` ⇒ 4 vs idempotent ⇒ 0). Deferring the
  function with `update_team` removes the command from the CLI surface and leaves one authority
  (this spec) for the day the semantics are settled.
- spec_revision 6 (2026-11-11): **the panel passes `DETAIL_INCLUDE` (D-029).** The §2.2 comment
  claimed that the panel and the row/card fragments both call `issue_detail(db, ref)` with no
  `include`; the panel renders the comments, so it is the detail page and the panel that pass the
  constant, and only the row/card fragments stay collection-free. `issue_detail` rejects any other
  `include` name with `Validation(field="include", rule="INCLUDE_INVALID")` (§2.2, §6). The
  frontmatter is set to 6 so the field agrees with the newest entry here (the previous one was
  written as 5 while the field still read 4).
- spec_revision 5 (2026-11-11): **the dashboard's contract, closed against SPEC-030 and SPEC-070
  (D-025, D-027).** (1) `q` stops being an unsupported filter: it is a **title filter** declared in
  `ListFilters` (SPEC-030 §2.6), so the three places that rejected it — the §5 pseudocode comment,
  the §5 flow rule and the §6 error-table row — are gone, and the rule also leaves the §6 use-case
  matrix because no use case can raise it any more. §5 now says what it should: "`q` is a title
  filter, not search: workspace search is SPEC-090 / `search_issues`". (2) §2.2 declares the edge
  helpers the dashboard shipped with no contract: `require_team`, `resolve_filters`,
  `board_counts` and the public constant `DETAIL_INCLUDE` (a constant, **not** the default of
  `include`, so that fragments keep the cheap path). (3) §2.2 states that `list_board`'s signature —
  `team` + `filters: ListFilters | None` — is the contract and that the six loose filter kwargs are
  not. **Housekeeping:** the frontmatter had stayed at `spec_revision: 3` while this section already
  recorded a `spec_revision 4` (the English translation); the field is now 4 and the entries below
  keep their original numbers, so this change is the 5.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
- **`spec_revision` 3 (2026-11-08, Fix Agent, round 3).** Closes the blockers of
  `specs/reviews/spec-review-02-r2.md`:
  - **Retry (D-018).** `with_write_retry` is declared here as the owner of the only retry loop
    (`app/retry.py`, `attempts=3`, configurable `retry_on`) and all use cases are invoked
    wrapped; `UnitOfWork.write()` of SPEC-030 does not retry. `create_issue` retries
    `DUPLICATE_NUMBER` through the same helper (a single `next_number` strategy).
  - **`IssuePage` with a single owner.** The local dataclass is removed: it is imported from
    `hifa.adapters.sqlite.reads` (SPEC-030 §2.6), where it lives next to `ListFilters`.
  - **Symbols with a declared owner.** `slugify`, `branch_name`, `apply_milestones`,
    `validate_attachment_url` (`app/rules.py` / `app/validation.py`) and `exhausted`
    (`app/ranking.py`) are no longer attributed to SPEC-020 (R17 is no longer circular).
  - **`reads` contract respected.** §5.3 calls `list_view(conn, ListFilters(...))`, the real signature;
    `set_state` is used without `milestones=` (the milestones go through `update(issue)`, 030 §2.4).
  - **§7 complete.** `REQ-F040-26` and `REQ-F040-28` gain scenarios; 30/30 REQ with a tag and 0 orphans.
  - **`COMMENT_DELETED` removed**: `comment` has no soft delete in SPEC-010 and the only rejection
    of `update_comment` is `BODY_EMPTY`. `InvalidTransition` keeps an explicit producer (this layer).
- **`spec_revision` 2 (2026-11-08, Fix Agent, round 2).** §2 was completed so that it covers everything
  that 050/060/070 consume, with D-012 as the direction of the fix (040 rules; the edges adjust).
  - **Complete port (§2).** The following were added, with exact signature, return type and declared errors:
    `create_member`, `update_member`, `deactivate_member`, `list_members`, `member_detail`,
    `assign_issue`, `set_priority`, `set_due_date`, `add_attachment`, `list_attachments`,
    `list_comments`, `update_comment`, `create_label`, `update_label`, `retire_label`, `list_labels`,
    `label_detail`, `create_state`, `update_state`, `list_states`, `list_teams`, `team_detail`,
    `list_projects`, `project_detail`, `list_cycles`, `cycle_detail`, `rebalance_board`,
    `list_board`, `list_issues`, `issue_detail`, `agenda`, `search_issues`. The corresponding
    dataclasses were added (`AssignIssue`, `SetPriority`, `SetDueDate`, `RebalanceBoard`,
    `UpdateComment`, `AddAttachment`, `CreateMember`, `UpdateMember`, `DeactivateMember`,
    `CreateLabel`, `UpdateLabel`, `RetireLabel`, `CreateState`, `UpdateState`) as well as the returns
    `IssuePage`, `Board`, `BoardColumn`.
  - **Renames (single vocabulary).** `get_issue`→`issue_detail`, `get_board`→`list_board`,
    `get_agenda`→`agenda`; `<x>_detail` / `list_<plural>` as the only convention. `MoveIssue` replaced
    `relative_to` with `before_id`/`after_id` and gained `sort_order` (validated shortcut) and `rebalance`.
    `delete_issue` does **not** exist (D-015): deletion is `trash_issue`.
  - **Rules that live here (§4).** R6 composite close (`count_open_children` + `rules.validate_close`,
    not in 030's `set_state`); R7 milestones by `state.type` and R7bis on create; R8 reference anchoring and
    validation of the edge `sort_order`; R9 a single rebalance mechanism with a **1e-6** threshold and
    `RANKING_DEGRADED` as a bug; R17 `branch_name` (`eng-123-title-slug`, does not regenerate a
    manual value); R18 keyset (D-011) and R19 read models without shadowing (D-019, `visible`).
  - **§5 rewritten.** `create_issue` (with `branch_name` and R7bis), `move_issue` (payload of the three
    edges, fractional position, rebalance for gaps `< 1e-6`) and `list_issues` (keyset with opaque cursor,
    `limit + 1`, no `OFFSET`).
  - **§6 aligned to D-014.** `RANKING_DEGRADED`, `UOW_NESTED` and `HIFA_ERROR` move from **1** to **6**
    (`1` stays reserved for `hifa doctor`); `INVALID_CURSOR`, `LIMIT_RANGE`,
    `ORDER_INVALID`, the then-rejected-`q` rule (removed later, D-025), `INCLUDE_INVALID`,
    `POSITION_OUT_OF_ORDER`,
    `UNIQUE_MEMBER_EMAIL`, `MEMBER_INACTIVE`, `MEMBER_ALREADY_INACTIVE`,
    `ATTACHMENT_URL_*`, `UNIQUE_LABEL_NAME`, `UNIQUE_STATE_NAME`, `STATE_TYPE_INVALID`,
    `CYCLE_ALREADY_COMPLETED`, `READ_ONLY_UOW` and the use case → error matrix for the tests were added.
  - **§3.1 and §4.2** gained the rows for the new use cases (one row = one transaction) and
    REQ-F040-25…30.
- **Edge gaps that remain recorded (not touched here):**
  - 050/060/070 must rename `get_issue`/`get_board`/`get_agenda` to `issue_detail`/`list_board`/
    `agenda`, and `assign_issue`/`add_attachment`/`search_issues`/`rebalance` already have a use case
    (GAP-01…GAP-06 of 050 §2.3.1 are closed by this review).
  - **060 R8 still offers `DELETE ?hard=true` ⇒ 204**, contradicting D-015; it is fixed in
    SPEC-060 (here there is no `delete_issue` nor physical deletion).
  - **060 `IssueMove.sort_order`** is kept as a shortcut, but the command validates it against the
    neighbors (R8): the edge cannot write an arbitrary position.
  - `create_relation`/`delete_relation` are not added as aliases of `relate_issues`/`unrelate_issues`:
    two write functions for the same table would duplicate validations (R1). If an edge insists
    on those names, the adapter goes in the edge.
  - The presentation helpers (`base64url`, `render`, `to_json`, `basename`, `parse_query`, `fetch`,
    `board_vm`, `snippet`) are **not** part of the port: §2.3 documents why and where they live.

