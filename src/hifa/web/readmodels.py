"""Pure view models for the templates (SPEC-070 §2.3). No IO, no SQL, no Jinja.

Every derived value a template prints (glyphs, formatted dates, css classes, urls, counters)
is resolved here: the templates only read keys, loop and compare booleans.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from urllib.parse import urlencode

from hifa.adapters.sqlite.reads import Board, IssueDetail, IssueListItem, IssuePage
from hifa.domain.identifier import parse as parse_identifier
from hifa.web.markdown import render_markdown

PRIORITY_GLYPHS: dict[int, str] = {0: "\u2013", 1: "U", 2: "H", 3: "M", 4: "L"}
PRIORITY_LABELS: dict[int, str] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}
SORT_LABELS: dict[str, str] = {
    "manual": "Manual",
    "created": "Created",
    "updated": "Updated",
    "priority": "Priority",
    "due": "Due",
}
SORT_OPTIONS: tuple[str, ...] = ("manual", "created", "updated", "priority", "due")
FILTER_KEYS: tuple[str, ...] = ("state", "assignee", "label", "cycle", "project", "q")
MONTHS: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
ATTACHMENT_SCHEMES: frozenset[str] = frozenset({"http", "https", "file"})


# --- small pure helpers ---------------------------------------------------------------


def initials(name: str | None) -> str | None:
    """`"Ginkelte"` -> `"GI"`: first two alphanumeric characters, uppercased."""
    if not name:
        return None
    letters = [ch for ch in name.strip() if ch.isalnum()]
    return ("".join(letters[:2]) or name.strip()[:2]).upper() or None


def priority_glyph(priority: int) -> str:
    """`\u2013`, `U`, `H`, `M`, `L` (SPEC-070 §2.5.5)."""
    return PRIORITY_GLYPHS.get(int(priority), "\u2013")


def priority_label(priority: int) -> str:
    """`Urgent`, `High`, `Medium`, `Low`, `No priority`."""
    return PRIORITY_LABELS.get(int(priority), "No priority")


def due_label(value: date | None) -> str | None:
    """`2026-11-20` -> `Nov 20`."""
    return None if value is None else f"{MONTHS[value.month - 1]} {value.day}"


def is_overdue(value: date | None, today: date) -> bool:
    """True when the due date is in the past."""
    return value is not None and value < today


def _stamp(moment: datetime | None) -> str:
    """`Nov 8 14:03` (no locale, no timezone database)."""
    if moment is None:
        return ""
    return f"{MONTHS[moment.month - 1]} {moment.day} {moment.hour:02d}:{moment.minute:02d}"


def _relative(moment: datetime | None, now: datetime | None) -> str:
    """`2 h ago` / `3 d ago`, falling back to an absolute stamp."""
    if moment is None:
        return ""
    if now is None:
        return _stamp(moment)
    seconds = int((now - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} m ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} d ago"
    return _stamp(moment)


def _query(**params: object) -> str:
    """Query string with the empty values dropped, order preserved."""
    pairs = [(key, value) for key, value in params.items() if value not in (None, "")]
    return urlencode(pairs)


def active_filters(filters: dict[str, str | None]) -> dict[str, str]:
    """Only the filters that are actually set."""
    return {key: value for key, value in filters.items() if value}


def chips(
    filters: dict[str, str | None], *, path: str, extra: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """One removable chip per active filter (`remove_url` = the same page without it)."""
    extra = extra or {}
    out: list[dict[str, str]] = []
    active = active_filters(filters)
    for key in FILTER_KEYS:
        value = active.get(key)
        if not value:
            continue
        remaining = {name: item for name, item in active.items() if name != key}
        remaining.update(extra)
        out.append(
            {
                "kind": key,
                "value": value,
                "label": value,
                "remove_url": f"{path}?{_query(**remaining)}" if remaining else path,
            }
        )
    return out


# --- rows and cards -------------------------------------------------------------------


def build_row_vm(item: IssueListItem, *, today: date) -> dict[str, object]:
    """`row_vm` of SPEC-070 §2.3.3."""
    labels = sorted(item.labels, key=lambda label: label.name.casefold())
    return {
        "identifier": item.identifier,
        "title": item.title,
        "priority": item.priority,
        "priority_glyph": priority_glyph(item.priority),
        "priority_label": priority_label(item.priority),
        "state_name": item.state_name,
        "state_type": item.state_type,
        "state_color": item.state_color,
        "assignee_name": item.assignee_name,
        "assignee_initials": initials(item.assignee_name),
        "labels": [{"name": label.name, "color": label.color} for label in labels],
        "due_date": None if item.due_date is None else item.due_date.isoformat(),
        "due_label": due_label(item.due_date),
        "is_overdue": is_overdue(item.due_date, today),
        "sort_order": item.sort_order,
        "is_archived": False,
        "is_trashed": False,
        "is_urgent": item.priority == 1,
        "has_assignee": bool(item.assignee_name),
        "has_due": item.due_date is not None,
        "has_labels": bool(labels),
        "url": f"/{item.team_key}/issue/{item.identifier}",
        "team_key": item.team_key,
    }


def build_card_vm(item: IssueListItem, *, today: date) -> dict[str, object]:
    """`card_vm` of SPEC-070 §2.3.2: the row keys plus the column it belongs to."""
    card = build_row_vm(item, today=today)
    card["state_id"] = item.state_id
    card["board_order"] = item.board_order
    return card


# --- list / board ---------------------------------------------------------------------


def build_list_vm(
    page: IssuePage,
    *,
    team: dict[str, str],
    filters: dict[str, str | None],
    sort: str,
    limit: int,
    offset: int,
    counts: dict[str, int],
    today: date,
    can_write: bool = False,
) -> dict[str, object]:
    """`list_vm` of SPEC-070 §2.3.3."""
    path = f"/{team['key']}/list"
    active = active_filters(filters)
    sort_value = None if sort == "manual" else sort
    base = _query(**active, sort=sort_value, limit=limit)
    next_url = None
    if page.has_more and page.next_cursor:
        next_url = (
            f"{path}?{base}&cursor={page.next_cursor}"
            if base
            else f"{path}?cursor={page.next_cursor}"
        )
    sort_links = [
        {
            "key": key,
            "label": SORT_LABELS[key],
            "url": f"{path}?{_query(**active, sort=None if key == 'manual' else key)}",
            "is_active": key == sort,
        }
        for key in SORT_OPTIONS
    ]
    return {
        "team": team,
        "rows": [build_row_vm(item, today=today) for item in page.items],
        "filters": {key: filters.get(key) for key in FILTER_KEYS},
        "chips": chips(filters, path=path, extra={"sort": sort_value or ""}),
        "sort": sort,
        "sort_links": sort_links,
        "next_cursor": page.next_cursor,
        "next_url": next_url,
        "shown": len(page.items),
        "limit": limit,
        "offset": offset,
        "has_more": page.has_more,
        "counts": counts,
        "can_write": can_write,
        "filter_action": path,
        "view": "list",
        "target": "#issues-tbody",
        "filter_text": {key: (value or "") for key, value in filters.items()},
    }


def build_board_vm(
    board: Board,
    *,
    team: dict[str, str],
    filters: dict[str, str | None],
    counts: dict[str, int],
    today: date,
    can_write: bool = False,
) -> dict[str, object]:
    """`board_vm` of SPEC-070 §2.3.2; empty columns are rendered by the template."""
    path = f"/{team['key']}/board"
    columns = []
    for group in board.groups:
        columns.append(
            {
                "state_id": group.key,
                "name": group.label,
                "type": group.state_type,
                "color": group.color,
                "position": group.position,
                "count": group.total,
                "issues": [build_card_vm(item, today=today) for item in group.issues],
                "truncated": group.truncated,
                "is_empty": not group.issues,
                "more_url": f"/{team['key']}/list?state={group.key}" if group.truncated else None,
            }
        )
    return {
        "team": team,
        "columns": columns,
        "filters": {key: filters.get(key) for key in FILTER_KEYS},
        "chips": chips(filters, path=path),
        "counts": counts,
        "can_write": can_write,
        "filter_action": path,
        "view": "board",
        "target": "#board",
        "filter_text": {key: (value or "") for key, value in filters.items()},
    }


# --- detail ---------------------------------------------------------------------------


def _attachment_url(url: str) -> str | None:
    """`http`, `https` and `file` only; any other scheme is shown as text (R16)."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return url if scheme in ATTACHMENT_SCHEMES else None


def author_name(members: Mapping[str, str], author_id: str | None) -> str | None:
    """The member who wrote a comment or performed a history entry (never the assignee).

    `None` when the id is missing or unknown: the template then prints `unknown` instead of
    inventing an author (R20).
    """
    if not author_id:
        return None
    return members.get(author_id)


def build_issue_vm(
    detail: IssueDetail,
    *,
    today: date,
    now: datetime | None = None,
    can_write: bool = False,
) -> dict[str, object]:
    """`issue_vm` of SPEC-070 §2.3.4: `row_vm` plus the detail of one issue."""
    item = detail.issue
    try:
        _, number = parse_identifier(item.identifier)
    except Exception:  # pragma: no cover - identifiers are always valid
        number = 0

    labels_all = sorted(item.labels, key=lambda label: label.name.casefold())
    children = [
        {
            "identifier": child.identifier,
            "title": child.title,
            "state_name": child.state_name,
            "state_color": child.state_color,
            "priority_glyph": priority_glyph(child.priority),
        }
        for child in detail.children
    ]
    comments = [
        {
            "id": comment.id,
            "author_name": author_name(detail.members, comment.author_id),
            "author_initials": initials(author_name(detail.members, comment.author_id)),
            "body_html": render_markdown(comment.body),
            "created_label": _relative(comment.created_at, now),
            "is_edited": comment.edited_at is not None,
            "resolved": comment.is_resolved,
        }
        for comment in detail.comments
    ]
    relations = []
    for relation, other in detail.relations:
        direction = "out" if relation.issue_id == item.id else "in"
        label = str(relation.type)
        if direction == "in" and label == "blocks":
            label = "blocked by"
        relations.append(
            {
                "id": relation.id,
                "type": str(relation.type),
                "direction": direction,
                "label": label,
                "identifier": other.identifier,
                "title": other.title,
                "state_name": other.state_name,
                "url": f"/{other.team_key}/issue/{other.identifier}",
            }
        )
    attachments = [
        {
            "id": attachment.id,
            "title": attachment.title,
            "subtitle": attachment.subtitle,
            "url": _attachment_url(attachment.url),
            "host_label": attachment.source_type or "local file",
        }
        for attachment in detail.attachments
    ]
    history = [
        {
            "field": entry.field,
            "actor_name": author_name(detail.members, entry.actor_id),
            "old_label": entry.old_value or "",
            "new_label": entry.new_value or "",
            "created_label": _relative(entry.created_at, now),
        }
        for entry in detail.history
    ]

    vm = build_row_vm(item, today=today)
    vm.update(
        {
            "number": number,
            "description_html": render_markdown(detail.description),
            "estimate": item.estimate,
            "project": None
            if detail.project is None
            else {"id": detail.project.id, "name": detail.project.name},
            "cycle": (
                None
                if detail.cycle is None
                else {
                    "id": detail.cycle.id,
                    "number": detail.cycle.number,
                    "name": detail.cycle.name or f"Cycle {detail.cycle.number}",
                }
            ),
            "parent": (
                None
                if detail.parent is None
                else {"identifier": detail.parent.identifier, "title": detail.parent.title}
            ),
            "children": children,
            "labels_all": [
                {"id": label.id, "name": label.name, "color": label.color} for label in labels_all
            ],
            "states": [],
            "assignees": [],
            "comments": comments,
            "relations": relations,
            "attachments": attachments,
            "history": history,
            "estimate_label": "\u2013" if item.estimate is None else f"{item.estimate:g} pts",
            "project_label": "\u2013" if detail.project is None else detail.project.name,
            "cycle_label": (
                "\u2013"
                if detail.cycle is None
                else detail.cycle.name or f"Cycle {detail.cycle.number}"
            ),
            "parent_label": (
                "\u2013"
                if detail.parent is None
                else f"{detail.parent.identifier} {detail.parent.title}"
            ),
            "team_key": item.team_key,
            "created_label": _stamp(item.created_at),
            "updated_label": _relative(item.updated_at, now),
            "can_write": can_write,
        }
    )
    return vm


# --- page context ---------------------------------------------------------------------


def build_page(
    *,
    title: str,
    team: dict[str, str] | None,
    teams: tuple[dict[str, str], ...] | list[dict[str, str]],
    nav: str | None,
    viewer: dict[str, str] | None = None,
    version: str = "",
    can_write: bool = True,
    flash: str | None = None,
) -> dict[str, object]:
    """`page` of SPEC-070 §2.3.1: the context every template receives."""
    return {
        "title": title,
        "team": team,
        "teams": list(teams),
        "nav": nav,
        "is_board": nav == "board",
        "is_list": nav == "list",
        "is_search": nav == "search",
        "viewer": viewer,
        "version": version,
        "can_write": can_write,
        "flash": flash,
    }


def team_vm(team: object) -> dict[str, str]:
    """A `Team` entity as a template dict with its board url."""
    key = str(team.key)
    return {
        "key": key,
        "name": str(team.name),
        "icon": str(team.icon or ""),
        "color": str(team.color or ""),
        "url": f"/{key}/board",
    }


def team_nav_vm(team: object, *, issues_count: int, active: bool) -> dict[str, object]:
    """One entry of `page["teams"]` (SPEC-070 §2.3.1): identity, url, issue count and active.

    It is built per request from the database, so a team created while the server is running
    appears in the next page without a restart. `issues_count` is `open_count + closed_count`
    of `v_team_progress` and `active` is `True` only on that team's own routes.
    """
    entry: dict[str, object] = dict(team_vm(team))
    entry["issues_count"] = int(issues_count)
    entry["active"] = bool(active)
    return entry


__all__ = [
    "ATTACHMENT_SCHEMES",
    "FILTER_KEYS",
    "PRIORITY_GLYPHS",
    "PRIORITY_LABELS",
    "SORT_LABELS",
    "SORT_OPTIONS",
    "active_filters",
    "author_name",
    "build_board_vm",
    "build_card_vm",
    "build_issue_vm",
    "build_list_vm",
    "build_page",
    "build_row_vm",
    "chips",
    "due_label",
    "initials",
    "is_overdue",
    "priority_glyph",
    "priority_label",
    "team_nav_vm",
    "team_vm",
]
