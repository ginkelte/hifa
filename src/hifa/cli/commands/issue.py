"""`hifa issue` — issue subcommands (SPEC-050 §2.5.3).

T-05 implements `issue create`. The command only orchestrates the edge: it normalizes
flags, resolves context (database, team, member), builds `CreateIssue` and calls
`create_issue`. No business rule lives here: titles, priorities, dates and labels are
validated by `app/` through `domain.rules`.

The canonical `Issue` JSON object (SPEC-050 §2.4.2) is assembled in `_canonical`: it is
a presentation helper (it resolves names for the envelope), not a use case.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.commands import Actor, CreateIssue
from hifa.app.issues import create_issue
from hifa.cli import output
from hifa.cli.context import CliContext
from hifa.cli.errors import EXIT_NOT_FOUND, CliError, fail
from hifa.config import ConfigError, resolve_config
from hifa.domain.entities import Issue, Label, Member
from hifa.domain.errors import HifaError, NotFound

issue_app = typer.Typer(
    name="issue",
    help="Manage issues.",
    no_args_is_help=True,
    add_completion=False,
)

#: Columns of the row printed by every issue mutation (SPEC-050 §2.4.1).
ISSUE_TABLE_COLUMNS: tuple[str, ...] = (
    "identifier",
    "p",
    "state",
    "assignee",
    "title",
    "labels",
    "due",
)

#: Priority -> single-cell glyph (SPEC-050 §2.4.1).
PRIORITY_GLYPHS: dict[int, str] = {0: "–", 1: "U", 2: "H", 3: "M", 4: "L"}

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
            team_ref = _resolve_team(repos, team, cfg.default_team)
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


def _resolve_team(repos: Repos, flag: str | None, default_team: str | None) -> str:
    candidate = flag or os.environ.get("HIFA_TEAM") or default_team
    if candidate and candidate.strip():
        return candidate.strip()
    teams = [team for team in repos.teams.list() if team.archived_at is None]
    if len(teams) == 1:
        return teams[0].key
    raise CliError(
        "team required: use --team, HIFA_TEAM or [cli].default_team in .hifa.toml",
        code="missing_context",
        field="team",
    )


def _resolve_assignee(repos: Repos, ref: str | None) -> str | None:
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
        raise NotFound(resource="member", key=value)
    return member.id


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


# --- presentation -----------------------------------------------------------------------


def _canonical(repos: Repos, issue: Issue) -> dict[str, Any]:
    """Complete `Issue` object in SPEC-050 §2.4.2 order (no key is ever omitted)."""
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
