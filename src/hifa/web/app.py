"""Dashboard application factory.

The web layer is an **edge**: it parses the request, calls the `app/` read/write functions
and prints the read models from `hifa.web.readmodels`. No SQL, no business rules, no
`async def` on anything that touches SQLite (the driver is sync and would block the loop).

`fastapi`, `jinja2` and `uvicorn` live in the `[web]` extra: nothing in the core imports
this module.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from hifa import __version__
from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import Database, RevisionWatch
from hifa.adapters.sqlite.reads import IssueDetail, ListFilters
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app import boards, members
from hifa.app.commands import (
    Actor,
    ArchiveIssue,
    CommentOnIssue,
    CreateIssue,
    MoveIssue,
    RestoreIssue,
    TrashIssue,
    UpdateIssue,
    UpdateState,
)
from hifa.app.issues import (
    archive_issue,
    comment_on_issue,
    create_issue,
    move_issue,
    restore_issue,
    trash_issue,
    update_issue,
)
from hifa.app.teams import list_states, update_state
from hifa.domain.entities import Comment, Issue, Team, WorkflowState
from hifa.domain.errors import Conflict, HifaError, InvalidTransition, NotFound, Validation
from hifa.domain.ranking import UNIT_GAP, between
from hifa.web import readmodels

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

#: No `unsafe-eval`, no inline script, no framing.
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT_CACHE = "public, max-age=60"
SHORT_CACHE_PATHS = frozenset({"/static/js/app.js", "/static/css/app.css"})
SEARCH_UNAVAILABLE = "search is not implemented yet"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: The only place the revision poll interval is written: the template prints it as
#: `page["revision_poll"]` and nothing else repeats the duration.
REVISION_POLL = "5s"
#: Methods that cannot commit: their `HX-Revision` is the number observed before they ran.
#: The write guard reads the same set, so these methods never reach its question at all: a
#: maintainer changing this constant for one consumer changes it for both.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: `Sec-Fetch-Site` values that name this edge itself, or a navigation the reader started.
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
#: The guard's one sentence: a statement about the caller, not about the instance, and a
#: different sentence from the read-only refusal so a reader can tell which one answered.
CROSS_SITE_REFUSAL = "cross-site write refused"


class FragmentRequiresHtmx(Exception):
    """A `/_/*` route was called without `HX-Request: true`."""


def build_environment() -> Environment:
    """Jinja environment: autoescape on, `StrictUndefined` (a missing key is a bug)."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(("html", "xml"), default_for_string=True, default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


# --- dependencies ---------------------------------------------------------------------


def require_htmx(request: Request) -> None:
    """`/_/*` is only for htmx; a bare browser hit gets a 400."""
    if request.headers.get("HX-Request") != "true":
        raise FragmentRequiresHtmx()


HTMX = Depends(require_htmx)


def _database(request: Request) -> Database:
    return request.app.state.database


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _can_write(request: Request) -> bool:
    return not request.app.state.read_only


def _revisions(request: Request) -> RevisionWatch:
    return request.app.state.revisions


def _observed(request: Request) -> int | None:
    """`PRAGMA data_version` of this request, read once and cached on the request.

    The probe is defined as **one** read: the middleware asks before the route runs, the route asks
    again, and the header is written after it. Caching the raw answer — `None` included, hence
    `hasattr` and not a default — is what keeps it to one read and makes the JSON body, the page
    marker and the `HX-Revision` header one observation instead of three that can disagree.
    """
    if not hasattr(request.state, "revision"):
        request.state.revision = _revisions(request).read()
    return request.state.revision


def _revision(request: Request) -> int:
    """The revision this request observed: one read, shared by the page marker and the header.

    The number is opaque, so an unreadable probe becomes `0` (never a number a live connection
    reports): the page still renders, and the first probe tick that answers reloads it once.
    """
    number = _observed(request)
    return 0 if number is None else number


def _today() -> date:
    return datetime.now(UTC).date()


def _int_param(name: str, raw: str | None, *, default: int, rule: str) -> int:
    """A query-string integer or a `Validation` (`?limit=abc` is a `422`, never a `500`)."""
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise Validation(f"{name} is not an integer", rule=rule, field=name, value=raw) from exc


def _pagination(request: Request) -> tuple[str | None, int, int]:
    """`(cursor, offset, limit)` of the listing, validated at the edge.

    `cursor` and `offset` are mutually exclusive; the ranges (limit 1-200, offset 0-500) are
    enforced by `ListFilters` itself, so `?limit=999` is a `422 LIMIT_RANGE` and never a
    silent clamp.
    """
    query = request.query_params
    cursor = query.get("cursor") or None
    offset_raw = query.get("offset") or None
    offset = _int_param("offset", offset_raw, default=0, rule="OFFSET_RANGE")
    limit = _int_param("limit", query.get("limit"), default=DEFAULT_LIMIT, rule="LIMIT_RANGE")
    if cursor and offset_raw:
        raise Validation(
            "cursor and offset are mutually exclusive",
            rule="CURSOR_OFFSET_CONFLICT",
            field="offset",
            value=offset_raw,
        )
    return cursor, offset, limit


def _teams_vm(request: Request, current: Team | None = None) -> list[dict[str, object]]:
    """Sidebar teams, read **per request** from the database.

    There is no cached team list: a team created by the CLI while the server is running has to
    appear in the sidebar of the next page without a restart.
    """
    return [
        readmodels.team_nav_vm(
            team,
            issues_count=issues_count,
            active=current is not None and team.id == current.id,
        )
        for team, issues_count in boards.teams_with_counts(_database(request))
    ]


def _page(
    request: Request,
    *,
    title: str,
    nav: str | None,
    team: Team | None = None,
    flash: str | None = None,
    refresh_url: str | None = None,
) -> dict[str, object]:
    return readmodels.build_page(
        title=title,
        team=None if team is None else readmodels.team_vm(team),
        teams=_teams_vm(request, team),
        nav=nav,
        viewer=request.app.state.viewer,
        version=__version__,
        can_write=_can_write(request),
        flash=flash,
        revision=_revision(request),
        revision_poll=REVISION_POLL,
        refresh_url=refresh_url,
    )


def _render(
    request: Request, template: str, context: dict[str, object], *, status_code: int = 200
) -> HTMLResponse:
    context = {"page": context.pop("page"), **context}
    return _templates(request).TemplateResponse(request, template, context, status_code=status_code)


def _error_page(
    request: Request,
    *,
    status_code: int,
    message: str,
    back_url: str | None = None,
    title: str = "Error",
) -> HTMLResponse:
    """`error.html` with the same layout; never a traceback."""
    if request.url.path.startswith("/_/") and request.headers.get("HX-Request") == "true":
        return _fragment_error(request, status_code=status_code, message=message)
    page = _page(request, title=title, nav="")
    return _render(
        request,
        "error.html",
        {"page": page, "error": {"status": status_code, "message": message, "back_url": back_url}},
        status_code=status_code,
    )


def _fragment_error(request: Request, *, status_code: int, message: str) -> HTMLResponse:
    """A small fragment for a failed `/_/*` call: a whole document inside a `<tbody>` breaks it."""
    response = _render(
        request,
        "partials/fragment_error.html",
        {"page": _page(request, title="Error", nav=""), "error": {"message": message}},
        status_code=status_code,
    )
    response.headers["HX-Retarget"] = "#toasts"
    response.headers["HX-Reswap"] = "beforeend"
    return response


def _form_error(request: Request, *, team: str, status_code: int, message: str) -> HTMLResponse:
    """Re-render the quick-add form with the error inside it.

    `HX-Retarget` + `HX-Reswap: outerHTML` replaces the `<form>` that failed instead of
    injecting the whole document into `#issues-tbody`.
    """
    response = _render(
        request,
        "partials/quick_add.html",
        {
            "page": _page(request, title="Quick add", nav="list"),
            "vm": {"team": {"key": team}, "can_write": True},
            "form_error": message,
            "form_errors": None,
        },
        status_code=status_code,
    )
    response.headers["HX-Retarget"] = "#quick-add"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _list_form_error(
    request: Request, entity: Team, errors: dict[str, str], *, status_code: int = 422
) -> HTMLResponse:
    """Re-render `list.html` with the errors of the quick-add form next to the field.

    This is the no-JS path: a plain form post that fails validation comes back as a page (not
    a fragment), so the browser shows the message without any script.
    """
    vm = _list_page(request, entity, _filters(request))
    page = _page(request, title=f"List \u00b7 {entity.key}", nav="list", team=entity)
    return _render(
        request,
        "list.html",
        {
            "page": page,
            "vm": vm,
            "form_error": None,
            "form_errors": errors,
        },
        status_code=status_code,
    )


def _filters(request: Request) -> dict[str, str | None]:
    query = request.query_params
    return {
        "state": query.get("state") or None,
        "assignee": query.get("assignee") or None,
        "label": query.get("label") or None,
        "cycle": query.get("cycle") or None,
        "project": query.get("project") or None,
        "q": query.get("q") or None,
    }


#: Query-string keys the poll re-sends: the fragment it loads is the one the filter bar loads,
#: so the filters of the page travel with it.
REFRESH_KEYS = ("q", "state", "assignee", "label", "cycle", "project", "sort", "limit")


def _refresh_url(request: Request, *, view: str, team: str) -> str:
    """The fragment URL that re-renders the collection this page is showing."""
    query = request.query_params
    params: list[tuple[str, str]] = [("view", view), ("team", team)]
    params.extend((key, query[key]) for key in REFRESH_KEYS if query.get(key))
    return "/_/issues?" + urlencode(params)


def _team_or_default(request: Request, team: str | None) -> Team:
    """The `{team}` of the route or, when absent, the default/single team of the app."""
    db = _database(request)
    if team:
        return boards.require_team(db, team)
    wanted = request.app.state.default_team
    teams = boards.list_teams(db)
    if wanted:
        match = next((item for item in teams if item.key == str(wanted).upper()), None)
        if match is not None:
            return match
    if len(teams) == 1:
        return teams[0]
    raise NotFound(resource="team", key=team or "")


def _list_filters(
    request: Request,
    team: Team,
    *,
    cursor: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> ListFilters:
    """The query string as a `ListFilters` with the ids resolved."""
    raw = _filters(request)
    ids = boards.resolve_filters(
        _database(request),
        team.key,
        state=raw["state"],
        assignee=raw["assignee"],
        label=raw["label"],
    )
    label_id = ids["label_id"]
    return ListFilters(
        team_id=team.key,
        state_id=ids["state_id"],
        assignee_id=ids["assignee_id"],
        label_ids=(label_id,) if label_id else (),
        project_id=raw["project"],
        cycle_id=raw["cycle"],
        order=request.query_params.get("sort") or "manual",
        limit=limit,
        cursor=cursor,
        offset=offset,
        q=raw["q"],
    )


def _list_page(request: Request, team: Team, filters: dict[str, str | None]) -> dict[str, object]:
    """Shared pipeline of `list.html` and the `view=list` fragment."""
    db = _database(request)
    sort = request.query_params.get("sort") or "manual"
    cursor, offset, limit = _pagination(request)
    page = boards.list_issues(
        db, _list_filters(request, team, cursor=cursor, offset=offset, limit=limit)
    )
    counts = boards.board_counts(db, team.key)
    return readmodels.build_list_vm(
        page,
        team=readmodels.team_vm(team),
        filters=filters,
        sort=sort,
        limit=limit,
        offset=offset,
        counts=counts,
        today=_today(),
        can_write=_can_write(request),
    )


# --- pages ------------------------------------------------------------------------------

router = APIRouter()


@router.get("/")
def index(request: Request) -> Response:
    """`303` to the default team's board; never "the first one by `created_at`".

    With two or more teams and no default the answer is `200` and the team picker: choosing
    for the user would be guessing. A single team still redirects, and no team at all is a
    `404`.
    """
    teams = boards.list_teams(_database(request))
    wanted = request.app.state.default_team
    if wanted:
        match = next((team for team in teams if team.key == str(wanted).upper()), None)
        if match is not None:
            return RedirectResponse(f"/{match.key}/board", status_code=303)
    if len(teams) == 1:
        return RedirectResponse(f"/{teams[0].key}/board", status_code=303)
    if len(teams) >= 2:
        page = _page(request, title="Choose a team \u00b7 hifa", nav=None)
        reason = "no_default_team" if not wanted else "several_teams"
        return _render(request, "team_picker.html", {"page": page, "reason": reason})
    return _error_page(
        request,
        status_code=404,
        message="no default team: pick one",
        title="No team",
    )


@router.get("/{team}/board")
def board_page(request: Request, team: str) -> HTMLResponse:
    db = _database(request)
    entity = boards.require_team(db, team)
    filters = _filters(request)
    limit = _int_param(
        "limit", request.query_params.get("limit"), default=DEFAULT_LIMIT, rule="LIMIT_RANGE"
    )
    board = boards.list_board(
        db,
        team=entity.key,
        filters=_list_filters(request, entity, limit=limit),
        limit_per_group=limit,
    )
    vm = readmodels.build_board_vm(
        board,
        team=readmodels.team_vm(entity),
        filters=filters,
        counts=boards.board_counts(db, entity.key),
        today=_today(),
        can_write=_can_write(request),
    )
    page = _page(
        request,
        title=f"Board \u00b7 {entity.key}",
        nav="board",
        team=entity,
        refresh_url=_refresh_url(request, view="board", team=entity.key),
    )
    return _render(request, "board.html", {"page": page, "vm": vm})


@router.get("/{team}/list")
def list_page(request: Request, team: str) -> HTMLResponse:
    db = _database(request)
    entity = boards.require_team(db, team)
    vm = _list_page(request, entity, _filters(request))
    page = _page(
        request,
        title=f"List \u00b7 {entity.key}",
        nav="list",
        team=entity,
        refresh_url=_refresh_url(request, view="list", team=entity.key),
    )
    return _render(
        request,
        "list.html",
        {"page": page, "vm": vm, "form_error": None, "form_errors": None},
    )


@router.get("/{team}/issue/{ref}")
def issue_page(request: Request, team: str, ref: str) -> Response:
    """One URL per issue: a ULID or a lowercase identifier redirects to the canonical one.

    Render the detail carrying the team's states and the archive flags, so the edit form offers
    its `state` control and stays off an issue that cannot take a write.
    """
    db = _database(request)
    entity = boards.require_team(db, team)
    detail = boards.issue_detail(db, ref, include=boards.DETAIL_INCLUDE, include_trashed=True)
    canonical = detail.issue.identifier
    if ref != canonical or detail.team.key != entity.key:
        return RedirectResponse(f"/{detail.team.key}/issue/{canonical}", status_code=303)
    vm = _issue_detail_vm(request, detail)
    page = _page(
        request, title=f"{canonical} \u00b7 {detail.issue.title}", nav="issue", team=entity
    )
    return _render(request, "issue_detail.html", {"page": page, "vm": vm})


@router.get("/search")
def search_page(request: Request, q: str = "") -> HTMLResponse:
    """`SearchService` does not exist yet: report it instead of faking a search."""
    if not q.strip():
        return _error_page(
            request,
            status_code=400,
            message="type something to search",
            back_url="/",
            title="Search",
        )
    return _error_page(
        request, status_code=501, message=SEARCH_UNAVAILABLE, back_url="/", title="Search"
    )


# --- fragments -------------------------------------------------------------------------


@router.get("/_/issues", dependencies=[HTMX])
def issues_fragment(request: Request, view: str = "list", team: str = "") -> HTMLResponse:
    """Rows (`view=list`) or the whole board (`view=board`) of one team."""
    entity = _team_or_default(request, team)
    if view == "board":
        db = _database(request)
        filters = _filters(request)
        board = boards.list_board(
            db,
            team=entity.key,
            filters=_list_filters(request, entity),
        )
        vm = readmodels.build_board_vm(
            board,
            team=readmodels.team_vm(entity),
            filters=filters,
            counts=boards.board_counts(db, entity.key),
            today=_today(),
            can_write=_can_write(request),
        )
        return _render(
            request,
            "partials/board.html",
            {"page": _page(request, title="Board", nav="board", team=entity), "vm": vm},
        )
    vm = _list_page(request, entity, _filters(request))
    return _render(
        request,
        "partials/issue_rows.html",
        {
            "page": _page(request, title="List", nav="list", team=entity),
            "vm": vm,
            "form_error": None,
            "form_errors": None,
        },
    )


def _optional_id(field_name: str, value: object) -> str | None:
    """A neighbour id of the drag payload: `null` or a non-empty string, nothing else."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Validation(
            f"{field_name} must be a state id or null",
            rule="COMMAND_FIELD",
            field=field_name,
            value=value,
            code="COMMAND_FIELD",
        )
    return value.strip()


def _neighbour_positions(
    state_id: str,
    neighbours: list[WorkflowState],
    *,
    before: str | None,
    after: str | None,
) -> tuple[float | None, float | None]:
    """`(prev, next)` persisted positions around the dropped column.

    `neighbours` is the team's states without the moved one, already ordered by `position`.
    Both anchors travel together in a drop to the middle and are validated here — an anchor
    from another team, the state itself or an empty pair is a `422`, never a silent fallback.
    """
    if before is None and after is None:
        raise Validation(
            "before or after is required",
            rule="STATE_POSITION_ANCHOR",
            field="before",
            value=None,
            code="STATE_POSITION_ANCHOR",
        )
    positions = {state.id: state.position for state in neighbours}
    for field_name, anchor in (("before", before), ("after", after)):
        if anchor is None:
            continue
        if anchor == state_id:
            raise Validation(
                "a state cannot be anchored to itself",
                rule="STATE_POSITION_SELF",
                field=field_name,
                value=anchor,
                code="STATE_POSITION_SELF",
            )
        if anchor not in positions:
            raise Validation(
                f"state {field_name} is not a neighbour of this state: {anchor}",
                rule="STATE_NEIGHBOR_TEAM_MISMATCH",
                field=field_name,
                value=anchor,
                code="STATE_NEIGHBOR_TEAM_MISMATCH",
            )
    ordered = [state.id for state in neighbours]
    if before is not None and after is not None:
        return positions[before], positions[after]
    if before is not None:
        index = ordered.index(before)
        lower = neighbours[index - 1].position if index > 0 else None
        return lower, positions[before]
    assert after is not None  # narrowed by the check above
    index = ordered.index(after)
    upper = neighbours[index + 1].position if index + 1 < len(neighbours) else None
    return positions[after], upper


@router.post("/_/states/{state_id}/position", dependencies=[HTMX])
def state_position(
    request: Request, state_id: str, payload: dict[str, Any] | None = None
) -> Response:
    """Move one board column: the only write of the drag & drop.

    The body is `{"before": str|null, "after": str|null}` with the ULIDs of the neighbours the
    column landed between — never a raw position. Both anchors are sent by the client;
    they are **not** alternatives: `before` is the column on the left and `after` the one on
    the right, `null` at an edge. The route reads the team's states, asks
    `domain.ranking.between` for the slot and persists it with `update_state(position=…)`, so
    the arithmetic exists in one module and the client never sees a float.

    `204` with `HX-Trigger: board-reordered` on success; `403` when the server is read-only,
    `404` for an unknown state, `422` for no anchor at all (`{null, null}`), a neighbour of
    another team, the state itself as a neighbour, inverted neighbours or an exhausted gap.
    """
    if not _can_write(request):
        return PlainTextResponse("server is read-only", status_code=403)
    body = payload or {}
    before = _optional_id("before", body.get("before"))
    after = _optional_id("after", body.get("after"))
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as tx:
            state = tx.states.get(state_id)
        if state is None:
            raise NotFound(resource="state", key=state_id)
        neighbours = [
            summary.state
            for summary in list_states(uow, state.team_id)
            if summary.state.id != state_id
        ]
        position = between(
            *_neighbour_positions(state_id, neighbours, before=before, after=after),
            gap=UNIT_GAP,
        )
        update_state(
            uow,
            UpdateState(state=state_id, position=position),
            Actor(source="web", label="web"),
        )
    finally:
        uow.close()
    return Response(status_code=204, headers={"HX-Trigger": "board-reordered"})


@router.get("/_/issues/{identifier}/row", dependencies=[HTMX])
def issue_row_fragment(request: Request, identifier: str) -> HTMLResponse:
    detail = boards.issue_detail(_database(request), identifier)
    row = readmodels.build_row_vm(detail.issue, today=_today())
    return _render(
        request,
        "partials/issue_row.html",
        {"page": _page(request, title="Row", nav="list"), "row": row},
    )


@router.get("/_/issues/{identifier}/card", dependencies=[HTMX])
def issue_card_fragment(request: Request, identifier: str) -> HTMLResponse:
    detail = boards.issue_detail(_database(request), identifier)
    card = readmodels.build_card_vm(detail.issue, today=_today())
    return _render(
        request,
        "partials/issue_card.html",
        {"page": _page(request, title="Card", nav="board"), "card": card},
    )


@router.get("/_/issues/{identifier}/panel", dependencies=[HTMX])
def issue_panel_fragment(request: Request, identifier: str) -> Response:
    """Answer the panel of one issue, carrying the team's states and the archive flags.

    The panel is where the description is edited, so it holds the same edit form as the detail
    page; without the states its `state` control submitted nothing, and without the flags an
    archived issue kept a form every write of which is refused.
    """
    detail = boards.issue_detail(
        _database(request), identifier, include=boards.DETAIL_INCLUDE, include_trashed=True
    )
    vm = _issue_detail_vm(request, detail, surface="panel")
    response = _render(
        request,
        "partials/issue_panel.html",
        {"page": _page(request, title=vm["identifier"], nav="issue"), "vm": vm},
    )
    response.headers["HX-Push-Url"] = f"/{detail.team.key}/issue/{detail.issue.identifier}"
    return response


@router.patch("/_/issues/{identifier}", dependencies=[HTMX])
def issue_edit_fragment(
    request: Request, identifier: str, view: str = "list", body: bytes = Body(default=b"")
) -> Response:
    """One field write from the list, the board or the panel; the element comes back edited.

    `view` picks the element that answers — the `<tr>` of the list (`outerHTML`), the card of
    the board or the whole panel — and defaults to `list` when the caller does not say. The body
    is `application/x-www-form-urlencoded`: a key that is absent keeps the stored value, an empty
    string clears the field the command lets be cleared.

    A refusal keeps the element: `422` re-renders the same row, card or panel with the control
    marked and the message beside it, and `409` (a write to an archived issue) re-renders it with
    the message and no control. `404` is the only answer that cannot keep it — the element the
    client asked for does not exist, and `error.html`'s fragment says so in the toast area.
    """
    if not _can_write(request):
        return _fragment_error(request, status_code=403, message="server is read-only")
    fields = _parse_form(body)
    try:
        _update_from_form(request, identifier, fields)
    except Validation as exc:
        control, message = _refusal(exc, identifier=identifier, verb="edit")
        return _issue_edit_fragment(
            request,
            identifier,
            view=view,
            status_code=422,
            form_errors={control: message} if control else None,
            form_error=None if control else message,
        )
    except InvalidTransition as exc:
        return _issue_edit_fragment(
            request,
            identifier,
            view=view,
            status_code=409,
            form_error=refusal_sentence(exc, verb="edit", identifier=identifier),
        )
    response = _issue_edit_fragment(request, identifier, view=view, status_code=200)
    response.headers["HX-Trigger"] = "issue-updated"
    return response


@router.post("/{team}/issue")
def issue_create_page(request: Request, team: str, body: bytes = Body(default=b"")) -> Response:
    """Quick add without JS: a plain form post, no `HX-Request` guard.

    Success is a `303` to the detail; a validation error re-renders `list.html` with `422` and
    the messages next to the field. With htmx enabled the same form is intercepted by
    `hx-post="/_/issues"` and never reaches this route.
    """
    db = _database(request)
    entity = boards.require_team(db, team)
    if not _can_write(request):
        return _error_page(
            request,
            status_code=403,
            message="server in read-only mode",
            back_url=f"/{entity.key}/list",
            title="Read-only",
        )
    fields = _parse_form(body)
    if not (fields.get("title") or "").strip():
        return _list_form_error(request, entity, {"title": "title is required"})
    try:
        issue = _create_from_form(request, entity.key, fields)
    except Validation as exc:
        field = exc.field or "title"
        return _list_form_error(
            request, entity, {field: refusal_sentence(exc, verb="create", identifier=None)}
        )
    return RedirectResponse(f"/{entity.key}/issue/{issue.identifier}", status_code=303)


@router.post("/{team}/issue/{identifier}")
def issue_edit_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """The edit form without JS: a plain form post, no `HX-Request` guard.

    Success is a `303` to the canonical detail. A validation error re-renders
    `issue_detail.html` with `422`, the refused control marked `.is-invalid` and the message
    next to it — the form shows the **server's** current value, never the typed one, because
    the read model reads the row again. With htmx enabled the same form is intercepted by
    `hx-patch="/_/issues/{identifier}"` and never reaches this route.

    `404` (unknown issue, state or member) and `409` (a write to an archived issue) travel to
    the single handlers of `_install_error_handlers`: one HTTP mapping per error, in one place.
    """
    db = _database(request)
    entity = boards.require_team(db, team)
    if not _can_write(request):
        return _error_page(
            request,
            status_code=403,
            message="server in read-only mode",
            back_url=f"/{entity.key}/issue/{identifier}",
            title="Read-only",
        )
    try:
        issue = _update_from_form(request, identifier, _parse_form(body))
    except Validation as exc:
        return _issue_page_error(request, entity, identifier, exc)
    return RedirectResponse(f"/{entity.key}/issue/{issue.identifier}", status_code=303)


@router.post("/{team}/issue/{identifier}/archive")
def issue_archive_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Archive one issue from the detail page's own form: `303` to the list.

    The form carries its confirmation; without it nothing runs and the page comes back `422`
    with the block reopened, because the confirmation is this edge's precondition and no use
    case knows it exists. A refusal of the use case — open sub-issues, an issue already in the
    trash — re-renders the same page with the sentence the catalogue gives that rule.
    """
    return _layer_page(request, team, identifier, body, verb="archive")


@router.post("/{team}/issue/{identifier}/delete")
def issue_delete_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Send one issue to the trash from its own form: `303` to the list.

    Deleting an issue the trash already holds is a no-op that still redirects: the reader asked
    for a state the row is in, and the answer is the list they expected to land on.
    """
    return _layer_page(request, team, identifier, body, verb="delete")


@router.post("/{team}/issue/{identifier}/restore")
def issue_restore_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Bring one issue back out of both layers: `303` to its own detail.

    `restore` never asks: there is nothing to confirm about undoing, so the form carries no
    `confirm` field and none is required.
    """
    return _layer_page(request, team, identifier, body, verb="restore")


@router.post("/{team}/issue/{identifier}/comment")
def issue_comment_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Write one comment from the detail page's form: `303` to the section that holds it.

    The form is flat: it sends `body` and never a `parent`, so the no-JS path starts a thread at
    the root of the list. A `parent` sent by a client that built its own request is passed
    through untouched — the use case is what decides whether it names a comment of this issue.

    A refusal re-renders the page with the sentence the catalogue gives that rule and the
    composer back where it was: `422` for an empty body, a parent of another issue or an issue
    in the trash, `409` for an issue under the archive layer. `404` travels to the single
    handler, because a reference that resolves to nothing has no page to come back to.
    """
    db = _database(request)
    entity = boards.require_team(db, team)
    if not _can_write(request):
        return _error_page(
            request,
            status_code=403,
            message="server in read-only mode",
            back_url=f"/{entity.key}/issue/{identifier}",
            title="Read-only",
        )
    fields = _parse_form(body)
    typed = fields.get("body", "")
    try:
        comment = _write_comment(request, identifier, fields)
    except InvalidTransition as exc:
        return _issue_page_error(
            request,
            entity,
            identifier,
            exc,
            verb="comment",
            status_code=409,
            controls=readmodels.COMMENT_FORM_FIELDS,
            draft=typed,
        )
    except Validation as exc:
        return _issue_page_error(
            request,
            entity,
            identifier,
            exc,
            verb="comment",
            controls=readmodels.COMMENT_FORM_FIELDS,
            draft=typed,
        )
    detail = boards.issue_detail(_database(request), comment.issue_id)
    return RedirectResponse(
        f"/{detail.team.key}/issue/{detail.issue.identifier}#comments", status_code=303
    )


@router.post("/_/issues/{identifier}/archive", dependencies=[HTMX])
def issue_archive_fragment(
    request: Request, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """The panel's archive: `204`, bodyless, with the trigger the client listens for."""
    return _layer_fragment(request, identifier, body, verb="archive")


@router.post("/_/issues/{identifier}/delete", dependencies=[HTMX])
def issue_delete_fragment(
    request: Request, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """The panel's delete: `204`, bodyless, with its own trigger."""
    return _layer_fragment(request, identifier, body, verb="delete")


@router.post("/_/issues/{identifier}/restore", dependencies=[HTMX])
def issue_restore_fragment(
    request: Request, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """The panel's restore: `204`, bodyless, with its own trigger."""
    return _layer_fragment(request, identifier, body, verb="restore")


@router.post("/{team}/issue/{identifier}/move")
def issue_move_page(
    request: Request, team: str, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Move one issue into another column from its own form: `303` to the detail.

    The state the form names is the whole of the write; a state the team does not have is a
    `404`, because the reference the reader sent names nothing this page can move between.
    """
    db = _database(request)
    entity = boards.require_team(db, team)
    if not _can_write(request):
        return _error_page(
            request,
            status_code=403,
            message="server in read-only mode",
            back_url=f"/{entity.key}/issue/{identifier}",
            title="Read-only",
        )
    try:
        issue = _run_move(request, identifier, _parse_form(body))
    except InvalidTransition as exc:
        return _issue_page_error(request, entity, identifier, exc, verb="move", status_code=409)
    except Validation as exc:
        return _issue_page_error(request, entity, identifier, exc, verb="move")
    return RedirectResponse(f"/{entity.key}/issue/{issue.identifier}", status_code=303)


@router.get("/_/comments/{identifier}", dependencies=[HTMX])
def comments_fragment(request: Request, identifier: str) -> HTMLResponse:
    """Answer the whole list of one issue's comments: the `<ol>` the `outerHTML` swap replaces.

    The list alone, not the `<section>` that holds it: the section also carries the heading and
    the composer, and a swap that named it would empty the box the reader is typing in. The
    trash keeps its two surfaces — the page and the panel — so this read answers `404` for a
    trashed reference like the row and the card fragments do.
    """
    detail = boards.issue_detail(_database(request), identifier, include=boards.DETAIL_INCLUDE)
    vm = _issue_detail_vm(request, detail)
    return _render(
        request,
        "partials/comments.html",
        {"page": _page(request, title=detail.issue.identifier, nav="issue"), "vm": vm},
    )


@router.post("/_/comments/{identifier}", dependencies=[HTMX])
def comment_fragment(
    request: Request, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """Append one comment to the panel's list: `200` with the `<li>`, and the event that says so.

    `200` and not `204`, because the element is the answer: the client appends it to the list it
    names, and the box is emptied by a listener on `comment-created`, not by a status code. A
    refusal keeps the reader where they are — the panel comes back with the sentence and the
    composer — because a fragment's caller has no page to fall back on.
    """
    if not _can_write(request):
        return _fragment_error(request, status_code=403, message="server is read-only")
    fields = _parse_form(body)
    try:
        comment = _write_comment(request, identifier, fields)
    except InvalidTransition as exc:
        return _layer_refusal(
            request,
            identifier,
            "comment",
            exc,
            status_code=409,
            controls=readmodels.COMMENT_FORM_FIELDS,
            draft=fields.get("body", ""),
        )
    except Validation as exc:
        return _layer_refusal(
            request,
            identifier,
            "comment",
            exc,
            controls=readmodels.COMMENT_FORM_FIELDS,
            draft=fields.get("body", ""),
        )
    detail = boards.issue_detail(
        _database(request), comment.issue_id, include=boards.DETAIL_INCLUDE
    )
    vm = _issue_detail_vm(request, detail)
    response = _render(
        request,
        "partials/comment_item.html",
        {
            "page": _page(request, title=detail.issue.identifier, nav="issue"),
            "comment": _comment_item_vm(vm, comment.id),
        },
    )
    response.headers["HX-Trigger"] = "comment-created"
    return response


@router.post("/_/issues/{identifier}/move", dependencies=[HTMX])
def issue_move_fragment(
    request: Request, identifier: str, body: bytes = Body(default=b"")
) -> Response:
    """The panel footer's move: `204`, bodyless, with the trigger the client acts on.

    A refusal re-renders the panel and retargets it, because a bodyless answer with no target
    would fetch the refusal and throw it away.
    """
    if not _can_write(request):
        return _fragment_error(request, status_code=403, message="server is read-only")
    try:
        _run_move(request, identifier, _parse_form(body))
    except InvalidTransition as exc:
        return _layer_refusal(request, identifier, "move", exc, status_code=409)
    except Validation as exc:
        return _layer_refusal(request, identifier, "move", exc)
    return Response(status_code=204, headers={"HX-Trigger": "issue-moved"})


@router.post("/_/issues", dependencies=[HTMX])
def quick_add(request: Request, team: str, body: bytes = Body(default=b"")) -> Response:
    """Quick add: one `create_issue` call and the new `<tr>` back."""
    if not _can_write(request):
        return _form_error(request, team=team, status_code=403, message="server in read-only mode")
    fields = _parse_form(body)
    title = (fields.get("title") or "").strip()
    if not title:
        return _form_error(request, team=team, status_code=422, message=_title_required())
    try:
        issue = _create_from_form(request, team, fields)
    except Validation as exc:
        return _form_error(
            request,
            team=team,
            status_code=422,
            message=refusal_sentence(exc, verb="create", identifier=None),
        )
    identifier = issue.identifier
    detail = boards.issue_detail(_database(request), identifier)
    row = readmodels.build_row_vm(detail.issue, today=_today())

    response = _render(
        request,
        "partials/issue_row.html",
        {"page": _page(request, title="Row", nav="list"), "row": row},
        status_code=201,
    )
    response.headers["HX-Trigger"] = "issue-created"
    return response


@router.get("/_/revision", dependencies=[HTMX])
def revision(request: Request) -> JSONResponse:
    """The change signal of an open page: one number, JSON, never inserted anywhere.

    It is the `data_version` of the process's own read connection, so it moves when *another*
    connection commits — the CLI, a second tab, the request connections of this same process —
    and stays put when nothing writes. It is the number this request already observed (`_observed`),
    so the body cannot disagree with the `HX-Revision` header of the same answer. A probe that
    cannot answer is `{"revision": null}`: the client skips the tick instead of reloading on a
    number it cannot trust.
    """
    number = _observed(request)
    return JSONResponse(
        {"revision": number},
        status_code=200 if number is not None else 503,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/_/counts", dependencies=[HTMX])
def counts_fragment(request: Request, team: str) -> HTMLResponse:
    counts = boards.board_counts(_database(request), team)
    return HTMLResponse(
        f'<span id="counts">{counts["open"]} open \u00b7 {counts["urgent"]} urgent</span>'
    )


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    """Liveness plus a cheap database probe: `SELECT 1` and `PRAGMA quick_check`.

    A file that does not answer is `503 {"status": "degraded"}`: the process is up but it can
    serve nothing, and a 200 here would hide that from a supervisor.
    """
    if not boards.database_ok(_database(request)):
        return JSONResponse({"status": "degraded"}, status_code=503)
    return JSONResponse({"status": "ok"})


# --- helpers ---------------------------------------------------------------------------


def _parse_form(body: bytes) -> dict[str, str]:
    """`application/x-www-form-urlencoded` without pulling `python-multipart` in."""
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover - defensive
        return {}
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


PRIORITY_NAMES: dict[str, int] = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 0}


def _parse_priority(value: str | None) -> int:
    """`P1`/`1`/`urgent` -> `1`; empty/absent -> `0`; anything else is a `Validation`.

    A non-empty value that is not a priority is **never** folded into "no priority": the issue
    would be created with a field the user did not ask for and no one would notice. The error
    carries `field="priority"` so the edges can put it next to the input.
    """
    if value is None or not value.strip():
        return 0
    text = value.strip().lower().lstrip("p")
    if text.isdigit() and 0 <= int(text) <= 4:
        return int(text)
    if text in PRIORITY_NAMES:
        return PRIORITY_NAMES[text]
    raise Validation(
        "priority must be 0-4, P0-P4, urgent, high, medium, low or none",
        rule="PRIORITY_INVALID",
        field="priority",
        value=value,
    )


def _create_from_form(request: Request, team: str, fields: dict[str, str]) -> Issue:
    """One `create_issue` from form fields, shared by the htmx and the no-JS route."""
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        return create_issue(
            uow,
            CreateIssue(
                team=team,
                title=(fields.get("title") or "").strip(),
                description=fields.get("description") or None,
                priority=_parse_priority(fields.get("priority")),
                state=fields.get("state") or None,
                labels=(fields["label"],) if fields.get("label") else (),
                due_date=fields.get("due_date") or None,
            ),
            Actor(source="web", label="web"),
        )
    finally:
        uow.close()


# --- edit form -------------------------------------------------------------------------

#: Form control -> the `UpdateIssue` field it writes. The two names coincide except for the
#: assignee, which the form sends as a member reference and the command as `assignee_id`.
EDIT_COMMAND_FIELDS: dict[str, str] = {
    "title": "title",
    "description": "description",
    "priority": "priority",
    "state": "state",
    "assignee": "assignee_id",
    "due_date": "due_date",
    "estimate": "estimate",
}
#: Controls an empty string clears (the command writes `None`). `title` is not one of them —
#: an empty title is a `422`, never a cleared column — and `state` is not either: an issue
#: without a state does not exist, so an empty state means "unchanged".
EDIT_CLEARABLE = frozenset({"description", "assignee", "due_date", "estimate"})
#: The `field` a use case reports -> the control that carries it (only the ones that differ).
EDIT_FIELD_ALIASES: dict[str, str] = {"assignee_id": "assignee"}


def _parse_estimate(value: str) -> float | None:
    """`"3"` -> `3.0`; empty -> `None` (clear); anything else is a `Validation` on `estimate`.

    Only the syntax lives here: whether the number is part of the team's scale is
    `rules.validate_estimate`, called by the use case inside the transaction.
    """
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise Validation(
            "estimate must be a number",
            rule="ESTIMATE_INVALID",
            field="estimate",
            value=value,
        ) from exc


# --- refusals --------------------------------------------------------------------------

#: The verbs a closed parent names in a sentence of its own. A write that raises the same rule
#: under any other verb is a control this layer has no sentence for, and it falls back instead
#: of borrowing the word of a write it did not perform.
CLOSE_VERBS = frozenset({"move", "archive"})

#: The rules only a comment write raises. Their sentences are the domain's, word for word: the
#: catalogue fixes one wording for both edges and the rule is where it is written, so the web
#: quotes it instead of keeping the second copy that could drift from the first.
COMMENT_RULES = frozenset({"BODY_EMPTY", "COMMENT_PARENT_MISMATCH", "COMMENT_DEPTH_EXCEEDED"})


def refusal_sentence(refusal: HifaError | None, *, verb: str, identifier: str | None) -> str:
    """Compose the one line a refused write shows the reader.

    `refusal` is the error a route caught, or `None` for the one refusal no use case produces:
    the missing confirmation of the two forms that take an issue out of every listing. The
    sentence is selected by the rule the error carries and by nothing else — two of those rules
    arrive as `Validation` and one as `InvalidTransition`, so the class of the exception says
    nothing the rule does not.

    `verb` is the route's own word. One rule reads it (a parent closed with open sub-issues is
    worded per verb), and so does the confirmation; a write that raises that rule under another
    verb falls back rather than claim a verb it did not perform.

    `identifier` is the identifier of the issue the route resolved, never the reference it was
    asked with and never the value the error carries: those rules put three different things in
    that value — an identifier, an internal id and a workflow-state type — so a sentence built
    from it would name the wrong subject twice out of three times.

    A rule outside the catalogue is the last row of the table: the fallback prefix and the
    error's own message. Two sentences of this layer quote the domain rather than restate it,
    because the number they carry is not this edge's to format: the fallback, and
    `COMMENT_DEPTH_EXCEEDED`, whose bound the rule builds from `MAX_COMMENT_DEPTH`.
    """
    if refusal is None:
        return f"confirm the {verb} of {identifier}"
    rule = getattr(refusal, "rule", None)
    if rule == "ISSUE_ARCHIVED":
        return f"{identifier} is archived; restore it first"
    if rule == "ISSUE_TRASHED":
        return f"{identifier} is in the trash; restore it first"
    if rule in COMMENT_RULES:
        # The three comment rules quote the domain: one of them carries the bound the rule
        # builds from `MAX_COMMENT_DEPTH`, and the other two would only be a second place to
        # edit a wording the CLI prints as it is. The catalogue and the rule say the same
        # sentence, and the rule is the copy that is written.
        return refusal.message
    if rule == "CLOSE_WITH_OPEN_CHILDREN" and verb in CLOSE_VERBS:
        count = refusal.details.get("open_children", 0)
        return f"cannot {verb} {identifier}: it has {count} open sub-issues"
    return f"invalid value: {refusal.message}"


def _title_required() -> str:
    """The create form's own precondition, in the catalogue's fallback wording."""
    return refusal_sentence(
        Validation("title is required", rule="TITLE_REQUIRED", field="title"),
        verb="create",
        identifier=None,
    )


def _refusal(
    refusal: HifaError,
    *,
    identifier: str | None,
    verb: str,
    controls: tuple[str, ...] = readmodels.EDIT_FORM_FIELDS,
) -> tuple[str | None, str]:
    """`(control, message)` of a refused write: the control is `None` when it names none.

    The control is resolved here (the error names a command field, the form a control) and the
    message is not: it comes from the module's one builder, so the fallback prefix is written
    once and the page, the panel and the inline edit cannot drift apart.
    """
    reported = refusal.field or ""
    control = EDIT_FIELD_ALIASES.get(reported, reported)
    field = control if control in controls else None
    return field, refusal_sentence(refusal, verb=verb, identifier=identifier)


def _update_command(identifier: str, fields: dict[str, str]) -> UpdateIssue:
    """`UpdateIssue` of one form post: a control that is absent stays `UNSET` (unchanged)."""
    kwargs: dict[str, Any] = {"issue": identifier}
    for name in readmodels.EDIT_FORM_FIELDS:
        if name not in fields:
            continue
        raw = fields[name]
        if name == "priority":
            kwargs["priority"] = _parse_priority(raw)
        elif name == "estimate":
            kwargs["estimate"] = _parse_estimate(raw)
        elif name == "state":
            if raw.strip():
                kwargs["state"] = raw.strip()
        elif name in EDIT_CLEARABLE and not raw.strip():
            kwargs[EDIT_COMMAND_FIELDS[name]] = None
        else:
            kwargs[EDIT_COMMAND_FIELDS[name]] = raw
    return UpdateIssue(**kwargs)


def _update_from_form(request: Request, identifier: str, fields: dict[str, str]) -> Issue:
    """One `update_issue` from form fields, shared by the page and the fragment route.

    raises: `NotFound` (issue, state, member), `Validation`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        return update_issue(
            uow,
            _update_command(identifier, fields),
            Actor(source="web", label="web"),
        )
    finally:
        uow.close()


#: The three layer writes, each with the word its route uses, the use case that performs it,
#: the command it takes, the trigger a fragment answers and where its no-JS form lands.
_LAYER_WRITES: dict[str, tuple[Callable[..., Issue], Callable[[str], Any], str, str]] = {
    "archive": (archive_issue, ArchiveIssue, "issue-archived", "list"),
    "delete": (trash_issue, TrashIssue, "issue-deleted", "list"),
    "restore": (restore_issue, RestoreIssue, "issue-restored", "detail"),
}


def _run_layer_write(request: Request, identifier: str, verb: str) -> Issue:
    """One layer write through the use case of `verb`, on its own unit of work.

    raises: `NotFound(issue)`, `Validation`, `InvalidTransition`
    """
    use_case, command, _, _ = _LAYER_WRITES[verb]
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        return use_case(uow, command(identifier), Actor(source="web", label="web"))
    finally:
        uow.close()


def _run_move(request: Request, identifier: str, fields: Mapping[str, str]) -> Issue:
    """One `move_issue` from the body of a form: the state it names is the whole of the write.

    raises: `NotFound` (issue, state), `Validation`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    target = (fields.get("state_id") or "").strip()
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        return move_issue(
            uow,
            MoveIssue(issue=identifier, target_state=target or None),
            Actor(source="web", label="web"),
        )
    finally:
        uow.close()


def _write_comment(request: Request, identifier: str, fields: Mapping[str, str]) -> Comment:
    """One `comment_on_issue` from the fields of a form: `body` always, `parent` when sent.

    `parent` is not a control of this layer: both composers are flat, so the field only arrives
    from a client that built its own request. It is passed through untouched, because whether it
    names a comment of this issue is a rule of the use case and not of an edge.

    raises: `NotFound` (issue, parent comment), `Validation`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    parent = (fields.get("parent") or "").strip() or None
    uow = UnitOfWork(request.app.state.db_path, clock=SystemClock(), idgen=UlidGen())
    try:
        with uow.read() as repos:
            author = members.local_member(repos)
        return comment_on_issue(
            uow,
            CommentOnIssue(issue=identifier, body=fields.get("body", ""), parent_id=parent),
            Actor(member_id=author, source="web", label="web"),
        )
    finally:
        uow.close()


def _needs_confirmation(verb: str) -> bool:
    """Two of the three writes take an issue out of every listing, and ask before they do."""
    return verb != "restore"


def _layer_page(
    request: Request, team: str, identifier: str, body: bytes, *, verb: str
) -> Response:
    """A layer write from a page form: its confirmation, its refusal or its `303`."""
    db = _database(request)
    entity = boards.require_team(db, team)
    if not _can_write(request):
        return _error_page(
            request,
            status_code=403,
            message="server in read-only mode",
            back_url=f"/{entity.key}/issue/{identifier}",
            title="Read-only",
        )
    fields = _parse_form(body)
    if _needs_confirmation(verb) and fields.get("confirm") != "1":
        return _issue_page_error(request, entity, identifier, None, verb=verb, confirm_open=verb)
    try:
        issue = _run_layer_write(request, identifier, verb)
    except (Validation, InvalidTransition, NotFound) as exc:
        return _issue_page_error(request, entity, identifier, exc, verb=verb)
    where = _LAYER_WRITES[verb][3]
    target = (
        f"/{entity.key}/issue/{issue.identifier}" if where == "detail" else f"/{entity.key}/list"
    )
    return RedirectResponse(target, status_code=303)


def _layer_fragment(request: Request, identifier: str, body: bytes, *, verb: str) -> Response:
    """A layer write from a fragment: its refusal, or the bodyless `204` both clients expect."""
    if not _can_write(request):
        return _fragment_error(request, status_code=403, message="server is read-only")
    fields = _parse_form(body)
    if _needs_confirmation(verb) and fields.get("confirm") != "1":
        return _layer_refusal(request, identifier, verb, None)
    try:
        _run_layer_write(request, identifier, verb)
    except (Validation, InvalidTransition, NotFound) as exc:
        return _layer_refusal(request, identifier, verb, exc)
    return Response(status_code=204, headers={"HX-Trigger": _LAYER_WRITES[verb][2]})


def _layer_refusal(
    request: Request,
    identifier: str,
    verb: str,
    exc: HifaError | None,
    *,
    status_code: int = 422,
    controls: tuple[str, ...] = (),
    draft: str | None = None,
) -> HTMLResponse:
    """The panel, re-rendered with the refusal inside it and the block that refusal belongs to.

    The confirmation form swaps nothing on success, so a refusal has to name its own target or
    the marked block is fetched and thrown away.

    `controls` is what the panel can mark: a layer refusal names no field, but a comment one
    names `body`, and the box has to come back marked the way the page's copy comes back marked.
    `draft` is what the reader had typed when the write was refused: a refusal that empties the
    box is a paragraph they have to type again.
    """
    detail = boards.issue_detail(
        _database(request), identifier, include=boards.DETAIL_INCLUDE, include_trashed=True
    )
    control, message = (
        _refusal(exc, identifier=detail.issue.identifier, verb=verb, controls=controls)
        if exc is not None and controls
        else (None, refusal_sentence(exc, verb=verb, identifier=detail.issue.identifier))
    )
    response = _issue_edit_fragment(
        request,
        identifier,
        view="panel",
        status_code=status_code,
        form_errors={control: message} if control else None,
        form_error=None if control else message,
        confirm_open=verb,
        draft=draft,
    )
    response.headers["HX-Retarget"] = "#panel"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


def _comment_item_vm(vm: Mapping[str, object], comment_id: str) -> dict[str, object]:
    """The item of the comment just written, shaped by the builder that shapes the whole list.

    The item is read back through `build_issue_vm` instead of being composed here: one builder
    decides what a comment looks like, so the `<li>` the fragment appends and the list the page
    renders cannot drift in author, body, stamp or edit mark.
    """
    comments = cast(list[dict[str, object]], vm["comments"])
    return next(item for item in comments if item["id"] == comment_id)


def _issue_detail_vm(
    request: Request,
    detail: IssueDetail,
    *,
    form_errors: Mapping[str, str] | None = None,
    form_error: str | None = None,
    confirm_open: str | None = None,
    surface: str = "page",
    draft: str | None = None,
) -> dict[str, object]:
    """Build the model of the detail page, the panel and a refused write: the single builder.

    The team's states and the archive flags come from the detail itself, so every render of an
    issue — page, panel or refusal — is composed from the one read the route already made and no
    render opens a second one. A refused write arrives as `form_errors`/`form_error` and is
    carried into the model; both stay absent otherwise.

    `surface` is the one thing the model cannot read from the issue: the composer exists on both
    surfaces, and the two copies must not share an `id` nor both claim the fragment's target.
    The page's composer posts to its own route and the browser follows the redirect; the panel's
    posts the fragment and the client appends. Deciding it here keeps the template free of the
    conditional that would otherwise have to know which page it is in.
    """
    vm = readmodels.build_issue_vm(
        detail,
        today=_today(),
        now=datetime.now(UTC),
        can_write=_can_write(request),
        states=detail.states,
        is_archived=detail.archived_at is not None,
        is_trashed=detail.trashed,
        form_errors=form_errors,
        form_error=form_error,
        confirm_open=confirm_open,
    )
    panel = surface == "panel"
    vm["comment_composer_id"] = "panel-comment-body" if panel else "comment-body"
    vm["comment_draft"] = draft or ""
    vm["comment_composer_fragment"] = f"/_/comments/{detail.issue.identifier}" if panel else None
    return vm


def _issue_page_error(
    request: Request,
    entity: Team,
    identifier: str,
    exc: HifaError | None = None,
    *,
    verb: str = "edit",
    confirm_open: str | None = None,
    status_code: int = 422,
    controls: tuple[str, ...] = readmodels.EDIT_FORM_FIELDS,
    draft: str | None = None,
) -> HTMLResponse:
    """Re-render `issue_detail.html` with the refusal: the control marked and the stored value.

    `exc` is the refusal a use case raised, or `None` for the one the edge refuses itself: a
    confirmation form posted without its confirmation. Either way the page comes back with the
    message §6 composes for it and, for the confirmation, the block the reader has to reopen.
    """
    detail = boards.issue_detail(
        _database(request), identifier, include=boards.DETAIL_INCLUDE, include_trashed=True
    )
    if exc is None:
        control, message = (
            None,
            refusal_sentence(None, verb=verb, identifier=detail.issue.identifier),
        )
    else:
        control, message = _refusal(
            exc, identifier=detail.issue.identifier, verb=verb, controls=controls
        )
    vm = _issue_detail_vm(
        request,
        detail,
        form_errors={control: message} if control else None,
        form_error=None if control else message,
        confirm_open=confirm_open,
        draft=draft,
    )
    page = _page(
        request,
        title=f"{detail.issue.identifier} \u00b7 {detail.issue.title}",
        nav="issue",
        team=entity,
    )
    return _render(request, "issue_detail.html", {"page": page, "vm": vm}, status_code=status_code)


def _issue_edit_fragment(
    request: Request,
    identifier: str,
    *,
    view: str,
    status_code: int,
    form_errors: Mapping[str, str] | None = None,
    form_error: str | None = None,
    confirm_open: str | None = None,
    draft: str | None = None,
) -> HTMLResponse:
    """The element a `PATCH` answers — the row of the list, the card or the panel — refused or not.

    The element is re-read after the write, so what comes back is the stored row: the client
    swaps it in place and never has to merge the fields it did not send.
    """
    detail = boards.issue_detail(
        _database(request),
        identifier,
        include=boards.DETAIL_INCLUDE if view == "panel" else frozenset(),
        include_trashed=view == "panel",
    )
    message = form_error or next((text for text in (form_errors or {}).values() if text), None)
    control = next((name for name, text in (form_errors or {}).items() if text), None)
    if view == "panel":
        vm = _issue_detail_vm(
            request,
            detail,
            form_errors=form_errors,
            form_error=form_error,
            confirm_open=confirm_open,
            surface="panel",
            draft=draft,
        )
        response = _render(
            request,
            "partials/issue_panel.html",
            {"page": _page(request, title=vm["identifier"], nav="issue"), "vm": vm},
            status_code=status_code,
        )
        response.headers["HX-Push-Url"] = f"/{detail.team.key}/issue/{detail.issue.identifier}"
        return response
    if view == "board":
        card = readmodels.build_card_vm(
            detail.issue,
            today=_today(),
            can_write=_can_write(request),
            form_error=message,
            invalid_field=control,
        )
        return _render(
            request,
            "partials/issue_card.html",
            {"page": _page(request, title="Card", nav="board"), "card": card},
            status_code=status_code,
        )
    row = readmodels.build_row_vm(
        detail.issue,
        today=_today(),
        can_write=_can_write(request),
        form_error=message,
        invalid_field=control,
    )
    return _render(
        request,
        "partials/issue_row.html",
        {"page": _page(request, title="Row", nav="list"), "row": row},
        status_code=status_code,
    )


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FragmentRequiresHtmx)
    async def _fragment(_request: Request, _exc: FragmentRequiresHtmx) -> PlainTextResponse:
        return PlainTextResponse("fragment requires htmx", status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, _exc: RequestValidationError) -> HTMLResponse:
        return _error_page(
            request,
            status_code=422,
            message="invalid request",
            back_url="/",
            title="Invalid request",
        )

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> HTMLResponse:
        return _error_page(
            request,
            status_code=404,
            message=f"{exc.resource} '{exc.key}' does not exist",
            back_url="/",
            title="Not found",
        )

    @app.exception_handler(Validation)
    async def _validation(request: Request, exc: Validation) -> HTMLResponse:
        # The last row of §6's catalogue, from the one builder: a `Validation` that reaches this
        # handler is one no route handled, so no verb of this layer performed it and no issue was
        # resolved for it — the naming rules cannot be selected without an identifier, and every
        # writing route resolves the reference and handles its `Validation` before this runs.
        return _error_page(
            request,
            status_code=422,
            message=refusal_sentence(exc, verb="", identifier=None),
            back_url="/",
            title="Invalid request",
        )

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict) -> HTMLResponse:
        return _error_page(
            request, status_code=409, message=exc.message, back_url="/", title="Conflict"
        )

    @app.exception_handler(InvalidTransition)
    async def _transition(request: Request, exc: InvalidTransition) -> HTMLResponse:
        return _error_page(
            request, status_code=409, message=exc.message, back_url="/", title="Conflict"
        )

    @app.exception_handler(HifaError)
    async def _hifa(request: Request, exc: HifaError) -> HTMLResponse:
        return _error_page(
            request,
            status_code=500,
            message="internal error",
            back_url="/",
            title="Error",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, _exc: Exception) -> HTMLResponse:
        return _error_page(
            request,
            status_code=500,
            message="internal error",
            back_url="/",
            title="Error",
        )


def _install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next):
        path = request.url.path
        # One revision per request: the page marker and the header are the same number, which is
        # what lets the client adopt the header without reloading what it was just given.
        if not path.startswith("/static/"):
            _revision(request)
        response = await call_next(request)
        # `204` is the fourth answer that carries the number: the bodyless writes of §2.1.3 render
        # no element for it to land on, so nothing adopts it there, but the client reads the header
        # of the write it just made and the answer must not be the one that omits it (§2.1.3,
        # §5.7 step 5).
        if not path.startswith("/static/") and response.status_code in (200, 201, 204):
            number = _revision(request)
            if request.method not in READ_METHODS:
                # A write of this request moves the counter of a *different* connection, so the
                # number the page adopts has to be the one observed after the write.
                number = _revisions(request).read() or number
            response.headers.setdefault("HX-Revision", str(number))
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        path = request.url.path
        if path.startswith("/static/vendor/"):
            response.headers["Cache-Control"] = IMMUTABLE
        elif path in SHORT_CACHE_PATHS:
            response.headers["Cache-Control"] = SHORT_CACHE
        elif path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", SHORT_CACHE)
        elif not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _origin_authority(origin: str) -> str | None:
    """Return the authority an `Origin` names, or `None` when it names no authority.

    The answer is `host[:port]` with any userinfo stripped, so a genuine same-authority request
    that carries credentials in `Origin` is not refused, and no credential can travel into a
    caller or a log. The scheme is dropped on purpose: behind a cloudflared quick tunnel the
    browser reports `https://<tunnel>` while the socket this process accepts is plain `http`, and
    this edge declares no `X-Forwarded-Proto` trust that could reconcile the two. `null` — an
    opaque origin — and anything that does not parse to an authority answer `None`, and the
    caller refuses both, because no page of this application produces either.

    Comparison is case-insensitive and the port is compared **as addressed**: the name and the
    port are what a foreign page cannot forge without controlling them. A malformed port is not
    rejected here — nothing reads `.port`, so it comes back inside the authority and fails the
    comparison as text. Two legitimate-but-different spellings fail that comparison the same way,
    which is the fail-closed direction: the stored authority is `.strip()`ed while the origin is
    not, so a header padded with whitespace never matches, and a port compares as text, so `:080`
    is not `:80`.
    """
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    return parts.netloc.rpartition("@")[2].lower() or None


def _is_cross_site_request(request: Request) -> bool:
    """Return whether this request comes from another authority.

    The method is not consulted here: the middleware asks this question only for a request whose
    method is outside `READ_METHODS`, so the caller owns whether the method matters, and a
    cross-site `GET` answers `True` here and is still served.

    `Sec-Fetch-Site` is the browser's own statement about who initiated the request, so it is
    read first and decides alone: `same-origin` and `none` are this edge, `cross-site` and
    `same-site` are not, and a value this edge does not know is refused rather than trusted.
    One host and one port serve this edge, so no page of it can be `same-site` to another.

    Without fetch metadata, `Origin` is the fallback and only its authority is compared. A
    request carrying neither header is allowed, and that is the contract, not a gap: there is no
    cookie, session or token to ride, so the subject of the guard is the browser the reader is
    using — which sends these headers on its own — and a client that sets its own headers can set
    any. That absence is what keeps the `httpx` client of these tests, an agent and `curl` working.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.strip().lower() not in ALLOWED_FETCH_SITES
    origin = request.headers.get("origin")
    if origin is None:
        return False
    host = request.headers.get("host")
    if host is None:
        # No served authority to compare against: an `Origin` that cannot be shown to be this
        # edge is another authority as far as this guard can tell. Only HTTP/1.0 reaches here —
        # a client always sends `Host` over HTTP/1.1 — so no test can drive this branch.
        return True
    return _origin_authority(origin) != host.strip().lower()


def _install_write_guard(app: FastAPI) -> None:
    """Install the one guard that refuses a write from another authority.

    It is a middleware and not a per-route dependency, because a dependency is opt-in and its
    failure mode is the route that forgets the line — the bug this guard exists to close.
    Deciding on the method alone covers every route registered now and every route a later
    phase registers, and covers a method no route serves yet (`PUT`, `DELETE`);
    `tests/e2e/web/test_write_guard.py` enumerates `app.routes` so that claim is a test and not
    a promise.

    It **returns** its refusal and never raises it: `@app.exception_handler` lives in Starlette's
    `ExceptionMiddleware`, inside the user middleware stack, so an exception raised here would
    reach no handler and would answer `500`. The guard reads no path, no route table, no database
    and no body, so it answers the same way with the database file locked.

    `create_app` installs this **before** `_install_security_headers`, and Starlette applies the
    user middleware in reverse registration order, so `_headers` stays outermost: a refusal
    leaves with the CSP, `nosniff` and `Referrer-Policy` of every other answer of this edge, and
    its own `Cache-Control: no-store` stands, because `_headers` supplies one only to an answer
    that has none.
    """

    @app.middleware("http")
    async def _write_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in READ_METHODS or not _is_cross_site_request(request):
            return await call_next(request)
        return PlainTextResponse(
            CROSS_SITE_REFUSAL,
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Nothing to start; one thing to stop — the read connection the probe keeps open."""
    try:
        yield
    finally:
        app.state.revisions.close()


def create_app(
    db_path: Path,
    *,
    default_team: str | None = None,
    read_only: bool = False,
) -> FastAPI:
    """Build the dashboard app for one SQLite file.

    The viewer is resolved once at startup, so an async error handler never needs it from the
    database. The team list is **not** cached: every request reads it again, which is what lets
    a team created by the CLI appear without restarting the server.
    """
    app = FastAPI(
        title="hifa",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    database = Database(db_path)
    app.state.database = database
    # One read connection for the life of the process, opened by the first request that needs a
    # revision and closed by `_lifespan`. A connection per request would answer its own baseline
    # forever, which is the one thing this number cannot do.
    app.state.revisions = RevisionWatch(database)
    app.state.db_path = Path(db_path)
    app.state.default_team = default_team
    app.state.read_only = read_only
    app.state.templates = Jinja2Templates(env=build_environment())
    _, (viewer_name, viewer_initials) = boards.startup_state(database)
    app.state.viewer = (
        None
        if not viewer_name
        else {
            "display_name": viewer_name,
            "initials": viewer_initials or readmodels.initials(viewer_name) or "",
        }
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Installed before `_install_security_headers` because Starlette applies the user middleware
    # in reverse registration order: the guard must sit *inside* `_headers`, not wrap it, so a
    # refusal still leaves with the security headers of every other answer.
    _install_write_guard(app)
    _install_security_headers(app)
    _install_error_handlers(app)
    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """App factory for `hifa serve --reload`: uvicorn needs an import string to watch files.

    `serve` hands the resolved settings over through `HIFA_SERVE_*`; a direct uvicorn call
    without them is a clear error instead of a silent wrong database.
    """
    db = os.environ.get("HIFA_SERVE_DB")
    if not db:  # pragma: no cover - defensive: only `hifa serve --reload` sets it
        raise RuntimeError("HIFA_SERVE_DB is not set; start the dashboard with `hifa serve`")
    return create_app(
        Path(db),
        default_team=os.environ.get("HIFA_SERVE_TEAM") or None,
        read_only=os.environ.get("HIFA_SERVE_READ_ONLY") == "1",
    )


__all__ = [
    "CSP",
    "FragmentRequiresHtmx",
    "REVISION_POLL",
    "build_environment",
    "create_app",
    "create_app_from_env",
    "router",
]
