"""Workspace bootstrap (`hifa init`, SPEC-100 §2.5 and §3.1, D-013).

`bootstrap` seeds in **one** transaction: 1 `workspace`, 1 `team`, the 6 canonical
`workflow_state` rows (`Todo` with `is_default=1`), the 4 workspace `label`s,
1 local `member` and its `team_membership`. Every step is *get-or-create* by natural key
(R11): a second run returns `created=[]` and `already_initialized=True`.

This module formats nothing and knows nothing about the CLI: it receives the already built
`UnitOfWork` plus the clock/ID generator via `uow.clock` / `uow.idgen` (D-017).
"""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.commands import Actor, CreateState, CreateTeam, Unset, UpdateState
from hifa.config import (
    DEFAULT_COLOR,
    DEFAULT_ICON,
    DEFAULT_TEAM_KEY,
    DEFAULT_TEAM_NAME,
    DEFAULT_TIMEZONE,
    ConfigError,
    slugify,
    validate_timezone,
)
from hifa.domain.entities import Label, Member, Team, WorkflowState
from hifa.domain.enums import EstimationType, TeamVisibility, WorkflowStateType
from hifa.domain.errors import Conflict, HifaError, NotFound, Validation
from hifa.domain.identifier import KEY_PATTERN, normalize_key
from hifa.domain.ranking import UNIT_GAP, between, last
from hifa.ports import Clock, IdGen

#: Default states: `(name, type, color, position, is_default)` — literals from SPEC-100 §2.5.
#: `Todo` sits between `Triage` and `Backlog` and is the default one (D-034): the canonical
#: order is Triage · Todo · Backlog · In Progress · Done · Canceled.
DEFAULT_STATES: tuple[tuple[str, str, str, float, int], ...] = (
    ("Triage", "triage", "#6B6F76", 1.0, 0),
    ("Todo", "unstarted", "#E2E2E2", 2.0, 1),
    ("Backlog", "backlog", "#6B6F76", 3.0, 0),
    ("In Progress", "started", "#F2C94C", 4.0, 0),
    ("Done", "completed", "#5E6AD2", 5.0, 0),
    ("Canceled", "canceled", "#95A2B3", 6.0, 0),
)

#: Default labels: `(name, color)`, workspace scope (`team_id IS NULL`).
DEFAULT_LABELS: tuple[tuple[str, str], ...] = (
    ("Bug", "#EB5757"),
    ("Feature", "#BB87FC"),
    ("Improvement", "#4EA7FC"),
    ("Docs", "#95A2B3"),
)

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class BootstrapOptions:
    """Validated `hifa init` flags (SPEC-100 §2.5).

    Validation runs in `__post_init__` (before touching the database, R15): an invalid flag
    raises `ConfigError(config_invalid_value)` with `field` populated.
    """

    team_key: str = DEFAULT_TEAM_KEY
    team_name: str = DEFAULT_TEAM_NAME
    icon: str = DEFAULT_ICON
    color: str = DEFAULT_COLOR
    timezone: str = DEFAULT_TIMEZONE
    workspace_name: str | None = None
    seed_labels: bool = True
    seed_member: bool = True
    force: bool = False
    member_name: str | None = None
    member_email: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "team_key", _validate_key(self.team_key))
        object.__setattr__(self, "team_name", _validate_name("team.name", self.team_name, 80))
        object.__setattr__(self, "icon", _validate_icon(self.icon))
        object.__setattr__(self, "color", _validate_color(self.color))
        object.__setattr__(self, "timezone", validate_timezone(self.timezone))
        if self.workspace_name is not None:
            object.__setattr__(
                self,
                "workspace_name",
                _validate_name("workspace.name", self.workspace_name, 80),
            )


@dataclass(frozen=True)
class BootstrapResult:
    """`hifa init` envelope (human, `--json` and `--quiet`). Order = JSON key order."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    workspace_id: str = ""
    team_id: str = ""
    member_id: str | None = None
    db_path: Path = Path(":memory:")
    already_initialized: bool = False

    def to_dict(self) -> dict[str, object]:
        """Exact shape of the `--json` output of SPEC-100 §2.7 / SPEC-050 §2.5.1."""
        return {
            "created": list(self.created),
            "skipped": list(self.skipped),
            "workspace_id": self.workspace_id,
            "team_id": self.team_id,
            "member_id": self.member_id,
            "db_path": str(self.db_path),
            "already_initialized": self.already_initialized,
        }


# --- flag validation (R15) -----------------------------------------------------------


def _invalid(field_name: str, value: object, expected: str) -> ConfigError:
    return ConfigError(
        f"{field_name}: invalid value {value!r} (expected: {expected})",
        code="config_invalid_value",
        field=field_name,
    )


def _validate_key(value: str) -> str:
    normalized = normalize_key(value) if isinstance(value, str) else ""
    if KEY_PATTERN.match(normalized) is None:
        raise _invalid("team.key", value, "2-10 characters A-Z/0-9 starting with a letter")
    return normalized


def _validate_name(field_name: str, value: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, value, "1 or more characters")
    text = value.strip()
    if len(text) > max_len or "\n" in text:
        raise _invalid(field_name, value, f"at most {max_len} characters on a single line")
    return text


def _validate_icon(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 8:
        raise _invalid("team.icon", value, "1 to 8 characters")
    if any(ch.isspace() for ch in value):
        raise _invalid("team.icon", value, "no whitespace")
    return value


def _validate_color(value: str) -> str:
    if not isinstance(value, str) or _COLOR_PATTERN.match(value) is None:
        raise _invalid("team.color", value, "#RRGGBB")
    return value


# --- bootstrap ---------------------------------------------------------------------------


def _initials(display_name: str) -> str:
    """First two letters, uppercased (SPEC-100 §3.1)."""
    letters = [ch for ch in display_name.strip() if ch.isalnum()]
    return ("".join(letters[:2]) or display_name.strip()[:2] or "??").upper()


def _default_workspace_name() -> str:
    """Workspace name derived from the current directory (`bootstrap` and `create_team`)."""
    base = Path.cwd().name or "hifa"
    return f"{base[:1].upper()}{base[1:]}"


def _workspace_name(opts: BootstrapOptions) -> str:
    if opts.workspace_name:
        return opts.workspace_name
    return _default_workspace_name()


def _local_member_name(opts: BootstrapOptions) -> str:
    if opts.member_name and opts.member_name.strip():
        return opts.member_name.strip()
    try:
        return getpass.getuser()
    except (KeyError, OSError):  # pragma: no cover - environments without passwd
        return "local"


def _member_email(opts: BootstrapOptions) -> str | None:
    if opts.member_email is not None:
        return opts.member_email.strip() or None
    return (os.environ.get("HIFA_ME_EMAIL") or "").strip() or None


def bootstrap(
    uow: UnitOfWork,
    opts: BootstrapOptions,
    *,
    clock: Clock,
    ids: IdGen,
) -> BootstrapResult:
    """Seed the workspace idempotently inside a single `uow.write()` (R9/R10/R11)."""
    now = clock.now()
    name = _workspace_name(opts)
    url_key = slugify(name)
    created: list[str] = []
    skipped: list[str] = []
    member_id: str | None = None

    with uow.write() as repos:
        existing_ws = repos.teams.workspace()
        existing_team = (
            repos.teams.get_by_key(existing_ws.id, opts.team_key)
            if existing_ws is not None
            else None
        )
        already_initialized = existing_ws is not None and existing_team is not None

        # 1. workspace (by url_key)
        if existing_ws is None:
            workspace = repos.teams.ensure_workspace(name=name, url_key=url_key)
            created.append("workspace")
        else:
            workspace = existing_ws
            skipped.append("workspace")

        # 2. team (by workspace_id, key)
        team = existing_team
        if team is None:
            team = repos.teams.insert(
                Team(
                    id=ids.new(),
                    workspace_id=workspace.id,
                    key=opts.team_key,
                    name=opts.team_name,
                    icon=opts.icon,
                    color=opts.color,
                    timezone=opts.timezone,
                    visibility=TeamVisibility.PUBLIC,
                    cycles_enabled=False,
                    cycle_duration=2,
                    cycle_start_day=1,
                    triage_enabled=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            created.append("team")
        else:
            skipped.append("team")

        # 3. the 6 workflow_state rows (by team_id, name)
        for state_name, state_type, color, position, is_default in DEFAULT_STATES:
            label = f"workflow_state:{state_name}"
            if repos.states.get_by_name(team.id, state_name) is not None:
                skipped.append(label)
                continue
            repos.states.insert(
                WorkflowState(
                    id=ids.new(),
                    team_id=team.id,
                    name=state_name,
                    type=WorkflowStateType(state_type),
                    color=color,
                    position=position,
                    is_default=bool(is_default),
                )
            )
            created.append(label)

        # 4. workspace labels (by workspace_id, NULL, name)
        if opts.seed_labels:
            for label_name, color in DEFAULT_LABELS:
                entry = f"label:{label_name}"
                if repos.labels.get_by_name(workspace.id, None, label_name) is not None:
                    skipped.append(entry)
                    continue
                repos.labels.insert(
                    Label(
                        id=ids.new(),
                        workspace_id=workspace.id,
                        team_id=None,
                        name=label_name,
                        color=color,
                    )
                )
                created.append(entry)

        # 5. local member (by email, or by display_name when there is no email)
        member: Member | None = None
        if opts.seed_member:
            display_name = _local_member_name(opts)
            email = _member_email(opts)
            if email:
                member = repos.members.get_by_email(email)
            else:
                wanted = display_name.casefold()
                member = next(
                    (
                        candidate
                        for candidate in repos.members.list(workspace.id, include_inactive=True)
                        if candidate.display_name.casefold() == wanted
                    ),
                    None,
                )
            if member is None:
                member = repos.members.insert(
                    Member(
                        id=ids.new(),
                        workspace_id=workspace.id,
                        name=display_name,
                        display_name=display_name,
                        email=email,
                        initials=_initials(display_name),
                        timezone=opts.timezone,
                        is_admin=True,
                        is_active=True,
                        created_at=now,
                    )
                )
                created.append("member")
            else:
                skipped.append("member")

            # 6. team_membership (by team_id, member_id)
            if repos.teams.membership(team.id, member.id):
                skipped.append("team_membership")
            else:
                repos.teams.add_member(team.id, member.id, is_owner=True, sort_order=0.0)
                created.append("team_membership")
            member_id = member.id

    return BootstrapResult(
        created=created,
        skipped=skipped,
        workspace_id=workspace.id,
        team_id=team.id,
        member_id=member_id,
        db_path=uow.path or Path(":memory:"),
        already_initialized=already_initialized,
    )


# --- create_team (SPEC-040 §2.2, REQ-F040-14) --------------------------------------------


@dataclass(frozen=True)
class TeamSummary:
    """One row of `team list`: the entity plus its counters (SPEC-050 §2.5.2).

    `issues_count` is `open + closed` — every live issue of the team, which is the ISSUES
    column of the table.
    """

    team: Team
    open_issues: int
    closed_issues: int = 0

    @property
    def issues_count(self) -> int:
        """Every live issue of the team: `open_count + closed_count` (SPEC-050 §2.5.2)."""
        return self.open_issues + self.closed_issues


@dataclass(frozen=True)
class TeamDetail:
    """`team show`: the entity, its workflow states and the `v_team_progress` counters."""

    team: Team
    states: tuple[WorkflowState, ...]
    progress: dict[str, int]


def _invalid_team(field_name: str, value: object, expected: str, rule: str) -> Validation:
    return Validation(
        f"{field_name}: invalid value {value!r} (expected: {expected})",
        rule=rule,
        field=field_name,
        value=value,
        code=rule,
    )


def _validate_team_key(value: str) -> str:
    normalized = normalize_key(value) if isinstance(value, str) else ""
    if KEY_PATTERN.match(normalized) is None:
        raise _invalid_team(
            "key", value, "2-10 characters A-Z/0-9 starting with a letter", "TEAM_KEY_INVALID"
        )
    return normalized


def _validate_team_name(value: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid_team("name", value, "1 or more characters", "TEAM_NAME_INVALID")
    if len(text) > 80 or "\n" in text:
        raise _invalid_team(
            "name", value, "at most 80 characters on a single line", "TEAM_NAME_INVALID"
        )
    return text


def _validate_team_icon(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 8:
        raise _invalid_team("icon", value, "1 to 8 characters", "TEAM_ICON_INVALID")
    if any(ch.isspace() for ch in value):
        raise _invalid_team("icon", value, "no whitespace", "TEAM_ICON_INVALID")
    return value


def _validate_team_color(value: str) -> str:
    if not isinstance(value, str) or _COLOR_PATTERN.match(value) is None:
        raise _invalid_team("color", value, "#RRGGBB", "TEAM_COLOR_INVALID")
    return value


def _validate_team_timezone(value: str) -> str:
    """IANA name; `ConfigError` from `config.validate_timezone` becomes a domain `Validation`."""
    try:
        return validate_timezone(value)
    except ConfigError as exc:
        raise _invalid_team(
            "timezone", value, "an IANA name, for example UTC", "TEAM_TIMEZONE_INVALID"
        ) from exc


def _reject_default_state(value: str | None) -> None:
    """`default_state` has no defined semantics yet; the canonical list already marks `Todo`."""
    if value is not None:
        raise HifaError(
            "default_state is not supported yet",
            code="NOT_IMPLEMENTED",
            field="default_state",
            hint="the six canonical states always mark Todo as default",
        )


def create_team(uow: UnitOfWork, cmd: CreateTeam, actor: Actor) -> Team:
    """Create a team and its six canonical states in **one** transaction (REQ-F040-14).

    The workspace is created when it is missing and reused otherwise; `DEFAULT_STATES` is the
    only source of the six states, so `create_team` and `bootstrap` cannot drift apart. A key
    collision raises `Conflict(rule='UNIQUE_TEAM_KEY')` before any insert; any other failure
    rolls the whole write back, so a team is never left without its states. `actor` is accepted
    for signature symmetry with the rest of `app/`: `team` has no history table.

    raises: `Validation(TEAM_KEY_INVALID|TEAM_WITHOUT_STATES)`, `Conflict(UNIQUE_TEAM_KEY)`
    """
    key = _validate_team_key(cmd.key)
    name = _validate_team_name(cmd.name)
    icon = _validate_team_icon(cmd.icon if cmd.icon is not None else DEFAULT_ICON)
    color = _validate_team_color(cmd.color if cmd.color is not None else DEFAULT_COLOR)
    timezone = _validate_team_timezone(cmd.timezone)
    _reject_default_state(cmd.default_state)
    if not DEFAULT_STATES:  # pragma: no cover - defensive: the tuple is a module constant
        raise Validation(
            "a team needs at least one workflow state",
            rule="TEAM_WITHOUT_STATES",
            field="state",
            value=key,
            code="TEAM_WITHOUT_STATES",
        )

    with uow.write() as tx:
        workspace = tx.teams.workspace()
        if workspace is None:
            workspace_name = _default_workspace_name()
            workspace = tx.teams.ensure_workspace(
                name=workspace_name, url_key=slugify(workspace_name)
            )
        if tx.teams.get_by_key(workspace.id, key) is not None:
            raise Conflict(
                f"team {key} already exists",
                rule="UNIQUE_TEAM_KEY",
                field="key",
                value=key,
                code="UNIQUE_TEAM_KEY",
            )

        now = uow.clock.now()
        team = tx.teams.insert(
            Team(
                id=uow.idgen.new(),
                workspace_id=workspace.id,
                key=key,
                name=name,
                icon=icon,
                color=color,
                description=cmd.description,
                timezone=timezone,
                visibility=TeamVisibility(cmd.visibility),
                cycles_enabled=cmd.cycles_enabled,
                cycle_duration=cmd.cycle_duration,
                cycle_start_day=cmd.cycle_start_day,
                issue_estimation_type=EstimationType(cmd.estimation_type),
                triage_enabled=cmd.triage_enabled,
                created_at=now,
                updated_at=now,
            )
        )
        for state_name, state_type, state_color, position, is_default in DEFAULT_STATES:
            tx.states.insert(
                WorkflowState(
                    id=uow.idgen.new(),
                    team_id=team.id,
                    name=state_name,
                    type=WorkflowStateType(state_type),
                    color=state_color,
                    position=position,
                    is_default=bool(is_default),
                )
            )
    return team


# --- team reads (SPEC-040 §2.3) -----------------------------------------------------------


def list_teams(uow: UnitOfWork, *, include_archived: bool = False) -> tuple[TeamSummary, ...]:
    """Teams with their issue counters, sorted by `key` ascending (SPEC-050 §2.5.2).

    Archived teams are excluded unless `include_archived`; the `WHERE` lives in the query
    (`TeamRepo.list_with_progress`), because filtering in Python after `list()` was a dead
    filter: `list()` already drops `archived_at IS NOT NULL`. The counters come from the same
    `v_team_progress` row via one `LEFT JOIN`, so a team without issues reports zeros instead
    of disappearing.
    """
    with uow.read() as tx:
        rows = tx.teams.list_with_progress(include_archived=include_archived)
        return tuple(
            TeamSummary(team=team, open_issues=open_count, closed_issues=closed_count)
            for team, open_count, closed_count in sorted(rows, key=lambda row: row[0].key)
        )


def team_detail(uow: UnitOfWork, ref: str) -> TeamDetail:
    """Team by key (case-insensitive) or ULID, with its states and progress counters.

    raises: `NotFound(team, key=ref)`
    """
    with uow.read() as tx:
        workspace = tx.teams.workspace()
        team = None
        if workspace is not None:
            team = tx.teams.get_by_key(workspace.id, ref) or tx.teams.get(ref)
        if team is None:
            raise NotFound(resource="team", key=ref)
        return TeamDetail(
            team=team,
            states=tuple(tx.states.list_for_team(team.id)),
            progress=tx.teams.progress(team.id),
        )


# --- workflow states: create_state / update_state (SPEC-040 §2.2) -------------------------

#: Longest accepted state name (the column is `TEXT`; the limit keeps the board readable).
STATE_NAME_MAX_LENGTH: int = 80

#: Color used when `create_state` receives no `--color` (the `workflow_state.color` default).
DEFAULT_STATE_COLOR: str = "#6B6F76"


@dataclass(frozen=True)
class StateSummary:
    """One row of `hifa state list`: the entity plus its live issue count."""

    state: WorkflowState
    issues_count: int = 0


def _invalid_state(field_name: str, value: object, expected: str, rule: str) -> Validation:
    return Validation(
        f"state {field_name}: invalid value {value!r} (expected: {expected})",
        rule=rule,
        field=field_name,
        value=value,
        code=rule,
    )


def _validate_state_name(value: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid_state("name", value, "1 or more characters", "STATE_NAME_EMPTY")
    if len(text) > STATE_NAME_MAX_LENGTH or "\n" in text:
        raise _invalid_state(
            "name", value, f"at most {STATE_NAME_MAX_LENGTH} characters", "STATE_NAME_INVALID"
        )
    return text


def _validate_state_type(value: str | WorkflowStateType) -> WorkflowStateType:
    try:
        return WorkflowStateType(value)
    except ValueError as exc:
        raise _invalid_state(
            "type",
            value,
            "triage|backlog|unstarted|started|completed|canceled",
            "STATE_TYPE_INVALID",
        ) from exc


def _validate_state_color(value: str) -> str:
    if not isinstance(value, str) or _COLOR_PATTERN.match(value) is None:
        raise _invalid_state("color", value, "#RRGGBB", "COLOR_FORMAT")
    return value


def _resolve_team(tx: Repos, ref: str) -> Team:
    """Team by key (case-insensitive, per workspace) or ULID; `NotFound(team)` otherwise."""
    workspace = tx.teams.workspace()
    team = tx.teams.get_by_key(workspace.id, ref) if workspace is not None else None
    if team is None:
        team = tx.teams.get(ref)
    if team is None:
        raise NotFound(resource="team", key=ref)
    return team


def _duplicate_state(
    states: list[WorkflowState], name: str, *, ignore_id: str | None = None
) -> WorkflowState | None:
    """First state of the team whose name matches case-insensitively (`UNIQUE_STATE_NAME`)."""
    wanted = name.casefold()
    return next(
        (state for state in states if state.id != ignore_id and state.name.casefold() == wanted),
        None,
    )


def _unique_position(
    siblings: list[WorkflowState], wanted: float, *, moving_id: str | None = None
) -> float:
    """`wanted`, moved off any slot a sibling already holds, inside the same neighbourhood.

    Two writers racing for the same drop (two dashboard tabs, the dashboard and a `state
    update`) compute the same midpoint from the same pair of neighbours. `workflow_state` has
    no `UNIQUE (team_id, position)` — the order is `ORDER BY position, name` — so a shared
    slot would silently decide the board order by name. The slot is halved towards the upper
    neighbour (the lower one at the end of the list) until it is free; `between` raises
    `RANKING_EXHAUSTED` when no float is left, and the edge maps it to `422` / exit 2.
    """
    occupied = {state.position for state in siblings if state.id != moving_id}
    value = wanted
    while value in occupied:
        upper = min((position for position in occupied if position > value), default=None)
        if upper is not None:
            value = between(value, upper, gap=UNIT_GAP)
            continue
        lower = max((position for position in occupied if position < value), default=None)
        if lower is None:  # pragma: no cover - every sibling holds a different slot
            break
        value = between(lower, value, gap=UNIT_GAP)
    return value


def create_state(uow: UnitOfWork, cmd: CreateState, actor: Actor) -> WorkflowState:
    """Create one custom state for a team in a single `uow.write()` (SPEC-040 §2.2).

    The team comes by key or ULID; the name is unique per team case-insensitively (an archived
    state still owns its name, because the table declares `UNIQUE (team_id, name)`). The new
    position is the explicit `position` (already computed by the edge), else the last one.
    `actor` is accepted for signature symmetry: `workflow_state` has no history table.

    raises: `NotFound(team)`, `Validation(STATE_TYPE_INVALID|STATE_NAME_EMPTY|COLOR_FORMAT|
    TEAM_ARCHIVED)`, `Conflict(UNIQUE_STATE_NAME)`
    """
    name = _validate_state_name(cmd.name)
    state_type = _validate_state_type(cmd.state_type)
    color = DEFAULT_STATE_COLOR if cmd.color is None else _validate_state_color(cmd.color)

    with uow.write() as tx:
        team = _resolve_team(tx, cmd.team)
        if team.archived_at is not None:
            raise _invalid_state("team", team.key, "a team that is not archived", "TEAM_ARCHIVED")
        siblings = tx.states.list_for_team(team.id, include_archived=True)
        duplicate = _duplicate_state(siblings, name)
        if duplicate is not None:
            raise Conflict(
                f"state {name} already exists in team {team.key}",
                rule="UNIQUE_STATE_NAME",
                field="name",
                value=name,
                code="UNIQUE_STATE_NAME",
            )
        position = _unique_position(
            siblings,
            float(cmd.position)
            if cmd.position is not None
            else last([state.position for state in siblings], gap=UNIT_GAP),
        )
        return tx.states.insert(
            WorkflowState(
                id=uow.idgen.new(),
                team_id=team.id,
                name=name,
                type=state_type,
                color=color,
                position=position,
                is_default=False,
            )
        )


def _resolve_state(tx: Repos, ref: str) -> WorkflowState:
    """State by ULID, else by name (case-insensitive) when exactly one team owns that name."""
    state = tx.states.get(ref)
    if state is not None:
        return state
    wanted = ref.casefold()
    matches = [
        candidate
        for team in tx.teams.list()
        for candidate in tx.states.list_for_team(team.id)
        if candidate.name.casefold() == wanted
    ]
    if not matches:
        raise NotFound(resource="workflow_state", key=ref)
    if len(matches) > 1:
        raise Validation(
            f"state name {ref} is ambiguous; pass the state of a single team",
            rule="STATE_NAME_AMBIGUOUS",
            field="state",
            value=ref,
            code="STATE_NAME_AMBIGUOUS",
        )
    return matches[0]


def update_state(uow: UnitOfWork, cmd: UpdateState, actor: Actor) -> WorkflowState:
    """Rename, recolor and/or reorder one state in a single `uow.write()` (SPEC-040 §2.2).

    Renaming repeats the case-insensitive duplicate check; `color=None` keeps the current one
    (`workflow_state.color` is `NOT NULL`). Reordering is the explicit `position` the edge
    computed with `domain.ranking`, and `_unique_position` keeps it off a sibling's slot. The
    team must still hold **exactly one** `is_default` when the write ends, so a database already
    broken (zero or two defaults) is rejected instead of being silently carried forward.

    raises: `NotFound(workflow_state)`, `Validation(STATE_NAME_EMPTY|STATE_TYPE_INVALID|
    COLOR_FORMAT)`, `Conflict(UNIQUE_STATE_NAME|STATE_DEFAULT_INVARIANT)`
    """
    with uow.write() as tx:
        state = _resolve_state(tx, cmd.state)
        team_states = tx.states.list_for_team(state.team_id)
        defaults = [candidate for candidate in team_states if candidate.is_default]
        if len(defaults) != 1:
            raise Conflict(
                f"team {state.team_id} must have exactly one default state, found {len(defaults)}",
                rule="STATE_DEFAULT_INVARIANT",
                field="is_default",
                value=len(defaults),
                code="STATE_DEFAULT_INVARIANT",
            )

        name = state.name
        if not isinstance(cmd.name, Unset):
            name = _validate_state_name(cmd.name)
            duplicate = _duplicate_state(
                tx.states.list_for_team(state.team_id, include_archived=True),
                name,
                ignore_id=state.id,
            )
            if duplicate is not None:
                raise Conflict(
                    f"state {name} already exists in this team",
                    rule="UNIQUE_STATE_NAME",
                    field="name",
                    value=name,
                    code="UNIQUE_STATE_NAME",
                )
        color = state.color
        if not isinstance(cmd.color, Unset) and cmd.color is not None:
            color = _validate_state_color(cmd.color)

        position = state.position
        if isinstance(cmd.position, (int, float)) and not isinstance(cmd.position, bool):
            position = _unique_position(team_states, float(cmd.position), moving_id=state.id)

        return tx.states.update(
            state.model_copy(update={"name": name, "color": color, "position": position})
        )


# --- state reads (SPEC-050 §2.5.3 `hifa state list`) ---------------------------------------


def list_states(uow: UnitOfWork, team: str) -> tuple[StateSummary, ...]:
    """States of one team ordered by `position` ASC, each with its live issue count.

    raises: `NotFound(team)`
    """
    with uow.read() as tx:
        entity = _resolve_team(tx, team)
        counts = tx.states.issue_counts(entity.id)
        return tuple(
            StateSummary(state=state, issues_count=counts.get(state.id, 0))
            for state in tx.states.list_for_team(entity.id)
        )


__all__ = [
    "DEFAULT_LABELS",
    "DEFAULT_STATE_COLOR",
    "DEFAULT_STATES",
    "STATE_NAME_MAX_LENGTH",
    "BootstrapOptions",
    "BootstrapResult",
    "StateSummary",
    "TeamDetail",
    "TeamSummary",
    "bootstrap",
    "create_state",
    "create_team",
    "list_states",
    "list_teams",
    "team_detail",
    "update_state",
]
