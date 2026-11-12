"""Typed application commands.

A command is the **only** transport for a write: there is no change `dict`. `__post_init__`
validates shape and local combinations: a primitive that does not convert is
`Validation(rule='COMMAND_FIELD')`. Semantics (empty title, priority range, membership in
the database) are validated by the use case through `domain.rules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, TypeVar

from hifa.domain.enums import EstimationType, Priority, TeamVisibility
from hifa.domain.errors import Validation
from hifa.domain.identifier import normalize_key


class _Unset:
    """Sentinel for 'do not touch this field'. It is not None: None clears."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


Unset = _Unset
UNSET: Final = _Unset()
Maybe = TypeVar("Maybe")  # documentary alias: T | Unset


def _command_field(field_name: str, value: object) -> Validation:
    return Validation(
        f"{field_name}: invalid value {value!r}",
        rule="COMMAND_FIELD",
        field=field_name,
        value=value,
        code="COMMAND_FIELD",
    )


@dataclass(frozen=True, slots=True)
class Actor:
    """Identity of whoever performs the write; never persisted on `issue`."""

    member_id: str | None = None
    source: Literal["cli", "api", "web", "import"] = "cli"
    label: str | None = None  # display name for history

    def __post_init__(self) -> None:
        if self.source not in ("cli", "api", "web", "import"):
            raise Validation(
                "actor.source must be cli|api|web|import",
                rule="ACTOR_SOURCE",
                field="source",
                value=self.source,
                code="ACTOR_SOURCE",
            )


@dataclass(frozen=True, slots=True)
class CreateIssue:
    """Issue creation. `team` is a ULID or key; `state` a name or ULID."""

    team: str
    title: str
    description: str | None = None
    priority: Priority | int = Priority.NO_PRIORITY
    state: str | None = None
    assignee_id: str | None = None
    project_id: str | None = None
    milestone_id: str | None = None
    cycle_id: str | None = None
    parent: str | None = None
    labels: tuple[str, ...] = ()
    estimate: float | None = None
    due_date: date | str | None = None
    board_order: float | None = None
    sort_order: float | None = None
    creator_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.team, str) or not self.team.strip():
            raise _command_field("team", self.team)
        if not isinstance(self.title, str):
            raise _command_field("title", self.title)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise _command_field("priority", self.priority)
        if isinstance(self.labels, (list, tuple)):
            cleaned: list[str] = []
            for raw in self.labels:
                if not isinstance(raw, str):
                    raise _command_field("labels", raw)
                text = raw.strip()
                if text and text not in cleaned:
                    cleaned.append(text)
            object.__setattr__(self, "labels", tuple(cleaned))
        else:
            raise _command_field("labels", self.labels)
        if self.due_date is not None and not isinstance(self.due_date, (str, date)):
            raise _command_field("due_date", self.due_date)
        if self.estimate is not None and (
            isinstance(self.estimate, bool) or not isinstance(self.estimate, (int, float))
        ):
            raise _command_field("estimate", self.estimate)


@dataclass(frozen=True, slots=True)
class CreateTeam:
    """Team creation.

    `key` is normalized (`strip().upper()`) but **not** validated here: the pattern is
    `Validation(rule='TEAM_KEY_INVALID')` from the use case, so the rule and the field are the
    ones the contract fixes. `icon`/`color`/`description` empty values become `None`.
    """

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
    default_state: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise _command_field("key", self.key)
        object.__setattr__(self, "key", normalize_key(self.key))
        if not isinstance(self.name, str):
            raise _command_field("name", self.name)
        object.__setattr__(self, "name", self.name.strip())
        for field_name in ("icon", "color", "description"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise _command_field(field_name, value)
            object.__setattr__(self, field_name, value.strip() or None)
        if not isinstance(self.timezone, str):
            raise _command_field("timezone", self.timezone)
        object.__setattr__(self, "timezone", self.timezone.strip())
        if not isinstance(self.visibility, str):
            raise _command_field("visibility", self.visibility)
        visibility = self.visibility.strip().lower()
        if visibility not in {member.value for member in TeamVisibility}:
            raise _command_field("visibility", self.visibility)
        object.__setattr__(self, "visibility", visibility)
        try:
            estimation = EstimationType(self.estimation_type)
        except ValueError as exc:
            raise _command_field("estimation_type", self.estimation_type) from exc
        object.__setattr__(self, "estimation_type", estimation)
        for field_name in ("cycles_enabled", "triage_enabled"):
            if not isinstance(getattr(self, field_name), bool):
                raise _command_field(field_name, getattr(self, field_name))
        for field_name in ("cycle_duration", "cycle_start_day"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise _command_field(field_name, value)
        if self.default_state is not None:
            if not isinstance(self.default_state, str):
                raise _command_field("default_state", self.default_state)
            object.__setattr__(self, "default_state", self.default_state.strip() or None)


@dataclass(frozen=True, slots=True)
class CreateState:
    """Workflow state creation.

    `team` is a ULID or key. `position` is the absolute fractional position
    (`None` = last of the team). The neighbour anchors (`--before`/`--after` on the CLI)
    are resolved at the edge, which computes the midpoint with `domain.ranking` and sends it
    here as `position`: the command never carries a neighbour.
    """

    team: str
    name: str
    state_type: str
    color: str | None = None
    position: float | None = None

    def __post_init__(self) -> None:
        _validate_state_fields(self)


@dataclass(frozen=True, slots=True)
class UpdateState:
    """Workflow state update.

    `name`/`color`/`position` use the `UNSET` sentinel: `UNSET` keeps, `None` clears (a `None`
    color falls back to the current one, because `color` is `NOT NULL`). Reordering is
    `position=<float>`: the caller (CLI or dashboard) resolves the neighbours and asks
    `domain.ranking` for the midpoint, so there is a single implementation of the arithmetic.
    """

    state: str
    name: str | Unset = UNSET
    color: str | None | Unset = UNSET
    position: float | None | Unset = UNSET

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state.strip():
            raise _command_field("state", self.state)
        object.__setattr__(self, "state", self.state.strip())
        name = self.name
        if name is not UNSET:
            if not isinstance(name, str):
                raise _command_field("name", name)
            object.__setattr__(self, "name", name.strip())
        color = self.color
        if color is not UNSET and color is not None:
            if not isinstance(color, str):
                raise _command_field("color", color)
            object.__setattr__(self, "color", color.strip())
        position = self.position
        if position is not UNSET and position is not None:
            if isinstance(position, bool) or not isinstance(position, (int, float)):
                raise _command_field("position", position)
            object.__setattr__(self, "position", float(position))


def _validate_state_fields(cmd: CreateState) -> None:
    """Shape validation of `CreateState` (membership in the enum is a use case rule)."""
    if not isinstance(cmd.team, str) or not cmd.team.strip():
        raise _command_field("team", cmd.team)
    object.__setattr__(cmd, "team", cmd.team.strip())
    if not isinstance(cmd.name, str):
        raise _command_field("name", cmd.name)
    if not isinstance(cmd.state_type, str):
        raise _command_field("state_type", cmd.state_type)
    object.__setattr__(cmd, "state_type", cmd.state_type.strip().lower())
    if cmd.color is not None:
        if not isinstance(cmd.color, str):
            raise _command_field("color", cmd.color)
        object.__setattr__(cmd, "color", cmd.color.strip() or None)
    if cmd.position is not None:
        if isinstance(cmd.position, bool) or not isinstance(cmd.position, (int, float)):
            raise _command_field("position", cmd.position)
        object.__setattr__(cmd, "position", float(cmd.position))


__all__ = [
    "UNSET",
    "Actor",
    "CreateIssue",
    "CreateState",
    "CreateTeam",
    "Maybe",
    "Unset",
    "UpdateState",
]
