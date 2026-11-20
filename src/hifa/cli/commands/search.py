"""`hifa search` — full-text search over the issues of the workspace.

The command is edge orchestration only: it parses the raw string with `parse_query`, composes
the `SearchQuery` from that plus the flags it was given, calls `search_issues` once with the
instant its own clock read and formats the page. It holds no query of its own.
"""

from __future__ import annotations

from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.sqlite.connection import Database
from hifa.app.search import (
    DEFAULT_LIMIT,
    MARK_END,
    MARK_START,
    SearchHit,
    SearchPage,
    SearchQuery,
    parse_query,
    search_issues,
    with_overrides,
)
from hifa.cli import output
from hifa.cli.commands.issue import PRIORITY_GLYPHS
from hifa.cli.context import CliContext
from hifa.cli.errors import CliError, fail
from hifa.config import ConfigError, resolve_config
from hifa.domain.errors import HifaError, Validation

#: The five orders `--sort` accepts.
SORTS: tuple[str, ...] = ("relevance", "updated", "created", "priority", "due")

#: The six columns of the search table, in order.
SEARCH_TABLE_COLUMNS: tuple[str, ...] = ("identifier", "p", "state", "updated", "title", "match")

#: The widths of the three columns this table narrows: its worst case is 119 cells of a 120
#: budget, so `STATE`, `TITLE` and `MATCH` are the three that give way.
SEARCH_COLUMN_FORMATS: dict[str, output.ColumnFormat] = {
    "STATE": output.ColumnFormat(max_cells=12),
    "TITLE": output.ColumnFormat(max_cells=30),
    "MATCH": output.ColumnFormat(max_cells=35, single_line=True),
}

#: Reverse video, used for the matched span when the terminal takes ANSI colour.
REVERSE_ON = "\x1b[7m"
REVERSE_OFF = "\x1b[27m"


def search_command(
    ctx: typer.Context,
    query: str | None = typer.Argument(None, help='Free text, "phrase", prefix* or field:value.'),
    team: str | None = typer.Option(None, "--team", help="Team key (team:KEY)."),
    state: str | None = typer.Option(None, "--state", help="State name or type (state:X)."),
    assignee: str | None = typer.Option(
        None, "--assignee", help="Member, email, me or none (assignee:X)."
    ),
    label: str | None = typer.Option(None, "--label", help="Label name (label:X)."),
    priority: str | None = typer.Option(
        None, "--priority", help="none, urgent, high, medium, low or 0..4."
    ),
    due: str | None = typer.Option(
        None, "--due", help="none, overdue, today, week, YYYY-MM-DD, >DATE or <DATE."
    ),
    is_scope: str | None = typer.Option(
        None, "--is", help="open, closed, archived, trashed or all."
    ),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", help="Page size, 1-200."),
    offset: int = typer.Option(0, "--offset", help="Rows to skip."),
    all_flag: bool = typer.Option(False, "--all", help="Every visibility (same as --is all)."),
    count: bool = typer.Option(False, "--count", help="Ask for the total of the filtered set."),
    sort: str = typer.Option(
        "relevance", "--sort", help="relevance, updated, created, priority, due."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over the global one)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="One identifier per hit."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Search the issues of the workspace; the query is the whole grammar of the verb."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)

    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive", code="invalid_option", field="output"
            )
        )

    try:
        page = _run_search(
            db_flag=db if db is not None else root.db,
            raw=query or "",
            team=team,
            state=state,
            assignee=assignee,
            label=label,
            priority=priority,
            due=due,
            is_scope=is_scope,
            limit=limit,
            offset=offset,
            all_flag=all_flag,
            count=count,
            sort=sort,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    _emit(page)
    if not page.hits and not json_mode and not quiet_mode:
        output.write_note("0 issues")


def _run_search(
    *,
    db_flag: str | None,
    raw: str,
    team: str | None,
    state: str | None,
    assignee: str | None,
    label: str | None,
    priority: str | None,
    due: str | None,
    is_scope: str | None,
    limit: int,
    offset: int,
    all_flag: bool,
    count: bool,
    sort: str,
) -> SearchPage:
    """Composes the query from the parsed text, the flags and the page options, and runs it."""
    if sort not in SORTS:
        raise Validation(
            f"--sort must be one of {', '.join(SORTS)}", rule="SORT_INVALID", field="sort"
        )
    cfg = resolve_config(db_flag=db_flag)
    parsed = parse_query(raw)
    filters = with_overrides(
        parsed,
        {
            "team": team,
            "state": state,
            "assignee": assignee,
            "label": label,
            "priority": priority,
            "due": due,
            "is_": is_scope,
        },
        all_flag=all_flag,
    )
    query = SearchQuery(
        text=parsed.text,
        filters=filters,
        limit=limit,
        offset=offset,
        sort=sort,  # type: ignore[arg-type]
        count=count,
    )
    return search_issues(Database(cfg.db_path), query, now=SystemClock().now())


def _hit_object(hit: SearchHit) -> dict[str, Any]:
    """One hit of the `--json` envelope: the eleven flat fields, in the declared order."""
    return {
        "issue_id": hit.issue_id,
        "identifier": hit.identifier,
        "title": hit.title,
        "state_name": hit.state_name,
        "state_type": hit.state_type,
        "priority": hit.priority,
        "due_date": hit.due_date,
        "assignee_display_name": hit.assignee_display_name,
        "updated_at": hit.updated_at,
        "snippet": hit.snippet,
        "score": hit.score,
    }


def _match_cell(snippet: str) -> str:
    """The `MATCH` cell: the markers stripped, or the matched span in reverse video with ANSI on."""
    if not snippet:
        return ""
    if output.options().color:
        return snippet.replace(MARK_START, REVERSE_ON).replace(MARK_END, REVERSE_OFF)
    return snippet.replace(MARK_START, "").replace(MARK_END, "")


def _table_row(hit: SearchHit) -> dict[str, Any]:
    return {
        "identifier": hit.identifier,
        "p": PRIORITY_GLYPHS.get(int(hit.priority), "\u2013"),
        "state": hit.state_name,
        "updated": hit.updated_at[:10],
        "title": hit.title,
        "match": _match_cell(hit.snippet),
    }


def _emit(page: SearchPage) -> None:
    """`--json` envelope, the six-column table or one identifier per hit."""
    rows = [_table_row(hit) for hit in page.hits]
    envelope = {
        "hits": [_hit_object(hit) for hit in page.hits],
        "total": page.total,
        "has_more": page.has_more,
        "backend": page.backend,
        "took_ms": page.took_ms,
    }
    output.emit(
        rows,
        table_cols=SEARCH_TABLE_COLUMNS,
        table_formats=SEARCH_COLUMN_FORMATS,
        json_obj=envelope,
        quiet_obj=[hit.identifier for hit in page.hits],
    )


__all__ = ["SEARCH_COLUMN_FORMATS", "SEARCH_TABLE_COLUMNS", "SORTS", "search_command"]
