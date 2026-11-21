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
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import (
    IssueDetail,
    IssueListItem,
    IssuePage,
    ListFilters,
    OrderKey,
)
from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app import members
from hifa.app.boards import (
    DETAIL_COLLECTIONS,
    RENDERED_INCLUDE,
    detail_collections,
    is_number_ref,
    issue_detail,
    list_blocked,
    list_issues,
    relation_reading,
)
from hifa.app.commands import (
    UNSET,
    Actor,
    AddLabel,
    ArchiveIssue,
    CommentOnIssue,
    CreateIssue,
    MoveIssue,
    RelateIssues,
    RemoveLabel,
    RestoreIssue,
    TrashIssue,
    UnrelateIssues,
    UpdateIssue,
)
from hifa.app.history import HistoryPage, HistoryRow, history_rows, wire_instant
from hifa.app.issues import (
    archive_issue,
    comment_on_issue,
    create_issue,
    move_issue,
    relate_issues,
    restore_issue,
    trash_issue,
    unrelate_issues,
    update_issue,
)
from hifa.app.labels import DEFAULT_LABEL_COLOR, add_label, remove_label
from hifa.cli import output
from hifa.cli.context import CliContext, resolve_team
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import ConfigError, resolve_config
from hifa.domain.entities import (
    Comment,
    Cycle,
    Issue,
    Label,
    Member,
    Project,
    Team,
)
from hifa.domain.errors import HifaError, NotFound, Validation

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

#: Columns of `issue relations`: how the type reads from here, and the far end.
RELATION_TABLE_COLUMNS: tuple[str, ...] = ("reads as", "identifier", "title", "state")

#: Columns of the single row `issue relate` prints.
RELATION_EDGE_COLUMNS: tuple[str, ...] = ("id", "type", "issue_id", "related_issue_id")

#: Columns of the row `issue comment` prints.
COMMENT_TABLE_COLUMNS: tuple[str, ...] = ("id", "issue", "author", "created", "body")

#: Columns of `issue history` and of the `HISTORY` section of `issue show --with`: the sentence
#: a row reads as, never the stored pair.
HISTORY_TABLE_COLUMNS: tuple[str, ...] = ("when", "actor", "event")

#: How the two text columns of a history row are narrowed: 20 + 2 + 20 + 2 + 76 = 120 cells, the
#: table budget. `WHEN` is fixed at 20 and gives nothing away.
HISTORY_COLUMN_FORMATS: Mapping[str, output.ColumnFormat] = {
    "actor": output.ColumnFormat(max_cells=20),
    "event": output.ColumnFormat(max_cells=76),
}

#: The read's window, as the one flag of `issue history` may name it.
HISTORY_LIMIT_DEFAULT = 50
HISTORY_LIMIT_MAX = 100

#: Section title of each collection and the columns of its table.
SHOW_SECTIONS: dict[str, tuple[str, ...]] = {
    "comments": ("id", "author", "created", "body"),
    "relations": RELATION_TABLE_COLUMNS,
    "history": HISTORY_TABLE_COLUMNS,
    "children": ("identifier", "title"),
}

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


@issue_app.command("relate")
def relate_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    relation_type: str = typer.Argument(
        ..., metavar="TYPE", help="blocks, duplicate, related, similar or blocked_by."
    ),
    other: str = typer.Argument(..., help="The other issue (identifier or ULID)."),
    undo: bool = typer.Option(False, "--undo", help="Remove the relation instead of adding it."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Relate two issues, or remove the relation with --undo."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)
    json_mode = output.options().json
    try:
        payload = _run_relate(
            db_flag=db if db is not None else root.db,
            ref=ref,
            relation_type=relation_type,
            other=other,
            undo=undo,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    if payload is None:
        # Removing a relation produces no object: `--json` says so with a literal null,
        # and the table mode has nothing to draw.
        if json_mode:
            output.write_json(None)
        return
    output.emit(
        _relation_row(payload),
        table_cols=RELATION_EDGE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["id"],
    )


@issue_app.command("relations")
def relations_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Every relation of one issue, named as it reads from that issue."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)
    json_mode = output.options().json
    quiet_mode = output.options().quiet

    try:
        rows, items = _run_relations(db_flag=db if db is not None else root.db, ref=ref)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    output.emit(
        rows,
        table_cols=RELATION_TABLE_COLUMNS,
        json_obj={"items": items},
        quiet_obj=[row["identifier"] for row in rows],
    )
    if not rows and not json_mode and not quiet_mode:
        output.write_note("0 relations")


@issue_app.command("blocked")
def blocked_command(
    ctx: typer.Context,
    team: str | None = typer.Option(None, "--team", help="Team key or ULID."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """The issues still held back by a blocker that is not finished."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)
    json_mode = output.options().json
    quiet_mode = output.options().quiet

    try:
        rows, items = _run_blocked(db_flag=db if db is not None else root.db, team=team)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    output.emit(
        rows,
        table_cols=ISSUE_TABLE_COLUMNS,
        json_obj={"items": items},
        quiet_obj=[row["identifier"] for row in rows],
    )
    if not rows and not json_mode and not quiet_mode:
        output.write_note("0 issues")


@issue_app.command("show")
def show_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue ULID, identifier or number (ENG-123, 123)."),
    collections: list[str] | None = typer.Option(
        None,
        "--with",
        help="comments, relations, history or children (repeatable); attachments is refused.",
    ),
    team: str | None = typer.Option(
        None, "--team", help="Team of a bare number: a key or a ULID (the number form only)."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Show one issue: its summary block and, per `--with`, one collection after it.

    A bare number resolves inside the team of this invocation (`--team`, the environment, the
    configuration or the only team of the workspace); the other forms name their own team.
    """
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        requested = _requested_collections(collections)
        payload, detail = _run_show(
            db_flag=db if db is not None else root.db,
            ref=ref,
            requested=requested,
            team_flag=team,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    if detail.previous_identifier is not None:
        output.write_note(
            f"identifier renamed: {detail.previous_identifier} is now {payload['identifier']}"
        )
    _emit_show(payload, detail, requested)


@issue_app.command("history")
def history_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    limit: int = typer.Option(
        HISTORY_LIMIT_DEFAULT, "--limit", help="Newest rows to keep, 1-100 (default 50)."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Event id, one per line."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """The story of one issue, one sentence per event, oldest first."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)
    json_mode = output.options().json
    quiet_mode = output.options().quiet

    try:
        _check_history_limit(limit)
        page, issue_id = _run_history(db_flag=db if db is not None else root.db, ref=ref)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    # `--limit` keeps the newest rows of the window and never reorders them: the cut is a log's,
    # not the panel's, and the answer stays forward.
    shown = page.rows[-limit:] if limit < len(page.rows) else page.rows
    output.emit(
        [_history_table_row(row) for row in shown],
        table_cols=HISTORY_TABLE_COLUMNS,
        table_formats=HISTORY_COLUMN_FORMATS,
        json_obj={"items": [_history_element(row, issue_id) for row in shown]},
        quiet_obj=[row.id for row in shown],
    )
    if not shown and not json_mode and not quiet_mode:
        output.write_note("0 events")
    elif len(shown) < page.window_size and not quiet_mode:
        # The second number is the window the read returned, so on a full one it is a floor and
        # this sentence never pretends to know how many events the issue has.
        output.write_note(f"showing {len(shown)} of {page.window_size} events")


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


@issue_app.command("label")
def label_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    labels: list[str] = typer.Argument(..., help="Label names or ULIDs (one per transaction)."),
    create: bool = typer.Option(
        False, "--create", help="Create the label in the issue's team when neither level has it."
    ),
    color: str | None = typer.Option(
        None, "--color", help="Color of a label created by --create, #RRGGBB."
    ),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
) -> None:
    """Attach one or more labels to one issue: N labels, N transactions."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        _reject_color_without_create(color, create)
        row, payload, replaced = _run_label_add(
            db_flag=db if db is not None else root.db,
            ref=ref,
            labels=_split_labels(labels),
            create=create,
            color=color,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue_replaced(row, payload, replaced)


@issue_app.command("unlabel")
def unlabel_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    labels: list[str] = typer.Argument(..., help="Label names or ULIDs (one per transaction)."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Identifier only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Detach one or more labels from one issue; a link the issue does not carry is exit 3."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        row, payload = _run_label_remove(
            db_flag=db if db is not None else root.db,
            ref=ref,
            labels=_split_labels(labels),
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_issue_replaced(row, payload, None)


@issue_app.command("comment")
def comment_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Issue identifier (ENG-123) or ULID."),
    body: str | None = typer.Argument(
        None, help="Comment body (markdown). Omit to read it from --body-file or stdin."
    ),
    body_file: str | None = typer.Option(
        None, "--body-file", help="File with the body ('-' = stdin)."
    ),
    parent: str | None = typer.Option(None, "--parent", help="Comment ULID to reply to."),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Comment id only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Write one comment on an issue, or one reply inside its thread."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    _start_output(root, json_output=json_output, quiet=quiet, no_color=no_color)

    try:
        text = _resolve_comment_body(body, body_file)
        row, payload = _run_comment(
            db_flag=db if db is not None else root.db, ref=ref, body=text, parent=parent
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit_comment(row, payload)


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


def _existing_db(db_flag: str | None) -> Path:
    """The database this invocation reads, or exit 3 when the file is not there."""
    cfg = resolve_config(db_flag=db_flag)
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found: {db_path}",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )
    return db_path


def _team_of_number(db_flag: str | None, db_path: Path, ref: str, flag: str | None) -> str | None:
    """The team a bare-number reference names, decided before the issue is read.

    Only the number form consults the team: an identifier names its own, so nothing is resolved
    for it and a `--team` beside one has no effect on the answer. The sources and the refusal are
    the invocation's one context resolution — the leaf flag, the environment, the configured
    default, or the only non-archived team — so a workspace that leaves the number unplaced is a
    `missing_context` and never a missing row.
    """
    text = (ref or "").strip()
    if not is_number_ref(text):
        return None
    cfg = resolve_config(db_flag=db_flag)
    uow = UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as repos:
            return resolve_team(repos, cfg, flag=flag)
    finally:
        uow.close()


def _run_relations(*, db_flag: str | None, ref: str) -> tuple[list[dict[str, Any]], list[dict]]:
    """Every relation of one issue, in the order the detail read publishes.

    The envelope carries the stored row and nothing else: which end of the row the asking
    issue is on, and therefore how the type reads from here, is derived from the issue and
    the two halves of the row instead of travelling as a second copy of the same fact. The
    order is the one `issue_detail` produced, so this table and `issue show --with relations`
    cannot disagree.
    """
    db_path = _existing_db(db_flag)
    detail = issue_detail(Database(db_path), ref)
    asking = detail.issue.id
    rows: list[dict[str, Any]] = []
    items: list[dict] = []
    for relation, far in detail.relations:
        rows.append(_relation_section_row(relation, far, asking_id=asking))
        items.append(_relation_item(relation))
    return rows, items


def _requested_collections(values: list[str] | None) -> tuple[str, ...]:
    """The `--with` values of one invocation, de-duplicated, in the order they were typed.

    A value outside the four is a typo and the command refuses it before any read; `attachments`
    is inside the flag's vocabulary and outside this version's payload, so it is refused with its
    own sentence. Both are the edge's own refusals: no use case is called for a flag it cannot
    receive, and `--json` answers with this edge's one error document like every other refusal.
    """
    requested: list[str] = []
    for raw in values or []:
        token = raw.strip()
        if token == "attachments":
            raise CliError(
                "--with attachments has no payload in this version",
                code="invalid_option",
                field="with",
            )
        if token not in DETAIL_COLLECTIONS:
            raise CliError(
                f"unknown --with value '{token}'; expected {', '.join(DETAIL_COLLECTIONS)}"
                " or attachments",
                code="invalid_option",
                field="with",
            )
        if token not in requested:
            requested.append(token)
    return tuple(requested)


def _include_names(requested: tuple[str, ...]) -> frozenset[str]:
    """The `include` names the read is called with: the requested collections it gates.

    Only the collections the read gates are names of that set; `relations` and `children` are read
    on every call and passing them would be refused as unknown.
    """
    return frozenset(name for name in requested if name in RENDERED_INCLUDE)


def _run_show(
    *, db_flag: str | None, ref: str, requested: tuple[str, ...], team_flag: str | None = None
) -> tuple[dict[str, Any], IssueDetail]:
    """Read one issue: the canonical object and, when asked for, the four collections.

    The reference is resolved by the detail read and by nothing else, so the trash layer answers
    for it once: a trashed identifier is not found, whatever `--with` carries. Only a bare number
    consults the invocation's team, and that context is decided here, before the issue is read: a
    workspace that leaves the number unplaced is a `missing_context`, not a missing row. `--with`
    costs no second read — one call answers the block and every collection — and the row itself is
    read through the repositories, because the object every write publishes carries fields the
    detail model does not.
    """
    db_path = _existing_db(db_flag)
    team = _team_of_number(db_flag, db_path, ref, team_flag)
    detail = issue_detail(Database(db_path), ref, include=_include_names(requested), team=team)

    uow = UnitOfWork(db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as repos:
            issue = repos.issues.get(detail.issue.id)
            if issue is None:  # pragma: no cover - the detail read just answered this row
                raise NotFound(resource="issue", key=ref)
            payload = _canonical(repos, issue)
    finally:
        uow.close()

    # `project`, `cycle`, `parent` and `milestone` come from the detail read: it is the read that
    # answers `null` for a parent in the trash and the one that joins the milestone's name.
    payload["project"] = _project_ref(detail.project)
    payload["cycle"] = _cycle_ref(detail.cycle)
    payload["parent"] = None if detail.parent is None else _identifier_ref(detail.parent)
    payload["milestone"] = _milestone_ref(detail.issue)
    if requested:
        payload.update(_collections_payload(detail, requested))
    return payload, detail


def _collections_payload(
    detail: IssueDetail, requested: tuple[str, ...]
) -> dict[str, list[dict[str, Any]]]:
    """The four collection keys of the payload, all of them, in the object's fixed order.

    A collection that was not requested and a collection with no rows are both `[]`: the object has
    one shape, and no key tells the two apart. The unrequested one is emptied even when the read
    could fill it — `relations` and `children` are read on every call — because the object answers
    what the caller asked for and nothing else.
    """
    payload: dict[str, list[dict[str, Any]]] = {}
    for name, values in detail_collections(detail).items():
        if name not in requested:
            payload[name] = []
        elif name == "history":
            payload[name] = _history_elements(detail)
        else:
            payload[name] = [_collection_item(name, value) for value in values]
    return payload


def _collection_item(name: str, value: Any) -> dict[str, Any]:
    """One element of one collection, in the shape its object has everywhere else.

    The four names are the closed set the command formats; `children` is the one whose element is
    the identifier reference a parent already carries. `history` is assembled in the read's order
    by `_history_elements`, because its element is resolved and a row costs a lookup.
    """
    if name == "comments":
        return _comment_canonical(value)
    if name == "relations":
        relation, _far = value
        return _relation_item(relation)
    return _identifier_ref(value)


def _run_blocked(*, db_flag: str | None, team: str | None) -> tuple[list[dict], list[dict]]:
    """The issues whose blockers are not finished, resolved by the port below."""
    cfg = resolve_config(db_flag=db_flag)
    db_path = Path(cfg.db_path)
    if str(db_path) == ":memory:" or not db_path.is_file():
        raise CliError(
            f"database not found: {db_path}",
            code="database_not_found",
            field="db",
            exit_code=EXIT_NOT_FOUND,
        )
    blocked = list_blocked(Database(db_path), team=team)
    return [_list_row(item) for item in blocked], [_list_item(item) for item in blocked]


def _run_relate(
    *,
    db_flag: str | None,
    ref: str,
    relation_type: str,
    other: str,
    undo: bool,
) -> dict[str, Any] | None:
    """Add or remove one relation and return the stored row (None when removing)."""
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        if undo:
            unrelate_issues(
                uow, UnrelateIssues(issue=ref, related=other, type=relation_type), actor
            )
            return None
        relation = relate_issues(
            uow, RelateIssues(issue=ref, related=other, type=relation_type), actor
        )
        return _relation_item(relation)
    finally:
        uow.close()


def _relation_item(relation: Any) -> dict[str, Any]:
    """The stored relation, the five keys a reader needs to place both ends."""
    return {
        "id": relation.id,
        "issue_id": relation.issue_id,
        "related_issue_id": relation.related_issue_id,
        "type": str(relation.type),
        "created_at": _iso(relation.created_at),
    }


def _history_page(detail: IssueDetail) -> HistoryPage:
    """The one formatter's answer for the history window of one detail read.

    The maps are the read's: `detail.states` resolves a state change (and carries the colour of
    its dot), `detail.members` names an actor and `detail.history_parents` names a parent. The
    function resolves nothing itself and costs no statement.
    """
    return history_rows(
        detail.history,
        states={state.id: state.name for state in detail.states},
        members=detail.members,
        parents=detail.history_parents,
        state_colors={state.id: state.color for state in detail.states},
    )


def _history_table_row(row: HistoryRow) -> dict[str, Any]:
    """One printed line of the story: the instant, the actor and the sentence."""
    return {"when": wire_instant(row.created_at), "actor": row.actor_label, "event": row.sentence}


def _history_element(row: HistoryRow, issue_id: str) -> dict[str, Any]:
    """One `HistoryEntry`: the stored pair plus the three labels, ten keys in a fixed order.

    `issue_id` is added here because a payload of one issue's page does not carry it and the
    element does; the stored pair stays untouched so a caller that wants the raw key reads it.
    """
    return {
        "id": row.id,
        "issue_id": issue_id,
        "actor_id": row.actor_id,
        "field": row.field,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "created_at": wire_instant(row.created_at),
        "old_label": row.old_label,
        "new_label": row.new_label,
        "actor_label": row.actor_label,
    }


def _history_elements(detail: IssueDetail) -> list[dict[str, Any]]:
    """Every row of the read's window as a `HistoryEntry`, in the read's own order.

    The envelope of `issue show --json` publishes the read's order (newest first) because that
    payload is the envelope; only the printed section and the standalone verb are forward.
    """
    by_id = {row.id: row for row in _history_page(detail).rows}
    return [_history_element(by_id[entry.id], detail.issue.id) for entry in detail.history]


def _check_history_limit(limit: int) -> None:
    """`--limit` outside 1-100 is refused before any read, with the flag's own sentence.

    The bound is the read's window, and the bucket is `issue list --limit`'s: one flag name with
    two codes would be one flag name a caller has to read twice.
    """
    if not 1 <= limit <= HISTORY_LIMIT_MAX:
        raise Validation(
            f"--limit must be between 1 and {HISTORY_LIMIT_MAX}",
            rule="LIMIT_RANGE",
            field="limit",
            value=limit,
        )


def _run_history(*, db_flag: str | None, ref: str) -> tuple[HistoryPage, str]:
    """The history window of one issue, and the id its elements travel with.

    The reference is resolved by the detail read, so the trash layer answers for it once: a
    trashed identifier is not found, exactly as it is for `issue show --with history`.
    """
    db_path = _existing_db(db_flag)
    detail = issue_detail(Database(db_path), ref, include=frozenset({"history"}))
    return _history_page(detail), detail.issue.id


def _relation_section_row(relation: Any, far: IssueListItem, *, asking_id: str) -> dict[str, Any]:
    """One row of the relations table: the reading, then the far end it points at.

    The cell renders the type name with its underscore as a space (`blocked by`): the name is one
    word of the stored vocabulary, and the table is the human surface of it.
    """
    return {
        "reads as": relation_reading(relation, asking_id).replace("_", " "),
        "identifier": far.identifier,
        "title": far.title,
        "state": far.state_name,
    }


def _relation_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["id"],
        "type": payload["type"],
        "issue_id": payload["issue_id"],
        "related_issue_id": payload["related_issue_id"],
    }


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
    member_id = members.local_member(repos)
    if member_id is None:
        raise CliError(
            "no member for --mine: use HIFA_MEMBER, an email or a ULID",
            code="missing_context",
            field="assignee",
        )
    return member_id


def _not_found(field: str, ref: str, *, noun: str | None = None) -> CliError:
    """Exit 3 in the one sentence both edges print for a reference that matched nothing."""
    return CliError(
        f"{noun or field} '{ref}' does not exist",
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
            actor = Actor(member_id=members.local_member(repos), source="cli", label="cli")
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


def _read_text(
    value: str | None,
    path_value: str | None,
    *,
    field: str,
    flags: tuple[str, str],
) -> str | None:
    """The text two spellings of one input give: the argument, the file, or `stdin`.

    `-` means `stdin` in either place, so `--description -` and `--description-file -` are the
    same request. A file that cannot be read is the edge's own refusal: it never reaches the
    core, which has no opinion about paths.
    """
    if value is not None and path_value is not None:
        raise CliError(
            f"use {flags[0]} or {flags[1]}, not both", code="invalid_option", field=field
        )
    if path_value is None:
        return sys.stdin.read() if value == "-" else value
    if path_value == "-":
        return sys.stdin.read()
    path = Path(path_value)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            f"cannot read {path}: {exc.strerror or exc}", code="file_unreadable", field=field
        ) from exc


def _resolve_description(description: str | None, description_file: str | None) -> str | None:
    """The description of a create or an edit, from the argument, a file or `stdin`."""
    return _read_text(
        description,
        description_file,
        field="description",
        flags=("--description", "--description-file"),
    )


def _resolve_comment_body(body: str | None, body_file: str | None) -> str:
    """The body of a comment, from the argument, a file or `stdin`.

    The value travels to the use case unmodified: whether an empty body is a comment at all
    is a rule of the core, not a question this edge answers. With neither a body nor a file,
    `stdin` is read only when it is not a terminal, so an interactive caller who typed
    nothing is told what is missing instead of hanging on a prompt nobody offered.
    """
    if body is None and body_file is None and not sys.stdin.isatty():
        body_file = "-"
    text = _read_text(body, body_file, field="body", flags=("BODY", "--body-file"))
    if text is None:
        raise CliError(
            "missing comment body: pass it as an argument, with --body-file, or on stdin",
            field="body",
        )
    return text


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
        local = members.local_member(repos, ref=os.environ.get("HIFA_MEMBER"))
        if local is None:
            raise CliError(
                "no member for --assignee ME: use HIFA_MEMBER, an email or a ULID",
                code="missing_context",
                field="assignee",
            )
        return local
    member = repos.members.get(value) or repos.members.get_by_email(value)
    if member is None:
        member = _member_by_display_name(repos, value)
    if member is None:
        # The field names the resource that is missing, not the flag that carried the value.
        raise _not_found("member", value)
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


def _emit_issue_replaced(
    row: dict[str, Any], payload: dict[str, Any], replaced: dict[str, Any] | None
) -> None:
    """One issue plus the label the write took off it: the only two-key envelope of the tree.

    `replaced` is `{id, name, color}` or `null`, the same three fields a `labels[]` element
    carries, so a client that removes the displaced chip reads one shape for both. The table is
    the row of `issue list` and `--quiet` is the identifier: neither has a column for it.
    """
    output.emit(
        row,
        table_cols=ISSUE_TABLE_COLUMNS,
        json_obj={"issue": payload, "replaced": replaced},
        quiet_obj=payload["identifier"],
    )


def _emit_show(payload: dict[str, Any], detail: IssueDetail, requested: tuple[str, ...]) -> None:
    """One issue: the block always, then one section per requested collection that has rows.

    A collection with no rows prints no section at all and one `0 <collection>` on `stderr`, the
    diagnostic `issue list` and `issue relations` already use; the exit code stays `0`. `--quiet`
    prints the identifier alone, whatever was requested, so a script that wants the collections
    asks for `--json`.
    """
    current = output.options()
    if current.json:
        output.write_json(payload)
        return
    if current.quiet:
        output.write_line(payload["identifier"])
        return

    for line in _show_block(payload):
        output.write_line(line)
    for name in DETAIL_COLLECTIONS:
        if name not in requested:
            continue
        rows = _section_rows(name, detail)
        if not rows:
            output.write_note(f"0 {name}")
            continue
        output.write_line("")
        output.write_line(name.upper())
        output.write_line(
            output.render_table(
                rows,
                SHOW_SECTIONS[name],
                formats=HISTORY_COLUMN_FORMATS if name == "history" else None,
            )
        )


def _show_block(payload: dict[str, Any]) -> list[str]:
    """The summary block: the identifier and the title, then one `key  value` line per field.

    The description is not one of the rows: a body is multi-line and arbitrarily long, and the
    block is one line per field, so the record's whole content travels in `--json` only.
    """
    state = payload["state"] or {}
    assignee = payload["assignee"] or {}
    project = payload["project"] or {}
    parent = payload["parent"] or {}
    fields: tuple[tuple[str, Any], ...] = (
        ("State", f"{state.get('name')} ({state.get('type')})" if state else None),
        ("Priority", payload["priority_label"]),
        ("Assignee", assignee.get("display_name")),
        ("Project", project.get("name")),
        ("Cycle", _cycle_label(payload["cycle"])),
        ("Due", payload["due_date"]),
        ("Estimate", payload["estimate"]),
        ("Labels", ", ".join(label["name"] for label in payload["labels"]) or None),
        ("Parent", parent.get("identifier")),
        ("Created", payload["created_at"]),
        ("Updated", payload["updated_at"]),
    )
    lines = [f"{payload['identifier']}  {payload['title']}"]
    lines.extend(output.key_value_lines(fields))
    return lines


def _cycle_label(cycle: dict[str, Any] | None) -> str | None:
    """`Cycle N` when the row carries no name of its own, `None` when the issue has no cycle."""
    if not cycle:
        return None
    return cycle.get("name") or f"Cycle {cycle['number']}"


def _section_rows(name: str, detail: IssueDetail) -> list[dict[str, Any]]:
    """The rows of one section, read from the model the block was built from.

    The author and the actor are display names out of the model's member map, so a comment or a
    history row costs no second query; a row without a member renders the empty cell.
    """
    if name == "comments":
        return [
            {
                "id": comment.id,
                "author": _member_name(detail.members, comment.author_id),
                "created": _iso(comment.created_at),
                "body": comment.body,
            }
            for comment in detail.comments
        ]
    if name == "relations":
        return [
            _relation_section_row(relation, far, asking_id=detail.issue.id)
            for relation, far in detail.relations
        ]
    if name == "history":
        return [_history_table_row(row) for row in _history_page(detail).rows]
    return [{"identifier": child.identifier, "title": child.title} for child in detail.children]


def _member_name(members: Mapping[str, str], member_id: str | None) -> str | None:
    """Display name of one member, or `None` for a row that carries no member."""
    return None if member_id is None else members.get(member_id)


def _emit_comment(row: dict[str, Any], payload: dict[str, Any]) -> None:
    """One comment: the table row, the canonical object or the id it was written with.

    A comment carries no identifier, so `--quiet` prints its `id` — the only name the record
    has, and the one the next reply needs for `--parent`.
    """
    output.emit(
        row,
        table_cols=COMMENT_TABLE_COLUMNS,
        json_obj=payload,
        quiet_obj=payload["id"],
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
    return Actor(member_id=members.local_member(repos), source="cli", label="cli")


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


def _run_label_add(
    *, db_flag: str | None, ref: str, labels: tuple[str, ...], create: bool, color: str | None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """One `AddLabel` per label, each in its own transaction, and the issue after the last one.

    A repeated value is one label: the edge de-duplicates before the first call, so no second
    transaction is paid for a value it already wrote. With more than one label the envelope
    reports the **last** replacement, because the issue it carries is the one after the final
    write and one key cannot carry two.
    """
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue: Issue | None = None
        replaced: Label | None = None
        for name in labels:
            result = add_label(
                uow,
                AddLabel(
                    issue=ref,
                    label=name,
                    create_if_missing=create,
                    color=(color or DEFAULT_LABEL_COLOR) if create else None,
                ),
                actor,
            )
            issue = result.issue
            if result.replaced is not None:
                replaced = result.replaced
        if issue is None:  # pragma: no cover - the argument is `required=True`
            raise CliError("label needs at least one label", code="invalid_option", field="label")
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload, None if replaced is None else _label_ref(replaced)


def _run_label_remove(
    *, db_flag: str | None, ref: str, labels: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One `RemoveLabel` per label; detaching never replaces a sibling."""
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        issue: Issue | None = None
        for name in labels:
            issue = remove_label(uow, RemoveLabel(issue=ref, label=name), actor)
        if issue is None:  # pragma: no cover - the argument is `required=True`
            raise CliError("label needs at least one label", code="invalid_option", field="label")
        with uow.read() as repos:
            payload = _canonical(repos, issue)
    finally:
        uow.close()
    return _table_row(payload), payload


def _run_comment(
    *, db_flag: str | None, ref: str, body: str, parent: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write one comment and read back the row it left, for the table and the envelope."""
    uow = _open_uow(db_flag)
    try:
        with uow.read() as repos:
            actor = _actor(repos)
        comment = comment_on_issue(
            uow, CommentOnIssue(issue=ref, body=body, parent_id=parent), actor
        )
        with uow.read() as repos:
            payload = _comment_canonical(comment)
            row = _comment_row(repos, comment)
    finally:
        uow.close()
    return row, payload


def _reject_board_anchor(before: str | None, after: str | None) -> None:
    """A field edit carries no position: a board anchor is not one of its flags."""
    if before is not None or after is not None:
        raise CliError(
            "edit takes no board anchor: use `hifa issue move` when the position matters",
            code="invalid_option",
            field="before",
        )


def _reject_color_without_create(color: str | None, create: bool) -> None:
    """A color that would be written nowhere is a typo, not a request."""
    if color is not None and not create:
        raise CliError(
            "--color needs --create: there is no label to paint without it",
            code="invalid_option",
            field="color",
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


def _comment_canonical(comment: Comment) -> dict[str, Any]:
    """Complete `comment` object in canonical order (no key is ever omitted)."""
    return {
        "id": comment.id,
        "issue_id": comment.issue_id,
        "parent_id": comment.parent_id,
        "author_id": comment.author_id,
        "body": comment.body,
        "resolved_at": _iso(comment.resolved_at),
        "edited_at": _iso(comment.edited_at),
        "created_at": _iso(comment.created_at),
        "updated_at": _iso(comment.updated_at),
    }


def _comment_row(repos: Repos, comment: Comment) -> dict[str, Any]:
    """One row of the comment table: the issue it hangs from and the author it names.

    Neither name is on the row: the issue is stored as its ULID and the author as a member.
    An authorless comment (a memberless actor) stays `None`, and the table renders it `–`.
    """
    issue = repos.issues.get(comment.issue_id)
    author = repos.members.get(comment.author_id) if comment.author_id else None
    return {
        "id": comment.id,
        "issue": issue.identifier if issue is not None else None,
        "author": author.display_name if author is not None else None,
        "created": _iso(comment.created_at),
        "body": comment.body,
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


def _identifier_ref(issue: Any) -> dict[str, Any]:
    """`{id, identifier, title}`: the shape `parent` and every `children` element share."""
    return {"id": issue.id, "identifier": issue.identifier, "title": issue.title}


def _parent_ref(parent: Issue | None) -> dict[str, Any] | None:
    return None if parent is None else _identifier_ref(parent)


def _project_ref(project: Project | None) -> dict[str, Any] | None:
    """`{id, name}` of the issue's project, or `None` when it has none."""
    return None if project is None else {"id": project.id, "name": project.name}


def _cycle_ref(cycle: Cycle | None) -> dict[str, Any] | None:
    """`{id, number, name}` of the issue's cycle, or `None` when it has none."""
    if cycle is None:
        return None
    return {"id": cycle.id, "number": cycle.number, "name": cycle.name}


def _milestone_ref(item: IssueListItem) -> dict[str, Any] | None:
    """`{id, name}` of the row's milestone, or `None` when the issue carries no milestone.

    The name comes from the join the read carries, so a key this layer publishes has a producer in
    the layer that read the row. No v1 write stores a milestone id, so this is `None` on every
    issue the product can produce today.
    """
    if item.milestone_id is None:
        return None
    return {"id": item.milestone_id, "name": item.milestone_name}


def _iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


__all__ = [
    "COMMENT_TABLE_COLUMNS",
    "ISSUE_TABLE_COLUMNS",
    "PRIORITY_GLYPHS",
    "comment_command",
    "create_command",
    "issue_app",
]
