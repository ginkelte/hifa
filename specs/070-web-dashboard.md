---
id: SPEC-070
title: Web dashboard — Jinja views, htmx fragments, assets and command palette
component: entry
status: review
version: 3
spec_revision: 17
depends_on: [SPEC-000, SPEC-010, SPEC-040, SPEC-060]
implements:
  - src/hifa/web/**
  - tests/e2e/web/**
  - pyproject.toml
acceptance: specs/acceptance/070-web-dashboard.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-13
---

# SPEC-070 — Web dashboard

## 1. Responsibility and boundaries

**Does:** defines the local `hifa` dashboard: full HTML routes, htmx fragments, template
inventory, read models (plain dicts) each template receives, vendored assets, dark visual design,
`Cmd+K` command palette, keyboard shortcuts, optimistic inline editing and drag & drop that reports
**neighbors** (`before`/`after`) and leaves the position calculation in `app/` (SPEC-040). The web
layer does not compute fractional positions.

**Does NOT do (explicit):**
- **It does not consume the REST API from the browser** (SPEC-060 §1). Every interaction is an HTML
  request to the server that returns HTML; there is no `fetch('/api/v1/...')` in any file under
  `web/static/js`.
- It contains no business logic and no SQL: it receives read models already built by `app/` and
  prints them.
- It computes nothing in Jinja: `|length`, arithmetic, date comparisons, lookups and method calls
  are forbidden. If a template needs a derived value, the read model brings it.
- It has no Node build step, bundler, TypeScript, Sass or PostCSS. CSS and JS are served as is.
- It loads nothing from the network: zero CDN, zero remote fonts, zero telemetry (REQ-N005).
- It does not implement auth, multi-user, or a light theme. Dark, fixed.
- It does not render unsanitized markdown: allowlist is mandatory (REQ-S003).

## 2. Public contract

### 2.1 Route map

Two families, deliberately separated: **pages** (full HTML, navigable, bookmarkable) and
**fragments** (partial HTML for htmx, `/_/` prefix, never linked from an `<a href>` without a
fallback).

#### 2.1.1 Pages (full page, `GET`)

| Route | Query | Template | Returns |
|---|---|---|---|
| `GET /` | — | `team_picker.html` (only in the multi-team case) | `303` to `/{team}/board` with the team from `[cli].default_team` (SPEC-100 §2.3) **when that team exists**; otherwise `303` to the only non-archived team if there is exactly one; with **2+ teams and a default that names none of them** ⇒ `200` `team_picker.html` (§2.3.8, R24); no teams ⇒ `404`. Never a cookie, a session, an alphabetical rule nor "the first one by `created_at`" (explicit context, SPEC-050 R4, D-030) |
| `GET /{team}/board` | `state`, `assignee`, `label`, `cycle`, `project`, `q` | `board.html` | board by `workflow_state` |
| `GET /{team}/list` | same + `sort`, `cursor` | `list.html` | dense paginated table (keyset) |
| `GET /{team}/issue/{identifier}` | — | `issue_detail.html` | full detail + comments + history |
| `GET /search` | `q` (req), `team` (optional) | `search.html` | workspace-wide results |
| `GET /{team}/issue/{ulid}` | — | — | `303` to the canonical URL with `identifier` |

`{team}` is the `team.key` (case-insensitive in the route, normalized to uppercase in links).
A nonexistent `{identifier}` ⇒ `404` with `error.html` (same layout, message + link to the list).
A nonexistent `{team}` ⇒ `404`. An empty `q` in `/search` ⇒ `400`.

**`q` on the board and on the list is a title filter, not search (D-025).** The box above the board
and above the list sends `?q=`; the server translates it to a `LIKE '%term%'` over the issue **title**
(SPEC-030 §2.6, `ListFilters.q`), with the term's `%`, `_` and `\` escaped and case-insensitive. It is
not the search grammar of SPEC-090: no `field:value`, no negation, no ranking, no description or
comment matching. Workspace search is `GET /search` (and `/_/palette`), which is **a different route
with a different contract** (SPEC-090 §2). An empty or blank `q` means "no filter" and never a `400`
here — the `400` applies only to `/search`, where `q` is required.

#### 2.1.2 Forms (full page, they work without JS)

| Route | Body (form-urlencoded) | Without JS | With htmx |
|---|---|---|---|
| `POST /{team}/issue` | `title`, `description?`, `state?`, `priority?`, `assignee?`, `due_date?` | `303` to `/{team}/issue/{identifier}` | same endpoint intercepted by `hx-post`, see 2.1.3 |
| `POST /{team}/issue/{identifier}` | same fields; absent = no change | `303` to the detail | same |
| `POST /{team}/issue/{identifier}/move` | `state`, `before?`, `after?` | `303` to the detail | same |
| `POST /{team}/issue/{identifier}/comment` | `body`, `parent?` | `303` to the detail (`#comments`) | same |
| `POST /{team}/issue/{identifier}/archive` | `undo?` | `303` to the list | same |

Failed validation in a form without JS ⇒ `422` + template with the errors next to the field
(`form_errors` in the read model). Never a 500 nor a silent redirect.

#### 2.1.3 Fragments (htmx, `GET`/`PATCH`/`POST`/`DELETE`)

| Method | Route | Returns | `hx-target` | `hx-swap` | Trigger |
|---|---|---|---|---|---|
| GET | `/_/issues?view=list&…filters` | `<tbody>` with the row `<tr>`s | `#issues-tbody` | `innerHTML` | filter `change`, `keyup changed delay:250ms` in the search box |
| GET | `/_/issues?view=board&…` | `<div class="board">` with columns | `#board` | `innerHTML` | same |
| GET | `/_/issues/{identifier}/row?view=list` | one `<tr id="issue-{identifier}">` | `#issue-{identifier}` | `outerHTML` | manual refresh |
| GET | `/_/issues/{identifier}/card` | one `<article class="card">` | `#card-{identifier}` | `outerHTML` | manual refresh |
| GET | `/_/issues/{identifier}/panel` | `<aside id="panel">` with the detail | `#panel` | `innerHTML` | row click (`hx-get` + `hx-push-url`) |
| GET | `/_/issues/{identifier}/field/{field}` | edit control for one field | the field itself | `outerHTML` | click on the field |
| PATCH | `/_/issues/{identifier}?view=list\|board` | the updated row/card | the element itself | `outerHTML` | inline-edit blur/Enter, select change |
| POST | `/_/issues` | new `<tr>` | `#issues-tbody` | `afterbegin` | quick-add submit |
| POST | `/_/issues/{identifier}/move` | `204` with no body | — | — | sortablejs drop (2.6.4) |
| POST | `/_/states/{state_id}/position` | `204` with no body | — | — | column drop (2.6.5) |
| POST | `/_/issues/{identifier}/labels` | `<span class="chip">` chip | `#labels-{identifier}` | `beforeend` | picking a label |
| DELETE | `/_/issues/{identifier}/labels/{label_id}` | `200` empty body (htmx removes the node) | the chip | `outerHTML swap:120ms` | click on the chip's `×` |
| GET | `/_/comments/{identifier}` | `<ol>` of comments | `#comments` | `innerHTML` | refresh |
| POST | `/_/comments/{identifier}` | one `<li>` | `#comments` | `beforeend` | composer submit |
| GET | `/_/palette?q=` | `<ul role="listbox">` of results | `#palette-results` | `innerHTML` | `input` with `delay:120ms` |
| GET | `/_/counts?team={team}` | `<span>` of counters | `#counts` | `outerHTML` | `every 30s` |
| GET | `/_/board/{team}` | full board (recovery) | `#board` | `innerHTML` | after a drop error |
| GET | `/_/revision` | `{"revision": <n>}` — **JSON, the only non-HTML body under `/_/`** (§2.1.3.1) | — | `none` (the body is read, never inserted) | `every 5s` while the tab is visible (§2.6.8) |

Headers the server must read or emit:

| Header | Direction | Use |
|---|---|---|
| `HX-Request: true` | input | if missing on a `/_/*`, respond `400` (`fragment requires htmx`) |
| `HX-Trigger` | output | events of §2.6.4/§2.6.5 (`issue-updated`, `issue-moved`, `issue-created`, `board-reordered`) |
| `HX-Retarget` | output | on `422` of an inline edit: re-targets the `<form>` that failed |
| `HX-Push-Url` | output | when opening the panel: pushes `/{team}/issue/{identifier}` |
| `HX-Reswap` | output | `none` when the fragment only updates counters |
| `HX-Revision` | output | the revision the response was rendered at (§2.6.8): on every `200`/`201` of this layer, pages and fragments alike, so the page adopts it and never reloads what it has just been given |

`422` rule in fragments: htmx **does not** swap 4xx responses by default. A global listener in
`app.js` enables the swap for `422` and adds `.is-invalid` to the field:

```js
document.body.addEventListener('htmx:beforeSwap', (e) => {
  if (e.detail.xhr.status === 422) {
    e.detail.shouldSwap = true;
    e.detail.isError = false;
  }
});
```

#### 2.1.3.1 The one JSON body under `/_/`: `GET /_/revision` (D-049)

The `/_/` family answers **HTML fragments** (R1, §2.1.3) and the guard of R5 keeps it out of a
browser navigation. `GET /_/revision` keeps the guard and the prefix, and answers **JSON**. The
exception is declared here, in one place, instead of being left for a reader to notice:

| Aspect | Contract |
|---|---|
| Body | `{"revision": <int>}` — exactly one key, no envelope, no timestamp, no `ok` flag. The single exception is the `503`: the probe could not read a number, the key stays and its value is `null`, because a key that disappears turns a server fault into a parse error on the client |
| `Content-Type` | `application/json` |
| `Cache-Control` | `no-store`, on **every** answer of the route, the `400` and the `503` included: a cached revision would freeze the page for as long as the cache holds it |
| `HX-Request: true` | still required, like every other `/_/*` (R5, §6). The guard is the family's, not the payload's: it is what keeps the probe out of a bookmark, an `<iframe>`, a crawler and a browser navigation |
| Mode | a **read**: it opens (reuses) a read-only connection and executes one `PRAGMA`. `--read-only` changes nothing and does not disable it (§2.6.8) |
| Number | `PRAGMA data_version` of the process's long-lived read connection, opaque; composition and limits in D-049 |

**Why it is not a fragment.** A fragment is markup that htmx inserts somewhere; this answer is
**compared**, never inserted — the route has no `hx-target` and its `hx-swap` is `none`. A `<span>`
carrying the number would have to be parsed out of the DOM to be compared, and it would invite the
next reader to swap it into the page, which is the timer-driven re-render §2.6.8 exists to forbid.

**Why the exception and not a route outside `/_/`.** There is no precedent for JSON under `/_/`, and
this is therefore the **first** exception: `/_/counts` answers a `<span>`, the rest of the family
answers HTML, and the only `JSONResponse` of the module is `/healthz`, which is deliberately
**outside** the prefix (§2.1.6). Moving the probe next to `/healthz` (`/revision`) would put a
machine-readable answer at the root, where pages live, as a one-segment path a future `/{team}`
route can collide with and a person will try in a browser — and it would need its own non-navigable
guard, which `/_/` already has. The prefix says "this URL is not a page and never will be", which
stays true; what changes is only the media type, so the exception costs one paragraph and one
`Content-Type` instead of a second convention for machine routes.

#### 2.1.4 Calls to the application port (SPEC-040, D-012)

The web layer is an edge: **it does not define application functions**. Each route calls a function
from SPEC-040 §2.2, with that spec's signature and without its own adapters. There is no
`board_vm` as a port function: it is an edge read model (§2.3). There is no `fractional` anywhere
(§2.6.4).

| Route | Actual call | Notes |
|---|---|---|
| `GET /{team}/board` | `app.boards.list_board(db, *, team=key, filters=ListFilters(...), group_by="state", limit_per_group=N)` | `N` in `1..200`, default 50 (D-011); `team` by key or ULID; filters — `q` included — travel in **one** `ListFilters`, never as loose kwargs (D-027) |
| `GET /{team}/list`, `GET /_/issues?view=list` | `app.boards.list_issues(db, ListFilters(team_id=..., order=..., limit=..., cursor=..., offset=...))` | returns `IssuePage` (`items`, `next_cursor`, `has_more`) — owner SPEC-030 §2.6 |
| `GET /{team}/issue/{identifier}` | `app.boards.issue_detail(db, ref, include=app.boards.DETAIL_INCLUDE)` | `NotFound(issue)` ⇒ `404`; the constant is declared in SPEC-040 §2.2 (D-027) |
| `…/panel` | `app.boards.issue_detail(db, ref, include=app.boards.DETAIL_INCLUDE)` | the panel renders the comments, so it pays for them: one extra read in an explicit user action, never in a board render (D-029) |
| `/_/issues/{id}/row` | `app.boards.issue_detail(db, ref)` | empty `include`: only the base `IssueDetail` — the constant is deliberately **not** the default (D-027) |
| `GET /search`, `GET /_/palette` | `SearchService(reads, clock).parse(q)` + `.search(query)` (SPEC-090 §2) | the web layer does **not** declare its own search flags or envelope (D-016) |
| `GET /` and the sidebar of **every** page | `app.teams.list_teams(db)` | called per request, never once in `create_app` nor memoized on `app.state` (R23, D-030): the CLI is a second writer to the same file. The team counts come from `v_team_progress`, not from a query written here |
| `POST …/move`, `POST /_/issues/{id}/move` | `app.issues.move_issue(uow, MoveIssue(issue=ref, target_state=..., position=..., before_id=..., after_id=...), Actor(..., source="web"))` | §2.6.4 |
| `POST …/move` in the same state | `app.issues.reorder_issue(uow, ReorderIssue(issue=ref, before=..., after=..., scope="board"), Actor(...))` | the rebalance is decided by the server (R10) |
| `PATCH /_/issues/{identifier}` | `app.issues.update_issue(uow, UpdateIssue(issue=ref, <field>=...), Actor(...))` | `UNSET` = "no change" |
| `POST /{team}/issue`, `POST /_/issues` | `app.issues.create_issue(uow, CreateIssue(...), Actor(...))` | |
| `POST …/archive` | `app.issues.archive_issue(uow, ArchiveIssue(issue=ref), Actor(...))` | soft delete (D-015: there is no physical delete) |
| `POST …/labels`, `DELETE …/labels/{id}` | `app.labels.add_label` / `app.labels.remove_label` | one call per label |
| `POST …/comment`, `POST /_/comments/{id}` | `app.issues.comment_on_issue(uow, CommentOnIssue(...), Actor(...))` | |
| `GET /_/revision` | **nothing**: no use case, no read model, no template | the one route of this layer that answers a number it reads itself (§2.1.3.1, D-049). A `data_version` read is a property of the connection, not of the domain: promoting it to `app/` would move an edge detail into the core (P4) and give the CLI and the API a function neither of them can use |

A signature change in SPEC-040 propagates here with a `spec_revision` bump; a new function is never
added in `web/` just to make the route "fit".

#### 2.1.5 Client / server boundary

| Piece | Where it lives | Is it a port? |
|---|---|---|
| `build_board_vm`, `build_list_vm`, `build_issue_vm`, `build_row_vm`, `build_card_vm` | `src/hifa/web/readmodels.py` (pure Python, no IO) | no: edge read model |
| `render_markdown` | `src/hifa/web/markdown.py` (§2.3.6) | no: edge helper |
| `Alpine.data('issueRow', …)`, `htmx:beforeSwap`, shortcuts, `Sortable.init` | `src/hifa/web/static/js/app.js` | no: **client JS**, no build step |
| `ranking.between`, `ranking.rebalance` | `src/hifa/domain/ranking.py` via `app` (SPEC-040) | yes, and **only** there |

Forbidden in client JS: arithmetic on `board_order`/`sort_order`, any `sort_order`/`rebalance` field
in a request body, and any position calculation. Client JS sends **identifiers**
(`state_id`, `before`, `after`) and nothing else.

#### 2.1.6 Health probe (`/healthz`, D-028)

`GET /healthz` is registered by the web router (no `/_/` prefix, no `HX-Request` guard, no template)
and answers **`200`** with `{"status": "ok"}`. It is a **liveness** probe: it verifies only that the
process is up and that the web router is mounted — no database access, no `PRAGMA`, no read model, no
template. It therefore keeps working while the database is locked, which is exactly what a supervisor
or a container probe needs.

It is **not** a duplicate of `/health` (SPEC-060 §2.5): that one is the **readiness** check — it opens
its own verification connection, runs `PRAGMA integrity_check`, compares `user_version` and answers
`200 {"status":"ok"}` or `503 {"status":"degraded"}`. Two different questions, two different paths,
neither shadowing the other: `/healthz` belongs to the web layer, `/health` to the API. The path is
public in SPEC-001 §2 and in the `hifa serve` contract, so it is kept (D-028).


### 2.2 Templates

```
src/hifa/web/templates/
├── base.html              # <html>, <head>, sidebar, header, #panel, #palette, #toasts
├── board.html             # extends base; columns + cards
├── list.html              # extends base; filters + table + pagination
├── issue_detail.html      # extends base; detail of one issue
├── search.html            # extends base; search results
├── team_picker.html       # extends base; team selection when GET / cannot resolve one
├── error.html             # extends base; 404/400/500 with a message
└── partials/
    ├── sidebar.html       # teams + views navigation
    ├── filter_bar.html    # filter chips and selects
    ├── issue_rows.html    # <tbody> for list (fragment)
    ├── issue_row.html     # <tr> of one issue
    ├── board.html         # full <div class="board"> (fragment)
    ├── board_column.html  # one state column
    ├── issue_card.html    # <article class="card"> of one issue
    ├── issue_panel.html   # <aside id="panel"> of the detail
    ├── field_control.html # edit control for one field
    ├── comments.html      # <ol> of comments
    ├── comment_item.html  # one comment <li>
    ├── palette.html       # <ul> of palette results
    ├── toasts.html        # toast container
    └── empty_state.html   # empty state (column, table, search)
```

`base.html` defines exactly these blocks: `title`, `head`, `content`, `panel`, `scripts`.
A child template **cannot** inject `<script>` (forbidden by CSP, R14).

### 2.3 Read models

Templates receive plain `dict`s. Jinja can only: access keys with `.` or `[]`, use `{% if %}`,
`{% for %}`, and the `join` filter. Everything else (counters, formatted dates, CSS classes, URLs)
already comes resolved.

#### 2.3.1 Common context (`page`, on every page)

```python
page = {
    "title": "Board · ENG",  # str
    "team": {
        "key": "ENG",
        "name": "Engineering",
        "icon": "🧪",
    },  # or None in /search and in the picker
    "teams": [  # sorted by name; read from the database on every request (R23, D-030)
        {"key": "ENG", "name": "Engineering", "icon": "🧪", "issues_count": 12, "active": True},
    ],
    "nav": "board",  # "board" | "list" | "search" | None (the picker has no active view)
    "viewer": {"display_name": "ginkelte", "initials": "VI"},  # or None
    "version": "0.1.0",
    "can_write": True,  # False if read_only
    "flash": None,  # str | None, one-shot message
    "revision": 7,  # int: the number this page was rendered at (§2.6.8, D-049)
    "revision_poll": "5s",  # str: the htmx `every` argument, from REVISION_POLL (§2.6.8)
    "refresh_url": "/_/issues?view=board&state=todo",  # str | None: the fragment the poll reloads
}
```

Three keys of `page` exist for the revision poll (§2.6.8) and carry no logic of their own — the
template prints them and computes nothing (R2):

- `revision` — the number of §2.1.3.1 as this request observed it; `<body>` renders it as
  `data-revision`, which is what the poller compares against. It is **opaque**: a template never
  orders it, subtracts it or shows it to the user.
- `revision_poll` — the htmx duration literal of the one constant `REVISION_POLL` of `web/app.py`.
  It travels as a string because `every` takes the literal (`5s`), and putting it here keeps the
  interval in one place: the trigger of §2.1.3 and the table of §2.6.8 read it, nothing repeats it.
- `refresh_url` — the fragment URL that re-renders the collection the page is showing, with the
  filters of the current request baked in: `/_/issues?view=board&…` on a board page,
  `/_/issues?view=list&…` on a list page. It is `None` on `/search`, on the detail page and on the
  picker, where there is no collection to reload and therefore no poll.

Two keys of `teams[]` are **additive and defaulted (D-026)**, and they exist so the sidebar needs
no logic (R2):

- `issues_count` — `open_count + closed_count` of `v_team_progress` (SPEC-010 §3.3) for that team:
  every issue with `archived_at IS NULL AND trashed = 0`. Default `0`, which is also the real value
  for a team without issues (it has no row in the view). It is never computed in Jinja and never
  read with a query issued by this layer.
- `active` — `True` only for the team of the current `/{team}/…` route; `False` on `/search` and on
  the picker, where no team is active yet. The sidebar marks it and **never hides it**: with one
  team the block renders exactly as it does with ten (R23).

#### 2.3.2 `board_vm` (board.html)

```python
board_vm = {
    "team": {"key": "ENG", "name": "Engineering", "icon": "🧪", "color": "#5E6AD2"},
    "columns": [
        {
            "state_id": "01JCV3...",
            "position": 2.0,
            "name": "Todo",
            "type": "unstarted",
            "color": "#E2E2E2",
            "count": 4,
            "issues": [card_vm, ...],
            "truncated": False,
            "more_url": None,  # True + url if limit_per_group cut the list
        },
    ],
    "filters": {"assignee": None, "label": None, "cycle": None, "project": None, "q": None},
    "chips": [{"kind": "label", "value": "bug", "label": "bug", "remove_url": "/ENG/board?label="}],
    "counts": {"open": 12, "urgent": 2, "total": 18},
    "can_write": True,
}
```

`columns` comes sorted by `workflow_state.position` ASC, without archived issues, and each column
carries its `state_id` and its `position` so the template can emit `data-state-id` /
`data-position` (§2.6.5). `issues` comes sorted by `board_order` ASC. A column with no issues
includes the empty state in its template (the read model does not add fake rows).

It is built by `readmodels.build_board_vm(board: Board, ...)` (pure Python, no IO) from the `Board`
returned by `app.boards.list_board`; it is **not** a SPEC-040 function (§2.1.5). `limit_per_group`
is clamped to `1..200` with default `50` (D-011); if it cut the list, `truncated=True` and
`more_url` points to `/{team}/list?state={state_id}` (the board does not paginate: there is no
offset on the board).

```python
card_vm = {
    "identifier": "ENG-13",
    "title": "Fix SSO login",
    "priority": 2,
    "priority_glyph": "H",
    "priority_label": "High",
    "assignee_name": "Ginkelte",
    "assignee_initials": "VI",  # None if unassigned
    "labels": [{"name": "bug", "color": "#EB5757"}],  # sorted by name
    "due_date": "2026-11-20",
    "due_label": "Nov 20",  # None if no date
    "is_overdue": True,
    "board_order": 1536.0,
    "state_id": "01JCV3...",
    "url": "/ENG/issue/ENG-13",
}
```

#### 2.3.3 `list_vm` (list.html)

```python
list_vm = {
    "team": {"key": "ENG", "name": "Engineering", "icon": "🧪"},
    "rows": [row_vm, ...],
    "filters": {"state": None, "assignee": None, "label": None, "q": None},
    "chips": [...],
    "sort": "priority",  # "manual"|"created"|"updated"|"priority"|"due" (D-011)
    "sort_links": [
        {"key": "priority", "label": "Priority", "url": "/ENG/list?sort=priority&reverse=1"}
    ],
    "next_cursor": "MTUzNi4wfDAxSk...",  # str | None = base64url(sort_key + "|" + issue.id) (D-011)
    "next_url": "/ENG/list?cursor=MTUzNi4wfDAxSk...",  # None if has_more is False
    "shown": 50,  # rows on this page (== effective limit)
    "limit": 50,  # 1..200, default 50 (D-011)
    "offset": 0,  # 0..500, infinite scroll ONLY; mutually exclusive with cursor
    "has_more": True,
    "counts": {"open": 12, "urgent": 2, "total": 18},
    "can_write": True,
}
```

```python
row_vm = {
    "identifier": "ENG-13",
    "title": "Fix SSO login",
    "priority": 2,
    "priority_glyph": "H",
    "priority_label": "High",
    "state_name": "In Progress",
    "state_type": "started",
    "state_color": "#F2994A",
    "assignee_name": "Ginkelte",
    "assignee_initials": "VI",
    "labels": [{"name": "bug", "color": "#EB5757"}],
    "due_date": "2026-11-20",
    "due_label": "Nov 20",
    "is_overdue": True,
    "sort_order": 1536.0,  # backlog manual order: read-only, never sent back
    "is_archived": False,
    "is_trashed": False,
    "url": "/ENG/issue/ENG-13",
}
```

#### 2.3.4 `issue_vm` (issue_detail.html and partials/issue_panel.html)

It is `row_vm` **plus** these keys (never less: the panel and the page use the same dict):

```python
issue_vm = {
    **row_vm,
    "number": 13,
    "description_html": "<p>…</p>",  # rendered + sanitized markdown (never None: "" if empty)
    "estimate": 3.0,
    "project": {"id": "…", "name": "Q4 hardening"},  # or None
    "cycle": {"id": "…", "number": 1, "name": "Sprint 1"},  # or None
    "parent": {"identifier": "ENG-2", "title": "SSO"},  # or None
    "children": [
        {
            "identifier": "ENG-40",
            "title": "…",
            "state_name": "Todo",
            "state_color": "#E2E2E2",
            "priority_glyph": "M",
        }
    ],
    "labels_all": [{"id": "…", "name": "bug", "color": "#EB5757"}],
    "states": [
        {
            "id": "…",
            "name": "In Progress",
            "type": "started",
            "color": "#F2994A",
            "is_current": True,
        }
    ],
    "assignees": [{"id": "…", "name": "Ginkelte", "initials": "VI", "is_current": True}],
    "comments": [
        {
            "id": "…",
            "author_name": "Ginkelte",
            "author_initials": "VI",
            "body_html": "<p>…</p>",
            "created_label": "2 h ago",
            "is_edited": False,
            "resolved": False,
        }
    ],
    "relations": [
        {
            "id": "…",
            "type": "blocks",
            "direction": "out",
            "identifier": "ENG-7",
            "title": "…",
            "state_name": "Todo",
        }
    ],
    "attachments": [
        {
            "id": "…",
            "title": "Network log",
            "subtitle": None,
            "url": "file:///tmp/hifa.log",
            "host_label": "local file",
        }
    ],
    "history": [
        {
            "field": "state_id",
            "actor_name": "Ginkelte",
            "old_label": "Todo",
            "new_label": "In Progress",
            "created_label": "2 h ago",
        }
    ],
    "created_label": "Nov 8 14:03",
    "updated_label": "2 h ago",
}
```

`direction` in `relations` is `"out"` (`blocks`, `duplicate`, `related`, `similar`) or `"in"`
(the issue is the target; shown as `blocked_by` when `type == "blocks"`). The read model resolves
the inverse; the view does not compute it.

#### 2.3.5 `search_vm` (search.html)

```python
search_vm = {
    "q": "login sso",
    "hits": [
        {
            "identifier": "ENG-13",
            "title": "Fix SSO login",
            "snippet_html": "…&lt;b&gt;<mark>login</mark> with <mark>SSO</mark>…",  # escaped + server-side <mark>
            "state_name": "In Progress",
            "state_color": "#F2994A",
            "url": "/ENG/issue/ENG-13",
        }
    ],
    "count": 2,
    "teams": [{"key": "ENG", "name": "Engineering"}],
    "team_filter": None,
}
```

`count` is `len(hits)` for this page; no total is promised (same reason as `total_estimate` in
SPEC-060).

#### 2.3.6 Markdown rendering (D-020)

`description_html`, `body_html` (comments) and the panel description are rendered with
**`markdown-it-py`** (CommonMark) + an **in-house allowlist** sanitization, in that order:

```python
# src/hifa/web/markdown.py
from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
# html=False: the user's raw HTML is escaped, not interpreted.

ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "a",
    "blockquote",
    "h1",
    "h2",
    "h3",
}
ALLOWED_ATTRS = {("a", "href")}
ALLOWED_SCHEMES = {"http", "https", "mailto"}


def render_markdown(text: str | None) -> str:
    """markdown-it-py -> HTML, then allowlist (tags/attrs/schemes). Never returns None."""
```

- The allowlist is implemented **at the edge** (walking the tree with `html.parser`, or with an
  in-house sanitizer of ~60 lines); neither `bleach` nor `nh3` is added.
- `a[href]` only with the `http`, `https` or `mailto` scheme; any other one (including
  `javascript:`, `data:`, `vbscript:`) drops the `href` attribute and keeps the text. `img` is
  **not** in the allowlist: markdown with an image comes out as text.
- `markdown-it-py` is declared in `pyproject.toml` in the **base group** (not in the `[web]`
  extra): the dashboard uses the renderer, but the dependency is not optional (D-020). Version
  pinned in `pyproject.toml` and in `uv.lock` like the rest of the dependencies.
- No `markdown-it` on the client: no JS renderer is vendored, nor a minified `marked`/`markdown-it`.
  The browser receives HTML that is already sanitized.
- Mandatory test: `**bold**` ⇒ `<strong>bold</strong>`; `<script>alert(1)</script>` and
  `[x](javascript:alert(1))` do not survive (REQ-S070-01).

#### 2.3.7 Template prohibitions

| Forbidden | Why |
|---|---|
| `{{ x\|length }}`, `{{ a + b }}`, `{{ x > 3 }}` | arithmetic and comparisons live in the read model |
| `{% set %}` with expressions | same |
| `{{ issue.state.name }}` (domain object) | only plain dicts with the keys declared above |
| `{{ x\|safe }}` on user text | only exceptions: `description_html`/`body_html` (R15) and `snippet_html` (R17), already escaped by the server |
| `<script>` in child templates | CSP `script-src 'self'` (R14) |
| `{{ url }}` without a scheme check in `attachments` | `javascript:` in an `href` (R16) |

#### 2.3.8 `team_picker_vm` (team_picker.html)

`GET /` renders this page when it cannot resolve a team: **two or more non-archived teams and a
`[cli].default_team` that names none of them** (R24). It is a `200`, not a `404` — the request is
valid, only the context is unusable, and answering with an error would hide a picker the user can
actually use. `default_team` itself is never missing: `config.py` resolves it to `[team].key` when
`[cli].default_team` is unset, so the reachable trigger is a default that points **outside** the
database, not an absent one. The "no default at all" branch (`reason = "no_default_team"`) exists only
for callers that build the app by hand, `create_app(default_team=None)`; the CLI can never reach it.

```python
team_picker_vm = {
    "title": "Choose a team · hifa",  # str
    "teams": [  # page["teams"], same order (name ASC), plus the resolved url
        {
            "key": "ENG",
            "name": "Engineering",
            "icon": "🧪",
            "issues_count": 12,
            "url": "/ENG/board",
        },
    ],
    "reason": "no_default_team",  # "no_default_team" | "several_teams"
}
```

- One row per team, each an `<a href="{url}">` to `/{team}/board`: the picker links, it does not
  post. No `<form>`, no `hx-get`, no JS — it works before any script loads (R4).
- `url` is resolved in the read model, never in Jinja (R2).
- The list and its order are the ones of `page["teams"]` (§2.3.1): one source, one ordering, so the
  picker and the sidebar cannot disagree about what teams exist.
- The template receives `page` as well (it extends `base.html`), with `team = None`, `nav = None` and
  every entry of `teams[]` carrying `active = False`.
- `reason` is informational copy ("no default team configured" / "this workspace has several
  teams"). The template prints it; it never branches on it.

### 2.4 Vendored assets

Everything lives in `src/hifa/web/static/`, served by `StaticFiles` mounted at `/static`.
**Zero network** (REQ-N005): no CDN, no Google Fonts, no downloaded source maps.

| File | Version | License | Expected size (gzip -9) | Use |
|---|---|---|---|---|
| `vendor/htmx.min.js` | 2.0.4 | BSD-2-Clause | **16 KB** | fragments, `hx-*`, `HX-Trigger` |
| `vendor/alpine.csp.min.js` | 3.14.8 | MIT | **16 KB** | local widget state (CSP build: no `eval`, see R14) |
| `vendor/sortable.min.js` | 1.15.2 | MIT | **10 KB** | board drag & drop |
| `css/app.css` | — | own | **≤ 20 KB** (target 14 KB) | the whole design (§2.5) |
| `js/app.js` | — | own | **≤ 16 KB** (target 10 KB) | Alpine registrations, `422` listener, shortcuts, drop, the change signal and its deferral |
| `img/sprite.svg` | — | own (Lucide, ISC) | **3 KB** | inline icons via `<use>` |
| `img/favicon.svg` | — | own | **1 KB** | favicon (no `.ico`) |

Total budget: **≤ 75 KB gzip** (real sum: ~66 KB). Verified in the test:

```bash
find src/hifa/web/static -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' \) \
  -exec sh -c 'gzip -9 -c "$1" | wc -c' _ {} \; | awk '{s+=$1} END {print s}'
```

Rules:

- Version and `sha256` of every vendor are recorded in `static/vendor/README.md`; updating a vendor
  requires updating that file and re-running the budget test.
- No minification at build time: the `.min.js` files are copied as is from the upstream package;
  `app.css` and `app.js` are written legibly (there is no build step, REQ-N007).
- `Cache-Control: public, max-age=31536000, immutable` for `/static/vendor/*` and
  `public, max-age=60` for `/static/js/app.js` and `/static/css/app.css` (no hashes in the name:
  the server is local and the cost of a stale file is an F5).
- No `@font-face`: system typography (0 KB).
- No product images: icons are `<svg><use href="/static/img/sprite.svg#check"/></svg>`.

### 2.5 Visual design

Dark, fixed, high density, no shadows except on overlays. A list row measures exactly **32 px**
(including padding and bottom border), the header 36 px, the sidebar 220 px.

#### 2.5.1 Palette

| Token | Hex | Use | Measured contrast |
|---|---|---|---|
| `--bg` | `#0D0E10` | app background | — |
| `--surface` | `#16181C` | bars, sidebar, table | 1.09:1 vs `--bg` |
| `--surface-2` | `#1F2228` | row hover, cards, inputs | 1.12:1 vs `--surface` |
| `--border` | `#2A2E37` | 1 px separators | — |
| `--text` | `#E6E8EB` | primary text | **15.7:1** on `--bg` |
| `--text-muted` | `#8B919C` | metadata, column labels | **6.1:1** on `--bg` |
| `--text-dim` | `#5C636E` | non-textual elements only (icons, `–`) | 3.2:1 (not used for text) |
| `--accent` | `#5E6AD2` | selection, focus, links | 4.1:1 on `--bg` / 4.7:1 for white on top |
| `--accent-hover` | `#6E79E0` | `--accent` hover | — |
| `--started` | `#F2994A` | `state.type = started` | **8.0:1** on `--surface` |
| `--completed` | `#4CB782` | `state.type = completed` | **7.1:1** |
| `--urgent` | `#EB5757` | priority 1, overdue | **5.1:1** |
| `--warning` | `#F2C94C` | priority 3, warnings | **11.2:1** |
| `--neutral` | `#6B6F76` | `backlog`/`canceled`, priority 0 | 3.5:1 (glyphs only) |

Rule: `--text-dim` and `--neutral` **never** carry informative text; the color of a
`workflow_state` comes from the database (`workflow_state.color`) and is used only as an 8×8 px dot
and a 2 px left border, never as a text color (it might not contrast).

#### 2.5.2 Typography and measurements

| Role | Value |
|---|---|
| UI family | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` |
| Mono family | `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace` |
| Base | `13px / 1.38` (18 px line height) |
| Metadata and column headers | `11px`, `text-transform: uppercase`, `letter-spacing: .04em`, `--text-muted` |
| Detail title | `17px / 1.3`, `600` |
| Identifier (`ENG-123`) | mono `12px`, `--text-muted` |
| Rows | `height: 32px`, `padding: 0 8px`, `border-bottom: 1px solid var(--border)` |
| Radii | `4px` (inputs, chips), `6px` (cards, overlay) |
| Spacing | 4/8/12/16/24 scale |
| Content width | fluid, `max-width: none`; fixed 220 px sidebar |

Hierarchy by weight and color, not by size: in the list the `title` is 13 px and the `identifier`
12 px mono; contrast does the rest (`--text` vs `--text-muted`). The whole UI fits in 1280×720
without horizontal scroll (columns of 19 chars minimum width in the board = 320 px).

#### 2.5.3 Wireframe — list view (`/{team}/list`)

```
+-------------+-------------------------------------------------------------------
| ENG Engineering v   [Board]  List  Cycles        / Search    Cmd+K    Ginkelte VI
+-------------+-------------------------------------------------------------------
| FILTERS     | IDENTIFIER  P  STATE        ASSIGNEE  TITLE                 DUE
|             |-------------------------------------------------------------------
| State       | ENG-13      H  In Progress  Ginkelte   Fix SSO login       Nov 20
|  (o) All    | ENG-12      M  Todo         Mo        Avatar cache        Nov 18
|  ( ) Open   | ENG-11      -  Backlog      -         Review nginx logs   -
|  ( ) Done   | ENG-10      L  Done         Ginkelte   Migrate to WAL      -
|             | ENG-09      H  Triage       Mo        Webhook retry       Nov 25
|             |
| Priority    |
|  [1] Urgent |
|  [2] High   |
|  [3] Medium |
|             |
| Labels      |
|  bug        |
|  auth       |
+-------------+-------------------------------------------------------------------
| 12 issues   | j k move    Enter open    c create    / search    Esc
+-------------+-------------------------------------------------------------------
```

Wireframe notes: the focused row carries `outline: 2px solid var(--accent)` with
`outline-offset: -2px`; the `P` column is the priority glyph of §2.5.4; `DUE` in red when
`is_overdue`; the `×` of each filter chip sits to the right of the chip, not in the sidebar.

#### 2.5.4 Wireframe — board view (`/{team}/board`)

```
+--------------------------------------------------------------------------------
| ENG Engineering v   [Board]  List  Cycles      / Search   Cmd+K    Ginkelte VI
+--------------------------------------------------------------------------------
| Todo (4)          | Backlog (3)       | In Progress (2)   | Done (7)          |
+-------------------+-------------------+-------------------+-------------------+
| ENG-12        M   | ENG-11        -   | ENG-13        H   | ENG-10        L   |
| Avatar cache      | Review logs       | Fix SSO           | Migrate to WAL    |
| Mo                |                   | Ginkelte   Nov 20 | Ginkelte          |
|                   |                   |                   |                   |
| ENG-08        H   | ENG-09        M   | ENG-14        -   | ENG-05        M   |
| Filter by tag     | Hook retry        | Filter panel      | Deploy staging    |
| Mo                | Mo                |                   |                   |
|                   |                   |                   |                   |
|                   | ENG-07        L   |                   | ENG-04        H   |
|                   | Bump deps         |                   | SSO on iOS        |
|                   | Ginkelte          |                   | Mo                |
+--------------------------------------------------------------------------------
| 12 open   2 urgent                             drop to move   Esc close
+--------------------------------------------------------------------------------
```

Notes: column header = state color dot + name + `count` in `--text-muted`;
card = 2 fixed lines (identifier + glyph on the right; title truncated to 1 line) + 1 line of
metadata (assignee on the left, `due_label` on the right, in red if overdue); card height varies
depending on whether there is metadata or not; column separator `1px var(--border)`; the empty
column shows `empty_state.html` with the text `Nothing in <state>`, in `--text-muted`, 11 px.

#### 2.5.5 Glyphs and classes

| Concept | Value |
|---|---|
| Priority | `–` (0), `U` (1), `H` (2), `M` (3), `L` (4), in mono; `--urgent` color for 1, `--text-muted` for the rest |
| State | 8 px dot with `background: <state.color>`, 2 px border on the card |
| Overdue | `--urgent` in `DUE` and in the state dot only if `is_overdue` |
| Labels | chip 18 px tall, `border-radius: 4px`, background `color-mix(in srgb, <label.color> 18%, var(--surface))`, `--text` text at 11 px |

#### 2.5.6 Breakpoints — the board on a phone

Below `900px` the shell drops the sidebar and the board stops being a horizontal strip. Each column
takes the full width and sits below the previous one, so the **page** is the only vertical scroller:
no nested `overflow-y` scrollers competing for the gesture, and no horizontal pan to get lost in.

| Rule | Value |
|---|---|
| `.board` | `flex-direction: column`, `overflow-x/y: visible` |
| `.column` | `flex: 0 0 auto`, `width: 100%`, no right border, bottom border as a separator |
| `.column-body` | `overflow-y: visible` — the page scrolls, not the column |
| `.column-head` | `40px` tall, so the strip is a comfortable target |
| `.card` | `10px 12px` padding — every card is at least a 44 px tall target |

The page never scrolls horizontally at 320 px: with one column per row there is no width to pan.

The panel is a full-width sheet at this breakpoint (`width: 100vw`, no left border) and its heading
is `position: sticky` at the top. A panel that covers the screen while its only way out scrolls
away with the content is a trap: the heading, with the `Close` control in it, stays under the thumb
at any scroll offset.

| Rule | Value |
|---|---|
| `.panel` | `width: 100vw`, no left border, no side padding |
| `.panel-head` | `position: sticky`, `top: 0`, `padding: 10px 16px`, surface background, bottom border |
| `.panel-head .btn` | `min-height: 40px` |
| panel content | `margin-left/right: 16px`, since the sheet has no side padding |


### 2.6 Interaction

#### 2.6.1 Command palette (`Cmd+K` / `Ctrl+K`)

```
+------------------------------------------------------+
| > log|                                               |
+------------------------------------------------------+
| ISSUES                                               |
|   ENG-13  Fix SSO login               In Progress    |
|   ENG-02  Legacy login without SSO    Done           |
| ACTIONS                                              |
|   Create issue…                            c         |
|   Go to the board                          g b       |
|   Go to the list                           g l       |
+------------------------------------------------------+
```

Normative behavior:

- `Cmd+K` (macOS) or `Ctrl+K` (the rest) opens the overlay; `Esc` closes it and returns focus to the
  element that had it. The overlay is a native `<dialog>` (`showModal()`), not a `div` with
  `position:fixed`.
- Input with `hx-get="/_/palette" hx-trigger="input changed delay:120ms" hx-target="#palette-results"
  hx-swap="innerHTML"`. With an empty `q` the server returns only the actions (no database query).
- Results in two fixed sections (`ISSUES`, `ACTIONS`); at most 8 issues + 4 actions. The number in
  `--text-muted` to the left of the title, the state on the right.
- Navigation: `ArrowDown`/`ArrowUp` move `aria-activedescendant`, `Enter` executes, `Tab` does not
  leave the input. Role `role="combobox"` + `aria-expanded` + `role="listbox"`/`option`.
- `Enter` on an issue navigates to `/{team}/issue/{identifier}` with `location.assign` (full
  navigation, not `hx-get`): the URL must stay in the history.
- Without JS, `Cmd+K` does not exist and nothing happens: no functionality is hidden behind the
  palette.

#### 2.6.2 Keyboard shortcuts

| Key | Context | Action |
|---|---|---|
| `Cmd/Ctrl+K` | global | opens/closes the palette |
| `j` | list or board with focus on the container | focuses the next row/card |
| `k` | same | focuses the previous row/card |
| `c` | list or board | opens the quick-add (list) or the composer of the focused column (board) |
| `/` | global | focuses the search input (and selects its content) |
| `Esc` | overlay open | closes overlay/palette and restores focus |
| `Esc` | no overlay, with filter chips | removes the last filter chip |
| `Enter` | focused row | navigates to the detail (`location.assign`) |
| `Cmd/Ctrl+Enter` | comment composer or quick-add | submits the form |

Implementation rules:

- Shortcuts are ignored if `event.target` is `input`, `textarea`, `select` or `[contenteditable]`
  — except `Esc` and `Cmd+Enter`.
- `j`/`k` move real focus (`element.focus()`), not a class: the row has `tabindex="-1"` and
  `:focus-visible` with the outline of §2.5.3. Focusing does `scrollIntoView({block: "nearest"})`.
- Focus state survives an htmx swap (`htmx:afterSwap` re-focuses by `data-identifier`).
- In the board, `j`/`k` walk the DOM in column order (left→right) and `board_order` within each one.
- The shortcuts are documented in `?` (not implemented in v1) and in the status-line help at the
  bottom, which is text, not a menu.

#### 2.6.3 Optimistic inline editing

The field turns into a control without a reload; the value is painted **before** the server
responds, and reverted if it fails.

```
click on the title
  → Alpine: input with the current value, select() of the text        (0 ms, local)
  → the original text is stored in data-original
Enter or blur
  → PATCH /_/issues/ENG-13?view=list
    HX-Request: true
    body JSON: {"title": "Fix SSO login (Okta)"}
  → hx-target="closest tr", hx-swap="outerHTML", hx-sync="closest tr:replace"
  → 200: the server returns the authoritative <tr> and replaces it
  → 422: beforeSwap enables the swap, the <tr> comes back with .is-invalid and data-original
         + toast "could not save: <error.message>"
  → 409: same, with the real state returned by the server
```

Rules:

- **The server is the truth.** Optimism is only local painting; the response always replaces the
  node. There is no state reconciliation on the client.
- The exact markup of the control (a single shape for all inline fields; without JS the `form` does
  a normal `POST`, with htmx the same `form` does `PATCH`):

```html
<form class="inline-edit" method="post" action="/ENG/issue/ENG-13"
      hx-patch="/_/issues/ENG-13?view=list"
      hx-target="closest tr" hx-swap="outerHTML"
      hx-sync="closest tr:replace" hx-disabled-elt="find button">
  <input name="title" value="Fix SSO login" data-original="Fix SSO login"
         hx-patch="/_/issues/ENG-13?view=list"
         hx-trigger="blur changed, keyup[key=='Enter']"
         hx-include="closest form" hx-target="closest tr" hx-swap="outerHTML"
         hx-sync="closest tr:replace">
</form>
```

- `hx-patch` on the `<form>` is what turns the submit into a `PATCH`; `action`/`method` stay as the
  no-JS fallback (2.6.6). `hx-include="closest form"` sends every field of the row form, not just
  the edited one: a `PATCH` with an absent field deletes nothing (R5 of SPEC-060), so the payload
  can be generous.
- `blur changed` (not a bare `blur`) avoids the `PATCH` when the user entered and left without
  touching anything.
- A single `PATCH` in flight per row: `hx-sync="closest tr:replace"` (discards the previous one,
  does not queue). `hx-disabled-elt="this"` while the request is in flight.
- Fields editable inline: `title` (text), `state` (`<select>`), `assignee` (`<select>`),
  `priority` (`<select>` with glyphs), `due_date` (`<input type="date">`), `estimate` (number),
  `labels` (chips + menu). `description` is **not** inline: it is edited in the panel with a
  `<textarea>` and a Save button (it needs markdown and is multi-line).
- Each control has `name` and `form` pointing to a hidden `<form method="post" action="/{team}/issue/{id}">`,
  so that without JS the same control works as a normal form (2.6.4).
- The `PATCH` body goes as JSON (`hx-ext` is not needed: htmx serializes JSON if `hx-vals='js:{...}'`
  or `json-enc` is used). Only `htmx.min.js` is vendored: `json-enc` is replaced by a 6-line
  `htmx:configRequest` in `app.js` that sets `Content-Type: application/json` and
  `JSON.stringify` of the `parameters` for `PATCH`es.

#### 2.6.4 Drag & drop (position computed on the server)

- One `Sortable` per column, `group: "board"`, `animation: 120`, `ghostClass: "card-ghost"`,
  `draggable: "[data-identifier]"`, no `handle` (the whole card drags), `forceFallback: false`.
- `onEnd` reads **only identifiers** from the DOM: the target column (`data-state-id` of the
  container) and the immediate neighbors after the drop (`data-identifier` of the previous and next
  card, or `null` at the edge). It does not read `data-board-order`, does no arithmetic and does not
  know that the column is `REAL`.

```js
function movePayload(evt) {
  const before = evt.item.previousElementSibling?.dataset.identifier ?? null;
  const after  = evt.item.nextElementSibling?.dataset.identifier ?? null;
  return { state_id: evt.to.dataset.stateId, before, after };
}
```

- The request carries **three** things and nothing else: `{"state_id": "...", "before": "ENG-12", "after": null}`.
  No `sort_order`, no `board_order`, no `rebalance`, no column list, no full order.
- The server translates that payload into the SPEC-040 §2.2 commands:
  - state change ⇒ `move_issue(uow, MoveIssue(issue=ref, target_state=state_id,
    position="before"|"after"|"last", before_id=before, after_id=after), actor)`; null `before` and
    `after` ⇒ `position="last"`.
  - same state (reorder) ⇒ `reorder_issue(uow, ReorderIssue(issue=ref, before=before,
    after=after, scope="board"), actor)`.
  The fractional position (`(prev+next)/2`, `±1024` at the edges, `1024` in an empty column) is
  computed by `domain/ranking.between` **inside** the transaction: the web layer never computes the
  float (R9).
- `POST /_/issues/{identifier}/move` responds `204` + `HX-Trigger: issue-moved`; the client does
  **not** re-render (the DOM is already where the user put it). `move` is the only optimistic
  mutation without a swap.
- Gap exhausted: if `ranking.between` cannot place the issue (`RANKING_EXHAUSTED`), SPEC-040
  rebalances the scope and retries **once** in the same transaction (REQ-F040-08). The client does
  not take part: `"rebalance": true` does **not** exist in the payload. If the retry also fails, the
  response is `422` `RANKING_EXHAUSTED` and the client reloads the board.
- On error (`409`/`422`/`5xx`) the client reloads the board: `hx-get="/_/board/{team}"` over
  `#board`, and shows a toast. The card is never left in a position the server did not accept.
- Without JS there is no drag: each card has a `<form method="post" action="/{team}/issue/{id}/move">`
  with a `<select name="state">` and a `Move` button (visible with `:focus-within`), which works the
  same without htmx and moves to the end of the target state (`position="last"`).

#### 2.6.5 Column drag & drop (board reorder)

Reordering the columns is a **presentation** operation: it writes `workflow_state.position` and
nothing else. It does not move issues, does not touch `issue.state_id`, `issue.board_order`,
`issue_history` or any counter (R25).

- One `Sortable` over the board container, `draggable: ".column"`, `filter: ".card"`,
  `animation: 120`, `ghostClass: "column-ghost"`, no `handle` (the whole header strip drags). The
  card instance of §2.6.4 lives **inside** each column and keeps its own `group: "board"`; the
  `filter` makes a drag that starts on a card belong to the card instance, so dropping a card on
  another column is still a state change and never a column move.
- Touch is not ambiguous: `delay: 220`, `delayOnTouchOnly: true`, `touchStartThreshold: 6`. A finger
  that swipes scrolls the page; a drag begins only after a long press that has also moved past a few
  pixels. Without them a column drag starts on the first `touchmove` and `Sortable` cancels the
  scroll, which on a phone means the page cannot be scrolled at all.
- `preventOnFilter: false`. `Sortable` defaults it to `true`, which makes the instance call
  `preventDefault()` on the `touchstart` of any contact that matches `filter` — a card. The gesture
  then never becomes a scroll: the page is frozen exactly where the finger touched a card, and the
  click that follows still opens the panel. A filtered element is not a drag target, so the instance
  has nothing to say about its gesture.
- `onEnd` reads **only** state ids from the DOM: the previous and next siblings of the moved
  column, `data-state-id` each, `null` at the edges.

```js
function columnPayload(evt) {
  const before = evt.item.previousElementSibling?.dataset.stateId ?? null;
  const after  = evt.item.nextElementSibling?.dataset.stateId ?? null;
  return { before, after };
}
```

- The request carries two ids and nothing else: `POST /_/states/{state_id}/position` with
  `{"before": "<id>|null", "after": "<id>|null"}`. No `position`, no float, no column list, no
  `rebalance`.
- Each column carries `data-state-id` **and** `data-position` (`board_column.html`): the first is
  what the payload sends, the second is the persisted value the column was rendered with, so the
  client can tell a no-op drop from a real one without asking the server. Neither attribute is
  read by the card drag.
- The route responds **`204` with no body** and `HX-Trigger: board-reordered`. The DOM is already
  where the user put it (Sortable moved the node), so there is no swap and no re-render: the
  server only persists. `board-reordered` exists so the counters and any other listener can
  refresh; no listener re-fetches `/_/board/{team}` on success.
- Server side: the router resolves `{state_id}` (unknown ⇒ `404`), validates that both neighbours
  belong to the same team and are not the moved state (`422` otherwise), reads the team's states
  with `list_states`, computes the new `position` with `domain.ranking.between(prev, next)` — the
  same helper `move_issue` uses, so the arithmetic still exists in one module — and calls
  `update_state(uow, UpdateState(state=<id>, position=<float>), actor)` (SPEC-040 §2.2). The edge
  computes no formula of its own and the client never sees a float.
- In `--read-only` (`can_write=False`) ⇒ **`403`** with `error.html`, the same answer every other
  write gets (§6), and the board does not initialize the column instance at all (R18).
- Failure: `404`/`422`/`5xx` ⇒ the client reloads `/_/board/{team}` over `#board` and shows a
  toast; the columns return to the order the server has. Like the card drop, the client never
  leaves the board in an order the server rejected.
- Without JS there is no column drag: the order is changed from the CLI with
  `hifa state update NAME --before A | --after A` (SPEC-050 §2.5.6) and the next page load renders
  the persisted order.

#### 2.6.7 A finger that moves is a scroll, not a tap

Rows and cards answer `click`; opening a detail is the only thing a tap on one does. On a touch
screen the browser can still deliver that `click` at the end of a gesture whose purpose was to
scroll, so the page opens a panel in the middle of a scroll and the reader loses the place — and on
a phone, where the panel covers the screen, loses the page.

The gesture is measured before the trigger sees it:

| Step | Rule |
|---|---|
| `touchstart` | record the first contact; more than one finger means "not a tap" |
| `touchmove` | a displacement past `10px` on either axis marks the gesture as movement |
| `click`, capture phase | if the gesture moved, `preventDefault()` and `stopPropagation()` on `.card`, `.row` and `[data-href]` |

Capture matters: the element's own `hx-get` listens on the bubble phase, so a closer registered
there runs too late. The threshold is a slop, not a drag detector — a deliberate tap with a shaky
thumb stays a tap.

#### 2.6.6 Progressive enhancement

| Capability | Without JS | With JS |
|---|---|---|
| Read board, list, detail | yes (full HTML from the server) | same |
| Navigate between pages | yes (`<a href>`) | same (no `hx-boost`: navigation is not intercepted) |
| Create issue | yes (`<form method="post">` → `303`) | quick-add with `hx-post`, row inserted |
| Edit a field | yes (panel form + Save) | optimistic inline |
| Comment | yes | `hx-post` + append |
| Move state | yes (`<select>` + button) | drag & drop |
| Reorder columns | no (`hifa state update NAME --before/--after`, SPEC-050 §2.5.6) | drag & drop of the column |
| Filter | yes (query string + submit) | `hx-get` with debounce |
| Search | yes (`/search?q=`) | same |
| Command palette, `j/k/c//` | no | yes |
| Notice a write from another connection | yes: a reload shows it | automatic, only when the revision changed (§2.6.8) |

Rules:

- **Every `hx-get` has an equivalent `href`**; every `hx-post`/`hx-patch`/`hx-delete` has an
  equivalent `<form method="post" action>`. A test walks the rendered HTML and fails if an element
  with `hx-get` has no `href`.
- `<noscript>` in `base.html`: one line saying that drag & drop, the palette, the shortcuts and the
  automatic refresh need JS and that everything else works the same. Without `<noscript>` there is
  no message (nothing is assumed about the user).
- `<body>` carries `class="no-js"` and `app.js` switches it to `js` on its first line; CSS hides
  JS-only controls with `.no-js [data-js-only] { display: none }`. That way there is no "flash" of
  dead controls.

#### 2.6.8 Noticing a write from another connection (D-049)

A page is a snapshot: it was rendered at one revision of the file, and the CLI — or an agent driving
it, or a second tab — can commit over it while it is open. The dashboard closes that gap with a
**change signal, not a timer**: one cheap number, compared, and only a difference re-renders
anything.

| Piece | Contract |
|---|---|
| Probe | `GET /_/revision` → `{"revision": <n>}`, `no-store`, `HX-Request` required (§2.1.3.1) |
| Number | `PRAGMA data_version` of the process's single long-lived read connection (D-049). **Opaque**: equality only — never `<`, never arithmetic, never a clock, never shown to the user |
| Interval | one constant, `REVISION_POLL = "5s"` in `src/hifa/web/app.py` beside `DEFAULT_LIMIT`/`MAX_LIMIT`, surfaced as `page["revision_poll"]` (§2.3.1). No template, script or test writes the duration a second time |
| Poller | `<div id="revision-probe" hx-get="/_/revision" hx-swap="none" hx-trigger="every {{ page.revision_poll }}">` in `base.html`: one request per **visible** tab per interval, and none while the tab is hidden. The visibility condition is **not** a `hx-trigger` filter: htmx compiles a `[condition]` through `Function`, which `script-src 'self'` forbids, and a filter that throws is treated as *do not fire*, so the poll would never run — the condition is enforced on the client by cancelling `htmx:beforeRequest` for this element while `document.hidden` |
| Page marker | `<body data-revision="{{ page.revision }}">` — the number this page was rendered with |
| Adopt | every `200`/`201` of this layer carries `HX-Revision` (§2.1.3); on `htmx:afterRequest` the poller stores it as the page's known number |

**Which pages carry the poller.** The board and the list carry it: they hold a collection that a write can change. The detail page (§2.3.1) carries its revision in `data-revision` and answers `HX-Revision`, and its `data-refresh` is empty — there is no collection to reload, so there is nothing for the poller to swap and it is not rendered. An empty `data-refresh` is the marker, not a missing value: the client reads it and spends the pending revision without a request.
| Reload | only when the probe's number differs from the known one: an **htmx** GET of `page["refresh_url"]` (§2.3.1) into `#board` / `#issues-tbody` with `innerHTML` — the same fragment the filter bar reloads, filters included. It travels through htmx and not through `fetch`: the fragment arrives carrying `hx-get`, `hx-target` and `hx-push-url` on its rows and cards, htmx binds attributes on the nodes it swaps, and a body written with `innerHTML` on a plain response leaves a board of inert cards — the tab stops opening panels until a manual reload |
| Never | the whole page, `history`, the URL; and never while a blocking state below holds |

**The do-not-interrupt rule.** A swap deletes whatever is inside the element it replaces, so the
refresh waits for the interaction instead of ending it:

| Blocking state | How the poller sees it | What is deferred |
|---|---|---|
| a detail panel is open | `#panel` has an element child (the `<aside>` of §2.1.3) | the board/list swap, which would delete the panel |
| the quick add holds unsaved text | `#quick-add .quick-add-input` is non-empty | every swap: the input lives inside the fragment being replaced |
| a drag is in flight (card or column) | `document.body.dataset.dragging === "true"`, set in the Sortable `onStart` of §2.6.4/§2.6.5 and cleared in `onEnd` | the board swap, which would move the node under the pointer |

**The reload is what the page is showing, not what its URL says.** A filter typed into the filter bar
lives in the form, not in the page's query: the poller appends the form's current values to
`page["refresh_url"]` before the request, because a reload without them answers a different question
and puts a filtered board under an input that still says so. A reload that never answers does not
freeze the poll either: the wait is bounded, and a tick that found the previous request still in
flight gives up on it and asks again, so one hung request cannot silence the page for the rest of the
session.

**The collection fragment carries rows, not the page's form.** The quick-add `<form>` belongs to the
page: a list fragment that shipped it as an out-of-band swap would replace the form the user is
typing into with a fresh one on every automatic reload, which is a reset nobody asked for. The form
is reset by htmx after its own successful submit, which is the only moment it needs to be.

One pending value, not a queue: the poller keeps the last revision it has seen and applies it when
the blocking interaction ends — the panel closes, the input is cleared or submitted, the drop's
request returns — **immediately**, without waiting for the next tick. Two revisions observed while
blocked produce one reload, because the number is opaque and only the last one read is meaningful.

**The panel is not refreshed under the reader.** An open panel is a blocking state like the
others: the poller never re-renders it, and a re-render of the panel is not a reason to spend the
pending revision (D-050). Two reasons, both of them about what the number can and cannot say: the
revision is global and carries no identity, so *"re-render the panel only if its issue changed"* is
not a condition that can be evaluated; and replacing the markup of the panel a person is reading is
a worse interruption than waiting for them to close it. What the panel shows when it is reopened
is the answer of `/_/issues/{identifier}/panel` (§2.1.4), which is as fresh as the click that asked
for it. When the panel closes, the pending revision applies like after any other interaction.

**After the user's own write.** Every write of §2.1.2/§2.1.3 answers with `HX-Revision`, and so does
every fragment: an answer that **replaced the collection** adopts its number, so a quick add, a drop
or a filter change does not make the poll re-render the same content a second later. An answer that
rendered something else — a panel, a row, the palette, the counters — **does not** adopt: its number
is the one the server observed, foreign commits included, and adopting it would spend the difference
between the page and the database without updating the page, which leaves a stale board behind a mute
poller. The residual is stated in D-049: a foreign commit that lands between the render and the
user's own write is masked by the adopted number, and appears on the next commit or on a reload.

**Without JavaScript** the page is exactly the page: the `hx-*` attributes of the poller do nothing,
`data-revision` is an unused attribute, and a reload shows the new data. Auto-refresh is an
improvement, never the only path (P5, §2.6.6).

**What it is not.** Not a timer that re-renders: no revision change, no request beyond the probe, no
swap, no flicker. Not a live-update channel: there is no SSE, no websocket and no long-lived request
(D-049). Not in `--read-only`'s way: the probe is a read, it is served in that mode, and a read-only
dashboard watching a CLI writer is exactly the case it exists for.

**Verification.** The probe, the page marker and the header are pytest-assertable (§7); the swap and
the do-not-interrupt rule are verified **by hand in a browser**, because this host has no Playwright
(the drag & drop debt): open `/ENG/board`, run `hifa issue add` or move five issues from the
terminal, and watch the column redraw within one interval — then repeat with the panel open, with
text in the quick add and mid-drag, and check that nothing moves until the interaction ends.

## 3. Data it touches

The dashboard does not execute SQL: the read models are built by `hifa/adapters/sqlite/reads.py`
(SPEC-030) and mutations go through the use cases (SPEC-040). This table declares where each thing
comes from. The one statement this layer issues itself is `PRAGMA data_version` (§2.1.3.1), which
reads a counter of the connection and touches no table: there is nothing of it to own in `app/`.

| View / fragment | Tables and views | Operation |
|---|---|---|
| `base.html` | `team` | SELECT (team list, active team) |
| `board.html`, `/_/issues?view=board` | `workflow_state`, `v_issue`, `issue_label`, `label`, `member` | SELECT |
| `list.html`, `/_/issues?view=list` | `v_issue`, `issue_label`, `label`, `member`, `workflow_state` | SELECT (one query with `GROUP_CONCAT` of labels, no N+1: REQ-N008) |
| `issue_detail.html`, `/_/issues/{id}/panel` | `v_issue`, `comment`, `issue_relation`, `attachment`, `issue_history`, `issue_label`, `workflow_state`, `member` | SELECT |
| `search.html`, `/_/palette` | `issue_fts`, `v_issue` | SELECT (SPEC-090) |
| `POST /{team}/issue`, `/_/issues` | `issue`, `issue_history` | INSERT (use case) |
| `PATCH /_/issues/{id}` | `issue`, `issue_history` | UPDATE |
| `POST …/move` | `issue`, `issue_history` | UPDATE `state_id`, `board_order` |
| `POST …/labels`, `DELETE …/labels/{id}` | `issue_label` | INSERT / DELETE |
| `POST …/comment`, `/_/comments/{id}` | `comment` | INSERT |
| `POST …/archive` | `issue` | UPDATE `archived_at` |
| `POST /_/states/{id}/position` | `workflow_state` | UPDATE `position` (presentation order only, R25) |
| `/_/revision` | — (none) | `PRAGMA data_version` on the process's own read-only connection (D-049): no table is read, no row is locked, no transaction is opened |

Invariants the view does **not** re-validate (guaranteed by the SPEC-010 schema): `UNIQUE(team_id,
number)`, composite FK `(team_id, state_id)`, `CHECK(ends_at > starts_at)`, `CHECK(priority 0..4)`.

**One connection lives longer than a request.** The revision probe is the single exception to "a
connection per call" (`Database.connect`/`ro_connection`, SPEC-030): `create_app` (or the first call
to `/_/revision`, lazily) opens **one** read-only connection, stores it on `app.state` and reuses it
for the life of the process, and a `lifespan` shutdown handler closes it. `PRAGMA data_version`
answers a per-connection counter that advances when *another* connection commits, so a connection
opened per request would always answer the same value and could not be the source at all (D-049).
The connection never holds a transaction — every read is autocommit — so SPEC-001 EC-012 ("`serve`
opens one connection per request, no long transactions") stays true: the invariant is about
transactions and locks, and this connection holds neither between probes.

## 4. Business rules

| ID | Rule |
|---|---|
| **R1** | **Two route families, no overlap.** Pages hang off `/{team}/…` and `/search`; fragments **only** off `/_/…`. A fragment is never served on a page route nor the other way around. → **REQ-F070-01**, **REQ-F070-02** |
| **R2** | **Zero logic in Jinja.** Templates only access read model keys. A static test greps `templates/**` for `\|length`, `{% set %}`, arithmetic operators and `.query`/entity attributes and fails. → **REQ-F070-03** |
| **R3** | **Complete read model.** Every template receives all the keys declared in §2.3, even if the value is `None`/`[]`/`""`. Defensive `default()` in the template for a key the read model must provide is forbidden. → **REQ-F070-03** |
| **R4** | **Without JS you can read and write.** Every page is full HTML; every mutation has a `<form method="post" action>` that responds `303`. Every `hx-get` has an `href`. → **REQ-F070-04** |
| **R5** | **A fragment requires `HX-Request`.** Without that header, `/_/*` responds `400` with `text/plain`. It keeps a fragment from being indexed or opened as a half page. → **REQ-F070-02** |
| **R6** | **Canonical URL.** The detail lives at `/{team}/issue/{identifier}`; requesting it by ULID redirects `303` to the identifier. Two URLs never serve the same content with 200. → **REQ-F070-01** |
| **R7** | **Bounded optimism.** The client paints the value before the response and **always** replaces the node with the server response. On error, it restores and notifies. There is no business state on the client. → **REQ-F070-05** |
| **R8** | **One `PATCH` in flight per row** (`hx-sync="closest tr:replace"`); the second discards the first. No queues and no automatic retries. → **REQ-F070-05** |
| **R9** | **Drag & drop sends neighbors, not positions.** Single payload: `{state_id, before, after}` with identifiers (`before`/`after` = `null` at the edge). Sending `sort_order`, `board_order`, `rebalance` or the whole column is forbidden; computing the float on the client is forbidden. The position (`(prev+next)/2`, `±1024`, `1024`) is computed by `domain/ranking.between` inside `move_issue`/`reorder_issue` (SPEC-040, D-012). → **REQ-F070-06** |
| **R10** | **The rebalance is decided by the server.** On `RANKING_EXHAUSTED`, SPEC-040 rebalances the scope and retries once in the same transaction (REQ-F040-08); the client does not request `rebalance` and does not know the 1024 multiple. If the retry fails, `422` `RANKING_EXHAUSTED` + board reload. → **REQ-F070-06** |
| **R11** | **htmx does not swap 4xx by default.** A global listener enables the swap only for `422` (and for `409` in the panel). Without that listener, an invalid field would leave the UI mute: it is the most common bug in this stack. → **REQ-F070-09** |
| **R12** | **Events by header.** Successful mutations emit `HX-Trigger` with `issue-created`, `issue-updated`, `issue-moved`, `board-reordered`, `comment-created`; the counters listen for those events (`hx-trigger="issue-moved from:body"`). Polling exists in exactly two places and nowhere else: `/_/counts` every 30 s (counters) and `/_/revision` every `REVISION_POLL` (the change signal of §2.6.8, R26). Neither re-renders the collection it watches. → **REQ-F070-10** |
| **R13** | **One read model per entity.** The panel and the detail page use the same `issue_vm`; the row and the card share `row_vm`/`card_vm` with the same derived keys (`priority_glyph`, `due_label`, `is_overdue`). → **REQ-F070-03** |
| **R14** | **Strict CSP without `unsafe-eval`.** `script-src 'self'` forces the Alpine CSP build (`Alpine.data()` registered in `app.js`); `x-data="{…}"` with expressions is forbidden and so is inline `<script>`. → **REQ-S070-02**, **REQ-S070-03** |
| **R15** | **Own HTML already escaped before `\|safe`.** `description_html` and `body_html` leave the server already escaped and with a tag allowlist (`p, br, strong, em, code, pre, ul, ol, li, a[href], blockquote, h1-h3`); `a[href]` only `http(s):`/`mailto:`. `snippet_html` leaves the server **escaped and with `MARK_START`/`MARK_END`** (R17, SPEC-090 §2.5). `\|safe` is applied **only** to those three fields. → **REQ-S070-01**, **REQ-S070-04** |
| **R16** | **URL scheme in attachments.** `attachments[].url` is validated in the read model: `http`, `https`, `file`; any other scheme is shown as text without an `href`. → **REQ-S070-05** |
| **R17** | **Search: escape first, then mark.** The snippet is escaped first and the terms are wrapped in `<mark>` afterwards; raw FTS5 HTML is never inserted. → **REQ-S070-04** |
| **R18** | **Read-only is visible.** With `can_write=False` no write `<form>` and no drag control are rendered; only reading remains. A `POST` in that mode ⇒ `403` with `error.html`. → **REQ-F070-04** |
| **R19** | **Palette in two phases.** With an empty `q` the server responds with **actions only** and does not query the database; with a non-empty `q` it adds up to 8 issues. The list is a `<ul role="listbox">` with `aria-activedescendant`; `Enter` navigates with `location.assign` (not `hx-get`) so the URL stays in the history. → **REQ-F070-07** |
| **R20** | **Shortcuts with a focus guard.** `j/k/c///Esc` are ignored if `event.target` is `input`, `textarea`, `select` or `[contenteditable]` (except `Esc` and `Cmd+Enter`). `j`/`k` move **real focus** (`tabindex="-1"` + `:focus-visible`), not a class, and survive an htmx swap. → **REQ-F070-08** |
| **R21** | **Search with state in the URL.** `/search?q=` is a full page and `GET /_/palette` is the fragment of the same query; both use `SearchService.parse` + `SearchService.search` (SPEC-090 §2, D-016). `q` travels in the query string, never in the body nor in a cookie. → **REQ-F070-11** |
| **R22** | **Keyset HTML pagination (D-011).** `limit` 1-200 default 50; cursor = opaque `base64url(sort_key + "\|" + issue.id)` generated by `encode_cursor` (SPEC-030 §2.6); `has_more` comes from requesting `limit + 1` (never `COUNT(*)`); `next_url` carries `cursor`. There is no `?page=N`. `offset` (0-500) exists **only** for `list.html` infinite scroll, is mutually exclusive with `cursor`, and its cap does not come from this spec. Invalid `sort`, `cursor` or `offset`, or `cursor` + `offset` together ⇒ `422` with `error.html`. → **REQ-F070-12** |
| **R23** | **The sidebar is a per-request read.** `page["teams"]` is built from the database on every request that renders a page; it is never captured once in `create_app`, never stored on `app.state` and never memoized for the life of the process. The CLI is the **other writer** of that same SQLite file (D-030), so a team created by `hifa team create` while `hifa serve` is running must appear in the sidebar on the **next request**, with no restart. The active team is marked and the block is never hidden, not even with a single team. → **REQ-F070-13** |
| **R24** | **`default_team` is never absent, and the root never guesses.** `config.py` always resolves `[cli].default_team` — the configured value, else `[team].key`, which itself defaults to `DEFAULT_TEAM_KEY` (SPEC-100 §2.3) — so "2+ teams and no default" is **not** a reachable state and is not what the picker is for. What `GET /` does, in this order: (1) the resolved default names a team that exists ⇒ `303` to `/{key}/board`; (2) exactly one non-archived team ⇒ `303` to it (this is the fresh-database case, where the default key exists); (3) **canonical picker case** — the default names a team that is **not in the database** (a second team created under another key, a regenerated `.bak`, a `.hifa.toml` copied from another workspace) and there are 2+ non-archived teams ⇒ `200` `team_picker.html` (§2.3.8) listing every team and linking to `/{team}/board`, with `reason = "several_teams"`; (4) **secondary case** — the caller passed no default at all (`create_app(default_team=None)`: embedding, tests, a bootstrap that skipped config) and there are 2+ teams ⇒ the same `200` picker, with `reason = "no_default_team"`; (5) no teams ⇒ `404` `error.html`. It never picks by `created_at`, alphabetical order, cookie or session, and never answers `404` when a usable answer exists. → **REQ-F070-14** |
| **R25** | **Reordering the columns changes the presentation order, nothing else.** `POST /_/states/{id}/position` writes `workflow_state.position` and **only** that: no issue changes `state_id` or `board_order`, no `issue_history` row is written and no counter moves. The payload is `{before, after}` with **state ids** (`null` at the edge), never a float; the new `position` is `domain.ranking.between(prev, next)` computed on the server and persisted by `update_state`. The response is `204` + `HX-Trigger: board-reordered` with no swap (the DOM already moved), `403` in `--read-only`, `404` for an unknown state and `422` for neighbours from another team. The canonical order a **new** team is seeded with is `DEFAULT_STATES` (SPEC-100 §2.5, D-034); an existing team is reordered with this route or with `hifa state update` (SPEC-050 §2.5.6), never with a migration. → **REQ-F070-06** |
| **R26** | **The page notices a foreign write by a change signal, never by a timer.** `GET /_/revision` answers `{"revision": <n>}` read from `PRAGMA data_version` on the process's single long-lived read connection (§2.1.3.1, §3, D-049); the number changes iff another connection committed since the previous read — which is what the CLI is, and what the process's own per-request connections are. The page carries the number it was rendered with (`<body data-revision>`, `page["revision"]`) and adopts the one every response returns (`HX-Revision`); the poller (`hx-trigger="every {{ page.revision_poll }}"`, `hx-swap="none"`, visibility enforced in the client by cancelling `htmx:beforeRequest` while the tab is hidden — never as a `[condition]` filter, which the CSP's `script-src 'self'` makes unrunnable) reloads `page["refresh_url"]` into `#board`/`#issues-tbody` **only when the numbers differ**, so no request and no swap happens while nothing changed. The reload is **deferred, never forced**, while a detail panel is open, the quick add holds unsaved text or a drag is in flight; the last revision seen is applied as soon as the interaction ends (§2.6.8). The number is opaque — compared for equality, never ordered, never arithmetic, never displayed. Without JS nothing of this runs and a reload shows the new data (P5). The probe is a read: `--read-only` neither blocks it nor changes it. → **REQ-F070-15** |

Component-specific requirements:

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N070-01** | Total assets ≤ **75 KB gzip** (htmx 16 + alpine 16 + sortable 10 + css 14 + js 6 + svg 4). | script from §2.4 in CI |
| **REQ-N070-02** | `GET /{team}/board` with 200 issues: p95 < **120 ms**, HTML ≤ **400 KB**. `GET /{team}/list` with 50 rows: HTML ≤ **150 KB**. | pytest-benchmark + `len(response.content)` |
| **REQ-N070-03** | No outbound request: no CDN, no remote fonts, local `/static/**`. | test that scans `templates/**` and `static/**` for `://` |
| **REQ-N070-04** | List row exactly **32 px**; `app.css` ≤ **20 KB** unminified. | Playwright test `getBoundingClientRect().height === 32` |
| **REQ-N070-05** | No `package.json`, no `node_modules`, no bundler: `web/` is Python + templates + static assets. | file-tree test |
| **REQ-S070-01** | See R15. | test with `<script>alert(1)</script>` and `[x](javascript:alert(1))` in `description` |
| **REQ-S070-02** | Header `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`. | header test on `/ENG/board` |
| **REQ-S070-03** | No inline `<script>` and no `on*=` in the served HTML; Alpine CSP build. | test that scans the rendered HTML |
| **REQ-S070-04** | See R17. | test with a term containing `<` |
| **REQ-S070-05** | See R16. | test with `javascript:alert(1)` as attachment `url` |

## 5. Flow

### 5.1 Rendering a page

```
1. GET /{team}/board → web router.
2. Resolve team by key (404 if it does not exist) and can_write (process read_only).
3. Parse filters from the query string; an invalid value is dropped (it does not break the page)
   except sort/cursor in list (422 with error.html).
4. `app.boards.list_board(db, *, team=key, group_by="state", limit_per_group=N)` → `Board`.
5. `readmodels.build_board_vm(board, filters, counts)` (pure, no IO): glyphs, due_label,
   is_overdue, chips, counts, urls. It is not a port function (§2.1.5).
6. `templates.TemplateResponse(request, "board.html", {"page": page, "vm": board_vm})`.
7. Middleware adds CSP and `Cache-Control: no-store` to pages (they are user state).
```

### 5.2 Rendering a fragment

```
1. GET /_/issues?view=list with HX-Request: true. Without the header ⇒ 400 text/plain.
2. Same filter and read model pipeline as 5.1 (there is no parallel path: the function is reused).
3. Render `partials/issue_rows.html` (only the <tbody>, no <html>).
4. 200 text/html response; htmx swaps into #issues-tbody.
```

### 5.3 Inline editing (PATCH)

```
1. blur/Enter on the control ⇒ htmx serializes {"title": "..."} and does PATCH /_/issues/ENG-13?view=list.
2. Router: resolve ref (404), validate body (422 with the <tr> in .is-invalid state).
3. `app.issues.update_issue(uow, UpdateIssue(issue=ref, title=...), actor)` → UPDATE issue +
   INSERT issue_history (one transaction).
4. Serialize the row → partials/issue_row.html; 200 with HX-Trigger: issue-updated.
5. Domain error ⇒ 422/409 with the same partial + message in `.field-error`; HX-Retarget to the form.
```

### 5.4 Drag & drop (move)

```
1. sortablejs onEnd builds {state_id, before, after} from the neighbors' data-identifier. No arithmetic.
2. POST /_/issues/ENG-13/move  {"state_id": "...", "before": "ENG-12", "after": "ENG-15"}
3. Router validates the payload (422), resolves issue and state (404) and calls:
   - different state ⇒ app.issues.move_issue(uow, MoveIssue(...), actor)
   - same state      ⇒ app.issues.reorder_issue(uow, ReorderIssue(..., scope="board"), actor)
4. Validation (open children) ⇒ 422 ⇒ same recovery; InvalidTransition (the issue is archived, R20) ⇒ 409 ⇒
   the client reloads /_/board/{team} and shows a toast.
5. RANKING_EXHAUSTED after the server's rebalance+retry ⇒ 422 ⇒ same recovery.
6. 204 + HX-Trigger: issue-moved ⇒ the counters refresh; the DOM is untouched.
```

### 5.5 Column drag & drop (board reorder)

```
1. sortablejs onEnd (column instance) builds {before, after} from the neighbours' data-state-id. No arithmetic.
2. POST /_/states/<state_id>/position  {"before": "<id>", "after": "<id>"}
3. Router: state (404) → neighbours belong to the same team and are not the moved state (422) →
   list_states(team) → new position = ranking.between(prev.position, next.position) →
   app.teams.update_state(uow, UpdateState(state=<id>, position=<float>), actor)
4. RANKING_EXHAUSTED (no float room between the neighbours) ⇒ 422 ⇒ the client reloads /_/board/{team}.
5. Read-only (can_write=False) ⇒ 403 error.html; the instance is not initialized in that mode.
6. 204 + HX-Trigger: board-reordered ⇒ counters refresh; the DOM and the issue rows are untouched.
```

### 5.6 Refreshing after a foreign write (§2.6.8, D-049)

```
1. The page is rendered at revision R (page["revision"], <body data-revision="R">) and carries the
   poller: #revision-probe with hx-trigger="every {{ page.revision_poll }}" and hx-swap="none"; the
   visibility guard lives in the client, cancelling htmx:beforeRequest while the tab is hidden.
2. Tick: htmx GET /_/revision with HX-Request: true.
3. Router: reuse the process's read-only connection, PRAGMA data_version in autocommit, and answer
   200 application/json {"revision": R'} with Cache-Control: no-store and HX-Revision: R'.
4. app.js: R' == R ⇒ nothing happens. No fragment request, no swap, no flicker.
5. R' != R ⇒ is a blocking state holding (panel open, quick add non-empty, drag in flight)?
   yes ⇒ R' becomes the single pending value; nothing is swapped now.
6. no ⇒ htmx GET page["refresh_url"] (the fragment the filter bar would load) with the filter form's
   live values appended, into #board or #issues-tbody, replacing the node; the answer carries
   HX-Revision: R'' ⇒ adopt R'' as the known revision and spend the pending value. The panel is not
   re-rendered here: it is a blocking state, and it shows what the click that opened it asked for.
   A blocking state is deferred once per observed revision, not once per tick, and the pending value
   survives until the collection is really reloaded — spending it while the swap is deferred would
   leave the board old and the poller with nothing left to apply.
7. A blocking state that ends with a pending revision applies it immediately: back to step 6, no
   waiting for the next tick.
8. A write of the page itself (quick add, inline edit, drop, filter change) answers with HX-Revision
   and the page adopts it in the htmx afterRequest hook: step 4 is already true on the next tick, so
   the user's own action never causes a second render of what it just produced.
```

## 6. Errors

| Situation | Status | Response | Effect on the UI |
|---|---|---|---|
| nonexistent `{team}` or `{identifier}` | 404 | `error.html` (`message`, `back_url`) | error page with a link to the list |
| `/search` without `q` | 400 | `error.html` | same |
| Fragment without `HX-Request` | 400 | `text/plain` `fragment requires htmx` | not applicable (it is a malformed client) |
| `/_/revision` without `HX-Request` | 400 | `text/plain` `fragment requires htmx` (R5, §2.1.3.1) | the tick is skipped and the page keeps what it has |
| Validation on a page (form without JS) | 422 | same template + `form_errors` | errors next to the field |
| Validation in a fragment / inline | 422 | partial with `.is-invalid` + `HX-Retarget` | field marked + toast |
| `Conflict` (label in use, etc.) | 409 | partial with message | toast, no destructive swap |
| `Validation` (drop with open children) | 422 | no body; the client reloads `/_/board/{team}` | the card returns to its place + toast |
| `InvalidTransition` (drop on an archived issue, R20) | 409 | no body; the client reloads `/_/board/{team}` | the card returns to its place + toast |
| Unknown `{state_id}` on `/_/states/{id}/position` | 404 | `text/plain` `workflow state does not exist` | the client reloads `/_/board/{team}` |
| Neighbour of the column drop from another team, or the moved state itself | 422 | `text/plain` `invalid neighbour` | same reload; the columns return to the server order |
| Write with `read_only` | 403 | `error.html` | read-mode banner |
| `/_/revision` when the probe connection cannot answer | 503 | `application/json` `{"revision": null}` + `Cache-Control: no-store` | the body is not a number ⇒ the client does nothing; no swap, and the next tick retries on its own |
| Unmapped exception | 500 | generic `error.html` (no traceback) | same |

Catalog of visible messages (exact text, lowercase initial, no trailing period):

| Case | Text |
|---|---|
| 404 issue | `issue 'ENG-999' does not exist` |
| 404 team | `team 'OPS' does not exist` |
| 404 state | `workflow state does not exist` |
| empty q | `type something to search` |
| move with open children | `cannot move ENG-13: it has 2 open sub-issues` |
| write on an archived issue | `ENG-13 is archived; restore it first` |
| read-only | `server in read-only mode` |
| invalid field | `invalid value` + the domain `error.message` |

In fragments, the error travels in the HTML (`.field-error`), **not** in JSON: the client of this
layer is htmx, not an agent. The one exception is `/_/revision`: its success body is JSON, so its
failures are JSON too (§2.1.3.1) — a probe that answered HTML on failure would make the media type
of an answer depend on the outcome, and the client would have to parse both. The probe is the only
route of this layer that **cannot** fail with a rendered page: it has no template, so it has no
`error.html` and no `partials/fragment_error.html` either.

## 7. Acceptance Criteria

```gherkin
@SPEC-070
Feature: hifa web dashboard

  Scenario: the root redirects to the default team board
    Given a workspace with one team ENG
    When GET / is requested
    Then the status is 303
    And the Location header is "/ENG/board"

  Scenario: the root redirects to the default team even with several teams
    Given a workspace with teams ENG and OPS and the default team OPS
    When GET / is requested
    Then the status is 303
    And the Location header is "/OPS/board"

  Scenario: a default that names no existing team falls back to the single team
    Given a workspace with one team OPS and the default team ENG
    When GET / is requested
    Then the status is 303
    And the Location header is "/OPS/board"

  Scenario: a default that names no existing team with several teams renders the picker
    Given a workspace with teams ENG and OPS and the default team XYZ
    When GET / is requested
    Then the status is 200
    And the HTML contains a link to "/ENG/board"
    And the HTML contains a link to "/OPS/board"
    And the HTML contains the issue count of each team
    And the HTML contains no form element

  Scenario: the root without any default at all renders the picker
    Given a workspace with teams ENG and OPS and an app built with no default team
    When GET / is requested
    Then the status is 200
    And the HTML contains a link to "/ENG/board"
    And the HTML contains a link to "/OPS/board"

  Scenario: a team created while the server runs appears in the sidebar without a restart
    Given the server is running with only team ENG and its sidebar lists one team
    When "hifa team create OPS Operations" is executed against the same database
    And GET /ENG/board is requested again on the same process
    Then the status is 200
    And the sidebar contains the link "/OPS/board"
    And the sidebar still marks ENG as the active team

  Scenario: the sidebar renders a single team like any other
    Given a workspace with one team ENG with 12 issues
    When GET /ENG/board is requested
    Then the HTML contains the TEAMS block
    And the block contains "ENG" and "12"

  Scenario: the board shows one column per workflow state in position order
    Given a team ENG with 4 issues spread across 2 states
    When GET /ENG/board is requested
    Then the status is 200
    And the HTML contains 6 columns in position order
    And each column shows its count
    And the HTML contains no inline script tags

  Scenario: the board is navigable without JavaScript
    Given a team ENG with one issue ENG-13
    When GET /ENG/board is requested without running JS
    Then the HTML contains an href link "/ENG/issue/ENG-13"

  Scenario: the list renders 50 rows and a next page link
    Given a team with 120 open issues
    When GET /ENG/list is requested
    Then the status is 200
    And the HTML contains 50 tr elements with a data-identifier attribute
    And the HTML contains a pagination href with the parameter "cursor=" (the opaque cursor, D-011)

  Scenario: the second page does not repeat rows
    Given a team with 7 issues and limit 3
    When next_url is followed until there is no more
    Then the union of identifiers has no duplicates
    And the last page does not contain the pagination block

  Scenario: cursor and offset together are rejected
    Given a team ENG with 120 open issues
    When GET /ENG/list?cursor=MTUzNi4wfDAxSk&offset=50 is requested
    Then the status is 422
    And the HTML contains the error layout

  Scenario: the infinite scroll offset does not exceed 500
    Given a team ENG with 600 open issues
    When GET /ENG/list?offset=501 is requested
    Then the status is 422
    And the HTML contains the error layout

  Scenario: the detail by ULID redirects to the canonical identifier
    Given an issue with identifier ENG-13
    When GET /ENG/issue/<its ULID> is requested
    Then the status is 303
    And the Location header is "/ENG/issue/ENG-13"

  Scenario: a nonexistent issue returns 404 with the layout
    Given a team ENG without the issue ENG-999
    When GET /ENG/issue/ENG-999 is requested
    Then the status is 404
    And the HTML contains "issue 'ENG-999' does not exist"
    And the HTML contains the link to the list

  Scenario: a fragment without HX-Request is rejected
    Given the app running
    When GET /_/issues?view=list is requested without the HX-Request header
    Then the status is 400
    And the body is plain text

  Scenario: the row fragment does not include the layout
    Given a team with 3 issues
    When GET /_/issues?view=list is requested with HX-Request true
    Then the status is 200
    And the body starts with the tbody tag
    And the body does not contain the html tag

  Scenario: inline editing returns the updated row
    Given an issue ENG-13 with title "Old"
    When PATCH /_/issues/ENG-13 is requested with body {"title":"New"}
    Then the status is 200
    And the body contains "New"
    And the body is a tr with id "issue-ENG-13"
    And the HX-Trigger header contains "issue-updated"

  Scenario: invalid inline editing returns 422 with the field marked
    Given an issue ENG-13
    When PATCH /_/issues/ENG-13 is requested with body {"title":""}
    Then the status is 422
    And the body contains the class "is-invalid"
    And the HX-Retarget header points to the row form

  Scenario: the drop sends neighbors and the server computes the position
    Given a column with board_order 1024 for ENG-12 and 2048 for ENG-15
    When POST /_/issues/ENG-13/move is requested with {"state_id":"<id>","before":"ENG-12","after":"ENG-15"}
    Then the status is 204
    And the board_order of ENG-13 is 1536.0
    And the HX-Trigger header contains "issue-moved"

  Scenario: dropping at the end of the state uses the edge neighbors
    Given a column with ENG-12 and ENG-15 where the highest board_order is 2048
    When POST /_/issues/ENG-13/move is requested with {"state_id":"<id>","before":"ENG-15","after":null}
    Then the status is 204
    And the board_order of ENG-13 is 3072.0

  Scenario: moving to a completed state with open children returns 422
    Given an issue ENG-13 with two open sub-issues
    When POST /_/issues/ENG-13/move is requested with the state "Done"
    Then the status is 422
    And the board_order of ENG-13 did not change

  Scenario: the rebalance is done by the server without the client asking
    Given a column with board_order 1024 and 1024.00001
    When POST /_/issues/ENG-14/move is requested with {"state_id":"<id>","before":"<ENG-12>","after":null}
    Then the status is 204
    And the board_order values of the column are multiples of 1024

  Scenario: the board renders Todo before Backlog
    Given a team ENG created with the canonical states
    When GET /ENG/board is requested
    Then the status is 200
    And the columns appear in the order "Triage", "Todo", "Backlog", "In Progress", "Done", "Canceled"
    And every column carries a data-state-id and a data-position attribute

  Scenario: dropping a column persists the new position
    Given a team ENG with Todo at position 2.0 and Backlog at position 3.0
    When POST /_/states/<todo id>/position is requested with {"before":"<backlog id>","after":"<in progress id>"}
    Then the status is 204
    And the body is empty
    And the HX-Trigger header contains "board-reordered"
    And the position of Todo is greater than 3.0 and less than 4.0
    And Backlog keeps its position

  Scenario: reordering the columns moves no issue
    Given a team ENG with 4 issues in Todo and 3 in Backlog
    When POST /_/states/<backlog id>/position is requested with {"before":null,"after":"<triage id>"}
    Then the status is 204
    And the state of the 4 issues is still Todo
    And the state of the 3 issues is still Backlog
    And the board_order of every issue did not change
    And no row was added to issue_history

  Scenario: dropping a column in read-only mode is forbidden
    Given the app created with read_only true
    When POST /_/states/<todo id>/position is requested with {"before":"<backlog id>","after":null}
    Then the status is 403
    And the body contains "server in read-only mode"
    And the position of Todo did not change

  Scenario: an unknown state on the column drop is a 404
    Given a team ENG
    When POST /_/states/<unknown id>/position is requested with {"before":null,"after":null}
    Then the status is 404

  Scenario: a column neighbour from another team is a 422
    Given teams ENG and OPS
    When POST /_/states/<ENG todo id>/position is requested with {"before":"<OPS backlog id>","after":null}
    Then the status is 422
    And the position of the ENG state did not change

  Scenario: removing a label returns 200 with an empty body
    Given an issue ENG-13 with the label "bug"
    When DELETE /_/issues/ENG-13/labels/<bug id> is requested
    Then the status is 200
    And the body is empty

  Scenario: the palette without text returns actions only
    Given the app running
    When GET /_/palette?q= is requested with HX-Request true
    Then the status is 200
    And the body contains "Create issue"
    And the body contains no issue identifier

  Scenario: the palette searches issues by title
    Given an issue ENG-13 with title "Fix SSO login"
    When GET /_/palette?q=login is requested with HX-Request true
    Then the body contains "ENG-13"

  Scenario: creating without JavaScript redirects to the detail
    Given a team ENG
    When POST /ENG/issue is requested with form title "New" and without HX-Request
    Then the status is 303
    And the Location header is "/ENG/issue/ENG-1"

  Scenario: the assets respect the budget
    Given the src/hifa/web/static tree
    When the gzip sizes of js, css and svg are summed
    Then the total is less than or equal to 76800 bytes

  Scenario: the CSP does not allow eval
    Given the app running
    When GET /ENG/board is requested
    Then the Content-Security-Policy header contains "script-src 'self'"
    And the header does not contain "unsafe-eval"

  Scenario: markdown is rendered with markdown-it-py and an in-house allowlist
    Given an issue with description "**bold** and <img src=x onerror=alert(1)>"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains "<strong>bold</strong>"
    And the HTML does not contain "onerror"

  Scenario: markdown is sanitized before rendering
    Given an issue with description "<script>alert(1)</script> and [x](javascript:alert(1))"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML does not contain "<script>alert(1)</script>"
    And the HTML does not contain "javascript:alert(1)"

  Scenario: an attachment with a dangerous scheme generates no link
    Given an issue with an attachment url "javascript:alert(1)"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML does not contain an href "javascript:alert(1)"
    And the attachment title is shown as text

  Scenario: read-only renders no write forms
    Given the app created with read_only true
    When GET /ENG/list is requested
    Then the status is 200
    And the HTML contains no form with method "post"

  Scenario: the row measures 32 px
    Given the app served and a browser with a 1280x800 viewport
    When the bounding box of the first row of /ENG/list is measured
    Then the height is 32 px

  Scenario: the revision probe keeps the htmx guard
    Given the app running
    When GET /_/revision is requested without the HX-Request header
    Then the status is 400
    And the body is plain text

  Scenario: the revision probe is stable when nothing changed
    Given the app running
    When GET /_/revision is requested twice with HX-Request true and no write in between
    Then both answers are 200
    And the Content-Type of both is "application/json"
    And both bodies carry the same "revision" number
    And the Cache-Control header contains "no-store"

  Scenario: the revision changes after a write from another connection
    Given the app running and the revision read from /_/revision
    When "hifa issue add" creates an issue in the same database from another connection
    And GET /_/revision is requested again on the same process
    Then the status is 200
    And the revision differs from the one read before the write

  Scenario: a page carries the revision it was rendered at
    Given a team ENG with 4 issues
    When GET /ENG/board is requested
    Then the status is 200
    And the body carries a data-revision attribute
    And the HX-Revision header carries the same number as that attribute
    And the body carries a data-refresh URL for "/_/issues?view=board"
    And the poller's hx-trigger carries the interval of REVISION_POLL and the visible-tab condition

  Scenario: read-only mode does not change the probe
    Given the app created with read_only true and one issue ENG-13
    When GET /_/revision is requested twice with HX-Request true
    Then both answers are 200 with a "revision" number
    And the two numbers are equal
    And the row count of the issue table did not change

  # The four scenarios below are manual on this host: no Playwright (the drag & drop debt).
  Scenario: no swap while the revision did not change
    Given a browser with /ENG/board open and nothing writing to the database
    When one polling interval elapses
    Then the page requested /_/revision once
    And the page requested /_/issues no times
    And the columns are the ones it already had

  Scenario: the board reloads when the revision changed
    Given a browser with /ENG/board open
    When "hifa issue add" creates an issue ENG-14 from another connection and one interval elapses
    Then the page requested /_/issues?view=board once
    And the card of ENG-14 is on the board
    And the scroll position of the page did not change

  Scenario: the refresh waits for the open panel and applies when it closes
    Given a browser with /ENG/board open and the panel of ENG-13 open
    When a CLI write commits and two polling intervals elapse
    Then the panel of ENG-13 is still open
    And the page requested /_/issues no times
    And closing the panel reloads the board at once, without waiting for the next interval

  Scenario: a drag in flight defers the swap
    Given a browser with /ENG/board open and a card being dragged
    When a CLI write commits and two polling intervals elapse
    Then no fragment was swapped under the pointer
    And the drop completes against the position the server has
    And the board reloads after the drop's request returns
```

## 8. Regeneration checklist

1. Read `SPEC-000`, `SPEC-010` and this spec. Do not read `src/hifa/web/`.
2. `web/app.py`: `APIRouter` with the routes of §2.1 — `GET /_/revision` included, as a
   `JSONResponse` behind the same `require_htmx` dependency and with `Cache-Control: no-store`
   (§2.1.3.1) — plus the constant `REVISION_POLL` of §2.6.8, the one long-lived read-only
   connection of §3 and its `lifespan` shutdown; `StaticFiles` at `/static`; `Jinja2Templates`
   with `autoescape=True` (default in `.html`) and no custom filters.
3. `web/views.py`: one function per route that (a) resolves team/ref, (b) calls `app/*`, (c) builds
   the read model, (d) renders. Zero Jinja with logic.
4. `web/readmodels.py`: pure functions `build_board_vm(board, filters, counts) -> dict`,
   `build_list_vm(page, ...)`, `build_issue_vm(...)`. Unit test for each one with sample data
   (they are dicts, compared with `==`). The template receives `"vm"`, never the `app/` object.
5. Download and vendor the 3 assets of §2.4 pinning version and `sha256` in
   `static/vendor/README.md`; do **not** add `package.json`.
6. `css/app.css` with the tokens of §2.5.1 as custom properties in `:root`; 32 px rows; no remote
   `@import`.
7. `js/app.js`: `Alpine.data('issueRow', …)`, `htmx:beforeSwap` listener for 422, the shortcuts of
   §2.6.2, `Sortable.init` with the neighbor payload of §2.6.4 and the poller of §2.6.8 (compare,
   adopt `HX-Revision`, honour the four blocking states, one pending value). **No** `fractional()`,
   no position arithmetic, no arithmetic on the revision, no `eval`, no `new Function` (CSP).
7b. `web/markdown.py`: `render_markdown` with `markdown-it-py` + in-house allowlist (§2.3.6), and
   `markdown-it-py` declared in the base group of `pyproject.toml` (D-020).
8. Templates of §2.2 — start with `base.html` with the 5 blocks and the `<noscript>`.
9. `pytest tests/e2e/web -q` green (49 scenarios of §7). The four scenarios marked manual in §7, the
   drag & drop and the 32 px row are checked by hand in a browser: this host has no Playwright.
   Then `python scripts/spec_contract_check.py --spec specs/070-web-dashboard.md` (compares
   registered routes against §2.1).
10. Serve and verify by eye: `hifa serve`, open `/ENG/board`, disable JS and check that you can
    still create, edit and move an issue.

## 9. Provenance

- htmx + Alpine + Jinja without a build: author's decision, 2026-11-08, over the React/Vite
  alternative (which would require `node` and ~40 MB of `node_modules` to render a 50-row table).
- Asset weights: `htmx.min.js` 2.0.4 = 15.8 KB gzip, `alpinejs` 3.14.8 CSP = 15.9 KB gzip,
  `Sortable.min.js` 1.15.2 = 10.2 KB gzip (measured with `gzip -9`), not estimated.
- Palette and density: a dark UI with 32 px rows and a 220 px sidebar is the reference Ginkelte
  declared; the hex values are our own and the contrasts are measured (15.7:1 / 6.1:1 / 8.0:1).
- `Alpine CSP build` instead of the normal build: the normal one uses `new Function`, incompatible
  with `script-src 'self'`; the alternative (CSP with `unsafe-eval`) was discarded because of
  REQ-S003.
- Keyset in the HTML list too: same reason as in SPEC-060 (offset duplicates rows when inserting).
- Drag & drop with a fractional position **computed on the server**: the SPEC-010 D5 schema already
  stores `board_order REAL` and SPEC-040 already has the calculation (`ranking.between`) and the
  rebalance (REQ-F040-08). The client sends neighbors because the prototype showed precision
  collapse (after 52 insertions in the same gap, `(a+b)/2 == a` in float64) and duplicating that
  calculation in JS gave two sources of truth for the same float (D-012).
- `/<team>/…` in the URL and no state in a cookie: shareable and `curl`-able links, aligned with
  REQ-N006 (read and navigate without JS).

## 10. Out of scope

- Light theme, `prefers-color-scheme`, configurable high contrast.
- Collaborative editing, remote cursors, presence.
- Push channels — SSE, websockets, long polling — and any sync protocol: the change signal is one
  polled number (§2.6.8, D-049).
- Calendar view, timeline, roadmap, cycles as their own page.
- List virtualization (>500 rows): keyset pagination of 50 solves the local case.
- File uploads (attachments are links, SPEC-000 §1).
- Printing/PDF export of the board, offline mode with a service worker.
- Full WCAG AA certified accessibility: visible focus, palette roles and measured contrast are
  covered, not a formal audit.
- Dedicated mobile view: the UI responds at 1280×720 and at 390 px degrades to a single column
  without guarantees.

## 11. Notes

- spec_revision 14 (2026-11-13): **the page notices a write from another connection, and `version` 3
  because the public surface gains a route (D-049).** A page was a snapshot: it showed what the file
  had when it loaded, while the CLI — the second writer D-030 already accepted for the sidebar — kept
  committing under it. The contract is a **change signal, not a timer**: §2.1.3 gains
  `GET /_/revision`, the only route of the layer that answers JSON, and the exception is declared in
  §2.1.3.1 together with its `Content-Type`, its `Cache-Control: no-store`, the `HX-Request` guard it
  keeps (R5) and the reason it is an exception instead of a route outside `/_/`. The number is
  `PRAGMA data_version` on **one long-lived read-only connection per process** (§3), because
  `data_version` is per connection and a connection per request — what the edge does today — always
  answers the same value; the in-process counter and `max(updated_at)` are discarded with their
  missing case written out (D-049). §2.6.8 states the polling contract as a contract: the interval is
  one constant (`REVISION_POLL`, surfaced as `page["revision_poll"]`), the trigger is
  `every …` with the visibility guard in the client, the reload happens **only** on a different
  number, the page knows the number it was rendered at (`<body data-revision>`) and adopts the one
  every response returns (`HX-Revision`), and the do-not-interrupt rule defers the swap while a panel
  is open, the quick add holds text or a drag is in flight. R26 carries the rule, R12 stops saying
  that `/_/counts` is the only poll, §5.6 carries the flow, §6 declares the two ways the probe can
  fail and stops claiming that every error of this layer is HTML, §7 grows nine scenarios (four of
  them manual: no Playwright on this host). `version` 3 follows `specs/README.md`:37-90 — a contract
  change bumps it — and the precedent of SPEC-050 rev 19, where two new leaf commands made the public
  surface and bumped it; rev 9 added a route without bumping and that is the inconsistency, not the
  rule.
- spec_revision 13 (2026-11-13): **a refused drop answers with the class SPEC-020 raises, so `version` 2
  (D-042).** The board publishes the error map of SPEC-040 §6: a move to a terminal state with open
  sub-issues is `Validation` ⇒ 422 with the same board reload and toast it already had (§5.4 step 4, §6 and
  the §7 scenario), while `InvalidTransition` stays 409 for its only producer — a drop on an issue whose
  `archived_at` is not null (SPEC-040 R20), named as its own row in §6.
- spec_revision 9 (2026-11-11): **the board columns can be dragged, and the order is persisted
  (D-034).** New §2.6.5 and §5.5 add one fragment, `POST /_/states/{state_id}/position`, with the
  same `HX-Request` guard as the rest of `/_/*`, a payload of **state ids** (`{"before", "after"}`,
  `null` at the edge), `204` + `HX-Trigger: board-reordered` and no swap — Sortable has already
  moved the node, so the server only persists `workflow_state.position` through
  `update_state(UpdateState(position=…))`, with the float coming from `domain.ranking.between`.
  `--read-only` ⇒ `403`, unknown state ⇒ `404`, neighbour from another team ⇒ `422` (§6, R25).
  Columns now emit `data-state-id` and `data-position`, and `board_vm` exposes them per group as
  **additive** fields (D-026), so the client knows what to send and what it was rendered with.
  The wireframe of §2.5.4 and the canonical seed of SPEC-100 §2.5 both read
  `Triage · Todo · Backlog · In Progress · Done · Canceled`: `Todo` moved to the left of `Backlog`
  because for the user `Backlog` is "some day" and `Todo` is "the next thing". The drag **only**
  changes the presentation order — no issue changes state, no `board_order` moves, no
  `issue_history` row is written — and an existing team is reordered by dragging or with
  `hifa state update` (SPEC-050 §2.5.6), never by a migration (D-034).
- spec_revision 8 (2026-11-11): **R24 now describes the root the code actually has (D-031).** The
  previous R24 (and §2.1.1, §2.3.8 and two scenarios) built the picker on "2+ teams and **no**
  `default_team`", a state `config.py` cannot produce: it always resolves `[cli].default_team`, falling
  back to `[team].key`, which defaults to `DEFAULT_TEAM_KEY`. The canonical picker case is a default
  that names a team **absent from the database** (a second team created under another key, a
  regenerated database, a `.hifa.toml` from another workspace) with 2+ teams present; the secondary
  one is `create_app(default_team=None)`, reachable only by embedding or tests, and it is why the page
  still carries `reason = "no_default_team"`. The scenario set grows to four cases (redirect by
  default with several teams, fallback to a single team, picker on a stale default, picker with no
  default at all), and the prohibition on choosing by `created_at`, alphabetical order or cookie is
  unchanged.
- spec_revision 7 (2026-11-11): **a team created from the CLI reaches the dashboard without a
  restart, and `GET /` never guesses which team you meant (D-030).** (1) §2.1.1 and the new R24
  replace the `404` of the ambiguous root with a **`200` picker**: with two or more teams and no
  `[cli].default_team`, `team_picker.html` (§2.3.8) lists every team with its name, key and issue
  count and links to `/{team}/board`. A single team still redirects; nothing is ever chosen by
  `created_at`, alphabetical order or cookie. (2) The new R23 makes the sidebar a **per-request
  read** (§2.1.4: `app.teams.list_teams(db)` on every request, never `app.state.teams` captured in
  `create_app`), so `hifa team create` against a running `hifa serve` shows up on the next request —
  the process no longer owns a snapshot of a file another writer can change. (3) §2.3.1 declares
  `issues_count` and `active` on `page["teams[]"]` as **additive keys with a `0`/`False` default**
  (D-026), which is what lets the sidebar and the picker render the count without any logic in Jinja;
  with one team the block renders exactly as before.
- spec_revision 6 (2026-11-11): **the panel pays for its comments (D-029) and `|default` is
  enforced, not just disallowed.** (1) §2.1.4 stops claiming that `…/panel` reads the base
  `IssueDetail`: the panel renders the comment list (§2.3, `issue_panel.html`), so its route passes
  `include=app.boards.DETAIL_INCLUDE`; `/_/issues/{id}/row` keeps the empty `include`. The constant
  is the one that fixes the set, the board and the rows stay collection-free, and the extra read
  happens in an explicit user action (D-029). (2) R3's ban on defensive `default()` is now checked:
  the static template test rejects `|default(` as well as `|length`, and the quick-add form and the
  rows fragment receive `form_oob`, `form_error` and `form_errors` from every route that renders
  them, so a missing key breaks the test instead of silently rendering the wrong HTML.
- spec_revision 12 (2026-11-12): the board instance sets `preventOnFilter: false`, so a touch that
  lands on a card still scrolls the page (§2.6.5).
- spec_revision 11 (2026-11-12): a gesture that moves never opens a panel (§2.6.7), and on a
  phone the panel is a full-width sheet whose heading stays put so its `Close` control does
  not scroll away (§2.5.6).
- spec_revision 10 (2026-11-12): the board declares its phone layout (§2.5.6) and the column drag
  declares its touch options (§2.6.5). One column per row below `900px`, so the page is the only
  vertical scroller and there is no width to pan at 320 px; `delay`/`touchStartThreshold` keep a
  swipe a scroll and make a column drag require a long press.
- spec_revision 5 (2026-11-11): **the dashboard's contract, closed against SPEC-030 and SPEC-040
  (D-025, D-026, D-027), plus `/healthz` (D-028).** (1) §2.1.1 states that `q` on the board and on the
  list is a **title filter** (`LIKE '%term%'` over `title`, `ESCAPE`, case-insensitive) and not the
  search grammar of SPEC-090, which stays on `/search` and `/_/palette` (D-025). (2) §2.1.4 now calls
  `list_board(db, *, team=key, filters=ListFilters(...), ...)` — filters travel in one object, never
  as the six loose kwargs the implementation had introduced — and the detail route passes the public
  constant `app.boards.DETAIL_INCLUDE`, which is deliberately not the default of `include` (D-027).
  (3) The fields this spec's templates read from the read models (`state_color`, `sort_order`,
  `board_order`, `created_at`, `BoardGroup.color`, `BoardGroup.truncated`) are declared in SPEC-030
  §2.6 as additive and defaulted (D-026): nothing of it changes here, the templates keep reading the
  same names. (4) New §2.1.6 declares `GET /healthz` — liveness, `{"status":"ok"}`, no database — and
  explains why it is not a duplicate of `/health` of SPEC-060 §2.5.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
- **r3 (`spec_revision: 3`, 2026-11-08, Fix Agent, spec-review-03 r2 §5) — port regressions
  closed against SPEC-040 (D-012).** (1) `list_board` and `issue_detail` replace the previous
  round's names in §2.1.4, §2.1.5 and §5.1; the detail passes `include=` for comments, attachments
  and history, and fragments request the base `IssueDetail`. (2) The drag & drop payload is
  translated into `MoveIssue(..., before_id=..., after_id=...)` in §2.1.4 and §2.6.4: no scalar
  anchor remains. (3) `IssuePage` is read through `items` (owner SPEC-030 §2.6). (4) `GET /`
  resolves the team by `[cli].default_team` (SPEC-100 §2.3) or a single team, never by cookie or by
  `created_at`. (5) Minor: `default` is removed from the allowed Jinja filters (§2.3, R3), the
  wireframe uses `Triage` instead of a nonexistent state, the pagination scenario verifies
  `cursor=` in the href, and `snippet_html` joins the `|safe` allowlist (R15/§2.3.7) so that the
  `<mark>` from SPEC-090 survives autoescape.
- **r2 (`spec_revision: 2`) — review blockers closed.** (1) Routes stop invoking nonexistent
  functions: §2.1.4 maps each route to `list_board` / `list_issues` / `issue_detail` /
  `SearchService.search` / `move_issue` / `reorder_issue` / `update_issue` / `create_issue` /
  `archive_issue` / `add_label` / `remove_label` / `comment_on_issue`, and §2.1.5 separates read
  models and client JS from what really is a port. (2) `fractional()` and the `sort_order` field were
  removed from the drag & drop payload: the client sends `{state_id, before, after}` and the
  position is computed by `move_issue`/`reorder_issue` on the server (R9/R10, §2.6.4). (3) Markdown:
  `markdown-it-py` + in-house allowlist in §2.3.6 and in `implements: pyproject.toml` (D-020, base
  group). (4) Pagination aligned with D-011 (R22, §2.3.3): cursor `base64url(sort_key|id)`, `limit`
  1-200/50, `limit+1` → `has_more`, `offset` 0-500 only in infinite scroll and exclusive with
  `cursor`.
- **Drift fixed along the way:** R21 pointed at `app.search_issues` (does not exist in SPEC-040); it
  now uses `SearchService` from SPEC-090, which is the authority for the search contract (D-016).
- **Pending for another round:** the board does not paginate (`limit_per_group` +
  `truncated`/`more_url`); enabling infinite scroll per column requires a new decision, because
  D-011 only allows `offset` in the list.
