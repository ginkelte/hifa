"""`hifa issue` — issue subcommands.

The commands only orchestrate the edge: they normalize flags, resolve context (database,
team, member), build the command object and call the use case. No business rule lives here:
titles, priorities, dates, states, transitions and the archive layer are validated by `app/`
through `domain.rules`.

`edit`, `move`, `archive`, `delete` and `restore` are one write each against the resolved
SQLite file: none opens a socket and none needs `hifa serve` running.

The canonical `Issue` JSON object is assembled in `_canonical`: it is
a presentation helper (it resolves names for the envelope), not a use case.
"""

from __future__ import annotations

import enum
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import IssueListItem, IssuePage, ListFilters, OrderKey
from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.boards import list_issues
from hifa.app.commands import (
    UNSET,
    Actor,
    ArchiveIssue,
    CreateIssue,
    MoveIssue,
    RestoreIssue,
    TrashIssue,
    UpdateIssue,
)
from hifa.app.issues import (
    archive_issue,
    create_issue,
    move_issue,
    restore_issue,
    trash_issue,
    update_issue,
)
from hifa.cli import output
from hifa.cli.context import CliContext, resolve_team
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import ConfigError, resolve_config
from hifa.domain.entities import Issue, Label, Member, Team
from hifa.domain.errors import HifaError, NotFound

issue_app = typer.Typer(
    name="issue",
    help="Manage issues.",
    no_args_is_help=True,
    add_completion=False,
)

#: Columns of the row printed by every issue mutation and by `issue list`.
ISSUE_TABLE_COLUMNS: tuple[str, ...] = (
    "identifier",
    "p",
    "state",
    "assignee",
    "title",
    "labels",
    "due",
)

#: Priority -> single-cell glyph.
PRIORITY_GLYPHS: dict[int, str] = {0: "–", 1: "U", 2: "H", 3: "M", 4: "L"}


class StateTypeChoice(enum.StrEnum):
    """The six workflow state types a listing can select."""

    triage = "triage"
    backlog = "backlog"
    unstarted = "unstarted"
    started = "started"
    completed = "completed"
    canceled = "canceled"


_PRIORITY_NAMES: dict[str, int] = {
    "none": 0,
    "no": 0,
    "nopriority": 0,
    "urgent": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}


@issue_app.command("create")
def create_command(
    ctx: typer.Context,
    title: str | None = typer.Argument(None, help="Issue title (or use -t/--title)."),
    title_option: str | None = typer.Option(None, "-t", "--title", help="Issue title."),
    description: str | None = typer.Option(None, "-d", "--description", help="Description."),
    description_file: str | None = typer.Option(
        None, "--description-file", help="File with the description ('-' = stdin)."
    ),
    team: str | None = typer.Option(None, "--team", help="Team key or ULID."),
    state: str | None = typer.Option(None, "-s", "--state", help="State name or ULID."),
    priority: str | None = typer.Option(
        None, "-p", "--priority", help="0..4, P0..P4, none|urgent|high|medium|low."
    ),
    estimate: float | None = typer.Option(None, "-e", "--estimate", help="Estimate (a number)."),
    assignee: str | None = typer.Option(None, "-a", "--assignee", help="ME, email or member ULID."),
    labels: list[str] | None = typer.Option(
        None, "-l", "--label", help="Label (repeatable or comma-separated)."
    ),
    due: str | None = typer.Option(None, "--due", help="Due date YYYY-MM-DD."),
    parent: str | None = typer.Option(None, "--parent", help="Parent issue (ENG-1 or ULID)."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Create an issue and print the saved entity."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive",
                code="invalid_option",
                field="output",
            )
        )

    try:
        row, payload = _run_create(
            db_flag=db if db is not None else root.db,
            title=title,
            title_option=title_option,
            description=description,
            description_file=description_file,
            team=team,
            state=state,
            priority=priority,
            estimate=estimate,
            assignee=assignee,
            labels=labels,
            due=due,
            parent=parent,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    output.emit(row, table_cols=ISSUE_TABLE_COLUMNS, json_obj=payload)


@issue_app.command("list")
def list_command(
    ctx: typer.Context,
    team: str | None = typer.Option(None, "--team", help="Team key or ULID."),
    state: list[str] | None = typer.Option(
        None, "-s", "--state", help="State name (case-insensitive) or ULID (repeatable)."
    ),
    state_type: list[StateTypeChoice] | None = typer.Option(
        None,
        "--state-type",
        help="triage, backlog, unstarted, started, completed or canceled (repeatable).",
    ),
    priority: list[int] | None = typer.Option(
        None, "-p", "--priority", help="Priority 0..4, 0 is No priority (repeatable)."
    ),
    assignee: str | None = typer.Option(
        None, "-a", "--assignee", help="Member id, display name or email."
    ),
    mine: bool = typer.Option(False, "--mine", help="Only the issues of the resolved member."),
    unassigned: bool = typer.Option(
        False, "--unassigned", help="Only the issues without an assignee."
    ),
    labels: list[str] | None = typer.Option(
        None, "-l", "--label", help="Label name (case-insensitive) or ULID (repeatable)."
    ),
    project: str | None = typer.Option(None, "--project", help="Project ULID."),
    cycle: str | None = typer.Option(None, "--cycle", help="Cycle ULID."),
    no_cycle: bool = typer.Option(False, "--no-cycle", help="Only the issues without a cycle."),
    parent: str | None = typer.Option(
        None, "--parent", help="Parent issue (ENG-1 or ULID): its direct children only."
    ),
    query: str | None = typer.Option(None, "--query", help="Substring of the title."),
    open_issues: bool = typer.Option(
        True, "--open/--all", help="Only open issues (default) or every state."
    ),
    archived: bool = typer.Option(False, "--archived", help="Only the archived issues."),
    trashed: bool = typer.Option(False, "--trashed", help="Only the trash (implies --all)."),
    sort: str = typer.Option(
        "manual", "--sort", help="manual (default), created, updated, priority or due."
    ),
    reverse: bool = typer.Option(False, "--reverse", help="Invert the order."),
    limit: int = typer.Option(50, "--limit", help="Page size, 1-200."),
    cursor: str | None = typer.Option(
        None, "--cursor", help="Cursor from the next_cursor of the previous page."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """List the issues of one team, one page at a time."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)
    json_mode = output.options().json
    quiet_mode = output.options().quiet

    try:
        _reject_contradictory_filters(
            assignee=assignee,
            mine=mine,
            unassigned=unassigned,
            archived=archived,
            trashed=trashed,
            cycle=cycle,
            no_cycle=no_cycle,
        )
        rows, items, page = _run_list(
            db_flag=db if db is not None else root.db,
            team=team,
            state=state,
            state_types=state_type,
            priorities=priority,
            assignee=assignee,
            mine=mine,
            unassigned=unassigned,
            labels=labels,
            project=project,
            cycle=cycle,
            no_cycle=no_cycle,
            parent=parent,
            query=query,
            open_issues=open_issues,
            archived=archived,
            trashed=trashed,
            sort=sort,
            reverse=reverse,
            limit=limit,
            cursor=cursor,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    envelope = {
        "items": items,
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
        "total_estimate": None,
    }
    output.emit(
        rows,
        table_cols=ISSUE_TABLE_COLUMNS,
        json_obj=envelope,
        quiet_obj=[row["identifier"] for row in rows],
    )
    if not rows and not json_mode and not quiet_mode:
        output.write_note("0 issues")


@issue_app.command("edit")
def edit_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    title: str | None = typer.Option(None, "-t", "--title", help="New title."),
    description: str | None = typer.Option(None, "-d", "--description", help="New description."),
    description_file: str | None = typer.Option(
        None, "--description-file", help="File with the new description ('-' = stdin)."
    ),
    clear_description: bool = typer.Option(
        False, "--clear-description", help="Write NULL in the description."
    ),
    priority: str | None = typer.Option(
        None, "-p", "--priority", help="0..4, P0..P4, none|urgent|high|medium|low."
    ),
    state: str | None = typer.Option(
        None, "-s", "--state", help="Target state name or ULID (appends to its column)."
    ),
    assignee: str | None = typer.Option(None, "-a", "--assignee", help="ME, email or member ULID."),
    clear_assignee: bool = typer.Option(False, "--clear-assignee", help="Unassign the issue."),
    project: str | None = typer.Option(None, "--project", help="Project reference."),
    clear_project: bool = typer.Option(False, "--clear-project", help="Detach the project."),
    milestone: str | None = typer.Option(None, "--milestone", help="Milestone reference."),
    clear_milestone: bool = typer.Option(False, "--clear-milestone", help="Detach the milestone."),
    cycle: str | None = typer.Option(None, "--cycle", help="Cycle reference."),
    clear_cycle: bool = typer.Option(False, "--clear-cycle", help="Detach the cycle."),
    parent: str | None = typer.Option(None, "--parent", help="Parent issue (ENG-1 or ULID)."),
    clear_parent: bool = typer.Option(False, "--clear-parent", help="Unparent the issue."),
    estimate: str | None = typer.Option(None, "-e", "--estimate", help="Estimate (a number)."),
    clear_estimate: bool = typer.Option(False, "--clear-estimate", help="Write NULL in estimate."),
    due: str | None = typer.Option(None, "--due", help="Due date YYYY-MM-DD."),
    clear_due: bool = typer.Option(False, "--clear-due", help="Write NULL in the due date."),
    before: str | None = typer.Option(None, "--before", hidden=True),
    after: str | None = typer.Option(None, "--after", hidden=True),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Change the fields of one issue and print the saved entity."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        _reject_board_anchor(before, after)
        row, payload = _run_edit(
            db_flag=db if db is not None else root.db,
            ref=ref,
            title=title,
            description=description,
            description_file=description_file,
            clear_description=clear_description,
            priority=priority,
            state=state,
            assignee=assignee,
            clear_assignee=clear_assignee,
            project=project,
            clear_project=clear_project,
            milestone=milestone,
            clear_milestone=clear_milestone,
            cycle=cycle,
            clear_cycle=clear_cycle,
            parent=parent,
            clear_parent=clear_parent,
            estimate=estimate,
            clear_estimate=clear_estimate,
            due=due,
            clear_due=clear_due,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue(row, payload)


@issue_app.command("move")
def move_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    state: str = typer.Option(
        ..., "-s", "--state", help="State name (case-insensitive) or ULID of the issue's team."
    ),
    before: str | None = typer.Option(None, "--before", help="Place the issue before this one."),
    after: str | None = typer.Option(None, "--after", help="Place the issue after this one."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Move one issue into a state of its team and print the saved entity."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        row, payload = _run_move(
            db_flag=db if db is not None else root.db,
            ref=ref,
            state=state,
            before=before,
            after=after,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue(row, payload)


@issue_app.command("archive")
def archive_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    undo: bool = typer.Option(False, "--undo", help="Undo the archive (restore the issue)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation."),
    no_input: bool = typer.Option(False, "--no-input", help="Never prompt; requires --yes."),
    hard: bool = typer.Option(False, "--hard", hidden=True),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Take one issue out of every listing, or undo that with `--undo`."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        _reject_hard_delete(hard)
        if not undo:
            _confirm_archive(ref, yes=yes, no_input=no_input)
        row, payload = _run_archive(db_flag=db if db is not None else root.db, ref=ref, undo=undo)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue(row, payload)


@issue_app.command("delete")
def delete_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation."),
    no_input: bool = typer.Option(False, "--no-input", help="Never prompt; requires --yes."),
    hard: bool = typer.Option(False, "--hard", hidden=True),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Send one issue to the trash: the row is kept and `restore` brings it back."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        _reject_hard_delete(hard)
        _confirm_delete(ref, yes=yes, no_input=no_input)
        row, payload = _run_delete(db_flag=db if db is not None else root.db, ref=ref)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue(row, payload)


@issue_app.command("restore")
def restore_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    hard: bool = typer.Option(False, "--hard", hidden=True),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Clear the archive and the trash of one issue (never prompts)."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        _reject_hard_delete(hard)
        row, payload = _run_restore(db_flag=db if db is not None else root.db, ref=ref)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue(row, payload)


# --- reads ------------------------------------------------------------------------------


def _reject_contradictory_filters(
    *,
    assignee: str | None,
    mine: bool,
    unassigned: bool,
    archived: bool,
    trashed: bool,
    cycle: str | None,
    no_cycle: bool,
) -> None:
    """The flag pairs that cannot both be true: the edge refuses, it never picks one.

    Each pair describes one column in two directions, so honouring both would mean
    guessing which half the caller meant instead of answering it.
    """
    if assignee is not None and mine:
        raise CliError(
            "use -a/--assignee or --mine, not both", code="invalid_option", field="assignee"
        )
    if assignee is not None and unassigned:
        raise CliError(
            "use -a/--assignee or --unassigned, not both", code="invalid_option", field="assignee"
        )
    if mine and unassigned:
        raise CliError(
            "use --mine or --unassigned, not both", code="invalid_option", field="assignee"
        )
    if archived and trashed:
        raise CliError(
            "use --archived or --trashed, not both", code="invalid_option", field="archived"
        )
    if cycle is not None and no_cycle:
        raise CliError("use --cycle or --no-cycle, not both", code="invalid_option", field="cycle")


def _run_list(
    *,
    db_flag: str | None,
    team: str | None,
    state: list[str] | None,
    state_types: list[StateTypeChoice] | None,
    priorities: list[int] | None,
    assignee: str | None,
    mine: bool,
    unassigned: bool,
    labels: list[str] | None,
    project: str | None,
    cycle: str | None,
    no_cycle: bool,
    parent: str | None,
    query: str | None,
    open_issues: bool,
    archived: bool,
    trashed: bool,
    sort: str,
    reverse: bool,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], IssuePage]:
    """Resolve the references and the team, build one filter object and read one page.

    The references are names or ids by contract, so the edge resolves them against the
    repositories and hands `list_issues` a `ListFilters` of ids; the predicates, the
    ordering and the page size belong to the layer below and are not repeated here.
    """
    cfg = resolve_config(db_flag=db_flag)
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found: {db_path}",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )

    uow = UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as repos:
            resolved = _team_entity(repos, resolve_team(repos, cfg, flag=team))
            filters = ListFilters(
                team_id=resolved.id,
                state_ids=_state_filter(repos, resolved, state),
                state_types=tuple(choice.value for choice in state_types or ()),
                priorities=tuple(int(value) for value in priorities or ()),
                assignee_id=_assignee_filter(repos, assignee, mine=mine),
                unassigned=unassigned,
                label_ids=_label_filter(repos, resolved, labels),
                project_id=project,
                cycle_id=cycle,
                no_cycle=no_cycle,
                parent_id=_parent_filter(repos, resolved, parent),
                q=query,
                is_open=_is_open_filter(open_issues=open_issues, trashed=trashed),
                archived_only=archived,
                trashed_only=trashed,
                order=cast("OrderKey", sort),
                reverse=reverse,
                limit=limit,
                cursor=cursor,
            )
    finally:
        uow.close()

    page = list_issues(Database(db_path), filters)
    items = [_list_item(item) for item in page.items]
    return [_list_row(item) for item in page.items], items, page


def _is_open_filter(*, open_issues: bool, trashed: bool) -> bool | None:
    """`True` for the default open listing, no predicate for `--all` and for the trash.

    The trash is the deeper layer: a trashed issue is reachable whatever its state, so the
    flag opens the state axis on its own.
    """
    return None if trashed or not open_issues else True


def _team_entity(repos: Repos, ref: str) -> Team:
    """Team by ULID or by key, in any case; an unknown reference is exit 3."""
    wanted = ref.strip()
    for team in repos.teams.list(include_archived=True):
        if team.id == wanted or team.key.casefold() == wanted.casefold():
            return team
    raise NotFound(resource="team", key=ref)


def _state_filter(repos: Repos, team: Team, refs: list[str] | None) -> tuple[str, ...]:
    """State names (case-insensitive, within the team) or ULIDs, resolved to ids."""
    if not refs:
        return ()
    candidates = repos.states.list_for_team(team.id, include_archived=True)
    by_ref: dict[str, str] = {}
    for candidate in candidates:
        by_ref[candidate.id] = candidate.id
        by_ref.setdefault(candidate.name.casefold(), candidate.id)
    ids: list[str] = []
    for ref in refs:
        text = ref.strip()
        found = by_ref.get(text) or by_ref.get(text.casefold())
        if found is None:
            raise _not_found("state", ref)
        ids.append(found)
    return tuple(ids)


def _label_filter(repos: Repos, team: Team, refs: list[str] | None) -> tuple[str, ...]:
    """Label names (case-insensitive; the team label before the workspace one) or ULIDs."""
    if not refs:
        return ()
    workspace = repos.teams.workspace()
    by_ref: dict[str, str] = {}
    if workspace is not None:
        team_labels = repos.labels.list(workspace.id, team_id=team.id, include_retired=True)
        shared_labels = repos.labels.list(workspace.id, team_id=None, include_retired=True)
        for candidate in (*team_labels, *shared_labels):
            by_ref[candidate.id] = candidate.id
            by_ref.setdefault(candidate.name.casefold(), candidate.id)
    ids: list[str] = []
    for ref in refs:
        text = ref.strip()
        found = by_ref.get(text) or by_ref.get(text.casefold())
        if found is None:
            raise _not_found("label", ref)
        ids.append(found)
    return tuple(ids)


def _parent_filter(repos: Repos, team: Team, ref: str | None) -> str | None:
    """Parent identifier or ULID of the resolved team, as an id; unknown is exit 3."""
    if ref is None:
        return None
    text = ref.strip()
    issue = repos.issues.get(text) or repos.issues.get_by_identifier(text)
    if issue is None or issue.team_id != team.id:
        raise _not_found("parent", ref, noun="issue")
    return issue.id


def _assignee_filter(repos: Repos, ref: str | None, *, mine: bool) -> str | None:
    """`--mine` is the resolved member; `-a` is the member reference of the vocabulary."""
    if not mine:
        return _resolve_assignee(repos, ref)
    member_id = _local_member(repos)
    if member_id is None:
        raise CliError(
            "no member for --mine: use HIFA_MEMBER, an email or a ULID",
            code="missing_context",
            field="assignee",
        )
    return member_id


def _not_found(field: str, ref: str, *, noun: str | None = None) -> CliError:
    """Exit 3 naming the flag whose reference matched nothing."""
    return CliError(
        f"{noun or field} not found: {ref}",
        code="not_found",
        field=field,
        exit_code=EXIT_NOT_FOUND,
    )


# --- orchestration ----------------------------------------------------------------------


def _run_create(
    *,
    db_flag: str | None,
    title: str | None,
    title_option: str | None,
    description: str | None,
    description_file: str | None,
    team: str | None,
    state: str | None,
    priority: str | None,
    estimate: float | None,
    assignee: str | None,
    labels: list[str] | None,
    due: str | None,
    parent: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    text = _resolve_title(title, title_option)
    body = _resolve_description(description, description_file)
    priority_value = _parse_priority(priority)
    label_refs = _split_labels(labels)

    cfg = resolve_config(db_flag=db_flag)
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found: {db_path}",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )

    uow = UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as repos:
            team_ref = resolve_team(repos, cfg, flag=team)
            assignee_id = _resolve_assignee(repos, assignee)
            actor = Actor(member_id=_local_member(repos), source="cli", label="cli")
        cmd = CreateIssue(
            team=team_ref,
            title=text,
            description=body,
            priority=priority_value,
            state=state,
            assignee_id=assignee_id,
            parent=parent,
            labels=label_refs,
            estimate=estimate,
            due_date=due,
        )
        issue = create_issue(uow, cmd, actor)
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _resolve_title(positional: str | None, option: str | None) -> str:
    if positional is not None and option is not None:
        raise CliError(
            "pass the title either as an argument or via --title, not both", field="title"
        )
    value = positional if positional is not None else option
    if value is None:
        raise CliError("missing title: pass it as an argument or with --title", field="title")
    return value


def _resolve_description(description: str | None, description_file: str | None) -> str | None:
    if description is not None and description_file is not None:
        raise CliError("use --description or --description-file, not both", field="description")
    if description_file is not None:
        if description_file == "-":
            return sys.stdin.read()
        path = Path(description_file)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(
                f"cannot read {path}: {exc.strerror or exc}",
                code="file_unreadable",
                field="description",
            ) from exc
    if description == "-":
        return sys.stdin.read()
    return description


def _parse_priority(raw: str | None) -> int:
    if raw is None:
        return 0
    value = raw.strip().lower()
    if value in _PRIORITY_NAMES:
        return _PRIORITY_NAMES[value]
    digits = value[1:] if value.startswith("p") else value
    if digits.isdigit() and 0 <= int(digits) <= 4:
        return int(digits)
    raise CliError(
        f"invalid priority: {raw!r} (0..4, P0..P4, none|urgent|high|medium|low)",
        field="priority",
    )


def _split_labels(labels: list[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    for raw in labels or []:
        for part in raw.split(","):
            name = part.strip()
            if name and name not in result:
                result.append(name)
    return tuple(result)


def _resolve_assignee(repos: Repos, ref: str | None) -> str | None:
    """Member id for a reference of the vocabulary: ULID, display name or email, any case."""
    if ref is None:
        return None
    value = ref.strip()
    if value.upper() == "ME":
        value = (os.environ.get("HIFA_MEMBER") or "").strip()
        if not value:
            members = _active_members(repos)
            if len(members) == 1:
                return members[0].id
            raise CliError(
                "no member for --assignee ME: use HIFA_MEMBER, an email or a ULID",
                code="missing_context",
                field="assignee",
            )
    member = repos.members.get(value) or repos.members.get_by_email(value)
    if member is None:
        member = _member_by_display_name(repos, value)
    if member is None:
        raise _not_found("assignee", value, noun="member")
    return member.id


def _member_by_display_name(repos: Repos, value: str) -> Member | None:
    """Member whose display name or email equals `value` ignoring case."""
    workspace = repos.teams.workspace()
    if workspace is None:
        return None
    wanted = value.casefold()
    for candidate in repos.members.list(workspace.id, include_inactive=True):
        if candidate.display_name.casefold() == wanted:
            return candidate
        if candidate.email is not None and candidate.email.casefold() == wanted:
            return candidate
    return None


def _local_member(repos: Repos) -> str | None:
    ref = (os.environ.get("HIFA_MEMBER") or "").strip()
    if ref:
        member = repos.members.get(ref) or repos.members.get_by_email(ref)
        return member.id if member is not None else None
    members = _active_members(repos)
    return members[0].id if len(members) == 1 else None


def _active_members(repos: Repos) -> list[Member]:
    workspace = repos.teams.workspace()
    if workspace is None:
        return []
    return [member for member in repos.members.list(workspace.id) if member.is_active]


# --- writes -----------------------------------------------------------------------------


def _start_output(root: CliContext, *, json_output: bool, quiet: bool, no_color: bool) -> None:
    """Resolve the format of the invocation: the global flags plus the leaf ones."""
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)
    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive",
                code="invalid_option",
                field="output",
            )
        )


def _emit_issue(row: dict[str, Any], payload: dict[str, Any]) -> None:
    """One issue: the table row, the canonical object or the identifier."""
    output.emit(
        row,
        table_cols=ISSUE_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["identifier"],
    )


def _open_uow(db_flag: str | None) -> UnitOfWork:
    """Unit of work over the resolved SQLite file; a missing file is exit 3."""
    cfg = resolve_config(db_flag=db_flag)
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found: {db_path}",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )
    return UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())


def _actor(repos: Repos) -> Actor:
    """Who writes: the local member, when the workspace has exactly one."""
    return Actor(member_id=_local_member(repos), source="cli", label="cli")


def _pair(value: str | None, clear: bool, *, flag: str, field: str, clear_flag: str) -> Any:
    """`UNSET` when the flag is absent, `None` when it is cleared, else the value.

    `flag` is the option the user typed and `field` the column the error envelope names: they
    differ whenever the flag and the column are spelled differently (`--project`/`project_id`).
    """
    if value is not None and clear:
        raise CliError(f"use {flag} or {clear_flag}, not both", code="invalid_option", field=field)
    if clear:
        return None
    return UNSET if value is None else value


def _parse_estimate(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise CliError(f"invalid estimate: {raw!r} (a number)", field="estimate") from None


def _update_fields(
    *,
    title: str | None,
    description: str | None,
    description_file: str | None,
    clear_description: bool,
    priority: str | None,
    state: str | None,
    assignee: str | None,
    clear_assignee: bool,
    project: str | None,
    clear_project: bool,
    milestone: str | None,
    clear_milestone: bool,
    cycle: str | None,
    clear_cycle: bool,
    parent: str | None,
    clear_parent: bool,
    estimate: str | None,
    clear_estimate: bool,
    due: str | None,
    clear_due: bool,
) -> tuple[dict[str, Any], str | None]:
    """Fields that carry a literal value, plus the assignee reference to resolve in the database.

    An absent flag leaves the field out of the mapping, which `UpdateIssue` reads as "keep the
    stored value"; a clear flag sends `None`, which writes NULL.
    """
    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title
    body = _pair(
        _resolve_description(description, description_file),
        clear_description,
        flag="--description",
        field="description",
        clear_flag="--clear-description",
    )
    if body is not UNSET:
        fields["description"] = body
    if priority is not None:
        fields["priority"] = _parse_priority(priority)
    if state is not None:
        fields["state"] = state
    assignee_ref: str | None = None
    if clear_assignee:
        fields["assignee_id"] = None
    elif assignee is not None:
        assignee_ref = assignee
    for name, flag, value, clear, clear_flag in (
        ("project_id", "--project", project, clear_project, "--clear-project"),
        ("milestone_id", "--milestone", milestone, clear_milestone, "--clear-milestone"),
        ("cycle_id", "--cycle", cycle, clear_cycle, "--clear-cycle"),
        ("parent", "--parent", parent, clear_parent, "--clear-parent"),
    ):
        resolved = _pair(value, clear, flag=flag, field=name, clear_flag=clear_flag)
        if resolved is not UNSET:
            fields[name] = resolved
    estimate_value = _pair(
        estimate,
        clear_estimate,
        flag="--estimate",
        field="estimate",
        clear_flag="--clear-estimate",
    )
    if estimate_value is not UNSET:
        fields["estimate"] = None if estimate_value is None else _parse_estimate(estimate_value)
    due_value = _pair(due, clear_due, flag="--due", field="due", clear_flag="--clear-due")
    if due_value is not UNSET:
        fields["due_date"] = due_value
    return fields, assignee_ref


def _run_edit(
    *,
    db_flag: str | None,
    ref: str,
    title: str | None,
    description: str | None,
    description_file: str | None,
    clear_description: bool,
    priority: str | None,
    state: str | None,
    assignee: str | None,
    clear_assignee: bool,
    project: str | None,
    clear_project: bool,
    milestone: str | None,
    clear_milestone: bool,
    cycle: str | None,
    clear_cycle: bool,
    parent: str | None,
    clear_parent: bool,
    estimate: str | None,
    clear_estimate: bool,
    due: str | None,
    clear_due: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fields, assignee_ref = _update_fields(
        title=title,
        description=description,
        description_file=description_file,
        clear_description=clear_description,
        priority=priority,
        state=state,
        assignee=assignee,
        clear_assignee=clear_assignee,
        project=project,
        clear_project=clear_project,
        milestone=milestone,
        clear_milestone=clear_milestone,
        cycle=cycle,
        clear_cycle=clear_cycle,
        parent=parent,
        clear_parent=clear_parent,
        estimate=estimate,
        clear_estimate=clear_estimate,
        due=due,
        clear_due=clear_due,
    )
    if not fields and assignee_ref is None:
        raise CliError("nothing to change", code="no_changes")

    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
            if assignee_ref is not None:
                fields["assignee_id"] = _resolve_assignee(repos, assignee_ref)
        issue = update_issue(uow, UpdateIssue(issue=ref, **fields), actor)
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _run_move(
    *, db_flag: str | None, ref: str, state: str, before: str | None, after: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    if before is not None and after is not None:
        raise CliError(
            "use --before or --after, not both", code="invalid_option", field="before_id"
        )
    position = "before" if before is not None else "after" if after is not None else "last"

    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue = move_issue(
            uow,
            MoveIssue(
                issue=ref,
                target_state=state,
                position=position,
                before_id=before,
                after_id=after,
            ),
            actor,
        )
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _run_archive(
    *, db_flag: str | None, ref: str, undo: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue = (
            restore_issue(uow, RestoreIssue(issue=ref), actor)
            if undo
            else archive_issue(uow, ArchiveIssue(issue=ref), actor)
        )
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _run_delete(*, db_flag: str | None, ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue = trash_issue(uow, TrashIssue(issue=ref), actor)
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _run_restore(*, db_flag: str | None, ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue = restore_issue(uow, RestoreIssue(issue=ref), actor)
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _reject_board_anchor(before: str | None, after: str | None) -> None:
    """A field edit carries no position: a board anchor is not one of its flags."""
    if before is not None or after is not None:
        raise CliError(
            "edit takes no board anchor: use `hifa issue move` when the position matters",
            code="invalid_option",
            field="before",
        )


def _reject_hard_delete(hard: bool) -> None:
    """Hifa has no physical deletion: the flag is rejected, never ignored."""
    if hard:
        raise CliError(
            "--hard is not supported: there is no physical deletion",
            code="invalid_option",
            field="hard",
        )


def _confirm_archive(ref: str, *, yes: bool, no_input: bool) -> None:
    """Ask `Archive ENG-1?` before the use case runs."""
    _confirm_destructive(f"Archive {ref}?", yes=yes, no_input=no_input)


def _confirm_delete(ref: str, *, yes: bool, no_input: bool) -> None:
    """Ask `Send ENG-1 to trash?` before the use case runs."""
    _confirm_destructive(f"Send {ref} to trash?", yes=yes, no_input=no_input)


def _confirm_destructive(prompt: str, *, yes: bool, no_input: bool) -> None:
    """Ask on `stderr` before the use case runs.

    A session without a terminal (or `--no-input`) never prompts and refuses straight away;
    a `no` answer refuses the same way. Only `--yes` skips the question.
    """
    if yes:
        return
    if not no_input and sys.stdin.isatty() and _ask(prompt):
        return
    raise CliError("operation canceled", code="confirmation_required")


def _ask(prompt: str) -> bool:
    """Write one yes/no question to `stderr` and read one answer from `stdin`.

    The question is a diagnostic, so it never shares the stream that carries data: a refused
    or aborted confirmation leaves `stdout` byte-empty in every format. An empty line (the
    default answer) and end of input are both a `no`.
    """
    typer.echo(f"{prompt} [y/N]: ", err=True, nl=False)
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


# --- presentation -----------------------------------------------------------------------


def _canonical(repos: Repos, issue: Issue) -> dict[str, Any]:
    """Complete `Issue` object in canonical order (no key is ever omitted)."""
    team = repos.teams.get(issue.team_id)
    state = repos.states.get(issue.state_id)
    assignee = repos.members.get(issue.assignee_id) if issue.assignee_id else None
    creator = repos.members.get(issue.creator_id) if issue.creator_id else None
    parent = repos.issues.get(issue.parent_id) if issue.parent_id else None
    labels = [
        label
        for label in (repos.labels.get(i) for i in repos.labels.ids_for_issue(issue.id))
        if label is not None
    ]

    return {
        "id": issue.id,
        "identifier": issue.identifier,
        "number": issue.number,
        "team": _team_ref(team),
        "title": issue.title,
        "description": issue.description,
        "priority": int(issue.priority),
        "priority_label": issue.priority.label,
        "estimate": issue.estimate,
        "due_date": _iso_date(issue.due_date),
        "state": _state_ref(state),
        "assignee": _member_ref(assignee),
        "creator": _member_ref(creator),
        "project": None,
        "milestone": None,
        "cycle": None,
        "parent": _parent_ref(parent),
        "labels": [_label_ref(label) for label in sorted(labels, key=lambda lbl: lbl.name)],
        "sort_order": issue.sort_order,
        "board_order": issue.board_order,
        "sub_issue_sort_order": issue.sub_issue_sort_order,
        "started_at": _iso(issue.started_at),
        "completed_at": _iso(issue.completed_at),
        "canceled_at": _iso(issue.canceled_at),
        "snoozed_until_at": _iso(issue.snoozed_until_at),
        "archived_at": _iso(issue.archived_at),
        "auto_archived_at": _iso(issue.auto_archived_at),
        "trashed": bool(issue.trashed),
        "branch_name": issue.branch_name,
        "is_open": bool(state.type.is_open) if state is not None else False,
        "is_visible": bool(issue.is_visible),
        "created_at": _iso(issue.created_at),
        "updated_at": _iso(issue.updated_at),
    }


def _table_row(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload["state"] or {}
    assignee = payload["assignee"] or {}
    labels = payload["labels"] or []
    return {
        "identifier": payload["identifier"],
        "p": PRIORITY_GLYPHS.get(int(payload["priority"]), "–"),
        "state": state.get("name"),
        "assignee": assignee.get("display_name"),
        "title": payload["title"],
        "labels": ", ".join(label["name"] for label in labels) or None,
        "due": payload["due_date"],
    }


def _list_row(item: IssueListItem) -> dict[str, Any]:
    """One row of `issue list`: the columns of the listing table."""
    return {
        "identifier": item.identifier,
        "p": PRIORITY_GLYPHS.get(int(item.priority), "–"),
        "state": item.state_name,
        "assignee": item.assignee_name,
        "title": item.title,
        "labels": ", ".join(label.name for label in _sorted_labels(item)) or None,
        "due": _iso_date(item.due_date),
    }


def _list_item(item: IssueListItem) -> dict[str, Any]:
    """One element of the page: every field the listing read model carries.

    Dates are serialized here because the summary is not a domain entity and the formatter
    only knows how to encode the types `--json` may meet.
    """
    return {
        "id": item.id,
        "identifier": item.identifier,
        "title": item.title,
        "priority": int(item.priority),
        "priority_label": item.priority_label,
        "estimate": item.estimate,
        "due_date": _iso_date(item.due_date),
        "state": {
            "id": item.state_id,
            "name": item.state_name,
            "type": item.state_type,
            "color": item.state_color,
        },
        "assignee": None
        if item.assignee_id is None
        else {"id": item.assignee_id, "display_name": item.assignee_name},
        "project_id": item.project_id,
        "cycle_id": item.cycle_id,
        "parent_id": item.parent_id,
        "labels": [
            {"id": label.id, "name": label.name, "color": label.color}
            for label in _sorted_labels(item)
        ],
        "sort_order": item.sort_order,
        "board_order": item.board_order,
        "is_open": bool(item.is_open),
        "team_key": item.team_key,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def _sorted_labels(item: IssueListItem) -> tuple[Any, ...]:
    """Labels by name, ignoring case, so two runs over the same data print the same order."""
    return tuple(sorted(item.labels, key=lambda label: label.name.casefold()))


def _team_ref(team: Any) -> dict[str, Any] | None:
    if team is None:
        return None
    return {"id": team.id, "key": team.key, "name": team.name, "icon": team.icon}


def _state_ref(state: Any) -> dict[str, Any] | None:
    if state is None:
        return None
    return {"id": state.id, "name": state.name, "type": str(state.type), "color": state.color}


def _member_ref(member: Member | None) -> dict[str, Any] | None:
    if member is None:
        return None
    return {
        "id": member.id,
        "name": member.name,
        "display_name": member.display_name,
        "initials": member.initials,
    }


def _label_ref(label: Label) -> dict[str, Any]:
    return {"id": label.id, "name": label.name, "color": label.color}


def _parent_ref(parent: Issue | None) -> dict[str, Any] | None:
    if parent is None:
        return None
    return {"id": parent.id, "identifier": parent.identifier, "title": parent.title}


def _iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["ISSUE_TABLE_COLUMNS", "PRIORITY_GLYPHS", "create_command", "issue_app"]
