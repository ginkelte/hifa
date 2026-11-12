"""`hifa state` — workflow state subcommands.

`list`, `create` and `update` only parse flags, resolve the database and delegate to
`app/teams.py`: no business rule and no SQL lives at this edge. The neighbour of
`--before`/`--after` is resolved here **by name** through `list_states` (an app read), and the
fractional midpoint comes from `domain.ranking.between` — the same helper the dashboard and
`move_issue` use — so the use case only ever receives a `position`.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import UNSET, Actor, CreateState, Unset, UpdateState
from hifa.app.teams import (
    StateSummary,
    create_state,
    list_states,
    update_state,
)
from hifa.cli import output
from hifa.cli.commands.team import MISSING_CONTEXT_MESSAGE, default_team
from hifa.cli.context import CliContext
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import Config, ConfigError, resolve_config
from hifa.domain.entities import WorkflowState
from hifa.domain.errors import HifaError, NotFound, Validation
from hifa.domain.ranking import UNIT_GAP, between

state_app = typer.Typer(
    name="state",
    help="Workflow states: list, create, update.",
    no_args_is_help=True,
    add_completion=False,
)

#: Columns of `state list` / `state create` / `state update`.
STATE_TABLE_COLUMNS: tuple[str, ...] = (
    "position",
    "name",
    "type",
    "color",
    "default",
    "issues",
)


class StateTypeChoice(enum.StrEnum):
    """`--type {triage,backlog,unstarted,started,completed,canceled}`."""

    triage = "triage"
    backlog = "backlog"
    unstarted = "unstarted"
    started = "started"
    completed = "completed"
    canceled = "canceled"


@state_app.command("list")
def list_command(
    ctx: typer.Context,
    team: str | None = typer.Option(None, "--team", help="Team key or ULID (default team)."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="State ids only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """List the workflow states of a team, ordered by position."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        summaries = _run_list(db_flag=db if db is not None else root.db, team=team)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    rows = [_row(summary) for summary in summaries]
    output.emit(
        [_table_row(row) for row in rows],
        table_cols=STATE_TABLE_COLUMNS,
        json_obj={"items": rows},
        quiet_obj=[row["id"] for row in rows],
    )


@state_app.command("create")
def create_command(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="State name, unique per team."),
    state_type: StateTypeChoice = typer.Option(
        ..., "--type", help="triage, backlog, unstarted, started, completed or canceled."
    ),
    color: str | None = typer.Option(None, "--color", help="State color, #RRGGBB."),
    team: str | None = typer.Option(None, "--team", help="Team key or ULID (default team)."),
    before: str | None = typer.Option(None, "--before", help="Insert before this state name."),
    after: str | None = typer.Option(None, "--after", help="Insert after this state name."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="New state id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Create one custom state; without anchors it goes last."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)
    _reject_anchor_pair(before, after)

    try:
        state, issues_count = _run_create(
            db_flag=db if db is not None else root.db,
            team=team,
            name=name,
            state_type=state_type.value,
            color=color,
            before=before,
            after=after,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    output.emit(
        _table_row({**_state(state), "issues_count": issues_count}),
        table_cols=STATE_TABLE_COLUMNS,
        json_obj={**_state(state), "issues_count": issues_count},
        quiet_obj=state.id,
    )


@state_app.command("update")
def update_command(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="State name or ULID to update."),
    new_name: str | None = typer.Option(None, "--name", help="New name."),
    color: str | None = typer.Option(None, "--color", help="New color, #RRGGBB."),
    team: str | None = typer.Option(None, "--team", help="Team key or ULID (default team)."),
    before: str | None = typer.Option(None, "--before", help="Move before this state name."),
    after: str | None = typer.Option(None, "--after", help="Move after this state name."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="State id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Rename, recolor or reorder one state of a team."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)
    _reject_anchor_pair(before, after)

    try:
        state, issues_count = _run_update(
            db_flag=db if db is not None else root.db,
            team=team,
            name=name,
            new_name=new_name,
            color=color,
            before=before,
            after=after,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    output.emit(
        _table_row({**_state(state), "issues_count": issues_count}),
        table_cols=STATE_TABLE_COLUMNS,
        json_obj={**_state(state), "issues_count": issues_count},
        quiet_obj=state.id,
    )


# --- orchestration ------------------------------------------------------------------------


def _reject_json_quiet(json_mode: bool, quiet_mode: bool) -> None:
    """`--json` and `--quiet` are mutually exclusive in every leaf command."""
    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive", code="invalid_option", field="output"
            )
        )


def _reject_anchor_pair(before: str | None, after: str | None) -> None:
    """`--before` and `--after` cannot travel together."""
    if before is not None and after is not None:
        raise fail(
            CliError(
                "--before and --after are mutually exclusive",
                code="invalid_option",
                field="before",
            )
        )


def _config(db_flag: str | None) -> Config:
    """Resolve the process configuration once per invocation."""
    return resolve_config(db_flag=db_flag)


def _uow(cfg: Config) -> UnitOfWork:
    """Borrow one UoW; a missing database file is a `not found` (exit 3)."""
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found at {db_path}; run 'hifa init'",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )
    return UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())


def _team_ref(cfg: Config, uow: UnitOfWork, team: str | None) -> str:
    """`--team`, else the configured default resolution (exit 2 when it cannot be resolved)."""
    if team is not None and team.strip():
        return team.strip()
    return default_team(cfg, uow)


def _neighbor_id(uow: UnitOfWork, team_ref: str, name: str | None) -> str | None:
    """Resolve the `--before`/`--after` name (or ULID) to its state id; `NotFound` otherwise."""
    if name is None:
        return None
    wanted = name.strip()
    for summary in list_states(uow, team_ref):
        if summary.state.name.casefold() == wanted.casefold() or summary.state.id == wanted:
            return summary.state.id
    raise NotFound(resource="workflow_state", key=wanted)


def _anchor_position(
    uow: UnitOfWork,
    team_ref: str,
    *,
    before: str | None,
    after: str | None,
    moving_id: str | None = None,
) -> float | None:
    """`position` for `--before`/`--after`, or `None` when neither flag was given.

    The anchor is a **name** (or ULID) resolved against the team, and the slot is the midpoint
    between it and its neighbour on the chosen side, computed by `domain.ranking.between` over
    the `workflow_state.position` scale (`UNIT_GAP`). `moving_id` is excluded from the walk so
    that moving a state against one of its own neighbours is a no-op instead of a drift.
    An anchor that is the moving state itself (`STATE_POSITION_SELF`) or that does not exist
    (`NotFound`, exit 3) is rejected before any write.
    """
    if before is None and after is None:
        return None
    summaries = [summary.state for summary in list_states(uow, team_ref)]
    wanted = before if before is not None else after
    assert wanted is not None  # narrowed by the early return above
    wanted = wanted.strip()
    anchor = next(
        (
            state
            for state in summaries
            if state.name.casefold() == wanted.casefold() or state.id == wanted
        ),
        None,
    )
    if anchor is None:
        raise NotFound(resource="workflow_state", key=wanted)
    field_name = "before" if before is not None else "after"
    if anchor.id == moving_id:
        raise Validation(
            "a state cannot be anchored to itself",
            rule="STATE_POSITION_SELF",
            field=field_name,
            value=wanted,
            code="STATE_POSITION_SELF",
        )
    neighbours = [state for state in summaries if state.id != moving_id]
    index = next(i for i, state in enumerate(neighbours) if state.id == anchor.id)
    if before is not None:
        lower = neighbours[index - 1] if index > 0 else None
        upper = neighbours[index]
    else:
        lower = neighbours[index]
        upper = neighbours[index + 1] if index + 1 < len(neighbours) else None
    return between(
        None if lower is None else lower.position,
        None if upper is None else upper.position,
        gap=UNIT_GAP,
    )


def _issues_count(uow: UnitOfWork, team_ref: str, state_id: str) -> int:
    """Visible issues in the state, from the same read `state list` formats (never assumed)."""
    for summary in list_states(uow, team_ref):
        if summary.state.id == state_id:
            return summary.issues_count
    return 0


def _optional_position(value: float | None) -> float | Unset:
    """`position` for `UpdateState`: the computed slot, or `UNSET` (= keep the current one)."""
    return UNSET if value is None else value


def _run_list(*, db_flag: str | None, team: str | None) -> tuple[StateSummary, ...]:
    cfg = _config(db_flag)
    uow = _uow(cfg)
    try:
        return list_states(uow, _team_ref(cfg, uow, team))
    finally:
        uow.close()


def _run_create(
    *,
    db_flag: str | None,
    team: str | None,
    name: str,
    state_type: str,
    color: str | None,
    before: str | None,
    after: str | None,
) -> tuple[WorkflowState, int]:
    cfg = _config(db_flag)
    uow = _uow(cfg)
    try:
        team_ref = _team_ref(cfg, uow, team)
        cmd = CreateState(
            team=team_ref,
            name=name,
            state_type=state_type,
            color=color,
            position=_anchor_position(uow, team_ref, before=before, after=after),
        )
        created = create_state(uow, cmd, Actor(source="cli", label="cli"))
        return created, _issues_count(uow, team_ref, created.id)
    finally:
        uow.close()


def _run_update(
    *,
    db_flag: str | None,
    team: str | None,
    name: str,
    new_name: str | None,
    color: str | None,
    before: str | None,
    after: str | None,
) -> tuple[WorkflowState, int]:
    cfg = _config(db_flag)
    uow = _uow(cfg)
    try:
        team_ref = _team_ref(cfg, uow, team)
        state_id = _neighbor_id(uow, team_ref, name)
        if state_id is None:  # pragma: no cover - `name` is a required argument
            raise CliError(MISSING_CONTEXT_MESSAGE, code="missing_context", field="state")
        cmd = UpdateState(
            state=state_id,
            name=new_name if new_name is not None else UNSET,
            color=color if color is not None else UNSET,
            position=_optional_position(
                _anchor_position(uow, team_ref, before=before, after=after, moving_id=state_id)
            ),
        )
        updated = update_state(uow, cmd, Actor(source="cli", label="cli"))
        return updated, _issues_count(uow, team_ref, updated.id)
    finally:
        uow.close()


# --- presentation -------------------------------------------------------------------------


def _state(state: WorkflowState) -> dict[str, Any]:
    """Complete `State` object in canonical order."""
    return {
        "id": state.id,
        "name": state.name,
        "type": str(state.type),
        "color": state.color,
        "position": state.position,
        "is_default": bool(state.is_default),
    }


def _row(summary: StateSummary) -> dict[str, Any]:
    """`state list` JSON item: the canonical object plus `issues_count` (additive)."""
    return {**_state(summary.state), "issues_count": summary.issues_count}


def _position_text(value: float | None) -> str:
    """One decimal, always: `1.0`, `2.0`, `2.5`."""
    if value is None:  # pragma: no cover - the column is NOT NULL
        return "–"
    return f"{value:.1f}"


def _table_row(row: dict[str, Any]) -> dict[str, Any]:
    """Table-shaped row: POSITION NAME TYPE COLOR DEFAULT ISSUES.

    `DEFAULT` is the marker of the board's default column: `*` for the `is_default` row and `–`
    for the rest, never `yes`/`no`.
    """
    return {
        "position": _position_text(row.get("position")),
        "name": row.get("name"),
        "type": row.get("type"),
        "color": row.get("color"),
        "default": "*" if row.get("is_default") else "–",
        "issues": int(row.get("issues_count", 0)),
    }


__all__ = [
    "STATE_TABLE_COLUMNS",
    "StateTypeChoice",
    "create_command",
    "list_command",
    "state_app",
    "update_command",
]
