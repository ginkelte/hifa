"""`hifa label` — the label catalogue.

`add`, `list`, `show`, `update` and `archive` only parse flags, resolve the database and delegate
to `app/labels.py`: no rule and no SQL lives at this edge. `archive --undo` is the undo of a
retire, and it is a second use case because it writes a different value of one column.

A `REF` of `show`, `update` and `archive` is a ULID or a name resolved over the whole workspace,
retired rows included; a name two teams share is refused instead of picked. The `TEAM` column and
the `Team` field print the stored `team_id` — the ULID the canonical `Label` carries — because no
use case returns a team key and this edge does not read one of its own to translate it.
"""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app import members
from hifa.app.commands import (
    UNSET,
    Actor,
    CreateLabel,
    RestoreLabel,
    RetireLabel,
    Unset,
    UpdateLabel,
)
from hifa.app.labels import (
    DEFAULT_LABEL_COLOR,
    create_label,
    label_detail,
    list_labels,
    restore_label,
    retire_label,
    update_label,
)
from hifa.cli import output
from hifa.cli.context import CliContext
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import Config, ConfigError, resolve_config
from hifa.domain.entities import Label
from hifa.domain.errors import HifaError

label_app = typer.Typer(
    name="label",
    help="Labels: add, list, show, update, archive.",
    no_args_is_help=True,
    add_completion=False,
)

#: Columns of `label list` and of the row `add`, `update` and `archive` print.
LABEL_TABLE_COLUMNS: tuple[str, ...] = ("name", "color", "team", "retired")

#: Empty cell of the table, as `output` renders it.
EMPTY = "–"


class GroupChoice(enum.StrEnum):
    """`--group {multiSelect,singleSelect}`: a closed set Typer rejects at parse time."""

    multiSelect = "multiSelect"
    singleSelect = "singleSelect"


@label_app.command("add")
def add_command(
    ctx: typer.Context,
    name: str = typer.Option(..., "-n", "--name", help="Label name, unique at its level."),
    color: str = typer.Option(DEFAULT_LABEL_COLOR, "--color", help="Label color, #RRGGBB."),
    team: str | None = typer.Option(None, "--team", help="Team key or ULID of the new label."),
    no_team: bool = typer.Option(
        False, "--no-team", help="Create a workspace label (the default anyway)."
    ),
    description: str | None = typer.Option(None, "-d", "--description", help="Description."),
    parent: str | None = typer.Option(None, "--parent", help="Group this label belongs to."),
    group: GroupChoice | None = typer.Option(
        None, "--group", help="Create a group: multiSelect or singleSelect."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Label id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Create one label: a workspace label by default, a team label with `--team`."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)
    _reject_team_pair(team, no_team)

    try:
        created = _run_create(
            db_flag=db if db is not None else root.db,
            name=name,
            color=color,
            team=None if no_team else team,
            description=description,
            parent=parent,
            group=None if group is None else group.value,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    payload = _canonical(created)
    output.emit(
        _table_row(created),
        table_cols=LABEL_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["id"],
    )


@label_app.command("list")
def list_command(
    ctx: typer.Context,
    team: str | None = typer.Option(None, "--team", help="Show what an issue of this team wears."),
    include_retired: bool = typer.Option(
        False, "--all", help="Include retired labels, flagged in the RETIRED column."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Label ids only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """List the catalogue ordered by name; retired labels only with `--all`."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        labels = _run_list(
            db_flag=db if db is not None else root.db,
            team=team,
            include_retired=bool(include_retired),
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    rows = [_table_row(label) for label in labels]
    output.emit(
        rows,
        table_cols=LABEL_TABLE_COLUMNS,
        json_obj={"items": [_canonical(label) for label in labels]},
        quiet_obj=[label.id for label in labels],
    )


@label_app.command("show")
def show_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Label ULID or name."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Label id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Show one label: identity, level, group and retirement, one field per line."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        label, parent = _run_show(db_flag=db if db is not None else root.db, ref=ref)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    current = output.options()
    if current.json:
        output.write_json(_canonical(label))
        return
    if current.quiet:
        output.write_line(label.id)
        return
    for line in _show_lines(label, parent):
        output.write_line(line)


@label_app.command("update")
def update_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Label ULID or name."),
    new_name: str | None = typer.Option(None, "--name", help="New name."),
    color: str | None = typer.Option(None, "--color", help="New color, #RRGGBB."),
    description: str | None = typer.Option(None, "-d", "--description", help="New description."),
    clear_description: bool = typer.Option(
        False, "--clear-description", help="Remove the description."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Label id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Rename, recolor or re-describe one label; the level of a label is not editable."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)
    _reject_no_changes(new_name, color, description, clear_description)
    _reject_description_pair(description, clear_description)

    try:
        updated = _run_update(
            db_flag=db if db is not None else root.db,
            ref=ref,
            name=new_name,
            color=color,
            description=None if clear_description else description,
            clears_description=bool(clear_description),
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    payload = _canonical(updated)
    output.emit(
        _table_row(updated),
        table_cols=LABEL_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["id"],
    )


@label_app.command("archive")
def archive_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Label ULID or name."),
    undo: bool = typer.Option(False, "--undo", help="Undo the retire."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Label id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Retire one label, or undo that with `--undo`: the row is never deleted."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    _reject_json_quiet(json_mode, quiet_mode)

    try:
        label = _run_archive(db_flag=db if db is not None else root.db, ref=ref, undo=undo)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    payload = _canonical(label)
    output.emit(
        _table_row(label),
        table_cols=LABEL_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["id"],
    )


# --- orchestration -------------------------------------------------------------------------


def _reject_json_quiet(json_mode: bool, quiet_mode: bool) -> None:
    """`--json` and `--quiet` are mutually exclusive in every leaf command."""
    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive", code="invalid_option", field="output"
            )
        )


def _reject_team_pair(team: str | None, no_team: bool) -> None:
    """`--team` and `--no-team` answer the same question with two values."""
    if team is not None and no_team:
        raise fail(
            CliError(
                "--team and --no-team are mutually exclusive",
                code="invalid_option",
                field="team",
            )
        )


def _reject_no_changes(
    name: str | None, color: str | None, description: str | None, clear_description: bool
) -> None:
    """An update with no flag is a refusal, not a silent success, and runs before the database."""
    if name is None and color is None and description is None and not clear_description:
        raise fail(CliError("nothing to change", code="no_changes"))


def _reject_description_pair(description: str | None, clear_description: bool) -> None:
    """Clearing and setting one column at once is a bad invocation, not a precedence."""
    if description is not None and clear_description:
        raise fail(
            CliError(
                "use -d/--description or --clear-description, not both",
                code="invalid_option",
                field="description",
            )
        )


def _config(db_flag: str | None) -> Config:
    """Resolve the process configuration once per invocation."""
    return resolve_config(db_flag=db_flag)


def _database(cfg: Config) -> Database:
    """Open the configured file for reading; a missing one is exit 3."""
    return Database(_existing_path(cfg))


def _uow(cfg: Config) -> UnitOfWork:
    """Borrow one UoW over the configured file."""
    return UnitOfWork(_existing_path(cfg), clock=SystemClock(), idgen=UlidGen())


def _existing_path(cfg: Config) -> Path:
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found at {db_path}; run 'hifa init'",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )
    return db_path


def _actor(repos: Repos) -> Actor:
    """Who writes: the local member, when the workspace has exactly one."""
    return Actor(member_id=members.local_member(repos), source="cli", label="cli")


def _run_create(
    *,
    db_flag: str | None,
    name: str,
    color: str,
    team: str | None,
    description: str | None,
    parent: str | None,
    group: str | None,
) -> Label:
    uow = _uow(_config(db_flag))
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        cmd = CreateLabel(
            name=name,
            team=team,
            color=color,
            description=description,
            parent=parent,
            is_group=group is not None,
            group_type=group,
        )
        return create_label(uow, cmd, actor)
    finally:
        uow.close()


def _run_list(*, db_flag: str | None, team: str | None, include_retired: bool) -> tuple[Label, ...]:
    return list_labels(_database(_config(db_flag)), team=team, include_retired=include_retired)


def _run_show(*, db_flag: str | None, ref: str) -> tuple[Label, Label | None]:
    db = _database(_config(db_flag))
    label = label_detail(db, ref)
    parent = label_detail(db, label.parent_id) if label.parent_id is not None else None
    return label, parent


def _run_update(
    *,
    db_flag: str | None,
    ref: str,
    name: str | None,
    color: str | None,
    description: str | None,
    clears_description: bool,
) -> Label:
    uow = _uow(_config(db_flag))
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        if clears_description:
            description_value: str | None | Unset = None
        elif description is not None:
            description_value = description
        else:
            description_value = UNSET
        cmd = UpdateLabel(
            label=ref,
            name=name if name is not None else UNSET,
            color=color if color is not None else UNSET,
            description=description_value,
        )
        return update_label(uow, cmd, actor)
    finally:
        uow.close()


def _run_archive(*, db_flag: str | None, ref: str, undo: bool) -> Label:
    uow = _uow(_config(db_flag))
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        if undo:
            return restore_label(uow, RestoreLabel(label=ref), actor)
        return retire_label(uow, RetireLabel(label=ref), actor)
    finally:
        uow.close()


# --- presentation --------------------------------------------------------------------------


def _canonical(label: Label) -> dict[str, Any]:
    """Complete `Label` object in canonical order (no key is ever omitted)."""
    return {
        "id": label.id,
        "name": label.name,
        "color": label.color,
        "description": label.description,
        "team_id": label.team_id,
        "parent_id": label.parent_id,
        "is_group": bool(label.is_group),
        "group_type": None if label.group_type is None else str(label.group_type),
        "retired_at": _iso(label.retired_at),
    }


def _table_row(label: Label) -> dict[str, Any]:
    """Table-shaped row: NAME COLOR TEAM RETIRED.

    `TEAM` is the stored `team_id` as it is — the ULID the canonical object carries — or `–` for
    a workspace label; `RETIRED` is the instant or `–`.
    """
    return {
        "name": label.name,
        "color": label.color,
        "team": label.team_id,
        "retired": _iso(label.retired_at),
    }


def _show_lines(label: Label, parent: Label | None) -> list[str]:
    """`label show` block: the name as header, then one `key  value` line per field."""
    group = EMPTY if label.group_type is None else str(label.group_type)
    fields: tuple[tuple[str, Any], ...] = (
        ("Color", label.color),
        ("Team", label.team_id),
        ("Group", group),
        ("Parent", None if parent is None else parent.name),
        ("Description", label.description),
        ("Retired", _iso(label.retired_at)),
    )
    lines = [label.name]
    lines.extend(f"{key:<12}{EMPTY if value is None else value}" for key, value in fields)
    return lines


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


__all__ = [
    "EMPTY",
    "GroupChoice",
    "LABEL_TABLE_COLUMNS",
    "add_command",
    "archive_command",
    "label_app",
    "list_command",
    "show_command",
    "update_command",
]
