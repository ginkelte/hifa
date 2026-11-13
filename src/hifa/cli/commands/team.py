"""`hifa team` — team subcommands.

`create`, `list` and `show` only parse flags, resolve the database and delegate to
`app/teams.py`: no business rule and no SQL lives at this edge. Every error is translated by
`cli/errors.py`, so the exit codes are the canonical ones (0 ok, 2 validation, 3 not found,
4 conflict).
"""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import Actor, CreateTeam
from hifa.app.teams import (
    TeamDetail,
    TeamSummary,
    create_team,
    list_teams,
    team_detail,
)
from hifa.cli import output
from hifa.cli.context import CliContext, resolve_team
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import DEFAULT_TIMEZONE, Config, ConfigError, resolve_config
from hifa.domain.entities import Team, WorkflowState
from hifa.domain.errors import HifaError

team_app = typer.Typer(
    name="team",
    help="Teams: create, list, show.",
    no_args_is_help=True,
    add_completion=False,
)

#: Columns of `team list` / `team create` (`KEY  NAME  ISSUES  ARCHIVED`).
TEAM_TABLE_COLUMNS: tuple[str, ...] = ("key", "name", "issues", "archived")


#: `--visibility` choices. A `StrEnum` makes Typer reject anything else
#: with exit 2 at parse time, so the edge declares the closed set.
class VisibilityChoice(enum.StrEnum):
    """`--visibility {public,private,restricted}`."""

    public = "public"
    private = "private"
    restricted = "restricted"


@team_app.command("create")
def create_command(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Team key (2-10 chars A-Z/0-9, starts with a letter)."),
    name: str = typer.Argument(..., help="Team name."),
    icon: str | None = typer.Option(None, "--icon", help="Emoji shown in the sidebar."),
    color: str | None = typer.Option(None, "--color", help="Team color, #RRGGBB."),
    description: str | None = typer.Option(None, "--description", help="Free-form description."),
    timezone: str | None = typer.Option(None, "--timezone", help="IANA timezone."),
    visibility: VisibilityChoice = typer.Option(
        VisibilityChoice.public, "--visibility", help="public, private or restricted."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Team key only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Create a team with its six canonical workflow states."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        payload = _run_create(
            db_flag=db if db is not None else root.db,
            key=key,
            name=name,
            icon=icon,
            color=color,
            description=description,
            timezone=timezone,
            visibility=visibility.value,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    row = {"key": payload["key"], "name": payload["name"], "issues": 0, "archived": False}
    output.emit(
        row,
        table_cols=TEAM_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["key"],
    )


@team_app.command("list")
def list_command(
    ctx: typer.Context,
    include_archived: bool = typer.Option(
        False, "--all", help="Include archived teams, flagged in the ARCHIVED column."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Team keys only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """List the teams ordered by key, with the live issue count of each one."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        summaries = _run_list(
            db_flag=db if db is not None else root.db,
            include_archived=bool(include_archived),
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    rows = [_row(summary) for summary in summaries]
    table_rows = [
        {
            "key": row["key"],
            "name": row["name"],
            "issues": row["issues_count"],
            "archived": row["archived"],
        }
        for row in rows
    ]
    # Lists use the `{items: [...]}` envelope, never a bare array.
    output.emit(
        table_rows,
        table_cols=TEAM_TABLE_COLUMNS,
        json_obj={"items": rows},
        quiet_obj=[row["key"] for row in rows],
    )


@team_app.command("show")
def show_command(
    ctx: typer.Context,
    key: str | None = typer.Argument(None, help="Team key (case-insensitive) or ULID."),
    include_states: bool = typer.Option(
        False, "--states", help="Add the workflow states, ordered by position."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Team key only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Show one team: identity, progress counters and the number of workflow states.

    Without `KEY` the team comes from the 2.2.2 resolution (`HIFA_TEAM`, `[cli].default_team`
    or the only non-archived team); when that fails: exit 2 `missing_context`.
    """
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        detail = _run_show(
            db_flag=db if db is not None else root.db,
            key=key,
            include_states=bool(include_states),
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    payload = _canonical(detail.team)
    json_obj: dict[str, Any] = {**payload, "progress": detail.progress}
    if include_states:
        # `states: State[]` sorted by `position` ASC, only with `--states`.
        json_obj["states"] = [_state(state) for state in detail.states]
    output.emit(
        _show_row(detail, include_states=include_states),
        json_obj=json_obj,
        quiet_obj=detail.team.key,
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


def _run_create(
    *,
    db_flag: str | None,
    key: str,
    name: str,
    icon: str | None,
    color: str | None,
    description: str | None,
    timezone: str | None,
    visibility: str,
) -> dict[str, Any]:
    uow = _uow(_config(db_flag))
    try:
        cmd = CreateTeam(
            key=key,
            name=name,
            icon=icon,
            color=color,
            description=description,
            timezone=timezone if timezone is not None else DEFAULT_TIMEZONE,
            visibility=visibility,
        )
        team = create_team(uow, cmd, Actor(source="cli", label="cli"))
        return _canonical(team)
    finally:
        uow.close()


def _run_list(*, db_flag: str | None, include_archived: bool = False) -> tuple[TeamSummary, ...]:
    uow = _uow(_config(db_flag))
    try:
        return list_teams(uow, include_archived=include_archived)
    finally:
        uow.close()


def _run_show(*, db_flag: str | None, key: str | None, include_states: bool = False) -> TeamDetail:
    cfg = _config(db_flag)
    uow = _uow(cfg)
    try:
        with uow.read() as repos:
            ref = resolve_team(repos, cfg, flag=key)
        return team_detail(uow, ref)
    finally:
        uow.close()


# --- presentation -------------------------------------------------------------------------


def _canonical(team: Team) -> dict[str, Any]:
    """Complete `Team` object in canonical order (no key is ever omitted)."""
    return {
        "id": team.id,
        "key": team.key,
        "name": team.name,
        "icon": team.icon,
        "color": team.color,
        "description": team.description,
        "timezone": team.timezone,
        "visibility": str(team.visibility),
        "parent_id": team.parent_id,
        "cycles_enabled": bool(team.cycles_enabled),
        "cycle_duration": int(team.cycle_duration),
        "cycle_start_day": int(team.cycle_start_day),
        "issue_estimation_type": str(team.issue_estimation_type),
        "default_issue_estimate": team.default_issue_estimate,
        "triage_enabled": bool(team.triage_enabled),
        "auto_close_period": team.auto_close_period,
        "archived_at": _iso(team.archived_at),
        "created_at": _iso(team.created_at),
        "updated_at": _iso(team.updated_at),
    }


def _row(summary: TeamSummary) -> dict[str, Any]:
    """`team list` JSON item: the canonical object plus `issues_count`.

    `open` is not emitted: only `issues_count` (`open + closed`) is part of the contract,
    and the table does not use `open` (its ISSUES column is `issues_count`).
    """
    team = summary.team
    return {
        **_canonical(team),
        "issues_count": summary.issues_count,
        "archived": team.archived_at is not None,
    }


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


def _show_row(detail: TeamDetail, *, include_states: bool = False) -> dict[str, Any]:
    """`team show` key-value block.

    With `--states` the `states` line keeps the count and adds the names (the default one
    flagged with `*`), so the flag is not dead in the human format either.
    """
    team = detail.team
    names = ", ".join(
        f"{state.name}*" if state.is_default else state.name for state in detail.states
    )
    states_cell: Any = len(detail.states)
    if include_states and names:
        states_cell = f"{len(detail.states)} ({names})"
    return {
        "key": team.key,
        "name": team.name,
        "icon": team.icon,
        "color": team.color,
        "visibility": str(team.visibility),
        "timezone": team.timezone,
        "states": states_cell,
        "open": detail.progress["open_count"],
        "closed": detail.progress["closed_count"],
        "urgent": detail.progress["urgent_open"],
        "archived": team.archived_at is not None,
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


__all__ = [
    "TEAM_TABLE_COLUMNS",
    "VisibilityChoice",
    "create_command",
    "list_command",
    "show_command",
    "team_app",
]
