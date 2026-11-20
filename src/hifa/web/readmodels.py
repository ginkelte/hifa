"""Pure view models for the templates. No IO, no SQL, no Jinja.

Every derived value a template prints (glyphs, formatted dates, css classes, urls, counters)
is resolved here: the templates only read keys, loop and compare booleans.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from typing import Any
from urllib.parse import urlencode

from hifa.adapters.sqlite.reads import Board, IssueDetail, IssueListItem, IssuePage
from hifa.app.history import SOMEONE, WINDOW_SIZE, HistoryPage, history_rows, wire_instant
from hifa.domain.entities import WorkflowState
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
#: Fallback for a state without a stored colour (`workflow_state.color` is nullable).
DEFAULT_STATE_COLOR = "#6B6F76"
#: The fields the edit form and the `PATCH` of an issue accept, in the order they are rendered.
#: The key is the name of the form control; the read model uses the same tuple to build the
#: per-field error slot, so a field without an error and a field that failed differ only in value.
EDIT_FORM_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "priority",
    "state",
    "assignee",
    "due_date",
    "estimate",
)
#: The controls of the comment composer. `body` is the only one it renders: the composer is
#: flat, so a refusal that names `parent_id` belongs to the element and not to a control nobody
#: can see. Neither name is a field of the issue, so neither is in the edit tuple.
COMMENT_FORM_FIELDS: tuple[str, ...] = ("body",)


# --- small pure helpers ---------------------------------------------------------------


def initials(name: str | None) -> str | None:
    """`"Ginkelte"` -> `"GI"`: first two alphanumeric characters, uppercased."""
    if not name:
        return None
    letters = [ch for ch in name.strip() if ch.isalnum()]
    return ("".join(letters[:2]) or name.strip()[:2]).upper() or None


def priority_glyph(priority: int) -> str:
    """Priority glyph: `\u2013`, `U`, `H`, `M`, `L`."""
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


def _day_stamp(day: date) -> str:
    """`11 Nov 2026`: the label of the day a run of events opens."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


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


def build_row_vm(
    item: IssueListItem,
    *,
    today: date,
    can_write: bool = False,
    form_error: str | None = None,
    invalid_field: str | None = None,
) -> dict[str, object]:
    """Template row of one issue for the list table.

    `can_write` is the only key the row uses to decide whether it carries its own edit
    control: in read-only mode the row is text and links, never a form.

    `form_error` and `invalid_field` are the answer of a refused write: the message the use
    case raised and the control that carries it. Both default to `None`, so a row read from
    the listing is the same dict as a row re-rendered after a refusal. Every value a control
    prints is resolved here (`due_value`, `estimate_value`, `assignee_id`), so the template
    only ever prints a key.
    """
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
        "assignee_id": item.assignee_id or "",
        "labels": [{"name": label.name, "color": label.color} for label in labels],
        "due_date": None if item.due_date is None else item.due_date.isoformat(),
        "due_value": "" if item.due_date is None else item.due_date.isoformat(),
        "due_label": due_label(item.due_date),
        "estimate_value": "" if item.estimate is None else f"{item.estimate:g}",
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
        "can_write": can_write,
        "form_error": form_error,
        "invalid_field": invalid_field,
        "title_invalid": invalid_field == "title",
        "description_invalid": invalid_field == "description",
    }


def build_card_vm(
    item: IssueListItem,
    *,
    today: date,
    can_write: bool = False,
    form_error: str | None = None,
    invalid_field: str | None = None,
) -> dict[str, object]:
    """Template card of one issue for the board: the row keys plus its column."""
    card = build_row_vm(
        item,
        today=today,
        can_write=can_write,
        form_error=form_error,
        invalid_field=invalid_field,
    )
    card["state_id"] = item.state_id
    card["board_order"] = item.board_order
    return card


def _state_vm(state: WorkflowState, current_id: str) -> dict[str, object]:
    """One option of the move `<select>`: the value is the state id, never its name."""
    return {
        "id": state.id,
        "name": state.name,
        "type": str(state.type),
        "color": state.color or DEFAULT_STATE_COLOR,
        "is_current": state.id == current_id,
    }


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
    """Template model of the list page: rows, filters, sort links and pagination."""
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
        "rows": [build_row_vm(item, today=today, can_write=can_write) for item in page.items],
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
    """Template model of the board: one column per state, empty ones included."""
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
                "issues": [
                    build_card_vm(item, today=today, can_write=can_write) for item in group.issues
                ],
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


#: How many rows the panel shows before offering the rest of the window.
PANEL_HISTORY_ROWS = 8


def author_name(members: Mapping[str, str], author_id: str | None) -> str | None:
    """The member who wrote a comment or performed a history entry (never the assignee).

    `None` when the id is missing or unknown: the template then prints `unknown` instead of
    inventing an author.
    """
    if not author_id:
        return None
    return members.get(author_id)


def _history_page(
    detail: IssueDetail, *, states: Sequence[WorkflowState] = (), now: datetime | None = None
) -> HistoryPage:
    """The one formatter's page for the history window of one detail read.

    The state map resolves a move and carries the colour of its dot; the member and parent maps
    are the read's. `now` only feeds the one-line summary under the title, so a caller without a
    clock still gets the rows.
    """
    rows = tuple(states) or tuple(getattr(detail, "states", ()))
    return history_rows(
        detail.history,
        states={state.id: state.name for state in rows},
        members=detail.members,
        parents=getattr(detail, "history_parents", None),
        state_colors={state.id: state.color for state in rows},
        now=now,
    )


def build_issue_vm(
    detail: IssueDetail,
    *,
    today: date,
    now: datetime | None = None,
    can_write: bool = False,
    states: Sequence[WorkflowState] = (),
    is_archived: bool = False,
    is_trashed: bool = False,
    form_errors: Mapping[str, str] | None = None,
    form_error: str | None = None,
    confirm_open: str | None = None,
) -> dict[str, object]:
    """Template model of one issue detail: `row_vm` plus the detail collections.

    `states`, `is_archived` and `is_trashed` are the detail's own facts: the caller hands over
    `detail.states`, `detail.archived_at is not None` and `detail.trashed`, so this function
    stays pure and the panel, the page and a refusal render the same dict from one read.

    `form_errors` and `form_error` are the answer of a refused write: the message of the use
    case, once per control and once for the element. The per-control dict is normalised to one
    entry per `EDIT_FORM_FIELDS` and `COMMENT_FORM_FIELDS`, `None` where the field is fine, so
    the template can ask for a
    key that is always there and still fail in CI on a real omission. `form_error` stands on
    its own because a refusal is not always about a control (`ISSUE_ARCHIVED` names no field).

    `confirm_open` says which of the two `<details>` blocks a refusal must re-render **open** —
    the form asked for no confirmation, so the block it refused is the one the reader has to see.
    """
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
    page = _history_page(detail, states=states, now=now)
    history: list[dict[str, object]] = []
    previous_day: date | None = None
    for entry in page.rows:
        day = entry.created_at.date()
        history.append(
            {
                "id": entry.id,
                "field": entry.field,
                "actor_id": entry.actor_id,
                "actor_label": entry.actor_label,
                "actor_initials": (
                    SOMEONE if entry.actor_label == SOMEONE else initials(entry.actor_label)
                ),
                "sentence": entry.sentence,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "old_label": entry.old_label,
                "new_label": entry.new_label,
                "dot_color": entry.dot_color,
                "created_at": wire_instant(entry.created_at),
                "created_label": _relative(entry.created_at, now),
                "day_label": None if day == previous_day else _day_stamp(day),
            }
        )
        previous_day = day

    errors = {
        name: (form_errors or {}).get(name) for name in EDIT_FORM_FIELDS + COMMENT_FORM_FIELDS
    }
    invalid_field = next((name for name, message in errors.items() if message), None)
    vm = build_row_vm(
        item,
        today=today,
        can_write=can_write,
        form_error=errors.get(invalid_field) if invalid_field else None,
        invalid_field=invalid_field,
    )
    vm.update(
        {
            "number": number,
            "description_html": render_markdown(detail.description),
            "description_raw": detail.description or "",
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
            "states": [_state_vm(state, item.state_id) for state in states],
            "assignees": [],
            "comments": comments,
            "relations": relations,
            "history": history,
            "history_panel": history[:PANEL_HISTORY_ROWS],
            "history_window": page.window_size,
            "history_hidden": max(0, page.window_size - PANEL_HISTORY_ROWS),
            "history_truncated": page.truncated,
            "history_floor": f"the last {WINDOW_SIZE} events" if page.truncated else None,
            "last_activity": (
                None
                if page.last_activity is None
                else {"text": page.last_activity.text, "row_id": page.last_activity.row_id}
            ),
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
            "confirm_open": confirm_open,
            "is_archived": is_archived,
            "is_trashed": is_trashed,
            "form_errors": errors,
            "form_error": form_error if form_error is not None else vm["form_error"],
            "edit_action": f"/{item.team_key}/issue/{item.identifier}",
            "edit_fragment": f"/_/issues/{item.identifier}",
            "priority_options": [
                {"value": value, "label": label, "is_current": value == item.priority}
                for value, label in PRIORITY_LABELS.items()
            ],
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
    revision: int = 0,
    revision_poll: str = "",
    refresh_url: str | None = None,
) -> dict[str, object]:
    """Context every template receives: title, team, sidebar, viewer and flash.

    `revision`, `revision_poll` and `refresh_url` are printed, never computed: the page marker
    and the poller are the whole of their use.
    """
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
        "revision": revision,
        "revision_poll": revision_poll,
        "refresh_url": refresh_url,
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
    """One entry of `page["teams"]`: identity, url, issue count and active.

    It is built per request from the database, so a team created while the server is running
    appears in the next page without a restart. `issues_count` is `open_count + closed_count`
    of `v_team_progress` and `active` is `True` only on that team's own routes.
    """
    entry: dict[str, object] = dict(team_vm(team))
    entry["issues_count"] = int(issues_count)
    entry["active"] = bool(active)
    return entry


# --- search ---------------------------------------------------------------------------

#: The two markers the snippet arrives with. They are the only markup this layer keeps.
SEARCH_MARK_START = "<mark>"
SEARCH_MARK_END = "</mark>"

#: The sentence of the page's empty state, and the class each state's block carries.
SEARCH_EMPTY_MESSAGE = "search the workspace"
EMPTY_CLASS = "empty"
ERROR_CLASS = "error"


def _stamp_value(value: str) -> datetime | None:
    """One ISO-8601 instant as a `datetime`, or `None` when it does not parse."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _snippet_html(snippet: str) -> str:
    """Escapes every piece of a snippet and keeps the marker pair as the only markup.

    The snippet arrives with the literal `<mark>`/`</mark>` around the terms and unescaped.
    Splitting on the opening marker and escaping each piece is what makes the page print the
    text once: escaping the whole string first would show `&lt;mark&gt;`, and inserting the raw
    string would let a title carry its own markup into the document.
    """
    pieces = snippet.split(SEARCH_MARK_START)
    rendered = escape(pieces[0])
    for piece in pieces[1:]:
        marked, _, rest = piece.partition(SEARCH_MARK_END)
        rendered += f"{SEARCH_MARK_START}{escape(marked)}{SEARCH_MARK_END}{escape(rest)}"
    return rendered


def _search_hit_vm(hit: Any, now: datetime | None) -> dict[str, object]:
    """One hit of the list: the six fields the CLI prints plus the keys the template needs."""
    identifier = str(hit.identifier)
    updated = _stamp_value(str(hit.updated_at))
    state_type = str(hit.state_type)
    priority = int(hit.priority)
    try:
        team_key = parse_identifier(identifier)[0]
    except ValueError:
        team_key = ""
    url = f"/{team_key}/issue/{identifier}" if team_key else f"/{identifier}"
    return {
        "identifier": identifier,
        "title": str(hit.title),
        "snippet_html": _snippet_html(str(hit.snippet)),
        "state_name": str(hit.state_name),
        "state_type": state_type,
        "priority": priority,
        "priority_glyph": priority_glyph(priority),
        "updated_label": _relative(updated, now),
        "url": url,
    }


def build_search_vm(
    *,
    q: str,
    page: Any = None,
    refusal: Any = None,
    teams: Sequence[Mapping[str, Any]] = (),
    team_filter: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Template model of the search page: exactly one of its four states and all its keys.

    `refusal` is the error the route caught; its sentence is composed by the edge's one
    builder and stored here, so the template prints it like any other value. `page` is the
    answer of the use case, or `None` when the request carried no query at all — which is the
    empty state, not an error.
    """
    hits: list[dict[str, object]] = []
    message: str | None = None
    message_class: str | None = None
    state = "empty"
    autofocus = False

    if refusal is not None:
        from hifa.web.app import refusal_sentence

        state = "error"
        message = refusal_sentence(refusal)
        message_class = ERROR_CLASS
    elif not q.strip():
        message = SEARCH_EMPTY_MESSAGE
        message_class = EMPTY_CLASS
        autofocus = True
    elif page is not None and page.hits:
        state = "results"
        hits = [_search_hit_vm(hit, now) for hit in page.hits]
    else:
        state = "no_results"
        message = f"nothing matches '{q}'"
        message_class = EMPTY_CLASS

    return {
        "q": q,
        "state": state,
        "message": message,
        "message_class": message_class,
        "autofocus": autofocus,
        "hits": hits,
        "count": len(hits),
        "teams": [{"key": str(team.get("key")), "name": str(team.get("name"))} for team in teams],
        "team_filter": team_filter,
    }


__all__ = [
    "DEFAULT_STATE_COLOR",
    "EDIT_FORM_FIELDS",
    "FILTER_KEYS",
    "PRIORITY_GLYPHS",
    "SEARCH_EMPTY_MESSAGE",
    "build_search_vm",
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
