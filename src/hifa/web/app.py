"""Dashboard application factory.

The web layer is an **edge**: it parses the request, calls the `app/` read/write functions
and prints the read models from `hifa.web.readmodels`. No SQL, no business rules, no
`async def` on anything that touches SQLite (the driver is sync and would block the loop).

`fastapi`, `jinja2` and `uvicorn` live in the `[web]` extra: nothing in the core imports
this module.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

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
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import ListFilters
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app import boards
from hifa.app.commands import Actor, CreateIssue, UpdateState
from hifa.app.issues import create_issue
from hifa.app.teams import list_states, update_state
from hifa.domain.entities import Issue, Team, WorkflowState
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
            "form_oob": False,
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
            "form_oob": False,
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
    page = _page(request, title=f"Board \u00b7 {entity.key}", nav="board", team=entity)
    return _render(request, "board.html", {"page": page, "vm": vm})


@router.get("/{team}/list")
def list_page(request: Request, team: str) -> HTMLResponse:
    db = _database(request)
    entity = boards.require_team(db, team)
    vm = _list_page(request, entity, _filters(request))
    page = _page(request, title=f"List \u00b7 {entity.key}", nav="list", team=entity)
    return _render(
        request,
        "list.html",
        {"page": page, "vm": vm, "form_oob": False, "form_error": None, "form_errors": None},
    )


@router.get("/{team}/issue/{ref}")
def issue_page(request: Request, team: str, ref: str) -> Response:
    """One URL per issue: a ULID or a lowercase identifier redirects to the canonical one."""
    db = _database(request)
    entity = boards.require_team(db, team)
    detail = boards.issue_detail(db, ref, include=boards.DETAIL_INCLUDE)
    canonical = detail.issue.identifier
    if ref != canonical or detail.team.key != entity.key:
        return RedirectResponse(f"/{detail.team.key}/issue/{canonical}", status_code=303)
    vm = readmodels.build_issue_vm(
        detail, today=_today(), now=datetime.now(UTC), can_write=_can_write(request)
    )
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
            "form_oob": True,
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
            raise NotFound(resource="workflow_state", key=state_id)
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
    detail = boards.issue_detail(_database(request), identifier, include=boards.DETAIL_INCLUDE)
    vm = readmodels.build_issue_vm(
        detail, today=_today(), now=datetime.now(UTC), can_write=_can_write(request)
    )
    response = _render(
        request,
        "partials/issue_panel.html",
        {"page": _page(request, title=vm["identifier"], nav="issue"), "vm": vm},
    )
    response.headers["HX-Push-Url"] = f"/{detail.team.key}/issue/{detail.issue.identifier}"
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
        return _list_form_error(request, entity, {field: f"invalid value: {exc.message}"})
    return RedirectResponse(f"/{entity.key}/issue/{issue.identifier}", status_code=303)


@router.post("/_/issues", dependencies=[HTMX])
def quick_add(request: Request, team: str, body: bytes = Body(default=b"")) -> Response:
    """Quick add: one `create_issue` call and the new `<tr>` back."""
    if not _can_write(request):
        return _form_error(request, team=team, status_code=403, message="server in read-only mode")
    fields = _parse_form(body)
    title = (fields.get("title") or "").strip()
    if not title:
        return _form_error(
            request, team=team, status_code=422, message="invalid value: title is required"
        )
    try:
        issue = _create_from_form(request, team, fields)
    except Validation as exc:
        return _form_error(
            request,
            team=team,
            status_code=422,
            message=f"invalid value: {exc.message}",
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
        return _error_page(
            request,
            status_code=422,
            message=f"invalid value: {exc.message}",
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
        response = await call_next(request)
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
    )
    database = Database(db_path)
    app.state.database = database
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
    "build_environment",
    "create_app",
    "create_app_from_env",
    "router",
]
