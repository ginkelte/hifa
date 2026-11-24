---
id: SPEC-070
title: Web dashboard — Jinja views, htmx fragments, assets and command palette
component: entry
status: review
version: 13
spec_revision: 45
depends_on: [SPEC-000, SPEC-010, SPEC-040, SPEC-060]
implements:
  - src/hifa/web/**
  - tests/e2e/web/**
  - pyproject.toml
acceptance: specs/acceptance/001-mvp-v1/16-dashboard.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-24
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
| `GET /{team}/issue/{identifier}` | — | `issue_detail.html` | full detail + comments + history; a **trashed** issue too — `200` with `Restore` as the only control (§2.1.1 below, D-059) |
| `GET /search` | `q` (optional), `team` (optional) | `search.html` | workspace-wide results, or the page's empty, no-results and error states (§2.1.8, D-063) |
| `GET /{team}/issue/{ulid}` | — | — | `303` to the canonical URL with `identifier` |

`{team}` is the `team.key` (case-insensitive in the route, normalized to uppercase in links).
A nonexistent `{identifier}` ⇒ `404` with `error.html` (same layout, message + link to the list).
A nonexistent `{team}` ⇒ `404`. `/search` without `q` is **`200` with the page's empty state**, not a
`400`: the sidebar links to `/search` from every page and a link that answers an error is the dead end
the page exists to remove (§2.1.8, D-063).

**An issue in the trash is a page, not a `404`, and exactly two routes serve it (D-059).**
`GET /{team}/issue/{identifier}` and `GET /_/issues/{identifier}/panel` answer **`200`** for an issue
the trash layer holds, and each renders the issue with **`Restore` as its only control**: no edit form,
no `name="state_id"` control, no archive block, no delete block. The `GET /{team}/issue/{ulid}` alias
follows the rule it already has and answers `303` to the canonical identifier URL, because an alias that
`404`s where its canonical URL answers `200` is two URLs disagreeing about one row. **No other surface of
this layer looks through the trash**: the board, the list, the search page, the palette, the counters and
the `row`/`card` fragments of §2.1.3 keep the read's default and never return a trashed row, so those two
fragments answer `404` for exactly the identifier whose page answers `200` — they are collection members,
and a `<tr>`/`<article>` for a row no collection contains is not a fragment this layer has. The read behind
the two routes is `issue_detail(..., include_trashed=True)` (SPEC-040 §2.2, R21), and the page and the panel
are its only consumers: the asymmetry — page and panel pass the keyword, row and card do not — is the one
thing an implementer can get wrong. The reasoning, the rejected `/trash` page and the price (the trash is
reachable by identifier and enumerated nowhere in the browser) are D-059's.

**`q` on the board and on the list is a title filter, not search (D-025).** The box above the board
and above the list sends `?q=`; the server translates it to a `LIKE '%term%'` over the issue **title**
(SPEC-030 §2.6, `ListFilters.q`), with the term's `%`, `_` and `\` escaped and case-insensitive. It is
not the search grammar of SPEC-090: no `field:value`, no negation, no ranking, no description or
comment matching. Workspace search is `GET /search` (and `/_/palette`), which is **a different route
with a different contract** (SPEC-090 §2). An empty or blank `q` means "no filter" and never a `400`
here; `/search` does not answer `400` for an empty `q` either — it renders its empty state (§2.1.8,
D-063), and the only `400` of this layer is the `/_/` family without `HX-Request` (R5). The cross-site
write guard answers **`403`** (R28, §6), not `400`, and the two are different refusals: a missing header is
a malformed client, an origin from elsewhere is a refused one.

#### 2.1.2 Forms (full page, they work without JS)

One form per write — and for the field edit, one form **per field**, seven on each detail surface (§2.6.3) —
and every one of them has a fragment counterpart in §2.1.3. With htmx the form on a
fragment-bearing surface posts **to its fragment** (`hx-post`/`hx-patch`); the page route is the no-JS
path (R4). The detail page's four writes that are not the field edit — `move`, `archive`, `delete`,
`restore` — carry `method`/`action` and no `hx-*`: they are the no-JS path by design, and the panel is
where those writes go out as fragments. The **field** forms are the exception and carry both, on both
surfaces: an inline control is one `<form method="post" action="/{team}/issue/{identifier}">` inside its
own field wrapper, with the `hx-patch` to §2.1.3's `?view=field` beside it (§2.6.3), so the same node is
the no-JS submit and the htmx one. A page route
is never the `hx-*` target of a fragment: its answer is a `303`, and a redirect followed inside an
htmx request swaps a whole document into the element the fragment was meant for.

| Route | Body (form-urlencoded) | Without JS | The fragment that answers with htmx |
|---|---|---|---|
| `POST /{team}/issue` | `title`, `description?`, `state?`, `priority?`, `assignee?`, `due_date?` | `303` to `/{team}/issue/{identifier}` | `POST /_/issues` (§2.1.3) |
| `POST /{team}/issue/{identifier}` | any subset of `title`, `description`, `priority`, `state`, `assignee`, `due_date`, `estimate`; absent = no change | `303` to the detail | `PATCH /_/issues/{identifier}?view=field&field={name}` (§2.1.3, §2.6.3) — one control per form — and `?view=list\|board` for the row and the card; `?view=panel` is the aside a layer refusal re-renders, and no request of this layer names it (§2.1.3) |
| `POST /{team}/issue/{identifier}/move` | `state_id` (required), `before?`, `after?` | `303` to the detail | `POST /_/issues/{identifier}/move` (`204`) |
| `POST /{team}/issue/{identifier}/comment` | `body`, `parent?` | `303` to the detail (`#comments`) | `POST /_/comments/{identifier}` |
| `POST /{team}/issue/{identifier}/archive` | `confirm=1` (required) | `303` to `/{team}/list` | `POST /_/issues/{identifier}/archive` (`204`) |
| `POST /{team}/issue/{identifier}/delete` | `confirm=1` (required) | `303` to `/{team}/list` | `POST /_/issues/{identifier}/delete` (`204`) |
| `POST /{team}/issue/{identifier}/restore` | — | `303` to the detail | `POST /_/issues/{identifier}/restore` (`204`) |

**The redirect names the page that shows the new truth.** A field edit, a move and a restore answer
`303` to `/{team}/issue/{identifier}`, because the issue is still where the reader was. An archive and
a delete answer `303` to `/{team}/list` for the one reason they share — **the issue has just left every
listing**, so the list is the page whose contents the write changed. The two differ in what the detail
answers, and neither answer is a `404`: after an **archive** the issue is out of the listings and in no layer
of the trash, so the detail answers `200` and carries the action group whose single control is `Restore`
(§2.6.9), which is where the no-JS reader undoes the write; after a **delete** the issue is in the trash, so
the detail also answers `200` (D-059, §2.1.1) but it is the trash's page — the issue, `Restore`, and nothing
else — and not the report of a deletion. Neither row sends the reader to the detail, because the detail would
show the issue instead of the effect the write produced (the `404` this paragraph used to predict was true of
a delete and of nothing else, and D-059 retires it there too).

**`confirm=1` is the web form of the CLI's `--yes` (P6).** `archive` and `delete` are the two writes
that take an issue out of every listing, and both refuse a body without the field with `422`
`confirmation_required` and the form re-rendered with the confirmation block (§2.6.9) — the same
refusal `hifa issue archive REF --no-input` produces with exit `2` (SPEC-050 §2.5.3). `restore` never
asks, because undoing is not destructive (SPEC-050 §2.5.3). `archive` and `restore` are **two routes**:
there is no `undo` field, because a capability reachable only through the body of another capability's
route cannot be declared, counted or mapped on its own (D-053).

**The confirmation is this edge's own precondition, and no rule of the core knows it exists.** `confirm=1`
is a field of a form, so its absence is decided where the form is read: the route refuses the body before
any use case is called, opens no transaction, writes nothing, and the sentence it answers with comes from
§6's one builder like every other `form_error`. There is no `Validation` rule, no error class and no
handler for it, and `confirmation_required` names this form contract and nothing else. `ArchiveIssue` and
`TrashIssue` take an `issue` reference and no `confirm` (SPEC-040 §2.2). The two edges therefore declare
the same precondition once each — the CLI as `--yes`/`--no-input` with the TTY prompt of SPEC-050 R6, the
dashboard as this required field — over a capability set neither invents: `archive` and `delete` are the
destructive writes of SPEC-050 §2.5.3, the surface P1 makes the reference. Guarding it in `app/` instead
was rejected with its price stated: a `confirm` on the command moves a UI affordance into SPEC-040 §2.2
and bumps `version` in both specs for a field that guards nothing about the resource; it gives the CLI a
second gate on top of the prompt it already asks; and it turns the CLI's documented refusal — `hifa:
operation canceled`, code `confirmation_required`, exit `2` (SPEC-050 §6) — into the `validation_error`
that every `Validation` prints, which is a public regression of a documented output rather than a
clarification of it.

**Two names for two things, and neither of them is a position.** `state` on a field form is a
*reference* to a workflow state (a name or a ULID, resolved like `hifa issue edit -s`), and setting it
appends the issue to the target column (SPEC-040 §2.2, `UpdateIssue`). `state_id` on the move form and
on the drop payload is *the column the issue was put in*. No body of this layer carries `position`,
`sort_order`, `board_order`, `rebalance` or a neighbour index (R9): the float is computed in `app/`,
inside the transaction, or it is not computed at all.

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
| PATCH | `/_/issues/{identifier}?view=list\|board\|panel\|field` | the updated row (`view=list`), card (`view=board`), panel (`view=panel`) or **one field wrapper** (`view=field`, §2.6.3) | `#issue-{identifier}` / `#card-{identifier}` / `#panel` / `closest .field-inline` | `outerHTML` for `list`, `board` and `field`, `innerHTML` for `panel` | inline-edit blur/Enter, select change, the description's `Save` and `Cmd/Ctrl+Enter` (§2.6.3) |
| POST | `/_/issues` | new `<tr>` | `#issues-tbody` | `afterbegin` | quick-add submit |
| POST | `/_/issues/{identifier}/move` | `204` with no body | — | `none` | sortablejs drop (§2.6.4); the panel footer's `<select>` + `Move` (§2.6.9, §5.7.1) |
| POST | `/_/issues/{identifier}/archive` | `204` with no body | — | `none` | panel action, with `confirm=1` (§2.6.9) |
| POST | `/_/issues/{identifier}/delete` | `204` with no body | — | `none` | panel action, with `confirm=1` (§2.6.9) |
| POST | `/_/issues/{identifier}/restore` | `204` with no body | — | `none` | panel action |
| POST | `/_/states/{state_id}/position` | `204` with no body | — | — | column drop (2.6.5) |
| POST | `/_/issues/{identifier}/labels` | `<span class="chip">` chip | `#labels-{identifier}` | `beforeend` | picking a label |
| DELETE | `/_/issues/{identifier}/labels/{label_id}` | `200` empty body (htmx removes the node) | the chip | `outerHTML swap:120ms` | click on the chip's `×` |
| GET | `/_/comments/{identifier}` | the whole `<ol id="comment-list">` of §2.6.10 | `#comment-list` (the `<ol>`) | `outerHTML` | refresh |
| GET | `/_/issues/{identifier}/history?limit={n}` | the whole `<ol id="history-list">` of §2.5.7, without the `Show all` control | `#history-list` (the `<ol>`) | `outerHTML` | `Show all N` click (§2.5.7, D-C) |
| POST | `/_/comments/{identifier}` | one `<li>` with `HX-Trigger: comment-created` — `200`, not `204`, because the element is the answer | `#comment-list` (the `<ol>`) | `beforeend` | composer submit |
| GET | `/_/palette?q=` | `<ul role="listbox">` of results | `#palette-results` | `innerHTML` | `input` with `delay:120ms` |
| GET | `/_/counts?team={team}` | `<span>` of counters | `#counts` | `outerHTML` | `every 30s` |
| GET | `/_/board/{team}` | full board (recovery) | `#board` | `innerHTML` | after a drop error |
| GET | `/_/revision` | `{"revision": <n>}` — **JSON, the only non-HTML body under `/_/`** (§2.1.3.1) | — | `none` (the body is read, never inserted) | `every 5s` while the tab is visible (§2.6.8) |

**The four writes with no body, and the one `view` value that answers the panel.** `move`, `archive`,
`delete` and `restore` answer `204` with an empty body and `HX-Trigger`, because the element that asked
is about to disappear (a card that left its column) or is the panel itself; a body would have to name
the node to remove, which is the client deciding what the collection contains. The collection is
re-read instead, from the fragment that renders it (`page["refresh_url"]`, §2.6.8), so the rows the
reader sees are the server's (D-054). In these four rows `—` in the `hx-target` column means the form
carries **no** target, and `none` in `hx-swap` is the attribute the rendered forms carry: a `204` has no
body to put anywhere, and the confirmation forms of §2.6.9 render exactly these two cells.

**Four values of `view`, four elements, and each names the node its caller stands on.** `view=list` and
`view=board` are unchanged: target the row or the card, swap `outerHTML`, body the `<tr>`/`<article>`.
`view=field` answers **one field wrapper** — the `<div class="field-inline" id="field-{name}-{identifier}">`
of §2.6.3 — and it is the answer of every editable field on the detail page and on the panel, because a
`PATCH` there must not re-render the element the reader is not looking at: the target is
`closest .field-inline`, the swap `outerHTML`, and the body the wrapper of §2.6.3 in its value state (or,
on a refusal, in the state §2.3.4's table gives it: the control `open` and marked for a `422`, the value
and the message with no disclosure for an archived issue's `409`). `view=field` names the field it answers
in a second query parameter, `field={name}` — `title`, `description`, `priority`, `state`, `assignee`, `due_date` or
`estimate` (the controls of §2.1.2) — because the body carries the value and not the field's identity: a
`422` for an empty value has a body of `title=` and nothing in it says which wrapper to answer. A `field`
that names none of the seven is a resolution that failed, and it is answered with the one body this layer
has for that: `404`, `partials/fragment_error.html`, `HX-Retarget: #toasts` and `HX-Reswap: beforeend`
(§6) — the partial is appended to the toast container, which is the value `_fragment_error` already
emits (`app.py:280`), not a replacement of its content. There is no wrapper to keep and no second media
type — the sentence inside the partial names the field, and the distinction between an unknown issue and an
unknown field is the message's, not the response's. `view=panel` is the whole `<aside>` with `innerHTML` into
`#panel`, and **it has no HTTP
client of its own any more**: the refusals of the layer writes (§2.6.9, §5.7) render that same aside
server-side — a `move`, an `archive`, a `delete` or a `restore` the domain refuses answers through
`_layer_refusal`, which builds the aside with `_issue_edit_fragment(..., view="panel")` in `app.py` — so
the body is produced by this value without a request that names it. The value stays reachable as
`PATCH /_/issues/{identifier}?view=panel`, the request §7's `the panel edit answers the panel` pins, and
no template of this layer sends it: the panel's pooled form posted it and the description editor answered
it, and both are gone (§2.6.3, D-080), so no successful write of the UI re-renders the aside.

**One row of this table is withdrawn with it: `GET /_/issues/{identifier}/field/{field}`.** It declared a
fragment that answers "the edit control for one field" on a click, and nothing in the tree ever served it:
no route under `src/hifa/web/app.py`, no template, no test. It is withdrawn rather than implemented because
the control state is **in the document** inside the field's `<details>` (§2.6.3): a click that fetched it
would leave a reader without JavaScript with a value that never opens, and a fragment that computes three
strings the same template already prints is a second rendering of one contract. The reveal is the
browser's, and this table has one row fewer.

**A refusal keeps the element.** A layer write that the domain refuses (`422`/`409`, §6) answers with
the **panel re-rendered** from the read model plus `form_error`, so the form stays where the reader is
and the message appears next to it; the target of those answers is `#panel` like the success of an edit.
A layer write never answers JSON, and never a bare status code with no body when the reader needs to
know why.

Headers the server must read or emit:

| Header | Direction | Use |
|---|---|---|
| `HX-Request: true` | input | if missing on a `/_/*`, respond `400` (`fragment requires htmx`) |
| `HX-Trigger` | output | events of §2.6.4/§2.6.5, of the layer writes and of the comment write (`issue-created`, `issue-updated`, `issue-moved`, `issue-archived`, `issue-deleted`, `issue-restored`, `board-reordered`, `comment-created`) |
| `HX-Retarget` | output | on a refusal that has **no element to keep**: `#toasts`, **with `HX-Reswap: beforeend`** (the value `_fragment_error` emits today, `app.py:280`), for a `/_/` answer of `partials/fragment_error.html` — the `404` of a `?view=field` naming no control of §2.1.2, a write in read-only mode (§6) — because the fragment is appended to the toast container and must not inherit the target the requesting control declared; never `closest form`, and never the wrapper a `view=field` `422`/`409` already answers in place; and `#panel` with `HX-Reswap: innerHTML` for the confirmations of §2.6.9, whose form posted with `hx-swap="none"` |
| `HX-Push-Url` | output | when opening the panel: pushes `/{team}/issue/{identifier}` |
| `HX-Reswap` | output | `none` when the fragment only updates counters |
| `HX-Revision` | output | the number the request observed (§2.6.8) — for a write, the number read **after** it committed, the same observation the page marker and `/_/revision` report and the one the board it just changed already reflects: on every `200`/`201`/`204` of this layer, pages and fragments alike, so the page adopts it and never reloads what it has just been given. The four bodyless writes of the table above carry it too (§5.7 step 5), and a `204` is the one answer that renders no element for the number to land on: nothing adopts it there, and the collection re-read the client performs afterwards is the answer that reports the number the page keeps (§5.7 steps 6-7, §2.6.8, D-050) |

`422`/`409` rule in fragments: htmx **does not** swap 4xx responses by default, and a refusal of this layer
that **replaces a node** needs a listener that says so — the field wrapper, the row, the card and the panel
are all answers of that kind. One listener in `app.js` enables the swap for the two statuses whose body
re-renders the element the request named, `422` and `409`, and for the `/_/` answers that carry
`HX-Retarget`, which is the `partials/fragment_error.html` of a failed resolution (`404`); a `4xx` that
answers `text/plain` — the unknown `state_id` of the two drops, the cross-site `403`, the `400` of a
missing `HX-Request` — carries no `HX-Retarget` and stays unswapped:

```js
document.body.addEventListener('htmx:beforeSwap', (e) => {
  var xhr = e.detail.xhr;
  if (xhr.status === 422 || xhr.status === 409 || xhr.getResponseHeader('HX-Retarget')) {
    e.detail.shouldSwap = true;
    e.detail.isError = false;
  }
});
```

**HIFA-114 wrote the `409` and the retargeted `404` into that listener, and the tree has carried them
since.** Until then the listener enabled exactly one status (`422`), and a `409` therefore fell through to
the generic `htmx:responseError`, which turned it into the one-line toast `could not save: the server
rejected the change` — the opposite of the answer §2.6.3 contracts: the wrapper back with the server's
value, the message and **no** control. The listener above (`app.js:213-220`) is now the whole of the rule:
htmx re-reads `isError` after `htmx:beforeSwap` and fires `htmx:responseError` only for a response it still
holds to be an error, so a `422`, a `409` and every answer carrying `HX-Retarget` are swapped in place — and
none of them becomes a toast over an element that already prints the sentence. What reaches
`htmx:responseError` is the one fault the swap cannot show, a `500`; that handler has no field to except and
no status to guess. §2.6.3 states that flow as a requirement of the implementation for exactly this reason.

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
| `GET /{team}/issue/{identifier}` | `app.boards.issue_detail(db, ref, include=frozenset({"comments", "history"}), include_trashed=True)` | `NotFound(issue)` ⇒ `404`, **except** for an issue the trash holds, which the keyword returns so the page can render it with `Restore` alone (§2.1.1, D-059). The two names are the ones of SPEC-040 §2.2's `DETAIL_INCLUDE` whose collection `IssueDetail` carries (SPEC-060 §2.5.1, SPEC-050 §2.5.3.2): the constant's third name is `attachments`, no v1 envelope has a key for it (GAP-07), and passing the whole constant would run SQL-7 row 12 for a collection this layer does not render (D-027) |
| `…/panel` | `app.boards.issue_detail(db, ref, include=frozenset({"comments", "history"}), include_trashed=True)` | the panel renders the comments, so it pays for them: one extra read in an explicit user action, never in a board render (D-029); the same set and the same keyword, so the panel of a trashed issue is `200` too (`Restore` alone, §2.1.1, D-059) |
| `/_/issues/{id}/row` | `app.boards.issue_detail(db, ref)` | empty `include`: only the base `IssueDetail` — the constant is deliberately **not** the default (D-027) — and **no** `include_trashed`, so a trashed identifier is `404` here while its page answers `200` (§2.1.1, D-059) |
| `/_/issues/{identifier}/history` | `app.boards.issue_detail(db, ref, include=frozenset({"comments", "history"}), include_trashed=True)` | `#history-list` re-rendered whole (§2.1.3, §2.5.7): **the panel's own two names and its own keyword**, so `Show all` on the panel of a **trashed** issue answers `200` exactly as the panel does (§2.1.1, D-059) and opens no third trash-reading surface — R29's two surfaces stay two, and this route is a re-render of the second. `limit` in the query string names the window the control asks for and **is not an argument**: the window is SQL-7 row 11's constant (**100**), which is why the control sends `?limit=100` and why a fragment cannot be asked for more; the refusal SPEC-050 §2.5.3.3 declares belongs to the CLI's flag, not to this route |
| `GET /search`, `GET /_/palette` | the `SearchQuery` composed from `app.search.parse_query(q)` + the route's parameters, then `app.search.search_issues(db, query, now=…)` (SPEC-090 §2.3) | the web layer does **not** declare its own search flags or envelope (D-016, D-064); `/search` passes `count=False` and the `now` its read model already uses (§2.3.5), and renders the four states of §2.1.8 |
| `GET /` and the sidebar of **every** page | `app.teams.list_teams(db)` | called per request, never once in `create_app` nor memoized on `app.state` (R23, D-030): the CLI is a second writer to the same file. The team counts come from `v_team_progress`, not from a query written here |
| `POST …/move`, `POST /_/issues/{id}/move` | `app.issues.move_issue(uow, MoveIssue(issue=ref, target_state=..., position=..., before_id=..., after_id=...), Actor(..., source="web"))` | §2.6.4 |
| `POST …/move` in the same state | `app.issues.reorder_issue(uow, ReorderIssue(issue=ref, before=..., after=..., scope="board"), Actor(...))` | the rebalance is decided by the server (R10) |
| `PATCH /_/issues/{identifier}` | `app.issues.update_issue(uow, UpdateIssue(issue=ref, <field>=...), Actor(...))` | `UNSET` = "no change"; setting `state` appends to the target column, position included (SPEC-040 §2.2); the element it answers is re-read after the write, and for `view=field` that read passes `include_trashed=True` as the panel's does, so a trashed issue's field answers `422` (§6) and not a `404` (§2.1.1, R29, §2.6.3) |
| `POST /{team}/issue`, `POST /_/issues` | `app.issues.create_issue(uow, CreateIssue(...), Actor(...))` | | |
| `POST …/archive` | `app.issues.archive_issue(uow, ArchiveIssue(issue=ref), Actor(...))` | soft delete (D-015: there is no physical delete). `Validation(CLOSE_WITH_OPEN_CHILDREN|ISSUE_TRASHED)` ⇒ `422` |
| `POST …/delete` | `app.issues.trash_issue(uow, TrashIssue(issue=ref), Actor(...))` | the trash layer: on a visible or archived issue `trashed = 1` and `updated_at` moves with it; an issue **already** in the trash is a no-op that reads no clock — no history row, `trashed` 1 and `updated_at` where they were (§5.7 step 7, SPEC-040 §5.5) — and an archived issue can be trashed (§2.1.2, SPEC-050 §2.5.3) |
| `POST …/restore` | `app.issues.restore_issue(uow, RestoreIssue(issue=ref), Actor(...))` | clears `archived_at` and `trashed`, one history row per layer cleared; the only write that accepts an archived issue, and a no-op on a visible one |
| `POST …/labels`, `DELETE …/labels/{id}` | `app.labels.add_label` / `app.labels.remove_label` | one call per label |
| `POST …/comment`, `POST /_/comments/{id}` | `app.issues.comment_on_issue(uow, CommentOnIssue(...), Actor(...))` | |
| `GET /_/revision` | **nothing**: no use case, no read model, no template | the one route of this layer that answers a number it reads itself (§2.1.3.1, D-049). A `data_version` read is a property of the connection, not of the domain: promoting it to `app/` would move an edge detail into the core (P4) and give the CLI and the API a function neither of them can use |

A signature change in SPEC-040 propagates here with a `spec_revision` bump; a new function is never
added in `web/` just to make the route "fit".

**The history fragment is one capability with two edges, so it needs no exemption.** The timeline's
transport is `issue history` on the CLI (SPEC-050 §2.5.3.3) and `GET /_/issues/{identifier}/history` here,
which is the shape `scripts/check_edge_parity.py` calls **parity**: the row is
`Capability(name="issue.history", cli="issue history", web=("GET", "/_/issues/{identifier}/history"))`,
with both edges present and **no** `debt=`/`reason=` — a single-edge row would be a violation, and this one
is not single: the verb is the reference surface and the fragment is the dashboard's second transport of
the same rows. The route also takes its `FRAGMENT_ALLOWLIST` entry, with the reason
"htmx partial: the whole `<ol>` of an issue's history", because every `/_/*` route must say why it exists
whether or not a capability row names it. Both declarations land in the implementation PR of HIFA-46, in
the same commit as the route; until then the checker fails on a fragment it can reach and cannot find, which
is the behaviour that makes it worth running.

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

#### 2.1.7 The write guard: a state-changing request from another origin is refused (D-057)

Every state-changing request of this layer passes one check **before the route is matched**. The check is
installed by `create_app` as an HTTP middleware — the mechanism `_headers` already uses — and it is **not**
a per-route dependency: a dependency is opt-in, and a route added by a later phase can forget it. The guard
decides on the method and on `Sec-Fetch-Site` and `Origin`, compared against `Host`, never on the path,
the route table, the database or the body, so every route that exists and every route a later phase
registers is covered by construction, and a method no route serves yet (`PUT`, `DELETE`) is covered too.
**R28** is the rule; this section is its contract.

| Request, method not in `GET`/`HEAD`/`OPTIONS` | Answer |
|---|---|
| `Sec-Fetch-Site: same-origin` | allowed |
| `Sec-Fetch-Site: none` — a navigation the user started (typed URL, bookmark) | allowed |
| `Sec-Fetch-Site: cross-site` or `same-site` | `403`, `text/plain`, `cross-site write refused` |
| no `Sec-Fetch-Site`, `Origin` with the authority of `Host` | allowed |
| no `Sec-Fetch-Site`, `Origin` with another authority | `403`, as above |
| no `Sec-Fetch-Site`, `Origin: null` | `403`, as above |
| neither header | allowed: the non-browser client |

`same-site` is refused because this edge is served from one host and port and no page of it can produce
that value, so a `same-site` request is necessarily another origin. `Sec-Fetch-Site` is the browser's own
statement about who initiated the request, so it is read first; `Origin` is the fallback for the client
that does not send it.

**The comparison is the authority, and the scheme is not in it.** The request's own origin is the
authority of `Host` — host and port as addressed — and `Origin` is refused when its authority differs from
it, compared case-insensitively. Comparing `scheme://host[:port]` against the connection would refuse the
tunnel's legitimate writes: behind a cloudflared quick tunnel the browser's `Origin` is `https://<tunnel>`
while the socket is plain `http`, and this corpus declares no `X-Forwarded-Proto` trust to tell the two
apart — `hifa serve` opens the socket itself and knows of no proxy (A4). The name and the port are what a
foreign page cannot forge without controlling them; the scheme it does not control adds nothing, and TLS
termination is out of scope of this rule. `Origin: null` — an opaque origin: a sandboxed frame, a
`file://` document, a redirect that stripped it — names no authority at all, so it is neither "another
origin" nor absent, and it is refused: no page of this application produces it.

**Absence is allowed, and that is the contract.** There is no cookie, no session and no token to ride or to
demand (SPEC-000 REQ-S005): the client that can set one header can set any, so refusing a headerless
request buys nothing and breaks the clients the corpus runs on — the `httpx` client of `tests/e2e/web/`, an
agent driving the fragments, `curl` in the no-JS path, the `hifa serve` smoke of
`specs/acceptance/001-mvp-v1/16-dashboard.feature`. The subject of the guard is the **browser the reader is
using**, which sends these headers on its own. It is not a lock on the port, and the limit is declared, not
implied: the holder of a published tunnel URL can still write with a client of their own.

**The refusal is a returned response; nothing is raised.** The middleware **returns** `403` with
`Content-Type: text/plain; charset=utf-8`, body `cross-site write refused` and `Cache-Control: no-store`.
No exception is raised, no exception handler is involved and none is added: `@app.exception_handler` lives
in Starlette's `ExceptionMiddleware`, **inside** the user middleware stack, so an exception raised by a
middleware is never mapped there and would answer `500`. The edge's one exception-and-handler pair is
`FragmentRequiresHtmx` (`src/hifa/web/app.py`), raised by the `HX-Request` dependency and mapped by its own
handler, both of them inside that stack; the read-only `403` is not an exception either — it is an inline
`return` in every write route of §2.1.2 and §2.1.3, which is the thirteen page forms of §2.1.2 and every
`/_/` write fragment of §2.1.3 (`POST /_/states/{state_id}/position` and `POST /_/issues` among them),
as R18 says. The status is the one this edge already uses for "understood and refused", and the two 403s
differ by their message, which §6 keeps as two rows: `server in read-only mode` is a statement about the
instance, `cross-site write refused` about the caller. Nothing is written, because the refusal is produced
before the handler, so no use case and no transaction runs. The body is a sentence and not a page because
the guard fires before routing and has no read model, no team and no sidebar; the corpus's other
guard-produced refusals (`fragment requires htmx`, `workflow state does not exist`) have the same shape.

**It sits inside `_headers`.** `_headers` wraps the guard, so a refusal leaves with the same
`Content-Security-Policy`, `X-Content-Type-Options: nosniff` and `Referrer-Policy` as every other answer of
this edge, and its own `Cache-Control: no-store` stands, because `_headers` supplies one only to an answer
that has none. The price is one revision probe on a request that is refused, and that probe answers `None`
instead of raising when it cannot read (D-049), so the refusal is still produced with the file locked. The
opposite order is rejected in D-057: a `text/plain` answer without `nosniff` would be the only answer of
this edge a browser may sniff.

**Order.** The guard precedes both refusals that already exist, because a middleware runs before the route
is matched, before the per-route `HX-Request` dependency and before the handler that reads
`app.state.read_only`:

1. the write guard — `403 cross-site write refused` (R28, this section)
2. `HX-Request` — `400 fragment requires htmx` (§6, R5)
3. read-only mode — `403 server in read-only mode` (§6, R18)
4. the route and the use case — `404` resolution, `422` validation, `422 confirmation_required` (refused
   by the route before any use case runs, §2.1.2), `422 ISSUE_TRASHED` and `409` archived (§6, R27) — the
   trash and the archive are two different refusals, and neither is the other's status.

A cross-site write to an identifier that does not exist is therefore `403`, not `404`: the refusal must not
depend on the target resolving, or a `404` would prove the guard was not in the way and the answer would
begin to depend on the database — which the guard must be able to answer with the file locked. A same-origin
write on a read-only server keeps the read-only `403` of §6; a cross-site one is the guard's.

**Reads are not covered, deliberately.** `GET`, `HEAD` and `OPTIONS` are served exactly as before. The
browser's same-origin policy already stops the attacker's script from reading the answer and this edge
emits no CORS header on any route (REQ-S005), so a cross-site read is a navigation whose bytes the attacker
cannot see. Writes were the hole because they are **sent**, not read.

**Out of scope of this rule.** Accounts, sessions and per-user permissions (the model has one local member
by design); TLS termination; the tunnel's configuration; rate limiting; a server-side CSRF token; any change
to the CLI, which never opens a socket (A4); any change to the meaning of the read-only mode; any new route,
verb or field; and the JSON API — this spec governs `web/**` only and `src/hifa/api/` serves no write route
today, so the writes of SPEC-060 get their own answer when they land.

#### 2.1.8 The search page: `GET /search?q=` (D-063)

One page, one query string, four states and no fragment. It is the web transport of the capability
`hifa search` exposes (SPEC-050 §2.5.1), so it consumes the contract of SPEC-090 §2 unchanged and
declares nothing of its own beyond the render model of §2.3.5 (D-016):

| Aspect | Contract |
|---|---|
| Route | `GET /search` — a **page** (R1): no `/_/` prefix, no `HX-Request` guard, no htmx attribute, no `hx-get` anywhere on it |
| Query | `q` (optional, the raw text of SPEC-090 §2.3 — free text, `"phrase"`, `prefix*`, `field:value`, `-term`) and `team` (optional; the `team:KEY` filter, and **the only filter this page exposes as a parameter**). Both travel in the query string, never in a body and never in a cookie (R21) |
| Use case | `app.search.parse_query(q)` + the `team` parameter composed into a `SearchQuery` (§2.1.4), then `app.search.search_issues(db, query, now=…)` with `count=False` and the `now` of §2.3.5 — one call per request |
| Template | `search.html`, extending `base.html` (blocks `title`, `content` only) |
| Model | `search_vm` of §2.3.5, complete in every state (R3) |
| Sidebar | the `Search` entry **stays**, on every page, and points at `/search` with no query — which is why that URL answers `200`. On this page it is marked active (`page.is_search`, §2.3.1) |
| Team in the crumb | `page.team` is `None` (no team is active on this route, §2.3.1); the `team` filter, when present, is shown as a static chip above the results — a label and a link to `/search?q=…` without it, **not** a `filter_bar` chip: this page renders no filter bar, so §2.6.2's "removes the last filter chip" row never applies to it |
| Total | never rendered: the page pays no `COUNT(*)` and shows `len(hits)` under the list (§2.3.5). `hifa search --count` is where a total belongs |
| Trash | never: the page reads through `search_issues`, which excludes `trashed` and `archived_at` at query time (SPEC-090 R10), so a trashed issue is absent for the same reason it is absent from the board (R29) |

**The four states, and there is no fifth.**

| State | Request | Status | What the reader sees |
|---|---|---|---|
| **empty** | no `q`, or a `q` that is only spaces | `200` | the search box, focused — `vm.autofocus` is `True` in this state only, and it is an HTML attribute, not a script — and the empty-state block of `partials/empty_state.html`: the sentence `search the workspace` and no result list. `search_issues` is **not called**: there is nothing to search for, and this is the state a click on the sidebar entry produces (D-063) |
| **results** | `q` (a valid query) with ≥ 1 hit | `200` | the search box carrying `q`, `len(hits)` under it, and one `<li>` per hit: identifier, the state's name (`state_name`, in a `<span data-state-type="{{ hit.state_type }}">` — no colour, §2.3.5), `priority_glyph`, `updated_label`, the title, and the snippet |
| **no results** | `q` (a valid query) with 0 hits | `200` | the search box carrying `q`, and the empty-state block with `nothing matches 'deploy'`. **Not** a `404` and **not** an error: the request was valid and the answer is that the workspace holds nothing (SPEC-090 §6) |
| **error** | `q` that SPEC-090 §6 refuses (`query_too_long`, `invalid_filter`, `invalid_limit`) | `422` | the search box carrying `q` **unchanged**, the sentence of §6 (`invalid value: <domain message>`), and no result list — a page that keeps the reader's query is a page they can fix |

- The empty and no-results blocks are **two renderings of one partial** (`partials/empty_state.html`,
  §2.2) with two keys, `message` and `message_class` — the same block the empty table and the empty
  column use. The template prints `vm.message` and `vm.message_class`; it composes no sentence and
  picks no class (R2).
- `422` is the code for every refusal of the query, and the page never answers `400` for a malformed
  `q`: a query SPEC-090 cannot read is a refusal of the input, and `422` is what the rest of this layer
  uses for one (§6). The `400` of the `/_/` family (R5) is untouched, and so is the write guard of R28 —
  which answers `403`, and is not a `400` of this layer at all.
- **The `q` never reaches SQL and never reaches the page as a value of its own**: it is escaped by Jinja
  in the input's `value`, and the hits come from the use case. `vm.q` is the raw string; the template
  escapes it because it is user text.
- **The snippet is escaped exactly once, here in the read model — not in `app/`, not in Jinja.** SPEC-090
  R15/REQ-S090-03 hands this layer the raw text with `MARK_START`/`MARK_END` around the matched terms;
  `build_search_vm` splits it on that pair, escapes each piece with the same escaping the rest of the read
  models use, and rejoins the pieces around the literal `<mark>`/`</mark>` (R17). The result is the
  `snippet_html` of §2.3.5 and it is the **third and last** field this layer renders with `|safe` (§2.3.7,
  R15). An edge that escaped the snippet twice would print `&lt;mark&gt;`; one that handed the raw string
  to `|safe` would let an issue title carry markup into the page — the two failures R17 exists to forbid,
  and both are pinned by a scenario of §7.

**Layout and the 320 px floor (P3/P5).**

- The results are a **list**, not a table: `<ul class="search-results">` with one `<li class="hit">` per
  hit, each one an `<a href="{url}">` that wraps the whole row. A six-column table (`IDENTIFIER  P  STATE
  UPDATED  TITLE  MATCH`, the CLI's) pans horizontally at 320 px, and a hit is a link, not a record to
  scan in columns — the CLI renders the table because a terminal has no links (§2.5.6).
- At **320 px** the list is one column, each hit is a block of at most three lines (identifier + state +
  priority on the first, the title on the second, the snippet on the third), the snippet truncates with
  `…` from the read model and never wraps into a fourth line, and the page has no horizontal scroll —
  the same floor the board meets with one column per row (§2.5.6).
- `min-width: 0` and `overflow-wrap: anywhere` on the hit's text: a 200-character identifier-shaped title
  is the input that breaks this layout, and it must wrap instead of pushing the page wide.

**Keyboard path (P5).** No part of this page needs a mouse:

1. the search box is a `<form method="get" action="/search">` with `name="q"` and a real `<button
   type="submit">`, so `Enter` in the box submits it and the result is a normal navigation — no htmx, no
   JS, nothing to degrade (R4);
2. `/` focuses the box from anywhere on the page and selects what is in it (R20, §2.6.2), and `Esc` in
   the box clears it — the two shortcuts that make a second search cost two keys;
3. `Tab` walks the hits in order and each hit is one tab stop (the `<a>`), with `:focus-visible` from
   §2.5.3 — no `tabindex` in the list, and no roving focus;
4. the results heading carries `tabindex="-1"` and, **when JS is on**, receives focus after a submit so
   a screen reader announces `2 results` instead of re-reading the sidebar: the one `focus()` this page
   performs and the only client behaviour it adds (§2.6.2's list of shortcuts is unchanged). Without
   JS the browser lands at the top of the document, which is the same place, and nothing is lost — the
   heading is in the reading order either way.

#### 2.1.9 What this page is not

- It is **not** `/_/palette` (`Cmd+K`): the palette is a fragment with two phases (R19) and its own
  contract, and this page declares nothing about it — not its markup, not its phases, not its fragment.
  The palette is a separate ticket and this revision contracts the page alone, so neither route borrows
  the other's markup. The **one** rule that already binds it is R21's: that rule is about every edge of
  this layer that composes a query, it names the palette as one of them, and it says so for the day the
  palette ships (§4 R21).
- It is **not** the board's or the list's `q` (D-025): that one is a `LIKE` over the title inside
  `ListFilters`. This one is the whole grammar of SPEC-090 and it ranks by bm25.
- It has **no fragments**: no pagination fragment, no infinite scroll, no live-updating box, no
  `hx-*` attribute. `limit` stays at `DEFAULT_LIMIT` (50, SPEC-090 §2.1) and the page never says how
  many matches exist, because it never counts them: `len(hits)` is all it knows, and a full page of 50
  is therefore indistinguishable from 50 of 500 — which is why the CLI has `--count` and this page
  does not. Paging a result set is not v1 (SPEC-090 §10).

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
    ├── field_title.html   # one inline field: the wrapper, its value state and its control (§2.6.3)
    ├── field_description.html # one file per control of §2.1.2, name and key written literally
    ├── field_priority.html
    ├── field_state.html
    ├── field_assignee.html
    ├── field_due_date.html
    ├── field_estimate.html
    ├── comments.html      # <ol> of comments
    ├── comment_item.html  # one comment <li>
    ├── history.html       # <ol id="history-list"> of the activity timeline (§2.5.7)
    ├── history_item.html  # one event <li>: rail dot + sentence + <time>
    ├── palette.html       # <ul> of palette results
    ├── toasts.html        # toast container
    ├── fragment_error.html # message for a refused fragment (never a whole document)
    └── empty_state.html   # empty state (column, table, search)
```

`base.html` defines exactly these blocks: `title`, `head`, `content`, `panel`, `scripts`.
A child template **cannot** inject `<script>` (forbidden by CSP, R14).

**The seven wrappers are seven partials, one per field, and each writes its own field's name and key as a
literal.** `partials/field_title.html`, `field_description.html`, `field_priority.html`, `field_state.html`,
`field_assignee.html`, `field_due_date.html` and `field_estimate.html` each print one whole
`<div class="field-inline">` — its value state and, when the field is writable, its control state — and
nothing else. None of them takes a context variable: each is included bare, from `issue_detail.html` and
from `partials/issue_panel.html`, with the includer's `vm` already in scope. Inside, the two literals the
wrapper is built from are written out: the entry it reads (`vm.fields["title"]`), the control's `name`, the
wrapper's `id` and `data-field` (`field-title-{{ vm.identifier }}`, `data-field="title"`), the value key of
§2.6.3's table (`vm.title`, `vm.description_html`, …) and the `?view=field&field=title` of the form and of
the control. Each partial has three gates and none of them asks which field it is: `field.writable` (is
there a control at all), `field.open` (is the disclosure open) and `field.invalid`/`field.message` (is the
value marked, and what does it say). The file *is* the field: nothing arrives to be interpreted.

`issue_detail.html` and `partials/issue_panel.html` each include them seven times, one line per field:

```jinja
{% include "partials/field_title.html" %}
```

Seven such lines — `title`, `description`, `priority`, `state`, `assignee`, `due_date`, `estimate`, in the
order §2.3.4 declares them — are the whole of the nesting, and the parent decides only **where** a wrapper
sits (§2.6.3, "Where the seven wrappers sit"), never what it renders. There is no `{% for %}` over `fields`
and no partial that is handed a field to render: a loop would index the dict with its own variable, and a
single partial told `name="title"` would have to compare that variable to seven strings to pick one of
three controls and one of seven value keys — the branch §2.3.7 forbids, and the reason the previous
revision of this paragraph was wrong when it said one file "never decides which field it is". A
`{% call %}` macro was the alternative and does not fit: Jinja passes one body, while a field's value state
and its control are two different expressions in two different elements, so the macro would have to
receive one of them and decide the other — which is the same `if name == …` under another name.

**The sidebar's view links carry `side-link`, and the active one also carries `is-active`.** These are the
two class names §2.1.8's page and its §7 scenario depend on, and neither was declared anywhere. The three
view links of `partials/sidebar.html` are
`<a class="side-link{% if page.is_board %} is-active{% endif %}" href="/{team}/board">Board</a>`, the same
for List over `is_list`, and `<a class="side-link{% if page.is_search %} is-active{% endif %}"
href="/search">Search</a>` over `is_search` — the three flags §2.3.1 declares. `is-active` is appended to
**exactly one** of the three on any page (or to none, on the detail page and in the picker, where all three
flags are `False`), which is why the pair reads as one string in the markup and in §7's assertion. Nothing
else marks the active view: no `aria-current`, no `data-*`, and the template compares no `nav` value —
the flags are that comparison, made once, in the read model.

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
    "nav": "board",  # "board" | "list" | "search" | "issue" | None (the picker has no active view)
    "is_board": True,  # bool: nav == "board"; the three flags base.html marks the view links with
    "is_list": False,
    "is_search": False,
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

`is_board`, `is_list` and `is_search` are the three flags `base.html` marks the view links with, and
each one is a comparison the read model makes once: `is_board = nav == "board"`, `is_list = nav ==
"list"`, `is_search = nav == "search"`. They are declared because they are keys the template reads
(R3); `nav = "issue"` — the detail page — turns all three `False`, and so does the picker's `None`.

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
    "assignee_id": "01M71T2X…",  # the control's value: the member id, or "" when unassigned (§2.6.3)
    "labels": [{"name": "bug", "color": "#EB5757"}],
    "due_date": "2026-11-20",
    "due_value": "2026-11-20",  # the control's value: `<input type="date">` takes the empty string
    "due_label": "Nov 20",
    "estimate_value": "3",  # the control's value: `f"{estimate:g}"`, or "" when unset
    "is_overdue": True,
    "sort_order": 1536.0,  # backlog manual order: read-only, never sent back
    "is_archived": False,
    "is_trashed": False,
    "can_write": True,  # §2.6.9's one gate; R3: every declared key, always
    "url": "/ENG/issue/ENG-13",
    # The answer of a refused write (§2.6.3): the message and the control that carries it, plus the one
    # boolean per control a template gates a class on — the row has one control, the detail and the panel
    # have seven and read `fields` (§2.3.4).
    "form_error": None,  # str | None
    "invalid_field": None,  # str | None: the control the refusal named
    "title_invalid": False,  # `invalid_field == "title"` — the row's one control
}
```

**The three keys of the tail are the refusal's, on every build.** `form_error`, `invalid_field` and
`title_invalid` default to `None`/`False`, so a row read from a listing and a row re-rendered after a refusal
are the same dict with different values — never the same dict with different keys (R3). `title_invalid` exists
because a template may not compare `invalid_field` to a string (§2.3.7: the flags are the comparison, made
once, here), and it is the **only** one a row carries: the row has one control — the inline `title` of §2.6.3
— so a refused `description` never reaches it. The detail page and the panel do not read a boolean at all:
they read `fields[<name>].invalid` (§2.3.4), the same comparison resolved once per control because they have
seven. The three control values — `assignee_id`, `due_value`, `estimate_value` — are what the `<input>`s
carry, resolved here so no template formats a member id, a date or a float.

#### 2.3.4 `issue_vm` (issue_detail.html and partials/issue_panel.html)

It is `row_vm` **plus** these keys (never less: the panel and the page use the same dict):

```python
issue_vm = {
    **row_vm,
    "number": 13,
    "description_html": "<p>…</p>",  # rendered + sanitized markdown (never None: "" if empty)
    "description_raw": "the **markdown** as stored",  # the textarea's value; "" when empty, never None
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
    # Flat, in creation order: the dashboard's composer cannot ask for a parent, so the list it
    # renders carries no tree (D-060). A thread started from the CLI reads here as its comments in
    # the order they were written.
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
    # The timeline (§2.5.7). `history` is every row of the read model's window, oldest-first, and
    # `history_panel` is the same list cut to 8: the panel iterates one and the page the other, and no
    # template slices or counts (§2.3.7, D-C). `history_window` is how many rows the read returned — the
    # N of `Show all N`, a window and never a total (≤100, SQL-7 row 11) — `history_hidden` is 0 when the
    # cut hides nothing (the condition `Show all N` renders on), `history_truncated` is the read's word
    # for a full window (the page prints `the last 100 events` when it is true), and `last_activity` is
    # the one-line summary under the title, `None` on an issue with no rows.
    "history": [
        {
            "id": 412,
            "field": "state_id",
            "actor_id": "01M71T2XA1DS4WJ2Z7ZXBTNW37",
            "actor_label": "Ginkelte",
            "sentence": "moved from Todo to In Progress",
            "old_value": "01M75KV68MF9TYWEK2GHWPNB80",
            "new_value": "01M75KV68MF9TYWEK2GHWPNB81",
            "old_label": "Todo",
            "new_label": "In Progress",
            "dot_color": "#F2C94C",
            "created_at": "2026-11-11T18:35:35Z",  # SPEC-040 §2.2's wire_instant(row.created_at)
            "created_label": "2 h ago",
        }
    ],
    "history_panel": [  # history[:8]: the same dicts, cut to eight — here the one row, so all of them
        {
            "id": 412,
            "field": "state_id",
            "actor_id": "01M71T2XA1DS4WJ2Z7ZXBTNW37",
            "actor_label": "Ginkelte",
            "sentence": "moved from Todo to In Progress",
            "old_value": "01M75KV68MF9TYWEK2GHWPNB80",
            "new_value": "01M75KV68MF9TYWEK2GHWPNB81",
            "old_label": "Todo",
            "new_label": "In Progress",
            "dot_color": "#F2C94C",
            "created_at": "2026-11-11T18:35:35Z",  # SPEC-040 §2.2's wire_instant(row.created_at)
            "created_label": "2 h ago",
        }
    ],
    "history_window": 1,  # HistoryPage.window_size: the rows the read returned, ≤ 100
    "history_hidden": 0,  # max(0, history_window - 8): 0 renders no `Show all`
    "history_truncated": False,  # HistoryPage.truncated: a full window prints `the last 100 events`
    "last_activity": {"text": "Ginkelte moved from Todo to In Progress · 2 h ago", "row_id": 412},
    "created_label": "Nov 8 14:03",
    "updated_label": "2 h ago",
    # The inline fields of §2.6.3, one entry per control of §2.1.2 always — the seven the surfaces render
    # in this order: title, description, priority, state, assignee, due_date, estimate. `writable` is the
    # one decision that outlives the request: whether the wrapper offers a control at all (§2.6.3's three
    # states); `open` is the wrapper a refusal keeps open (`<details open>`); `invalid` and `message` are
    # that control's entry of `form_errors`, resolved here so no template compares or indexes by a loop
    # variable (§2.3.7). A `GET` of a visible issue renders the seven closed, `writable`, and every
    # `invalid`/`message` `False`/`None`.
    "fields": {
        "title": {"writable": True, "open": True, "invalid": True, "message": "title is required"},
        "description": {"writable": True, "open": False, "invalid": False, "message": None},
        "priority": {"writable": True, "open": False, "invalid": False, "message": None},
        "state": {"writable": True, "open": False, "invalid": False, "message": None},
        "assignee": {"writable": True, "open": False, "invalid": False, "message": None},
        "due_date": {"writable": True, "open": False, "invalid": False, "message": None},
        "estimate": {"writable": True, "open": False, "invalid": False, "message": None},
    },
    "edit_action": "/ENG/issue/ENG-13",  # the form owner of every field control (§2.1.2)
    "edit_fragment": "/_/issues/ENG-13",  # the PATCH base; each control appends `?view=field&field=…`
}
```

`direction` in `relations` is `"out"` (`blocks`, `duplicate`, `related`, `similar`) or `"in"`
(the issue is the target; shown as `blocked_by` when `type == "blocks"`). The read model resolves
the inverse; the view does not compute it.

**`fields` is the render decision, and it is seven keys on every build.** A field wrapper shows its value,
and either its control or the reason there is none; which of the three is the server's answer, never a
client's. `writable` is `can_write and not (is_archived or is_trashed)` — the same two booleans the action
group of §2.6.9 branches on, resolved once here — and it is what makes the third state reachable:
`writable: False` means the issue takes no field write, which is exactly the archived issue of §6's `409` and
the trashed one of its `422`. `open` is `True` for the one field a refusal must keep in front of the reader:
on a `422` that has a control to hand back (the `PATCH …?view=field` of §2.1.3 answers that one wrapper,
§2.6.3), and on the trash, whose wrapper stays open to show the mark and the message. On the `409` of an
archived issue it is `False`, because that wrapper has no disclosure at all. The three combinations, and
nothing else, are the three states §2.6.3 renders:

| `writable` | `open` | What the wrapper prints |
|---|---|---|
| `True` | `False` or `True` | the `<details>` the browser opens and closes, with the value as its `<summary>` and the field's `<form>` inside it |
| `False` | `True` | a `<details open>` carrying the marked value and the message, and **no** control — the trash's `422` |
| `False` | `False` | the value and the message, with **no** `<details>` and no control — the archived issue's `409` |

`invalid` and `message` are the control's own entry of `form_errors` — the dict the refusal already fills,
normalised over `EDIT_FORM_FIELDS` — copied in beside the boolean the template gates `.is-invalid` on,
because a template may not compare a name to a string and may not index a dict with a **computed** key
(§2.3.7). The seven keys are always present, `None`/`False` when nothing was refused, so the page and the
panel render the same dict for the same issue (R3); the partial of one field reaches its own entry with a
literal key — `vm.fields["title"]`, written in `field_title.html` — and never with a variable (§2.2,
§2.3.7).

**A `view=field` refusal keys its message on the field the request named, not on the one the error names.**
`ISSUE_ARCHIVED` and `ISSUE_TRASHED` carry `field="ref"` and `field="trashed"` (`src/hifa/app/issues.py`), and
neither is one of the seven controls, so `_refusal` (`app.py`) resolves them to no control at all: left to
itself the route would fill `form_error`, and every `fields[<name>].message` would stay `None` — a wrapper
that prints only its own entry, `fields[<name>].message` and never `form_error` (§2.6.3), would come back mute
on the `409` and on the trash's `422`. For `view=field` the route therefore writes the message under the key of
the field the request named: `form_errors={requested_field: refusal_sentence(refusal, verb="edit",
identifier=…)}` — the key is the requested field's (`title` for `?view=field&field=title`), and the text is the
builder's, the sentence that names "archived" or the trash (`ENG-13 is archived; restore it first`), which is
what §7's archived-issue scenario reads in the wrapper. That entry is what sets `invalid`; the archived answer
is then `fields["title"] = {writable: False, open: False, invalid: True, message: …}` and the trash's
`{writable: False, open: True, invalid: True, message: …}`.

**Every key of a `history` element is produced above this layer, and none of them is computed by the
template.** The element **is** SPEC-040 §2.2's `HistoryRow` (`history_rows`), the one formatter the CLI
reads too (P2) — the same eleven keys under the same names, `dot_color` being the one colour key this
layer takes (the rail dot of §2.5.7.2, `None` for every field but `state_id`) — plus one key the formatter
cannot produce: `created_label`, the relative stamp, because "now" is the edge's clock and a row carries an
instant, not a distance from today. Within the element, `sentence` is the formatter's wording (§2.5.7 owns
it), `old_label`/`new_label` are its resolved strings and `old_value`/`new_value` are the stored TEXT it
keeps for the `data-*` attributes (P7 — a reader of the DOM can still see the key the row held);
`created_at` is the ISO string SPEC-040 §2.2's `wire_instant` renders from `HistoryRow.created_at` —
`2026-11-11T18:35:35Z`, seconds, UTC — and it goes into `<time datetime>` verbatim (§2.5.7). **The
conversion is not the template's, and this layer performs it by calling the formatter's function:** the row
carries a `datetime`, this read model calls `wire_instant` once for the element it builds, the CLI's
element serializer and the REST serializer call the same function for theirs, and the template is handed a
string. No route, no read model and no template or filter formats an instant of its own — §2.5.7.2's
`time` row puts the ISO instant in `<time datetime>` and the relative stamp beside it — so `created_label`
is the one thing this edge adds, the relative stamp, because "now" is its clock.

**Four keys of the section are not the element's, and three of them are not this layer's either.**
`history_panel` is the 8-row cut and is **layout**, which §2.5.7 gives to this layer (D-C).
`history_window`, `history_hidden` and `last_activity` come from SPEC-040 §2.2's `HistoryPage`:
`window_size` (how many rows the read returned — the N of `Show all N`, **a window and never a total**),
its `truncated` flag (a full window, which the page prints as `the last 100 events`) and the one-line
summary under the title (`{text, row_id}`, or `None`). `history_hidden` is the one subtraction this layer
does — `max(0, history_window - 8)` — and it is the condition the control renders on, not a count of the
issue's events: a template that did any of the four itself would be assembling a sentence and counting
rows, which §2.3.7 forbids for the same reason.

**The dict of a trashed issue is the same dict, and that is what lets the page render `Restore` alone.**
`is_trashed` is `True` only on a detail read with `include_trashed=True` — the page and the panel of
§2.1.1, and nothing else (§2.1.4) — and `False` on every other `issue_vm` this layer builds, the row and
the card included. Nothing else of the model changes with it: `states`, `assignees`, `labels_all` and the
rest are built exactly as they are for a visible issue, because the read model does not know which
controls a template will render and a model that dropped keys when `is_trashed` was set would make R3
("every declared key, always") conditional on the row — the one thing a template may not have to check.
What the trash changes is the **branch the template takes**, not the model's content: `vm.can_write`
gates the action group once and the layer picks which branch of it renders — an issue in either layer
fills the group with `Restore` and no other control, a visible one fills it with the move control, the
archive block and the delete block, and the group itself renders either way, on the page and on the
panel (§2.6.9, D-059). Two consequences for the model, both load-bearing: `is_archived` and `is_trashed` are
`bool` on `row_vm` (§2.3.3) — never `None`, never a `bool` derived from the other — because a trashed issue
can be archived as well (`archived_at` set and
`trashed = 1`), and the group renders `Restore` in that case too; and `states` is still `[]`-or-full on
that read, since a trashed issue's panel has no `state_id` control to feed and a model that emptied it
would be answering a question about the template it cannot see.

#### 2.3.5 `search_vm` (search.html)

```python
search_vm = {
    "q": "login sso",  # str: the raw text the reader typed, "" in the empty state
    "state": "results",  # "empty" | "results" | "no_results" | "error" (§2.1.8), for the tests
    "message": None,  # str | None: the sentence the state renders, built here (R2)
    "message_class": None,  # str | None: "empty" | "error" — the class the block carries
    "autofocus": False,  # bool: True only in the empty state (the input's `autofocus`)
    "hits": [
        {
            "identifier": "ENG-13",
            "title": "Fix SSO login",
            "snippet_html": "…&lt;b&gt;<mark>login</mark> with <mark>SSO</mark>…",  # escaped + server-side <mark>
            "state_name": "In Progress",
            "state_type": "started",
            "priority": 2,
            "priority_glyph": "H",
            "updated_label": "3d ago",  # the relative stamp of issue_vm (§2.3.4)
            "url": "/ENG/issue/ENG-13",
        }
    ],
    "count": 1,  # len(hits): this page promises no total (SPEC-090 §2.6.1)
    "teams": [{"key": "ENG", "name": "Engineering"}],
    "team_filter": None,  # {"key": "ENG", "name": "Engineering"} | None: the chip above the results
}
```

`count` is `len(hits)` for this page; no total is promised, for the same reason `total_estimate` is
`null` in SPEC-060 and the page passes `count=False` (SPEC-090 §2.6.1). One key per state, and all of
them in every state (R3):

| `state` | `message` | `message_class` | `autofocus` | `hits` | `count` |
|---|---|---|---|---|---|
| `"empty"` | `search the workspace` | `"empty"` | `True` | `[]` | `0` |
| `"results"` | `None` | `None` | `False` | the hits | `len(hits)` |
| `"no_results"` | `nothing matches '<q>'` — the query as the reader typed it, escaped once | `"empty"` | `False` | `[]` | `0` |
| `"error"` | `refusal_sentence(refusal)` — the builder's identifier-less arm, `invalid value: <SPEC-090 §6 message>` (§6) | `"error"` | `False` | `[]` | `0` |

- **The error state's sentence is built, not written.** `build_search_vm` receives the `SearchError` of
  SPEC-090 §6 the route caught and asks the one builder of §6 for its text:
  `refusal_sentence(refusal)` with **no** `verb` and **no** `identifier`, which is the arm that exists for
  a refusal about the request itself rather than about a row. The read model stores the result in
  `message`; the template prints it like any other. No branch of the route, no handler and no template
  composes one (§2.3.7, R2).
- `snippet_html` is the **only** HTML of a hit: `build_search_vm` splits SPEC-090's snippet on
  `MARK_START`/`MARK_END`, escapes each piece and rejoins it around the literal `<mark>`/`</mark>`
  (R17). `title`, `identifier`, `state_name` and the labels are plain text the template escapes.
- **A hit carries no colour, and this page invents none.** `SearchHit` of SPEC-090 §2.2 has
  `state_name` and `state_type` and no `state_color`, and this route performs no second read: the model
  is built from the page's one `search_issues(db, query, now=…)` call (§2.1.4, D-016). A `state_color`
  key would therefore have had no producer at all — neither the use case nor the adapter returns one, and
  a read of `workflow_state` per hit is what the one-call rule forbids. The template prints `state_name`
  and carries `state_type` as `data-state-type`, so a stylesheet can key off the type; nothing colours a
  hit today. `row_vm` and `issue_vm` keep their `state_color`, because the board and the list read it
  from the query that already joins `workflow_state` (SPEC-030 §2.6).
- `priority_glyph` is the one of SPEC-050 §2.4.1 (`– U H M L`), computed by the same helper `row_vm`
  uses; `updated_label` is `_relative(updated_at, now)` like `issue_vm`'s.
- **The template compares nothing.** It takes two truthiness branches and prints one class:
  `{% if vm.message %}` renders `partials/empty_state.html` with `vm.message` and
  `class="{{ vm.message_class }}"` — the block the empty, no-results and error states share — and
  `{% if vm.hits %}` renders the list. `{% if vm.autofocus %}autofocus{% endif %}` is the input's one
  derived attribute. `vm.state` exists so the tests can name the state and so the route can decide the
  three keys above; **no template compares it against a literal**, because §2.3.7 forbids a comparison
  in the template and R2 puts every branch's operand in the read model.

#### 2.3.6 Markdown rendering (D-020)

`description_html`, `body_html` (comments) and the panel description are rendered with
**`markdown-it-py`** (CommonMark) + an **in-house allowlist** sanitization, in that order:

```python
# src/hifa/web/markdown.py
from collections.abc import Mapping

from hifa.domain.identifier import IDENTIFIER_PATTERN
from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
MD.enable("table")
# html=False: the user's raw HTML is escaped, not interpreted.
# The commonmark preset ships the `table` rule and leaves it disabled; this enables that rule and
# nothing else — `gfm-like` would also add strikethrough, linkify and task lists (D-079).

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
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRS = {("a", "href"), ("a", "class"), ("th", "style"), ("td", "style")}
ALLOWED_SCHEMES = {"http", "https", "mailto"}
# One exception to the scheme rule, and the class is what grants it (R30): `("a", "class")` is kept
# only when the value is exactly `issue-ref`, and a `href` beginning with `/` — the site-relative
# canonical path — is kept only on an anchor that carries that class. Every other anchor keeps
# ALLOWED_SCHEMES unchanged and no other tag gains `class`. The class is unreachable from user text
# (`html=False` escapes an `<a class="issue-ref">` an author writes, commonmark has no attribute
# syntax and `linkify` stays off), so this module is its only writer.
# The only `style` the walker keeps, and only on `th`/`td`: an aligned column is the one place
# markdown-it writes one (`:--`, `:-:`, `--:`). Anything else drops the attribute, keeps the text.
ALLOWED_STYLES = {"text-align:left", "text-align:center", "text-align:right"}


def issue_ref_candidates(text: str | None) -> list[str]:
    """The folded distinct candidates of one text, in the order they first appear.

    A candidate is a maximal run of `[A-Za-z0-9-]` that `IDENTIFIER_PATTERN.fullmatch` accepts once
    the run is upper-cased, so `HIFA-1034` is one candidate and never `HIFA-103` plus a `4`, and
    `XHIFA-103` is one candidate and never `HIFA-103` after an `X`. The run bounds the candidate and
    the borrowed pattern decides whether it is one; this function adds neither a second pattern nor a
    second fold.

    `render_markdown` calls it where it rewrites the text it renders, and `_issue_detail_vm`
    (`src/hifa/web/app.py`) calls it to collect the description's and every comment's candidates
    before its one statement: one function, so what is linked and what is asked are the same
    grammar (R30, D-081)."""


def render_markdown(
    text: str | None,
    links: Mapping[str, tuple[str, str]] | None = None,
) -> str:
    """markdown-it-py -> HTML, then allowlist (tags/attrs/styles/schemes). Never returns None.

    `links` maps a candidate as the author wrote it, folded to upper case, to the canonical
    `(team_key, identifier)` of the issue it names; `None` renders exactly what this function
    rendered before the parameter existed.
    """
```

- The allowlist is implemented **at the edge** (walking the tree with `html.parser`, or with an
  in-house sanitizer of ~60 lines); neither `bleach` nor `nh3` is added.
- `a[href]` only with the `http`, `https` or `mailto` scheme; any other one (including
  `javascript:`, `data:`, `vbscript:`) drops the `href` attribute and keeps the text — the one
  exception is the site-relative `href` of an anchor that carries `class="issue-ref"`, which R30
  grants below and which no input can write. `img` is **not** in the allowlist: markdown with an
  image comes out as text.
- **The table is the one rule enabled on top of the preset** (D-079). `MD.enable("table")` turns on
  the rule the `commonmark` preset ships disabled; `gfm-like` is **not** used, because it would also
  enable strikethrough, linkify and task lists — `s`, `del` and a checkbox `input` the allowlist
  would have to carry, for no requirement. `table`, `thead`, `tbody`, `tr`, `th` and `td` join
  `ALLOWED_TAGS`; with `html=False` the parser emits no other element of a table, so the set stays
  closed.
- **Alignment is the only `style` the allowlist keeps, and only on `th`/`td`.** For `:--`, `:-:` and
  `--:` markdown-it writes `style="text-align:left|center|right"` on the `th` and the `td` of that
  column. `(th, "style")` and `(td, "style")` are accepted when the value, stripped and lowercased,
  is **exactly** one of `text-align:left`, `text-align:center`, `text-align:right`; any other value
  drops the attribute and keeps the cell text. No other tag carries `style`, and no other attribute
  joins `ALLOWED_ATTRS` beyond that `class` R30 grants to the one anchor (below). The value is tested
  by the check its own attribute selects — `href`
  against `ALLOWED_SCHEMES`, `style` against `ALLOWED_STYLES` — so a `style` value never passes
  through the `href` scheme test (`urlparse("text-align:center")` reads `text-align` as the
  scheme, which is not in `ALLOWED_SCHEMES`, and that is why the attribute would be dropped). The
  rule is an exact-match list and not a CSS parser: the renderer emits three literal
  strings, the allowlist accepts three literal strings, and anything a declaration parser would have
  to interpret is refused, so the check cannot be grown by a value it misreads.
- **The sanitizer wraps every `table` it emits in `<div class="table-scroll">…</div>`.** The wrapper
  is written by the walker and never passed through from user input: with `html=False` markdown-it
  cannot produce a `div` and a `<div>` the user writes is escaped to text, so `div` does **not**
  enter `ALLOWED_TAGS`. The wrapper is what the narrow viewport scrolls (see the next bullet),
  without turning the `table` itself into a block.
- **CSS contract (`static/css/app.css`).** The base rules style `.description table`
  (`border-collapse: collapse` and `margin: 0 0 12px`) and `.description th`/`.description td`
  (`1px solid var(--border)` and `padding: 6px 10px`); `th` carries `var(--surface-2)` and weight
  600, and `th`/`td` default to `text-align: left` — the inline `style` of an aligned column wins
  over the stylesheet, which is why the alignment survives. The `@media (max-width: 900px)` block
  keeps `.description pre { display: block; max-width: 100%; overflow-x: auto; }` unchanged and
  gains `.description .table-scroll { max-width: 100%; overflow-x: auto; }`; `.description table`
  leaves the media block entirely — no `display: block`, no `max-width` and no `overflow-x` on the
  table at any width. A wide table scrolls inside `.table-scroll` and keeps `display: table` at
  every width, because `display: block` on the table is what breaks the alignment of its own
  header with its cells. The shipped selector paired `.description table` with `.description pre`;
  the table half goes.
- `markdown-it-py` is declared in `pyproject.toml` in the **base group** (not in the `[web]`
  extra): the dashboard uses the renderer, but the dependency is not optional (D-020). Version
  pinned in `pyproject.toml` and in `uv.lock` like the rest of the dependencies.
- No `markdown-it` on the client: no JS renderer is vendored, nor a minified `marked`/`markdown-it`.
  The browser receives HTML that is already sanitized.
- **An issue reference written in the text becomes a link when, and only when, the caller says what
  it resolves to (R30, D-081).**
  - **The second parameter is a mapping, never a callable.** `links` maps the candidate the author
    wrote — folded to upper case, which is the key — to the canonical `(team_key, identifier)` of
    the issue it names. A callable would cost one lookup per match; a mapping costs the one
    statement the caller already made for the whole surface (the one-query bullet below, D-081).
    `None` is the default and it is today's rendering byte for byte: a page that passes no mapping
    gets the `description_html` and the `body_html` revision 44 served.
  - **The grammar is `domain/identifier.py`'s, and so is the fold.** The scanner's input is the
    **maximal run** of characters that can spell an identifier, and `IDENTIFIER_PATTERN.fullmatch`
    over that run, folded, is the only test: `IDENTIFIER_PATTERN` is the pattern and `parse` is the
    format, the two entries of SPEC-020 §2.3's published list this rule borrows, and the scan adds
    neither a second pattern nor a second format. The scan is
    `issue_ref_candidates(text: str | None) -> list[str]` in
    **`src/hifa/web/markdown.py`**, beside the renderer: it returns the folded distinct candidates
    of one text, and **both callers call that one function** — `render_markdown` where it rewrites
    the text, and the collector in `src/hifa/web/app.py` where `_issue_detail_vm` gathers the
    description's and every comment's candidates before its one statement — so a single grammar
    governs what is linked and what is asked. `domain/identifier.py` exports nothing new: its list
    is closed by SPEC-020 §2.3 and this rule only reads it. `parse` is case-sensitive, so the run is
    folded with `str.upper()` before that test — the same fold the read path applies (`upper(:ref)`
    in `issue_id_by_ref` and in `issue_ref_links`, SPEC-030 §2.6, SQL-7 row 1), which is why
    `hifa-103` resolves and why the mapping's key is the folded spelling.
  - **The boundary is the maximal run.** `HIFA-1034` is one candidate and not `HIFA-103` followed
    by `4`; `XHIFA-103` is one candidate and not `HIFA-103` after an `X`. A run delimited by a
    character outside `[A-Za-z0-9-]` is the whole candidate, and only a candidate the mapping
    resolves becomes a link, so neither of the two is ever half-linked.
  - **The anchor is a plain `href` and carries the canonical target.**
    `<a class="issue-ref" href="/{TEAM}/issue/{CANONICAL}">{as written}</a>` — the team key and the
    identifier of the mapping's value, never the spelling the author used. A non-canonical spelling
    is the same page reached by another path: `GET /HIFA/issue/hifa-103` and `GET /ENG/issue/HIFA-103`
    both answer `303` to `/HIFA/issue/HIFA-103`, because R6's contract — **two URLs never serve the
    same content with `200`** — leaves a second spelling no way to serve the page, so the canonical
    `href` is that rule's consequence and a `hifa-103` linked as written would pay a hop per click.
    The anchor's text is the author's own spelling, escaped as text and
    otherwise untouched. It is a navigation and not an `hx-get`: an
    htmx fetch would need a target and would take the middle click, the `Cmd`-click and the page
    without JS (R4) with it (D-081).
  - **What is never linked.** The inside of `code_inline`, `fence` and `code_block` — a reference in
    backticks is a reference to a reference — and the text of an existing markdown link (between
    `link_open` and `link_close`), nor the `href` of one. The scan rewrites the text of a paragraph,
    a heading, a list item, a blockquote and a table cell, and nothing else.
  - **A candidate that resolves to nothing stays exactly the text it is today.** No anchor, no
    `href`, no 404 — the rule the mapping's existence encodes, and the reason a pattern-only
    linkify was refused. Measured against `$HIFA_DB` on 2026-11-24 — a snapshot of that file, with a
    `\b([A-Z][A-Z0-9]{1,9}-\d+)\b` token scan of `issue.title`, `issue.description` and
    `comment.body` — the corpus holds **308 occurrences of 71 distinct tokens**, of which **42
    tokens name an existing issue** and **the other 29 do not**; 42 plus 29 is the 71, and 308 is
    the occurrences of all of them. Twelve of the 29 are named here, with the occurrences that make
    them expensive — `SPEC-070` (45 occurrences), `SPEC-050` (32), `SPEC-030` (21), `ENG-13` (19),
    `SPEC-040` (16), `SPEC-001` (11), `SPEC-060` (8), `SQL-7` (8), `SPEC-100` (7), `SPEC-090`,
    `SPEC-010` and `SPEC-020` (5 each) — because this corpus quotes its own spec ids, one SQL table
    name, and example identifiers no row carries, against four teams (`HIFA` 117 issues,
    `PROPERTEA` 5, `ENG` 2, `HERMES` 1). A pattern-only autolink emits one dead link per occurrence
    of those 29 tokens — 144 of them from the six counted above alone — so the renderer links only a
    candidate the caller resolved (D-081).
  - **A trashed issue links like any other.** Its page and panel answer `200` (R29, D-059), so the
    mapping does not filter on the trash and a reference to it is a live link; the archived issue is
    the same, its page answering `200` with `Restore` alone.
  - **One statement per surface, never one per match.** The caller collects the candidates of
    everything it is about to render — the description **and every comment body**, because the page
    and the panel print them together (R13) — with `issue_ref_candidates`, folds them in Python, and
    resolves the whole set with one call to `issue_ref_links(conn, refs)` (SPEC-030 §2.6, D-081):
    one statement projecting `issue.identifier` and `team.key` and joining `team` on
    `team.id = issue.team_id`, filtered by `WHERE issue.identifier IN (…)` with literal values, so
    the join is what makes the answer the canonical `(team_key, identifier)` pair and not an
    identifier alone. The mapping is then handed to the renderer. The fold is in Python and not in
    the `WHERE` clause because `upper(issue.identifier)` against the column would turn the statement
    into `SCAN issue` where a written value stays an index seek on `sqlite_autoindex_issue_2` — the
    defect SQL-7 row 1 was corrected for. The measurement that makes this a rule: **the corpus's
    busiest description holds 37 identifier occurrences, 22 of them distinct** (`HIFA-112` itself),
    then 32 occurrences of 9 distinct (`HIFA-114`) and 29 of 10 (`HIFA-46`) — `$HIFA_DB`,
    `issue.description`, measured 2026-11-24 — and the panel renders the description and every
    comment of the issue in one answer. §7 pins the two halves together: a page whose description
    and comments name **14** references costs **one** statement and prints **14** links, so N
    references do not cost N statements.
  - **`build_issue_vm` stays pure.** The mapping arrives as a keyword, built by the caller that
    already holds the connection (`_issue_detail_vm` in `src/hifa/web/app.py`, beside the detail
    read), so the builder performs no IO and renders with what it is given:
    `render_markdown(detail.description, links)` for `description_html` and
    `render_markdown(comment.body, links)` per comment for `body_html` (§2.3.4) are the two call
    sites.
  - **The allowlist's one new permission is the class, and no input can write it.** `a` is already
    allowed, but the scheme rule takes `http(s):`/`mailto:` only, so a site-relative `href` would be
    dropped with the anchor's text kept. The walker therefore accepts `("a", "class")` when the
    value is exactly `issue-ref`, and a `href` beginning with `/` **only** on an anchor that carries
    that class; every other anchor keeps R15's scheme rule unchanged. The class is unreachable from
    user text — `html=False` escapes an `<a class="issue-ref">` an author writes, commonmark has no
    attribute syntax and `linkify` stays off — so the module is the only writer of it.
  - **CSS contract (`static/css/app.css`).** The global `a` rule already colours a link
    (`color: var(--accent)`, `var(--accent-hover)` on hover) and carries no underline; `a.issue-ref`
    adds one — `text-decoration: underline; text-underline-offset: 2px;` — so a reference reads as a
    link inside a sentence, with no second palette and no new custom property. The selector is not
    scoped to `.description`, because the same anchor reaches a comment body.
- Mandatory test: `**bold**` ⇒ `<strong>bold</strong>`; `<script>alert(1)</script>` and
  `[x](javascript:alert(1))` do not survive (REQ-S070-01). A table renders as a `<table>` with its
  `<th>` and `<td>` (`| A | B |` / `|---|:-:|` / `| 1 | 2 |` gives `<th>A</th>` and `<td>1</td>`); the
  `th` and the `td` of a column declared `:-:` keep `text-align:center`; a `style` outside the three
  values (`style="color:red"`) is dropped and the cell text survives; raw HTML inside a cell stays
  escaped (`<b>x</b>` arrives as `&lt;b&gt;x&lt;/b&gt;`).
- Mandatory tests of the reference autolink (R30): `see ENG-12` ⇒
  `<a class="issue-ref" href="/ENG/issue/ENG-12">ENG-12</a>`; `see eng-12` ⇒ the same `href` with
  the text `eng-12`; `SPEC-050` ⇒ no anchor at all and the text unchanged; `` `ENG-12` `` ⇒
  `<code>ENG-12</code>` and no anchor; `[ENG-12](https://example.com/ENG-12)` ⇒ the link the author
  wrote, to `https://example.com/ENG-12`, with no anchor inside its text; `[ENG-12](/ENG/list)` ⇒
  one anchor whose text is `ENG-12` and which carries **no** `href`, because R15's scheme rule drops
  a site-relative target on every anchor but the one that carries `class="issue-ref"`; a text that
  names a real `HIFA-103` beside the glued `HIFA-1034` and `XHIFA-103` ⇒ exactly **one** anchor, to
  `/HIFA/issue/HIFA-103`, and none for either glued token
  (a scanner that split `HIFA-1034` into `HIFA-103` and `4`, or that fired after the `X`, is the
  only way to produce a second); an `ENG` page whose description names the existing `HIFA-1` ⇒ the
  anchor points at `/HIFA/issue/HIFA-1`, because the team comes from the mapping and not from the
  page; a surface whose description and three comments name 14 issues ⇒ 14 anchors from **one**
  statement; and `render_markdown("x")` with no mapping returns the bytes revision 44 served.

#### 2.3.7 Template prohibitions

| Forbidden | Why |
|---|---|
| `{{ x\|length }}`, `{{ a + b }}`, `{{ x > 3 }}` | arithmetic and comparisons live in the read model |
| `{% set %}` with expressions | same |
| `vm.fields[entry.name]` — a key computed by the template, in a `{% for %}` or otherwise | an **access** is a key written in the template (`vm.fields["title"]`, in `field_title.html`); a lookup with a computed key, and a branch on a field's name to choose a control, are the read model's decision — which is why §2.2 declares seven partials and not one partial told a name |
| `{% with %}`, `{% set %}`, or an `{% include %}` that passes a variable, to tell one field from another | the seven field partials of §2.2 take **no** variables: each names its own field with literals on both sides (`vm.fields["title"]`, `name="title"`, `?view=field&field=title`). The include is the whole declaration of which field a wrapper is, and a binding handed to a shared partial is the row above |
| `{{ issue.state.name }}` (domain object) | only plain dicts with the keys declared above |
| `{{ x\|safe }}` on user text | only exceptions: `description_html`/`body_html` (R15) and `snippet_html` (R17), already escaped by the server |
| `<script>` in child templates | CSP `script-src 'self'` (R14) |
| a sentence of §6 written as a literal or assembled with `{% if %}` | the catalogue's strings have one builder, `refusal_sentence` (§6): a template prints the `form_error` it is given and never composes one |

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
| `vendor/sortable.min.js` | 1.15.2 | MIT | **15 KB** | board drag & drop |
| `css/app.css` | — | own | **≤ 20 KB** (target 14 KB) | the whole design (§2.5) |
| `js/app.js` | — | own | **≤ 12 KB** (target 6 KB) | Alpine registrations, `422` listener, shortcuts, drop, the change signal and its deferral |
| `img/sprite.svg` | — | own (Lucide, ISC) | **3 KB** | inline icons via `<use>` |
| `img/favicon.svg` | — | own | **1 KB** | favicon (no `.ico`) |

Total budget: **≤ 75 KB gzip** (real sum: ~66 KB). Verified in the test:

```bash
find src/hifa/web/static -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' \) \
  -exec sh -c 'gzip -9 -c "$1" | wc -c' _ {} \; | awk '{s+=$1} END {print s}'
```

Every figure in that table is **`gzip -9`**, the two own files included: a raw byte count would hold
`app.js` to a ceiling a third of the declared one. The ceilings of `app.css` (**≤ 20 KB**) and
`app.js` (**≤ 12 KB**) are enforced at that compression level by
`tests/e2e/web/test_dashboard.py::test_asset_budget_is_under_75_kb_gzip`, together with the
**≤ 75 KB** total.

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

**The search page at 320 px is a column of blocks, never a table** (§2.1.8). One hit is three lines —
identifier, state and priority on the first, the title on the second, the snippet on the third — with
`overflow-wrap: anywhere` and `min-width: 0` on the text, so a 200-character title wraps instead of
widening the page. The six-column table of the CLI is the shape this view deliberately does not take:
at 320 px it would pan, and a hit is a link rather than a record to scan by column.


#### 2.5.7 The activity timeline — sentences, states and density (HIFA-46, D-074)

The timeline is the story of one issue, rendered from `issue_vm.history` in the panel
(`partials/issue_panel.html`) and on the page (`issue_detail.html`) through the same two partials of
§2.2 — `partials/history.html` and `partials/history_item.html`. **One component, two hosts**: there is
no second implementation and no template that renders a history row of its own (P4).

**What it answers, in this order.** (1) where the issue is now — the state chip of the header and the
last-activity line under the title; (2) who touched it last and when — the last row; (3) what the whole
story is — the list. The three questions are why the order is oldest-first (D-D) and why the newest
event is repeated above the list instead of replacing it.

**Row anatomy, always the same three parts.**

```
[AV]  Ginkelte moved from Todo to In Progress            2 h ago
```

1. **the avatar** — the actor's initials disc (`member.initials`), and the word `someone` in its place
   when the row's actor is unknown (§2.5.7.1);
2. **the sentence** — `<actor_label> <HistoryRow.sentence>`, two strings juxtaposed and never assembled
   by the template: `Ginkelte` is `actor_label`, `moved from Todo to In Progress` is `sentence`;
3. **the time** — the relative stamp inside `<time datetime="…">`, absolute in the attribute.

**The sentence grammar, per `field`.** This table is the **one owner of the wording**: SPEC-030 §2.4
enumerates the fields with their writers and quotes these sentences, and SPEC-050 §2.5.3.3 prints
`sentence` in a terminal. `old`/`new` are the row's two sides **after resolution** — the label, never the
stored key.

| `field` | old | new | sentence |
|---|---|---|---|
| `created` | – | – | `created the issue` |
| `state_id` | Backlog | Todo | `moved from Backlog to Todo` (both labels carry the state's dot colour) |
| `title` | old | new | `changed the title to “new”` (the old title is in the `title` attribute of the row, and reachable by keyboard: §2.5.7.4) |
| `description` | – | – | `edited the description` (D-E: no diff, and the two bodies are never rendered) |
| `priority` | – | High | `set priority to High` |
| `priority` | High | – | `cleared the priority` |
| `priority` | High | Low | `changed the priority from High to Low` |
| `assignee_id` | – | administrator | `assigned to administrator` |
| `assignee_id` | administrator | – | `unassigned` |
| `assignee_id` | administrator | Mo | `reassigned from administrator to Mo` |
| `parent_id` | – | HIFA-8 | `moved under HIFA-8` |
| `parent_id` | HIFA-8 | – | `removed it from HIFA-8` |
| `parent_id` | HIFA-8 | HIFA-9 | `moved it from HIFA-8 to HIFA-9` |
| `label` | – | bug | `added the label bug` (the name renders as the chip of §2.5.5) |
| `label` | bug | – | `removed the label bug` |
| `relation` | – | blocks HIFA-33 | `marked it as blocking HIFA-33` |
| `relation` | blocks HIFA-33 | – | `removed the blocking relation to HIFA-33` |
| `estimate` | – | 3 | `set the estimate to 3` (the trim is the grammar's, not the storage's: SPEC-030 §2.4 stores `3.0` and this row renders it `3`, and every estimation scale is whole, so the rule removes a trailing `.0` and nothing else) |
| `estimate` | 3 | – | `cleared the estimate` |
| `due_date` | – | 20 Nov 2026 | `set the due date to 20 Nov 2026` |
| `due_date` | 20 Nov 2026 | – | `removed the due date` |
| `due_date` | 20 Nov 2026 | 21 Nov 2026 | `moved the due date from 20 Nov 2026 to 21 Nov 2026` |
| `branch_name` | old | new | `changed the branch name` (a slug of the title the row above already shows) |
| `board_order` | a | b | `reordered it in the column` — the row the read did **not** drop, i.e. every `board_order` row whose predecessor by `id` is not a `state_id` row (SPEC-030 §2.6's `WHERE`, SQL-7 row 11). A rank a move produced never reaches this table, so there is no conditional rendering here: the event the move names is the `state_id` row above it |
| `started_at` | – | ts | `started work` |
| `completed_at` | – | ts | `completed it` |
| `completed_at` | ts | – | `cleared the completion` |
| `canceled_at` | – | ts | `canceled it` |
| `canceled_at` | ts | – | `cleared the cancellation` |
| `archived_at` | – | ts | `archived the issue` |
| `archived_at` | ts | – | `restored the issue` |
| `trashed` | 0 | 1 | `deleted the issue` |
| `trashed` | 1 | 0 | `restored it from the trash` |
| `project_id` | – | name | `moved to project X` / `removed it from project X` |
| `milestone_id` | – | name | `set milestone Y` / `removed it from milestone Y` |
| `cycle_id` | – | name | `added to cycle Z` / `removed it from cycle Z` |
| anything else | | | `changed <field>` (§2.5.7.1) |

`comment_added` has **no row here** because no writer produces one: the table has a sentence for every
`field` a writer can store (SPEC-030 §2.4, 21 values) and none for a field nobody writes. The revision
that lands HIFA-6's history row adds its sentence **in this table**, in the same PR that adds the writer
(D-A binds it).

**`started_at` has one row for the same reason.** It has a direction and no second one: `apply_milestones`
stamps it on entering a `started` state when it was empty and never clears it (the return to an open type
nulls `completed_at` and `canceled_at` only), so `cleared the start time` was the rendering of a row no
writer could store and it is gone. `completed_at` and `canceled_at` keep both rows because both of their
writers exist (SPEC-030 §2.4).

#### 2.5.7.1 The fallbacks, as rules

Four rules, and every one of them is a rule about the formatter and not about a template:

1. **A ULID never reaches the screen.** Not in the sentence, not in a `data-*` attribute that a CSS
   `content:` could print, not in a `title`. The raw pair is on the row as `data-old`/`data-new` (P7) and
   a `data-*` attribute is not text; the sentence always carries a **label**.
2. **A target that is gone or unknown renders `<kind> (deleted)`** — `state (deleted)`, `member
   (deleted)`, `HIFA-8 (deleted)`. It is not `—`, not the key, and not an exception: the row is a fact
   about the past and the entity it names may no longer be there. The kind is the field's: `state_id` →
   `state`, `assignee_id` → `member`, `parent_id` → the issue's identifier.
3. **A missing side renders the direction's own sentence.** `priority` with no new value is
   `cleared the priority`, `assignee_id` with no new value is `unassigned`, `archived_at` with no new
   value is `restored the issue`. There is **no dangling arrow** anywhere: a row whose old side is empty
   never renders `→ x`, and the sentence for the direction exists or the row falls to rule 4.
4. **An unknown actor renders `someone`**, and an unknown field renders `changed <field>` — the field's
   own name, in the reader's words only when the table above has a sentence for it. Neither is a refusal:
   the formatter answers, it never raises (SPEC-040 §2.2's `raises: nothing`).

#### 2.5.7.2 Layout and placement

| Rule | Value |
|---|---|
| placement | after the description, **before** the comments section, in the panel and on the page |
| scroll | header and footer stay pinned; only the panel body scrolls, and the timeline is the end of that body (HIFA-45) |
| order | **oldest-first**, `(created_at, id)`: `created` is the first row, the newest event the last (D-D) |
| last-activity line | under the title, one line, dim: `Ginkelte moved from Todo to In Progress · 2 h ago` — `last_activity.text`, and `last_activity.row_id` is the anchor a click scrolls to. `None` renders no line |
| day separators | `11 Nov 2026`, `position: sticky`, rendered **only when the day changes** — the read model marks the row (`history_item.html` prints the separator of the day it opens) |
| rail | a 1 px vertical line with a dot per row; the dot takes `dot_color` for a state event and `--border` otherwise. The rail is decoration: it is `aria-hidden` and carries no information of its own |
| time | relative in the row (`2 h ago`, `3 d ago`), the ISO instant in `<time datetime>`, the absolute stamp in its `title`. Never only relative (§2.5.7.4) |
| both hosts | the panel's body and the page use the same partial; the page does not re-sort, re-slice or re-format |

**The panel truncates at 8 and the page prints the window (D-C).** `history_panel` holds the first 8 rows
and `history` all of them. **The control belongs to the panel and to nothing else:** a panel whose
`history_hidden > 0` renders `Show all N` under its list, where **N is `history_window`** — the rows the
read returned, `HistoryPage.window_size`, which is **the window and not a total** (≤100, SQL-7 row 11) —
while the page's `history_hidden` is **0 by construction**, because the page already prints the window and
a control that expands what is on screen is a `button` that changes nothing. The control is a real `button`
with
`hx-get="/_/issues/{identifier}/history?limit=100"` and `hx-target="#history-list"`,
`hx-swap="outerHTML"` (§2.1.3): it expands **in place** and it does not navigate, and the label says
`Show all N` because N is what the swap will put on screen.

**A full window is a floor, and the page says so once.** An issue with 120 rows returns a window of 100, so
the panel's button reads `Show all 100` and after the swap the list holds those 100 rows — the 20 oldest of
the issue are not on the page and never will be through this control, because the read asks for `LIMIT 100`
and this edge adds no `COUNT(*)` (§2.5.7.3, and §7's 120-row scenario asserts the two lines together). What the reader must not be left to
infer is that 100 is everything: when `history_truncated` is true the section prints **one dim line**
`the last 100 events` under the list, on the page and after the expansion, and the fragment carries no
button of its own because the swap has already replaced the panel's cut with the whole window. The page
carries no button at all for the same reason one step further: it is already showing the window the swap
would produce. On the panel the two sentences travel together — `Show all 100` and `the last 100 events` —
and neither claims to be a total: the first names what the swap shows, the second names what the read could
reach.

#### 2.5.7.3 The states of the section

| state | rendering |
|---|---|
| loading (htmx) | 3 skeleton rows at a real row's height (`44px`), so the list does not jump when it arrives |
| empty | **nothing**: no header, no `No activity`, no empty state. A `created` row always exists, so an empty list is a bug of the read; a section that says "no activity" about an issue that exists is worse than a gap |
| single row | one row, no counter, no `1 event` chrome |
| long | panel: `history_panel` (8) + `Show all N` (D-C); page: the whole window — the same rows, and **the same element count as the payloads of the other two edges** (the read's) |
| window full | `history_truncated`: one dim line under the list, `the last 100 events`, on the page and after the expansion. It never replaces the `Show all N` control and never claims the total |
| error | inline `history unavailable`, dim, in the section's place: never a blank gap and never a stack trace. The rest of the page renders — a history that cannot be read does not take the issue with it |

`history unavailable` is the section's own sentence and it is not a `form_error`: no request failed, the
collection could not be assembled. It is the one string of this section a template may print as a
literal, because it is not a §6 refusal.

#### 2.5.7.4 Density and accessibility

| Rule | Value |
|---|---|
| one event per line | at 320 px a row may wrap to **two** lines and never to three: the sentence wraps, the time does not (`white-space: nowrap`) |
| never a table | no column headers, no `field`/`old`/`new` columns, no `→`: the table this section replaces is the defect the issue reports |
| no keys | no `state_id`, no ULID, no `field` name on screen (rule 1 of §2.5.7.1) |
| numbers | only where they mean something: the `N` of `Show all N` and the relative time |
| markup | the list is an `<ol id="history-list">`, each event is one `<li>`, the timestamp is a `<time datetime="2026-11-11T18:35:35Z">` |
| accessible name | *"Ginkelte moved from Todo to In Progress, 2 hours ago"* — the actor, the sentence and the time, in that order, set once as `aria-label` on the `<li>`. The avatar's initials are `aria-hidden` (the name is already in the sentence) and **the absolute stamp in `<time title>` is not part of the name**: the `<li>`'s explicit name replaces the name its subtree would compute, so an AT announces the instant once, as the row's tooltip and not a second time as text |
| target size | every row is at least **44 px** tall (the `.card` rule of §2.5.6 has the same floor) |
| nothing hover-only | the old title and the absolute timestamp are in `title` attributes **and** the row is focusable (`tabindex="0"`), so a keyboard user reaches both; the `Show all N` button is a real `button`, not a click handler on a `div` |
| no horizontal scroll | the section never widens the page at 320 px: `overflow-wrap: anywhere` and `min-width: 0` on the sentence, as the search hits of §2.5.6 do |

**The formatter is `app/`'s, and this layer owns no prettifying step (P2/P4).** The sentence, the three
labels, the dot colour, the size of the window and the one-line summary come from SPEC-040 §2.2's
`history_rows` and its `HistoryPage`, which the CLI verb of SPEC-050 §2.5.3.3 calls with the same arguments
and prints in the same words. This layer decides
**layout** — order, separators, the 8-row cut, the rail, the `Show all` control, the dim line of a full
window — and nothing else: a
Jinja template that built `moved from {{ a }} to {{ b }}` would be a second grammar, and the CLI would
have to match a string that exists only in a `.html` file. `domain/` is out of it entirely: the labels
arrive as strings from SPEC-030 §2.6's maps (and, for `priority`/`due_date`, from the two constants of
SPEC-040 §2.2), and a state's display name is not a concept the domain layer is asked to know.

#### 2.5.8 The inline field's classes (HIFA-114)

The seven wrappers of §2.6.3 are seven partials that print one skeleton (§2.2), so the classes they print are a
contract the stylesheet owes once, and each of them is declared here — with the rule this surface adds, or with
the one it already carries:

| Class | Rule | Where it prints |
|---|---|---|
| `.field-inline` | `margin: 0 0 6px; min-width: 0;` | the wrapper `<div>` of every field, on both surfaces |
| `.field-value` | `display: flex; gap: 8px; align-items: baseline; cursor: text;`, and `.field-value[data-readonly] { cursor: default; }` with `.field-value[data-invalid] { color: var(--urgent); }` | the `<summary>` when the field is `writable`, and the value of the two control-less states, which prints `data-readonly` and `data-invalid` on both — a `<div>` on the archived `409`, a `<summary>` inside the trash's open `<details>` on the `422` (§2.6.3) |
| `.field-text` | `min-width: 0; overflow-wrap: anywhere;` | the value's element: a `<span>` for the six single-line fields, so a long title wraps at 320 px, and a `<div>` for `description`, whose value is block markdown under a `<summary>` |
| `.field-label` | the rule it already carries in `app.css` (block, 12 px, `--text-muted`) | the value state's field name |
| `.field-edit` | `display: flex; gap: 6px; align-items: flex-start; margin-top: 4px;` | the field's own `<form>`, the control state |
| `.field-save` | `flex: 0 0 auto; padding: 2px 8px;` | the in-field `Save` of every control |
| `.inline-input` | **the rule it already carries in `app.css:431`** — `.inline-input { width: 100%; max-width: 360px }`, unchanged; `.is-invalid` takes `border-color: var(--urgent)`, which is also already there. This surface adds no rule for it, so the control keeps the 360 px cap and the row that uses the class does not gain a border, a background or a padding it does not have | the `<input>`/`<select>`/`<textarea>` inside `.field-edit`, and the title control of the list row (`partials/issue_row.html:21`), which is why the class is shared and not redeclared |
| `.detail-fields` | `display: grid; grid-template-columns: max-content 1fr; gap: 6px 12px;` | the `assignee`/`due_date`/`estimate` row of the detail body (§2.6.3) |

**Retirement is the same rule as addition: a class whose last consumer leaves goes with it, and the grep is the
evidence — for the class that stays, too.** `.form-title` and `.issue-edit` go because their only two consumers
were the two pooled forms (§2.6.3, D-080). `.form-actions` goes for the same reason: `grep -rn "form-actions"
src/` answers `issue_detail.html` and `partials/issue_panel.html` — the `Save` row inside those same two forms —
plus `static/css/app.css` twice, the rule and its `.confirm .form-actions` variant, and that variant's selector
matches nothing, because the four `<details class="confirm">` blocks of §2.6.9 print no `form-actions` element:
it is already dead and dies with the rule. `.field` goes too: `grep -rn 'class="field"' src/` answers seven
lines, all in `partials/issue_form_fields.html`, the partial this ticket deletes. `.detail-title` and
`.panel-title` go with the two headings their wrappers replaced: `grep -rn "detail-title\|panel-title" src/`
answers the two rules of `static/css/app.css` and nothing else — no template prints either class, because the
`<h1>` of the detail and the `<h2>` of the panel left with the seven wrappers (§2.6.3).

**`.field-input` stays, and the grep says so.** `grep -rn "field-input" src/` answers **eight** template lines:
seven in that same `partials/issue_form_fields.html`, which leave with it, and **one that does not** —
`partials/comment_composer.html:17`, the comment box, whose `class="field-input"` carries the rule
`textarea.field-input { min-height: 96px; font: 12px/1.4 var(--mono) }` of `app.css:444`: the 96 px box and the
mono face a comment is written in are that selector's, so deleting it would degrade a surface this ticket does
not touch and leave a live class with no rule. The class therefore stays, the rule stays, and only the seven
lines of the deleted partial go. The field wrapper's own controls take `.inline-input` — the row's class, with
the row's existing rule — and never `.field-input`, so the two never merge: `.field-input` is the composer's,
`.inline-input` is the one-line editor's. `.field-label` **stays** as well — the value state prints
`<span class="field-label">` in the new wrapper (§2.6.3) — which is why the three look alike in the grep and
only two of them retire.

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
  element that had it. The overlay is a native `<dialog>` opened with its modal method, not a `div` with
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
| `Esc` | a field control is open, no overlay | closes that field's `<details>` without a request: nothing was written (§2.6.3) |
| `Esc` | panel open, no overlay | closes the confirmation block of §2.6.9 if it is open, otherwise closes the panel |
| `Esc` | no overlay, no panel, with filter chips | removes the last filter chip |
| `Esc` | the search box of `/search` has focus (§2.1.8) | clears the box and keeps focus; the page does not submit itself |
| `Enter` | focused row | navigates to the detail (`location.assign`) |
| `Cmd/Ctrl+Enter` | any control of a `.field-inline` wrapper (§2.6.3), the comment composer of §2.6.10, or the quick-add | submits the form it is focused in |

Implementation rules:

- Shortcuts are ignored if `event.target` is `input`, `textarea`, `select` or `[contenteditable]`
  — except `Esc` and `Cmd+Enter`.
- `Cmd/Ctrl+Enter` submits the `form` the focused control belongs to (`form.requestSubmit()`), and it runs
  **before** the editable-target rule returns: it is how the multi-line `description` control of §2.6.3
  saves, because `Enter` in a `<textarea>` is a newline and never a submit. **HIFA-114 wrote that branch,
  and the tree has carried it since:** until then the shell's `keydown` handler returned as soon as
  `event.metaKey || event.ctrlKey` was set, before it examined a key, and nothing under `src/hifa/web/`
  called `requestSubmit()`, so the rule was a contract with no client behind it. The branch now runs first
  (`app.js:328-335`), and the table row above that names a `.field-inline` control is the one that depends
  on it.
- The submitted form must sit inside `.field-inline`, `.comment-composer` or `#quick-add` (`closest`; the
  client's `SUBMIT_SCOPE`). A `<details class="confirm">` form of §2.6.9 is **never** submitted by the
  shortcut: the confirmation is one click away and a key that fired it would be destructive.
- `Esc` closes the nearest open field control (`closest .field-inline details[open]`) **before** the panel
  branch runs, so one key never means two things: a reader who is typing in a field that sits in the panel
  closes the field first and the panel next. **HIFA-114 wrote that ordering, and the tree has carried it
  since:** until then the shell's `keydown` answered `Escape` with `closePanel()` and returned — there was
  no `details[open]` lookup and no field branch before it — so a field's `<details>` inside the panel
  closed only because the panel did, and a field outside the panel did not close at all. The field branch
  of `app.js:337-347` is what makes the ordering above the shipped behaviour and not a promise for later.
- `j`/`k` move real focus (`element.focus()`), not a class: the row has `tabindex="-1"` and
  `:focus-visible` with the outline of §2.5.3. Focusing does `scrollIntoView({block: "nearest"})`.
- Focus state survives an htmx swap, and the swap decides where it goes: `htmx:afterSwap` returns it by
  `data-field` to the wrapper of the field the answer replaced (§2.6.3) and by `data-identifier` to the row
  or card — **except** the swap that opens the panel, whose destination is the panel itself
  (`<aside id="panel" tabindex="-1">`, §2.6.9) and which returns before the `data-identifier` branch
  (`app.js:267-275`), so closing that panel leaves focus on `<body>`: the row that asked never takes it
  back, and nothing here promises a restoration the client does not perform.
- In the board, `j`/`k` walk the DOM in column order (left→right) and `board_order` within each one.
- The shortcuts are documented in `?` (not implemented in v1) and in the status-line help at the
  bottom, which is text, not a menu.

#### 2.6.3 Optimistic inline editing

**The field is the control.** The value on the page is the thing the reader clicks; it opens in place into an
input, a select or a textarea, and what it saves comes back as the value again. There is no second control to
go and find and no form that sends seven values at once.

It is the behaviour the list row already has (§2.3.3), applied to **seven** editable fields of an issue on
**both** detail surfaces — `GET /{team}/issue/{ref}` (`issue_detail.html`) and the panel
(`partials/issue_panel.html`): `title`, `description`, `priority`, `state`, `assignee`, `due_date` and
`estimate`. Each one is edited **alone**: a request carries one control's field and nothing else. The pooled
`<form class="issue-edit">` with its `<h2 class="form-title">Edit</h2>` (the detail page) and its
`<h3 class="form-title">Edit</h3>` (the panel) is **retired**, and the stylesheet's `.form-title` and
`.issue-edit` rules go with it: their only two consumers were those two forms, checked with
`grep -rn "form-title\|issue-edit" src/` before the rule was deleted (D-080).

**There is no client-side state machine.** The reveal is the browser's `<details>`/`<summary>` — the
script-free mechanism §2.6.9 already uses for the confirmations — so no Alpine component, no `x-data` and no
client flag decides which state a field is in. **The server decides** (`vm.fields[<name>]`, §2.3.4), and the
only thing a click changes is the `<details>`'s own `open` attribute. That is what keeps the editor working
with JavaScript switched off: the control state is **always in the document**, as the `<details>`'s content,
not fetched and not built by a script.

```
click the summary of field-title-ENG-13
  → the browser opens the <details>: the control is already there, holding the stored value
  → htmx records that value as the control's `lastValue` when it processes the node, and `blur changed`
    compares against it: a reader who opens the field and leaves it untouched sends nothing
Enter or blur (text), change (select), Cmd/Ctrl+Enter or Save (description)
  → PATCH /_/issues/ENG-13?view=field&field=title
    HX-Request: true
    Content-Type: application/x-www-form-urlencoded
    body: title=Fix+SSO+login+%28Okta%29
  → hx-target="closest .field-inline", hx-swap="outerHTML", hx-sync="closest .field-inline:replace"
  → 200: the server answers that wrapper in its value state; it replaces the node
         (HX-Trigger: issue-updated, the event the client turns into its `issue updated` toast)
  → 422: beforeSwap enables the swap, the wrapper comes back `open`, the control carries .is-invalid and
         the message sits beside it — with the server's value, not the reader's (R7)
  → 409: the issue is archived. The wrapper comes back with the server's value, the message and neither
         a `<details>` nor a control (`writable: False`, §2.3.4): the only write left on it is Restore
         (§2.6.9). The swap is the listener's: until HIFA-114 that listener enabled `422` alone, so a `409`
         died as the generic `could not save` toast; the ticket added the status and the `HX-Retarget`, and
         the tree has carried both since `79a6f80` (§2.1.3)
  → 422 ISSUE_TRASHED: the issue is in the trash. The wrapper comes back `open`, the value marked and the
         message beside it, and **no** write control (`writable: False, open: True`): the only write left
         is Restore (§2.6.9, R29). The status is 422 and not 409 — §6 keeps the two apart
  → 404: nothing is kept — partials/fragment_error.html is appended to #toasts, under
         HX-Retarget: #toasts and HX-Reswap: beforeend, and the field is untouched (§2.1.3, §6)
```

Rules:

- **The wrapper carries the three states of the field and is the element a `PATCH` answers.**
  `<div class="field-inline" id="field-{name}-{identifier}" data-field="{name}">` holds a `<details>` whose
  `<summary class="field-value">` is the value and whose content is the `<form class="field-edit">` with the
  control; when `vm.fields[name].writable` is `False` the control is not printed at all (§2.3.4's table).
  `id` is what the answer of §2.1.3's `view=field` renders and what the fragment is named by; `data-field` is
  what `htmx:afterSwap` focuses after a save (§2.6.9's focus table). The whole file of one field, its three
  states, exactly — `field_title.html`, the simplest of the seven; the other six differ only in the literal
  name, the value key of the table below and the control element:

```html
<div class="field-inline" id="field-title-{{ vm.identifier }}" data-field="title">
  {% if vm.fields["title"].writable %}
  <details{% if vm.fields["title"].open %} open{% endif %}>
    <summary class="field-value">
      <span class="field-label">Title</span>
      <span class="field-text">{{ vm.title }}</span>
    </summary>
    <form id="edit-title-{{ vm.identifier }}" class="field-edit" method="post" action="{{ vm.edit_action }}"
          hx-patch="{{ vm.edit_fragment }}?view=field&field=title"
          hx-target="closest .field-inline" hx-swap="outerHTML"
          hx-sync="closest .field-inline:replace" hx-disabled-elt="find button"
          hx-disinherit="hx-disabled-elt">
      <input type="text" name="title" value="{{ vm.title }}"
             class="inline-input{% if vm.fields['title'].invalid %} is-invalid{% endif %}"
             aria-label="Title of {{ vm.identifier }}" autocomplete="off"{% if vm.fields['title'].invalid %} aria-invalid="true"{% endif %}
             hx-patch="{{ vm.edit_fragment }}?view=field&field=title"
             hx-trigger="blur changed"
             hx-target="closest .field-inline" hx-swap="outerHTML"
             hx-sync="closest .field-inline:replace">
      <button type="submit" class="btn field-save">Save</button>
      {% if vm.fields["title"].message %}<p class="form-error" role="alert">{{ vm.fields["title"].message }}</p>{% endif %}
    </form>
  </details>
  {% elif vm.fields["title"].open %}
  <details open>
    <summary class="field-value" data-readonly data-invalid>
      <span class="field-label">Title</span>
      <span class="field-text">{{ vm.title }}</span>
    </summary>
    {% if vm.fields["title"].message %}<p class="form-error" role="alert">{{ vm.fields["title"].message }}</p>{% endif %}
  </details>
  {% else %}
  <div class="field-value" data-readonly data-invalid>
    <span class="field-label">Title</span>
    <span class="field-text">{{ vm.title }}</span>
  </div>
  {% if vm.fields["title"].message %}<p class="form-error" role="alert">{{ vm.fields["title"].message }}</p>{% endif %}
  {% endif %}
</div>
```

- **A wrapper with no control is still a wrapper.** The two `writable: False` states exist so that a refusal
  with nothing to offer — an archived issue's `409`, a trashed one's `422` — answers the element that was asked
  for instead of a toast, and so that a `GET` of such an issue renders its fields as values rather than as
  form-shaped furniture: the value is the same `<span class="field-label">`/`<span class="field-text">` pair,
  printed with `data-readonly` and `data-invalid` and no control on both states — `data-readonly` for the
  default cursor, `data-invalid` for the `.field-value[data-invalid]` colour of §2.5.8 — and the message is the
  `.form-error` line, which is where a reader learns why there is nothing to click. The `409` prints that value
  in a `<div>` with no `<details>` at all — there is nothing to disclose — and the trash's `422` prints it as
  the `<summary>` of the one `<details open>` that carries its message, so an empty `<details open>` is never
  printed either.
- **The form sits inside the field, and the control is inside the form.** So unlike the row's markup below,
  the field forms are **not** `hidden` and carry **no** `hx-include`: the control is a child of its own form,
  which is what htmx serializes by default (`hx-include` cannot reach a node by id here because the id would
  be the form the control already owns — one selector for one fact). The list row's markup below needs `form=` and
  `hx-include="#edit-{identifier}"` for the one reason a field wrapper does not: a `<form>` that is a child of
  a `<tr>` is foster-parented out of the table by the HTML parser, so there it has to be hidden, and its
  control has to name it twice. On neither surface may a copy of that shape be moved into a `<tr>`.
- **Every control state carries a `Save`.** It is the no-JS submit for the six fields that have no implicit
  one — a `<select>` and an `<input type="date">` do not submit on `change` without a script — and it is the
  description's required affordance. With JS the same button is harmless: the `form`'s `hx-patch` intercepts
  the submit, exactly as `Enter` does.
- **One `PATCH` in flight per field**: `hx-sync="closest .field-inline:replace"` discards that field's
  previous request and never queues it, and it leaves a neighbouring field's request alone — the same rule as
  the row's `closest tr:replace`, scoped to the smaller node.
- **The guard over a double submit stops at the form.** The form asks for it with
  `hx-disabled-elt="find button"`, and htmx resolves `find` against the element that **emitted** the request:
  inherited by the control of a `blur changed`/`change` save, the same attribute would match no `<button>`
  there, cover nothing and log a miss on every one of those saves. `hx-disinherit="hx-disabled-elt"` on the
  form stops the inheritance, so the guard covers the submits (`Save`, `Enter`, `Cmd/Ctrl+Enter`) — which is
  the double send it exists for — and the control's own saves keep the `hx-sync` of the rule above.
- **The seven controls, their value and their trigger.** The value state prints a key of §2.3.3/§2.3.4; the
  control state names the field of §2.1.2:

| Field | Value state | Control state | Trigger |
|---|---|---|---|
| `title` | `vm.title` | `<input type="text" name="title" value="{{ vm.title }}">` | `blur changed`, `Enter` |
| `description` | `vm.description_html` (sanitized markdown, §2.3.6) | `<textarea rows="6" name="description">{{ vm.description_raw }}</textarea>` | `Cmd/Ctrl+Enter` (§2.6.2) or `Save`; **never** `blur` |
| `priority` | `vm.priority_glyph` + `vm.priority_label` | `<select name="priority">` over `vm.priority_options` | `change` |
| `state` | `vm.state_color` dot + `vm.state_name` | `<select name="state">` over `vm.states` | `change` |
| `assignee` | `vm.assignee_name`, or `–` | `<input type="text" name="assignee" value="{{ vm.assignee_id }}">` | `blur changed`, `Enter` |
| `due_date` | `vm.due_label` (`.is-overdue` when `vm.is_overdue`) | `<input type="date" name="due_date" value="{{ vm.due_value }}">` | `change` |
| `estimate` | `vm.estimate_label` | `<input type="number" step="any" name="estimate" value="{{ vm.estimate_value }}">` | `blur changed`, `Enter` |

- **`description` is inline, and that is a deliberate change of this contract.** It used to be the one field
  the panel edited, with a `<textarea>` and a `Save`, and §2.1.3 used to justify `?view=panel` by it. It is now
  a field like the others, with one difference that is the whole reason this paragraph exists: **`Enter` in a
  `<textarea>` is a newline and never a submit**, so the multi-line field saves on **`Cmd/Ctrl+Enter`** —
  §2.6.2's rule, "submits the form it is focused in", which **HIFA-114 shipped**: the `keydown` listener
  returns on `metaKey`/`ctrlKey` only after that branch, so `requestSubmit()` runs before the
  editable-target rule (the branch is `app.js:328-335`, §2.6.2) — **or** on the `Save` that sits inside the
  field. `blur` is not a trigger here and must not be added:
  a reader who clicks away from a half-written note has not asked for it to be committed, and every other
  field can afford that bet only because its value is one line. Nothing else about the field changes: the
  value state is the rendered markdown and the control state is the stored source, which is what
  `description_raw` is for (§2.3.4).
- **`assignee` is a text input holding the member id, and that is what the read model can offer.** The
  view-model's `assignees` list is `[]` — `src/hifa/web/readmodels.py` builds `"assignees": []` inside
  `build_issue_vm`, so neither surface has a roster to render a `<select>` from — and `IssueDetail.members`
  is a `member.id → display_name` map of the **comment authors and history actors** of that issue
  (SPEC-030 §2.6, SQL-7 row 13), not the team's members. The roster read exists in `app/`
  (`active_members(repos)`) and nothing under `src/hifa/web/` calls it. So the control is
  `<input type="text" name="assignee" value="{{ vm.assignee_id }}">`, the member id or email the command
  resolves (`EDIT_COMMAND_FIELDS['assignee'] → assignee_id`, `app.py`), and an empty value unassigns. A member
  picker is a separate ticket, and this section does not pretend to have one: a `<select>` over an empty list
  would be an input nobody can use, and a template that re-derived the roster would be a second read.
- **What a save sends is one field.** The body is `application/x-www-form-urlencoded` — htmx's own encoding of
  the form — with the one control the form holds; an absent field changes nothing (R5 of SPEC-060) and an
  empty string clears the four the command lets be cleared (`description`, `assignee`, `due_date`,
  `estimate`), while `title` empty is a `422` and `state` empty means "unchanged" (`EDIT_CLEARABLE` in
  `app.py`). No extension and no request-rewriting hook: the single JSON body under `/_/` is the revision probe
  of §2.1.3.1 (D-049), and a body relabelled `application/json` is refused `422 invalid request` — what the
  shim this section used to prescribe produced in a browser, and why it is gone (D-056).
- **A refusal lands on the field that was refused, and the message is beside it.** `422` and `409` answer the
  wrapper itself (§2.1.3) — never the top of the aside and never the top of the page: on a `422` whose
  field is writable the wrapper comes back `open`, and the control keeps `.is-invalid`, `aria-invalid="true"`
  and the sentence of §6 in a `.form-error` next to it; on the trash's `422` it comes back `open` too, as the
  `<details open>` whose `<summary>` is the marked value and whose content is the message and no control;
  on the archived issue's `409` it comes back **without** a `<details>` — a `<div class="field-value"
  data-readonly data-invalid>` with the value and the message (§2.3.4's table, the `False | False` row). On
  the panel that is what makes a refused `title` a sentence under the title instead of a
  line above the footer. The `<p class="form-error">` line stays for a refusal with no field of its own, which
  is the layer writes' shape (§2.6.9, `partials/issue_panel.html`). Both statuses reach the wrapper through
  the swap the `htmx:beforeSwap` listener enables (§2.1.3) — `422` and `409` are both enabled in the tree —
  because a `4xx` htmx
  does not swap leaves the node where it was and turns the refusal into a toast with no field marked.
- **An issue in the trash is refused `422` on that same wrapper, and the read behind the answer has to see the
  trash.** `ISSUE_TRASHED` reaches `?view=field` like any other field refusal (§6, R29): the wrapper comes back
  `open` and `writable: False` (§2.3.4's middle row) — the value marked with `data-readonly data-invalid`,
  the message `ENG-13 is in the trash; restore it first` in its `.form-error`, the disclosure open — and
  **without** a
  write control, because R29 offers `Restore` alone. That answer only exists if the read behind
  `_issue_edit_fragment` passes `include_trashed=True` for `view == "field"` as it already does for
  `view == "panel"` (`app.py`): with the keyword missing, the read raises `NotFound` and the natural
  implementation answers `404` for a state the contract refuses with `422` — a resolution failure dressed as a
  refusal. The field and the panel are the two surfaces R29 names, so the keyword travels with both and with
  neither the row nor the card.
- **`blur changed` (not a bare `blur`)** avoids the `PATCH` when the reader opened the field and left without
  touching anything. `changed` is htmx's own modifier and it compares the control's value with the `lastValue`
  **htmx itself** recorded for that element when it processed the node (and again after each request) — not
  with any attribute of the markup. `data-original` is not a mechanism of this layer: `grep -rn
  "data-original" src/` answers exactly one line — the printed attribute in `partials/issue_row.html:20` — and
  no script of the tree reads it. The wrapper therefore does not print it, because it would carry a value
  nothing consults; and the row keeps the one it prints today, because the row is not this ticket's (§2.6.3's
  ninth rule, requirement 9): a `data-original` removed from `issue_row.html` would be a change to a surface
  this contract leaves exactly as it is, and an unnecessary one.
- **The server is the truth, and the only optimism is that the control keeps what the reader typed until the
  answer lands.** The response replaces the node, so there is nothing to reconcile; a refused value is
  discarded and the server's value is what comes back (R7). Focus returns to the `<summary>` of the
  `[data-field="{name}"]` wrapper that the response brought back in value state, to the marked control itself
  (`.is-invalid`, a focusable node) on a `422` or a `409` that still carries one, and on the archived issue's
  `409` it does not move at all, because that wrapper is a `<div class="field-value" data-readonly
  data-invalid>` with no `tabindex` and there is nowhere to put it: the message arrives through the
  `partials/fragment_error.html` toast and the wrapper's `.form-error`, and the reader hears it from the
  content change, not from a focus jump.
- **Keyboard.** `Enter` submits a text field (implicit submission of its form owner, the same `hx-patch`);
  `Esc` closes the open field with no request (§2.6.2); `Tab` leaves the control for the next field's
  `<summary>`, because a closed `<details>` contributes its summary and nothing else to the tab order — so the
  seven fields are seven stops and not fourteen. On the archived issue, whose wrapper has no `<details>`,
  there is no stop at all: the value is a `<div>` and the wrapper contributes nothing to the tab order; on the
  trash's `422` the wrapper is a `<details open>` and its `<summary>` is a stop, the disclosure the reader
  can close (§2.3.4).
- **The row of the list does not change.** `partials/issue_row.html` keeps its hidden form owner, its
  `form=`/`hx-include=` pair, its `hx-trigger="blur changed"` and its `hx-target="closest tr"`, and a `PATCH`
  to `?view=list` keeps answering the `<tr>` — the row is one line in a table, the wrapper of this section is a
  block on a page, and the shape that solves the foster-parenting hazard of a `<tr>` is the one thing the two
  must not share (§2.6.6, D-080). That markup, unchanged — attribute for attribute, the `data-original` it
  prints today included, which the wrapper does not have (§2.6.3's `changed` bullet):

```html
  <td class="cell-title">
    {% if row.can_write %}
    {#- Each inline control has `name` and `form`, and the form it names is a
        hidden `<form method="post" action="/{team}/issue/{id}">` of its own — the no-JS
        fallback, and the form Enter submits through. It sits inside the cell (a `<form>` as a
        child of `<tr>` is foster-parented out of the table) and it is hidden, so the control is
        associated by id and `hx-include` names that id instead of `closest form`. -#}
    <form id="edit-{{ row.identifier }}" class="inline-edit" method="post" hidden
          action="/{{ row.team_key }}/issue/{{ row.identifier }}"
          hx-patch="/_/issues/{{ row.identifier }}?view=list"
          hx-target="closest tr" hx-swap="outerHTML" hx-sync="closest tr:replace"></form>
    <input type="text" name="title" form="edit-{{ row.identifier }}" value="{{ row.title }}" data-original="{{ row.title }}"
           class="inline-input{% if row.title_invalid %} is-invalid{% endif %}"
           aria-label="Title of {{ row.identifier }}" autocomplete="off"{% if row.title_invalid %} aria-invalid="true"{% endif %}
           hx-patch="/_/issues/{{ row.identifier }}?view=list" hx-include="#edit-{{ row.identifier }}"
           hx-target="closest tr" hx-swap="outerHTML"
           hx-trigger="blur changed" hx-sync="closest tr:replace">
    {% else %}
    <a class="title" href="{{ row.url }}">{{ row.title }}</a>
    {% endif %}
    {% if row.form_error %}<span class="form-error" role="alert">{{ row.form_error }}</span>{% endif %}
    {% for label in row.labels %}<span class="chip chip-label" style="--label: {{ label.color }}">{{ label.name }}</span>{% endfor %}
  </td>
```

  **Why the two shapes differ, in one place.** A `<form>` as a child of `<tr>` is foster-parented out of the
  table by the HTML parser — the hazard the docstring of `tests/e2e/web/test_revision.py` names — so there the
  form is hidden, it is the first child of the `<td>` that holds the field, and the control names it twice:
  `form="edit-{identifier}"` (ownership, and the no-JS submit target) and `hx-include="#edit-{identifier}"`
  (what htmx serializes). `hidden` keeps it out of the layout, not out of the document, and
  `method`/`action` stay as the no-JS fallback (§2.6.6). `hx-patch` on the form is what turns a submit into a
  `PATCH`, and `Enter` in the control is the implicit submission of its form owner — the same `hx-patch`, so
  no second `hx-trigger` is declared. `hx-include` sends every field of that row's form, which here is one: a
  `PATCH` with an absent field deletes nothing (R5 of SPEC-060), so the payload can be generous.
- **Where the seven wrappers sit.** On the detail page `title` replaces the `<h1 class="detail-title">`,
  `description` replaces the `.description` block, `state` and `priority` replace the two read-only spans of
  `.detail-actions` (which keeps its class and its place in the header), and `assignee`, `due_date` and
  `estimate` render in a `.detail-fields` row of the body. On the panel `title` replaces the
  `<h2 class="panel-title">`, `state` and `priority` take the place of the `dl.meta` line under the header, and
  `description`, `assignee`, `due_date` and `estimate` follow it in the body. The `dl.meta` list stops echoing
  a field the body now controls: on the detail page it keeps `Project`, `Cycle`, `Parent`, `Labels`, `Created`
  and `Updated` (read-only, no write use case or another ticket — HIFA-106 for the first three, HIFA-61/HIFA-62
  for the labels) and loses `Assignee`, `Due` and `Estimate`; on the panel it is removed entirely, because
  `State`, `Priority`, `Assignee` and `Due` were all it held and all four are now fields.
- **Out of scope, and stated so nobody widens it here.** Inline editing on a **board card** (a card is a
  one-line summary and a `DblClick` editor there is its own ticket), `labels` as chips with a menu (the writes
  land with HIFA-61/HIFA-62), `project`/`cycle`/`parent` (no write use case carries them, HIFA-106) and
  comment editing (§2.6.10) are **not** part of this section.

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
- **The drop's request, its answer, and what it does with the body.** The request is
  `POST /_/issues/{identifier}/move` with `HX-Request: true` and the three keys of the payload above as
  the body, form-encoded like every other write of `/_/` (D-056; no `/_/` route parses JSON, §2.1.3.1).
  The answer is `204` with an empty body, `HX-Trigger: issue-moved` and `HX-Revision` (§2.1.3, §2.6.8).
  The drop re-renders nothing and swaps nothing: `Sortable` has already put the card in the column the
  reader dropped it in, so the DOM is where the reader put it and there is no element to replace. Of the
  route's two clients this is the one that renders nothing after a success — §5.7.1 is the other, and it
  is the whole of the difference between them. A **refusal** of the route answers the panel re-rendered
  plus `form_error` (§5.4), and the drop **reads the status and discards that body**: a panel is the one
  element the drop neither asked for nor opened, and inserting it would be the client deciding what the
  page shows.
- On `409`/`422`/`5xx` the drop takes its own recovery, because it threw the body away: it puts the card
  back where it was, reloads the collection it is showing — `/_/board/{team}` over `#board`, which is the
  `page["refresh_url"]` of §2.6.8 for that page — and shows a toast. The card is never left in a position
  the server did not accept, and the panel the refusal rendered is inserted nowhere.
- Gap exhausted: if `ranking.between` cannot place the issue (`RANKING_EXHAUSTED`), SPEC-040
  rebalances the scope and retries **once** in the same transaction (REQ-F040-08). The client does
  not take part: `"rebalance": true` does **not** exist in the payload. If the retry also fails, the
  response is `422` `RANKING_EXHAUSTED` and the client reloads the board.
- **The move control is not on the card.** It is a `<form method="post" action="/{team}/issue/{id}/move">`
  with a `<select name="state_id">` and a `Move` button — the options come from `issue_vm["states"]` and
  the value is the state `id` (§2.3.4) — and it lives in the panel footer and in the detail page's
  action group (§2.6.9), **always visible**: no `:focus-within`, no hover, a 44 px target. The rendered
  form sends `state_id` and nothing else — no neighbour, no position — so the move it makes is
  `position="last"`; the route also accepts `before`/`after` as identifiers when a control names them
  (§2.1.2), and no rendered control does. What its client does with the `204` of that form is §5.7.1's
  and not this section's: the footer's move is not the drop. A card carries no layer or move control: it
  is the drag target of this section, a `<select>` inside it would fight the gesture that owns it
  (§2.6.5), and a tap on a card outside a field control only opens the issue (§2.6.7).
- **Without JS** there is no drag and no panel: the same form is the path, from the detail page's action
  group, and it answers `303` to the detail — `state_id` alone puts the issue at the end of the target
  column (`position="last"`).

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
- In `--read-only` (`can_write=False`) ⇒ **`403`** with `partials/fragment_error.html`, the answer
  §6 declares for a write on a `/_/` route, and the board does not initialize the column instance
  at all (R18).
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
| Edit a field | yes: each field's `<details>` opens and its own `<form method="post">` saves the one value (§2.6.3, §2.1.2), on the detail page and in the panel | optimistic inline (§2.6.3): the same form `PATCH`es and the wrapper comes back for that one field |
| Comment | yes | `hx-post` + append |
| Move state | yes (`<select>` + button) | drag & drop on the board; the same `<select>` in the panel footer, whose answer re-reads the collection (§5.7.1) |
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
| Adopt | every `200`/`201`/`204` of this layer carries `HX-Revision` (§2.1.3), but **only an answer whose target is `#board` or `#issues-tbody` adopts it** as the page's known number. A panel, a row, the palette and the counters answer with the number the server observed — foreign commits included — and did not update the board, so they adopt nothing (D-050) |

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
and puts a filtered board under an input that still says so.

**The wait is bounded, and the retry is a request.** htmx has no request timeout of its own, so the
bound is the client's: `RELOAD_MS = 10000` in `src/hifa/web/static/js/app.js`, armed when the reload
is sent and cleared when its answer settles. A tick that found the previous request still in flight
gives up on it and asks again, so one hung request cannot silence the page for the rest of the
session (D-050). Clearing the client's own flag is not enough: htmx keys the request in flight on the
element that acts — `document.body` here, because the reload calls `htmx.ajax` without a `source` —
and while that slot is taken it queues the next call, resolves its promise at once and sends
nothing, so the retry would leave a page that looks alive over a board that never updates. The
watchdog therefore clears the flag, aborts the stale request on the body
(`htmx.trigger(document.body, "htmx:abort")`, htmx's own way of releasing the slot) and only then
asks the reload again through the same single path. The bound is on the wait, not on the answer: an
answer that arrives after it is ignored by its token and adopts nothing.

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

#### 2.6.9 The write actions: where they live, where focus goes, what a refusal looks like

The four writes of §2.1.2 that are not the field edit — move, archive, delete, restore — are reachable
from two places and only two: the **panel** (`partials/issue_panel.html`, its footer) and the **detail
page** (`issue_detail.html`, the action group of its header). The **edit** is not one of them and never was a
control of the footer: it is the fields themselves, on the row (§2.6.3) and on both detail surfaces (§2.6.3,
§2.6.6) — the value the reader clicks is the control, so there is no fifth button to place. The four that
remain are **layer and move writes**: they change where the issue sits or whether it is in the
collection at all, and they carry **no** control on a row or a card. On a row, a tap outside a field
control opens the detail and does nothing else (§2.6.7); a `×` on a row is the one control that turns a
scroll into a lost issue; and a `<select>` on a card would fight the drag that owns it (§2.6.4,
§2.6.5). Every control is visible at rest — nothing appears on hover or on `:focus-within` (P5), so the
move `<select name="state_id">` of §2.6.4 is a control of this footer and of this action group, not of a
row and not of a card.

The action group of the detail page carries these controls as **page forms without `hx-*`**: they post
to `/{team}/issue/{identifier}/…` and answer a page — `303` to `/{team}/list` for an archive or a delete,
`303` to the detail for a move or a restore (§2.1.2) — which is the no-JS path (R4). The panel's footer
carries them as **fragment forms**: `hx-post="/_/issues/{identifier}/{layer}"` with `hx-swap="none"`,
because the fragment answers `204` with no body (§2.1.3). What the client does with that answer is
declared once, in the flow of the write it belongs to — §5.7 for the three layer writes and **§5.7.1**
for `move`, the one of the four that does not take the issue out of the collection and still ends the
panel — and not repeated here. Neither copy carries `hx-target`: a `204` has no body to put anywhere, and
the element that asked is the panel that is about to close. There is no third surface and no form that
points at a page route with `hx-*`.

**The keyboard path is the tab order.** Every action is a `<form method="post">` with a `<button>` in
it — never a `div` with a click handler and never a control that appears on hover (P5). `Tab` reaches
the action group after the last editable field, `Enter`/`Space` submit the focused button, and no
global key is bound to any of these writes: `d`/`a` do not exist, because a single letter that trashes
an issue is one typo away from a lost issue. `Esc` closes the panel (§2.6.2) and, when the confirmation
block is open, closes the block first and leaves the panel open.

| Moment | What has focus |
|---|---|
| the panel opens | the panel itself (`<aside id="panel" tabindex="-1">`), focused once on swap |
| an inline edit is saved | the read-only value that replaced the control, on the re-rendered node with the same `data-field` |
| an edit is refused (`422`/`409`) | the field that failed: its control when the wrapper has one (`.is-invalid`), otherwise the wrapper itself, matched by `data-field` (§2.3.4's two `writable: False` states) |
| a confirmation is opened | the `confirm` checkbox, the first control of the block |
| a confirmation is refused (`422` `confirmation_required`) | the `confirm` checkbox, which carries `.is-invalid` and its message; the block is re-rendered with `open` |
| a confirmation is closed without submitting | the `<summary>` that opened it, which is still there and still focused |
| archive or delete succeeded | the collection container (`#issues-tbody` or `#board`): the panel is closed and the row it was opened from no longer exists |
| restore succeeded | the row `#issue-{identifier}` of the collection the client has just re-read, or that container when the page's filters leave the restored issue out: the panel closes here too, and the row it was opened from is back |
| a move from the panel footer succeeded | the row `#issue-{identifier}` or the card `#card-{identifier}` of the collection the client has just re-read, or that container when the page's filters leave the moved issue out: the panel closes here too (§5.7.1), and the issue comes back in the column the server put it in |

**A refusal is text next to the thing that failed, and it costs nothing.** The element answers `422`
(a domain rule said no) or `409` (the issue is archived, §6) with the **same element re-rendered** from
the read model plus `form_error` — the string the templates of this layer already read, next to
`form_errors` for a per-field dict (§6; `partials/quick_add.html` is the existing consumer): the field
carries `.is-invalid` and its message, the panel carries its `<p class="form-error">` line above the footer,
and a toast repeats it. The `error` key is not reused here: it belongs to `error.html` and
`partials/fragment_error.html`, which are pages and toasts, not the element the reader is standing on.
The value the reader typed is **not** kept — the element shows the server's value, because the write
was refused and the client is not allowed to hold a version of the row the database does not have (R7).
A `409` also says what to do next (`ENG-13 is archived; restore it first`), and the panel of an
archived or trashed issue renders `Restore` and nothing else.

**The gate is the action group's and `can_write` is the whole of it; the layer picks a branch inside,
never a second gate on the container.** The group renders when `vm.can_write` and nothing else
(`{% if vm.can_write %}` in both `issue_detail.html` and `partials/issue_panel.html`), and what it holds
is decided once, inside it:

- `vm.is_archived or vm.is_trashed` ⇒ the group holds `Restore` **alone**: the move
  `<select name="state_id">`, the archive `<details>` and the delete `<details>` are not rendered, and the
  seven field wrappers of §2.6.3 are built with `writable: False` (§2.3.4) so they render their value and no
  control (an archived issue's fields are refused `409`, a trashed one's `422`, §6).
- otherwise — a visible issue — ⇒ the group holds the move `<select name="state_id">`, the archive block and
  the delete block, and no `Restore`.

One gate, two branches, and the branches are exhaustive, so every issue this layer answers `200` for
renders a group with something in it. The gate is written out because the obvious shape is wrong twice
over. Gating the group on `not vm.is_trashed` would render **nothing** for a trashed issue whose page
answers `200`, shipping the refusal's own instruction — `ENG-13 is in the trash; restore it first` — with
no control to carry it out (D-059, §2.1.1). Gating the container on the layer while the other
controls carry its negation is the same mistake from the other side: the two conditions are disjoint, so
a **visible** issue would render the group with nothing in it — the move control, the archive block and the
delete block being the only markup the group has — which contradicts §2.1.2's write page forms and §5.7's
panel footer, where the `state_id` control, the archive block and the delete block all live. `Restore` is the one
write this layer offers an issue the trash holds: edit, move and archive on that issue refuse with
`ISSUE_TRASHED` (§6, SPEC-040 §6), and `delete`
is the no-op of that table, so the group's other controls being absent there is the page telling
the truth about what it can do.

**In the panel a refusal is carried by the response, not by the form.** The confirmation form posts with
`hx-swap="none"` because its success is a `204` with no body (§2.6.9 below, §2.1.3), so the `422` that
re-renders the open block names its own target: `HX-Retarget: #panel` with `HX-Reswap: innerHTML`, the
two headers §6 already gives a fragment's refusal. Without them the marked block would be fetched and
thrown away, which is the one way a refusal can cost the reader the block it refused.

**The confirmation is a `<details>` block with the form inside it, and it needs no script.**
`archive` and `delete` each render one `<details>` in the action group — the panel footer and the detail
page — whose `<summary>` is the action itself. The block is the same markup in both places; the **form's
attributes are the one difference**, and there are exactly two renderings of it.

**The detail page's copy is a page form**: `method`/`action`, no `hx-*`, and it answers `303`:

```html
<details class="confirm">
  <summary class="btn">Archive</summary>
  <p class="confirm-line">This takes ENG-13 out of every listing. Restore puts it back.</p>
  <form method="post" action="/ENG/issue/ENG-13/archive">
    <input type="checkbox" name="confirm" value="1" required>
    <label>Yes, archive ENG-13</label>
    <button type="submit" class="btn btn-danger">Archive</button>
  </form>
</details>
```

**The panel footer's copy is a fragment form**: the same block, the same body, `hx-post` to the `/_/`
route, `hx-swap="none"` and no `hx-target`, because that route answers `204` with no body (§2.1.3):

```html
<details class="confirm">
  <summary class="btn">Archive</summary>
  <p class="confirm-line">This takes ENG-13 out of every listing. Restore puts it back.</p>
  <form method="post" action="/ENG/issue/ENG-13/archive"
        hx-post="/_/issues/ENG-13/archive" hx-swap="none">
    <input type="checkbox" name="confirm" value="1" required>
    <label>Yes, archive ENG-13</label>
    <button type="submit" class="btn btn-danger">Archive</button>
  </form>
</details>
```

`method`/`action` stay on the panel's copy as the fallback the layer keeps everywhere (§2.6.3): with htmx
the `hx-post` is what fires, and without it the form still posts the page route.

**The `delete` block is that block with three strings replaced, and no markup of its own.** The markup,
the classes, the order, the two renderings (page form and fragment form) and the `<details class="confirm">`
that carries `open` on a refusal are the archive block's above, unchanged — which is exactly what
`16-dashboard.feature` asserts of it (`<details class="confirm" open>`, `name="confirm"`, `.is-invalid`).
What differs is the verb and the message, both declared here so that no implementer has to invent a
sentence a reader will see: the `<summary>` and the `<button>` read `Delete` (the button keeps
`class="btn btn-danger"`), the `<p class="confirm-line">` reads `This sends ENG-13 to the trash. Restore
brings it back.`, and the checkbox's `<label>` reads `Yes, delete ENG-13`; the form's `action` is
`/{team}/issue/{identifier}/delete` and its `hx-post` is `/_/issues/{identifier}/delete`. The `422`
`confirmation_required` re-renders that block with the catalogue's own sentence as its `form_error` —
`confirm the delete of ENG-13` (§6) — and that is the line the acceptance scenario greps
(`confirm the delete of ENG-11`). The two blocks do not share one sentence because the two layers are not
the same fact to a reader: an archive leaves the issue restorable from its own detail, and a delete puts it
one layer deeper, where only `Restore` on its page brings it back (§2.1.1, D-059).

**`Restore` is a bare form, because it has nothing to confirm.** It is the one control a reader presses
on an archived or a trashed issue, the same element in both places (the detail page's action group and
the panel footer), and it is written out like the two blocks above instead of being left to the
implementer:

```html
<form class="action-restore" method="post" action="/ENG/issue/ENG-13/restore">
  <button type="submit" class="btn">Restore</button>
</form>
```

A `<form>` with a `<button>` in the action group and **no** `<details>`: `restore` never asks (D-053), so
there is no `<summary>` to open first, no `name="confirm"` checkbox and no `confirm-line` — the whole
block is one button. The label is the button's own text, `Restore`, its class is `btn` and not
`btn btn-danger` (this write takes an issue out of a layer; it does not put one in), and the form's class
is `action-restore`, the only new class of §2.6.9. The panel's copy is that same form with
`hx-post="/_/issues/ENG-13/restore"` and `hx-swap="none"`, no `hx-target`, and `method`/`action` kept as
the fallback of §2.6.3 (§2.1.3).

**Why `<details>`, and what it costs.** It is the one native element that gives a visible, always
present control with a closed and an open state, driven by `Tab` + `Enter`/`Space` and a tap, with the
content in the document: no script toggles it, no route carries the open state, and no wire field
describes it. `required` on the checkbox is the browser's guard and `confirm=1` on the body is the
server's (§2.1.2, D-053); neither is the mechanism of the block. Three states, all server-rendered:
closed is the summary alone; open is the summary plus the form; **refused** (`422`
`confirmation_required`) re-renders the same `<details>` with the `open` attribute, so the block that
failed is still open in front of the reader and the checkbox carries `.is-invalid`. Closing it without
submitting is the summary again — which is why there is no `Cancel` link: a link would navigate away
from the panel the reader is in. `Esc` closes the block through the panel's key handler (§2.6.2) and
leaves the panel open; without JS `Esc` is the browser's, and the summary is the way back.

**Why not a `<dialog>`, and why not a modal.** A native `<dialog>` **does** render without a script when
it carries the `open` attribute — the reason it is rejected is the opposite one: `open` is an attribute
only the server or a script can set, so a dialog is either permanently open or permanently shut, and
there is no way for the reader to open it in response to pressing the button without JavaScript or
without a request that already knows the answer. A modal overlay is rejected on its own grounds too:
the panel is already a sheet on a phone, and a dialog inside it is a box inside a box, with a scrim
over content the reader can no longer reach (§2.5.6).

**At 320 px** the action group stacks — one full-width button per row, `min-height: 44px`, in the panel
footer, which is where the thumb is after a scroll (§2.5.6). The confirmation block lives in the same
footer, so opening it neither moves the page nor adds a scrim, and the panel's `width: 100vw` with no
side padding at that breakpoint already gives it the whole width: nothing in this section introduces a
horizontal scroll at 320 px.

**In `--read-only`** (`can_write=False`) none of these controls is rendered (R18) — no button and no
`<details>`, whose `<summary>` is the action and therefore cannot appear either — and the footer carries
the read-only line instead; a `POST` to any of the **thirteen** write page forms of §2.1.2 answers `403` with
`error.html`, and a `POST`/`PATCH`/`DELETE` to any of the `/_/` write fragments of §2.1.3 answers `403`
with `partials/fragment_error.html` and the catalogue's one message, `server in read-only mode` (§6),
because a `/_/` route never swaps a document into the element that asked. The thirteen page forms and their
fragments answer the same way: the family picks the element, and the message is one string. §6 names the
one route in the tree that answers `text/plain` instead, as the drift it is.

**Verification.** The statuses, the redirects, `HX-Trigger`, the `.is-invalid` markup, the `<details>`
with its `open` attribute on a `422` and the `name="confirm"` field are pytest-assertable (§7, and the
scenarios of `specs/acceptance/001-mvp-v1/16-dashboard.feature`). The focus behaviour, the 44 px target
and the 320 px layout are **verified by hand in a browser**, because this host has no Playwright (the
drag & drop debt): open `/ENG/list`, open a panel, tab to the action group, open both blocks, archive,
delete and restore, and check the focus lands where the table above says at 1280 px and at 320 px.

#### 2.6.10 The comment composer: one box, on both surfaces, and the list it appends to (D-060)

Writing a comment had a route and no element: §2.1.2 declared the form, §2.6.2 named `Cmd/Ctrl+Enter`
for "the comment composer", and no template carried one, so the only writer of the `comment` table in
the whole tree was a repository no use case called. This section gives it a home, a shape and a gate.

**Where it lives.** Both surfaces that render `issue_vm` render it, from the two templates that share
that view model (`R13`): `issue_detail.html` and `partials/issue_panel.html`. There is no third place,
and a surface that renders comments without it is a surface where a comment cannot be written.

**The gate.** It renders when `vm.can_write` is true **and** the issue is in neither layer:

```jinja
{% if vm.can_write and not vm.is_archived and not vm.is_trashed %}
```

An archived or trashed issue offers `Restore` and nothing else (§2.6.9), and a comment is a write to the
row like any other: it is refused there by the same rule (§6), so the page must not offer it. The
composer is **not** a member of the action group of §2.6.9 — it does not sit in the footer beside
`Move`, it sits under the comments it adds to, because that is the element it is about.

**The markup, on the detail page** — a plain form, so it works with no JavaScript:

```html
<section id="comments">
  <h2>Comments</h2>
  <ol id="comment-list" class="comment-list">
    <!-- one <li class="comment"> per comment, in creation order (§2.3.4) -->
  </ol>
  <form class="comment-composer" method="post" action="/ENG/issue/ENG-13/comment">
    <label class="visually-hidden" for="comment-body">Comment</label>
    <textarea id="comment-body" name="body" rows="3" required></textarea>
    <button type="submit" class="btn">Comment</button>
  </form>
</section>
```

**The markup, on the panel** — the same box, posting the fragment and appending to the list:

```html
<form class="comment-composer" method="post" action="/ENG/issue/ENG-13/comment"
      hx-post="/_/comments/ENG-13" hx-target="#comment-list" hx-swap="beforeend">
  <label class="visually-hidden" for="panel-comment-body">Comment</label>
  <textarea id="panel-comment-body" name="body" rows="3" required></textarea>
  <button type="submit" class="btn">Comment</button>
</form>
```

The panel's `method`/`action` are not decoration: with JavaScript off, or with the fragment failing,
the browser posts the same form to the same route and lands on the detail page at `#comments`.

**The box empties itself, on the event, not with `hx-on`.** The `POST` fragment answers `200` with the
`<li>` and `HX-Trigger: comment-created` (R12), and `app.js` resets `.comment-composer` on that event —
exactly the shape it already uses for the quick-add, which empties `#quick-add` on `issue-created` and on
nothing else. Resetting on the event and not on `htmx:afterRequest` is what makes "on a `200` and not on a
`422`" true by construction: the trigger is emitted by the success path, and a refusal has no event to
listen for. A comment appended with its text still in the box would be a comment written twice by the next
click, and a refused one whose text was cleared would be a paragraph the reader has to type again.

It cannot be an `hx-on::after-request` attribute: htmx compiles `hx-on` with `new Function`, and R14's
`script-src 'self'` without `unsafe-eval` forbids it. §2.6.8 records the same trap for the trigger filters,
and §8 step 7 for the build.

**The panel renders the same wrapper.** `partials/issue_panel.html` wraps its comments in
`<section id="comments">` with `<ol id="comment-list" class="comment-list">` inside, exactly as the page
does — the fragment's `hx-target="#comment-list"` needs that `<ol>` to exist on the surface that hosts the
composer, and it is the only `id` in the panel that a swap may name. A panel that rendered a bare `<ol>`
would append nowhere.

**Two shapes, one target, both valid HTML.** `#comments` is the `<section>` and `#comment-list` is the
`<ol>` inside it. Both fragments target **`#comment-list`**, and each uses the swap its node allows:
`GET` answers the whole `<ol>` and swaps it `outerHTML`, so the list is replaced where it stands; `POST`
answers one `<li>` with status `200` and appends it `beforeend`, so the item joins the list. The no-JS
`303` lands on `/{team}/issue/{identifier}#comments`, which is the section — the anchor a reader follows,
not a swap target.

Two older texts were wrong and are worth naming, because each looked reasonable: both routes targeting
`#comments` swapped an `<ol>` into an `<ol>` and appended an `<li>` into a `<section>`; and the `GET`
swapping the **innerHTML of `#comments`** deleted the `<h2>` and the composer that sit in that section
beside the list — a refresh that empties the box you are typing in. The list is the thing that changes,
so the list is what the swap names.

**The keys it reads.** `vm.can_write`, `vm.is_archived`, `vm.is_trashed`, `vm.comments` and
`vm.identifier` from §2.3.4, and `page.team.key` — the team key lives on the common context (§2.3.1),
not on `issue_vm`, because every URL the page builds reads it from there. All of them are already
declared, so the composer adds no key to the view model. Its `form_error`, when a refusal re-renders
it, is the one §6 names.

**Verification.** The markup, the gate, the `303` and the `#comments` fragment of the location, the
`422` with its sentence and the absence of the box on a layered issue are pytest-assertable (§7 and
`specs/acceptance/001-mvp-v1/16-dashboard.feature`). The `Cmd/Ctrl+Enter` submission and the 44 px
target are verified by hand in a browser at 1280 px and at 320 px, like the rest of §2.6.9.

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
| `issue_detail.html`, `/_/issues/{id}/panel`, `/_/issues/{id}/history` | `v_issue`, `comment`, `issue_relation`, `issue_history`, `issue_label`, `workflow_state`, `member`, `issue`, `team` | SELECT, plus the one `issue JOIN team ON team.id = issue.team_id` statement of §2.3.6 per render that carries a candidate, resolving the reference candidates of the description and of every comment into their canonical `(team.key, identifier)` (R30) |
| `search.html`, `/_/palette` | `issue_fts`, `issue`, `workflow_state`, `member`, `team`, `label`, `issue_label` | SELECT — the tables of SPEC-090 §3.2, read by `reads.search` and not by a query issued here |
| `POST /{team}/issue`, `/_/issues` | `issue`, `issue_history` | INSERT (use case) |
| `PATCH /_/issues/{id}` | `issue`, `issue_history` | UPDATE |
| `POST …/move` | `issue`, `issue_history` | UPDATE `state_id`, `board_order` |
| `POST …/labels`, `DELETE …/labels/{id}` | `issue_label` | INSERT / DELETE |
| `POST …/comment`, `/_/comments/{id}` | `comment` | INSERT |
| `POST …/archive` | `issue`, `issue_history` | UPDATE `archived_at` |
| `POST …/delete` | `issue`, `issue_history` | UPDATE `trashed`, `updated_at` |
| `POST …/restore` | `issue`, `issue_history` | UPDATE `archived_at` and `trashed`, one history row per layer cleared |
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
| **R2** | **Zero logic in Jinja.** Templates only access read model keys. A static test greps `templates/**` for `\|length`, `{% set %}`, arithmetic operators, `.query`/entity attributes and any sentence of §6 (§2.3.7), and fails. → **REQ-F070-03** |
| **R3** | **Complete read model.** Every template receives all the keys declared in §2.3, even if the value is `None`/`[]`/`""`. Defensive `default()` in the template for a key the read model must provide is forbidden. → **REQ-F070-03** |
| **R4** | **Without JS you can read and write.** Every page is full HTML; every mutation has a `<form method="post" action>` that responds `303`. Every `hx-get` has an `href`. → **REQ-F070-04** |
| **R5** | **A fragment requires `HX-Request`.** Without that header, `/_/*` responds `400` with `text/plain`. It keeps a fragment from being indexed or opened as a half page. → **REQ-F070-02** |
| **R6** | **Canonical URL.** The detail lives at `/{team}/issue/{identifier}`; requesting it by ULID redirects `303` to the identifier. Two URLs never serve the same content with 200. → **REQ-F070-01** |
| **R7** | **Bounded optimism.** The client paints the value before the response and **always** replaces the node with the server response. On error, it restores and notifies. There is no business state on the client. → **REQ-F070-05** |
| **R8** | **One `PATCH` in flight per row** (`hx-sync="closest tr:replace"`); the second discards the first. No queues and no automatic retries. → **REQ-F070-05** |
| **R9** | **Drag & drop sends neighbors, not positions.** Single payload: `{state_id, before, after}` with identifiers (`before`/`after` = `null` at the edge). Sending `sort_order`, `board_order`, `rebalance` or the whole column is forbidden; computing the float on the client is forbidden. The position (`(prev+next)/2`, `±1024`, `1024`) is computed by `domain/ranking.between` inside `move_issue`/`reorder_issue` (SPEC-040, D-012). → **REQ-F070-06** |
| **R10** | **The rebalance is decided by the server.** On `RANKING_EXHAUSTED`, SPEC-040 rebalances the scope and retries once in the same transaction (REQ-F040-08); the client does not request `rebalance` and does not know the 1024 multiple. If the retry fails, `422` `RANKING_EXHAUSTED` + board reload. → **REQ-F070-06** |
| **R11** | **htmx does not swap 4xx by default, and every refusal that replaces a node says so in one listener.** A global listener enables the swap for `422` and `409` — the two statuses whose body re-renders the element the request named — and for a `/_/` 4xx that carries `HX-Retarget`, which is the `partials/fragment_error.html` of a failed resolution; the `text/plain` refusals (an unknown `state_id`, the cross-site `403`, the missing-header `400`) carry no `HX-Retarget` and stay unswapped. Without that listener, an invalid field would leave the UI mute: it is the most common bug in this stack. The `409` half is the part HIFA-114 added, and §2.1.3 says why. → **REQ-F070-09** |
| **R12** | **Events by header.** Successful mutations emit `HX-Trigger` with `issue-created`, `issue-updated`, `issue-moved`, `issue-archived`, `issue-deleted`, `issue-restored`, `board-reordered`, `comment-created` — this is the complete catalogue, and the row of §2.1.3 that documents the header names the same set; the counters listen for those events (`hx-trigger="issue-moved from:body"`). Polling exists in exactly two places and nowhere else: `/_/counts` every 30 s (counters) and `/_/revision` every `REVISION_POLL` (the change signal of §2.6.8, R26). Neither re-renders the collection it watches. → **REQ-F070-10** |
| **R13** | **One read model per entity.** The panel and the detail page use the same `issue_vm`; the row and the card share `row_vm`/`card_vm` with the same derived keys (`priority_glyph`, `due_label`, `is_overdue`). → **REQ-F070-03** |
| **R14** | **Strict CSP without `unsafe-eval`.** `script-src 'self'` forces the Alpine CSP build (`Alpine.data()` registered in `app.js`); `x-data="{…}"` with expressions is forbidden and so is inline `<script>`. → **REQ-S070-02**, **REQ-S070-03** |
| **R15** | **Own HTML already escaped before `\|safe`.** `description_html` and `body_html` leave the server already escaped and with a tag allowlist (`p, br, strong, em, code, pre, ul, ol, li, a[href], blockquote, h1-h3, table, thead, tbody, tr, th, td`), where `th`/`td` also carry `style` when its value is `text-align:left`, `text-align:center` or `text-align:right`, and a `table` is wrapped in the sanitizer's own `div.table-scroll`, which is not an allowed tag (§2.3.6, D-079); `a[href]` only `http(s):`/`mailto:`, except the one anchor that carries `class="issue-ref"`, whose `href` may be the site-relative canonical path of R30 and whose class is the only `class` the walker keeps (§2.3.6, D-081). `snippet_html` leaves the server **escaped and with `MARK_START`/`MARK_END`** (R17, SPEC-090 R15). `\|safe` is applied **only** to those three fields. → **REQ-S070-01**, **REQ-S070-04** |
| **R16** | **No attachment surface in v1.** `issue_vm` carries no `attachments` key, because nothing can write an `attachment` row (`add_attachment` is declared in SPEC-040 §2.2 and implemented nowhere, GAP-07) and a list that can only be `[]` is what D-071 §2 refuses. The URL-scheme rule for `attachments[].url` — `http`, `https`, `file`, any other scheme rendered as text without an `href` — returns with the key, in the revision that lands `AddAttachment`. → (no requirement until then) |
| **R17** | **Search: escape every piece and keep the marker pair.** The snippet arrives from SPEC-090 already wrapped in `<mark>`/`</mark>` and unescaped (REQ-S090-03): the read model splits it on that pair, escapes **each piece between two markers** and rejoins them around the literal `<mark>`/`</mark>`, so the only markup `snippet_html` carries is the pair that was already there and the text around it is escaped exactly once, here. Escaping the whole string first and marking afterwards cannot be done at this edge — the markers already exist, and escaping before them would have to re-find the terms — and handing the raw snippet to `\|safe` would let an issue title put executable markup in the page. `build_search_vm` is the only producer of `snippet_html` and §2.1.8's page is its only consumer. → **REQ-S070-04** |
| **R18** | **Read-only is visible.** With `can_write=False` no write `<form>` and no drag control are rendered; only reading remains. A `POST` in that mode ⇒ `403`, with `error.html` from a page route and `partials/fragment_error.html` from a `/_/` route. → **REQ-F070-04** |
| **R19** | **Palette in two phases.** With an empty `q` the server responds with **actions only** and does not query the database; with a non-empty `q` it adds up to 8 issues. The list is a `<ul role="listbox">` with `aria-activedescendant`; `Enter` navigates with `location.assign` (not `hx-get`) so the URL stays in the history. → **REQ-F070-07** |
| **R20** | **Shortcuts with a focus guard.** `j/k/c///Esc` are ignored if `event.target` is `input`, `textarea`, `select` or `[contenteditable]` (except `Esc` and `Cmd+Enter`). `j`/`k` move **real focus** (`tabindex="-1"` + `:focus-visible`), not a class, and survive an htmx swap. → **REQ-F070-08** |
| **R21** | **Search with state in the URL, and a page that never answers `400`.** `/search?q=` is a full page and `GET /_/palette` is the fragment of the same query. **This rule binds every edge of this layer that composes a query**, and this revision ships exactly one of them: such an edge composes the `SearchQuery` from `parse_query(q)` plus its own parameters and calls `search_issues(db, query, now=…)` (SPEC-090 §2.3, D-016, D-064) — `now` is the instant this layer's clock read, the same one `updated_label` uses — and the palette, when its own ticket lands, is one of them, which is why §2.1.9 says the page contracts itself alone while this rule already names the palette's call. `q` travels in the query string, never in the body nor in a cookie. The page has exactly four states and no fifth (§2.1.8, D-063): **empty** (`200`, no `q`, the use case is not called), **results** (`200`), **no results** (`200`, `hits=[]` from a valid query) and **error** (`422`, a query SPEC-090 §6 refuses, re-rendered with `refusal_sentence(refusal)` and the query kept). It answers `400` for none of them: a missing query is the empty state and a malformed one is a validation. → **REQ-F070-11** |
| **R22** | **Keyset HTML pagination (D-011).** `limit` 1-200 default 50; cursor = opaque `base64url(sort_key + "\|" + issue.id)` generated by `encode_cursor` (SPEC-030 §2.6); `has_more` comes from requesting `limit + 1` (never `COUNT(*)`); `next_url` carries `cursor`. There is no `?page=N`. `offset` (0-500) exists **only** for `list.html` infinite scroll, is mutually exclusive with `cursor`, and its cap does not come from this spec. Invalid `sort`, `cursor` or `offset`, or `cursor` + `offset` together ⇒ `422` with `error.html`. → **REQ-F070-12** |
| **R23** | **The sidebar is a per-request read.** `page["teams"]` is built from the database on every request that renders a page; it is never captured once in `create_app`, never stored on `app.state` and never memoized for the life of the process. The CLI is the **other writer** of that same SQLite file (D-030), so a team created by `hifa team create` while `hifa serve` is running must appear in the sidebar on the **next request**, with no restart. The active team is marked and the block is never hidden, not even with a single team. → **REQ-F070-13** |
| **R24** | **`default_team` is never absent, and the root never guesses.** `config.py` always resolves `[cli].default_team` — the configured value, else `[team].key`, which itself defaults to `DEFAULT_TEAM_KEY` (SPEC-100 §2.3) — so "2+ teams and no default" is **not** a reachable state and is not what the picker is for. What `GET /` does, in this order: (1) the resolved default names a team that exists ⇒ `303` to `/{key}/board`; (2) exactly one non-archived team ⇒ `303` to it (this is the fresh-database case, where the default key exists); (3) **canonical picker case** — the default names a team that is **not in the database** (a second team created under another key, a regenerated `.bak`, a `.hifa.toml` copied from another workspace) and there are 2+ non-archived teams ⇒ `200` `team_picker.html` (§2.3.8) listing every team and linking to `/{team}/board`, with `reason = "several_teams"`; (4) **secondary case** — the caller passed no default at all (`create_app(default_team=None)`: embedding, tests, a bootstrap that skipped config) and there are 2+ teams ⇒ the same `200` picker, with `reason = "no_default_team"`; (5) no teams ⇒ `404` `error.html`. It never picks by `created_at`, alphabetical order, cookie or session, and never answers `404` when a usable answer exists. → **REQ-F070-14** |
| **R25** | **Reordering the columns changes the presentation order, nothing else.** `POST /_/states/{id}/position` writes `workflow_state.position` and **only** that: no issue changes `state_id` or `board_order`, no `issue_history` row is written and no counter moves. The payload is `{before, after}` with **state ids** (`null` at the edge), never a float; the new `position` is `domain.ranking.between(prev, next)` computed on the server and persisted by `update_state`. The response is `204` + `HX-Trigger: board-reordered` with no swap (the DOM already moved), `403` in `--read-only`, `404` for an unknown state and `422` for neighbours from another team. The canonical order a **new** team is seeded with is `DEFAULT_STATES` (SPEC-100 §2.5, D-034); an existing team is reordered with this route or with `hifa state update` (SPEC-050 §2.5.6), never with a migration. → **REQ-F070-06** |
| **R26** | **The page notices a foreign write by a change signal, never by a timer.** `GET /_/revision` answers `{"revision": <n>}` read from `PRAGMA data_version` on the process's single long-lived read connection (§2.1.3.1, §3, D-049); the number changes iff another connection committed since the previous read — which is what the CLI is, and what the process's own per-request connections are. The page carries the number it was rendered with (`<body data-revision>`, `page["revision"]`) and adopts the `HX-Revision` of an answer that replaced the collection; a panel, a row, the palette or the counters do not adopt (D-050); the poller (`hx-trigger="every {{ page.revision_poll }}"`, `hx-swap="none"`, visibility enforced in the client by cancelling `htmx:beforeRequest` while the tab is hidden — never as a `[condition]` filter, which the CSP's `script-src 'self'` makes unrunnable) reloads `page["refresh_url"]` into `#board`/`#issues-tbody` **only when the numbers differ**, so no request and no swap happens while nothing changed. The reload is **deferred, never forced**, while a detail panel is open, the quick add holds unsaved text or a drag is in flight; the last revision seen is applied as soon as the interaction ends (§2.6.8). The number is opaque — compared for equality, never ordered, never arithmetic, never displayed. Without JS nothing of this runs and a reload shows the new data (P5). The probe is a read: `--read-only` neither blocks it nor changes it. → **REQ-F070-15** |
| **R27** | **A write names identifiers, asks before it takes the issue out of the listings, and answers with the page's own element.** The **thirteen** page forms of §2.1.2 — the quick add, the **seven** field edits, `move`, the comment and the three layer writes (`archive`, `delete`, `restore`) — each have the `/_/` fragment of §2.1.3 that answers them, and each form on a fragment-bearing surface carries the `hx-*` that points at its fragment, never at the page route (a `303` followed inside an htmx request pastes a document into an element). The edit route is one route and seven forms per surface: every inline control of §2.6.3 is the sole field of its own `POST /{team}/issue/{identifier}`, which is why the seven share one page route and one fragment with a `field=` that names the one wrapper each answers. The bodies carry the fields of the use case and identifiers: `state` is a reference (edit), `state_id` is a column (move and drop), and no body carries `position`, `sort_order`, `board_order` or `rebalance` — neighbours travel as identifiers (`before`/`after`, `null` at the edge) and never as an index or a float, and R9 binds every write, not only the drop. `archive` and `delete` require `confirm=1` and refuse without it with `422` `confirmation_required`; `restore` never asks; `archive` and `restore` are two routes and `undo` is not a field (D-053). The answer is the page's own element: `200` with the row, the card, the panel or **one field wrapper** after an edit (`?view=field&field={name}` answers the single field a control changed, §2.6.3; `?view=panel` answers the aside a layer refusal re-renders), `204` with no body after a move, an archive, a delete or a restore, `200` with the one `<li>` a comment writes (§2.6.10), `303` from a page form, and `422`/`409` with the **same element re-rendered** plus its message — never a JSON body, because the client of this layer is htmx. After a layer write the client closes the panel and re-reads the collection from `page["refresh_url"]`, so the rows are the server's and not a node the client removed by hand (D-054). → **REQ-F013**, **REQ-F015**, **REQ-F019** |
| **R28** | **A state-changing request from another origin is refused before the route runs, and a request with no origin information is allowed.** Every request whose method is outside `GET`/`HEAD`/`OPTIONS` is answered `403`, `text/plain` `cross-site write refused`, `Cache-Control: no-store` — returned by one middleware installed by `create_app` inside `_headers`, never raised as an exception — when `Sec-Fetch-Site` is `cross-site` or `same-site`, or when that header is absent and `Origin` names another authority or is `null`; `same-origin`, `none`, an `Origin` whose authority is `Host`'s, and a request with neither header are served. The comparison is the authority (host and port, compared case-insensitively, the scheme dropped), so a browser whose `Origin` says `https` behind a plain `http` tunnel still writes, and `Origin: null` — which names no authority — is refused. The guard reads no path, no route table, no table and no body, so it precedes the `HX-Request` `400` (R5), the read-only `403` (R18) and the use case, and a cross-site write to an unknown identifier is `403` rather than `404`. It does not authenticate, does not close the tunnel and does not make a header a credential — a client that sets its own headers can set any, and a same-origin page can write by definition; and it covers no read, because a cross-site read is served and its bytes are unreadable to the other origin (REQ-S005). → **REQ-F070-16** |
| **R29** | **Exactly two surfaces read through the trash, and nothing else does.** `GET /{team}/issue/{identifier}` and `GET /_/issues/{identifier}/panel` answer `200` for an issue with `trashed = 1`, rendering the issue with `Restore` as its **only** control — no edit form, no `name="state_id"` control, no archive block, no delete block, no comment composer (§2.6.10) — and every write but `restore` refuses it, the comment included — and the `/{team}/issue/{ulid}` alias answers `303` to that same page (R6). Every other surface keeps the read's default and never returns a trashed row: the board, the list, the search page, the palette, the counters, and the `row` and `card` fragments of §2.1.3, so those two fragments answer `404` for exactly the identifier whose page answers `200`. The read behind those surfaces is `issue_detail(..., include_trashed=True)` (SPEC-040 §2.2) and **the four reads of §2.1.4 that pass the keyword are its only consumers**: the page, the panel fragment, the history fragment and the `?view=field` read behind a field refusal — the first two are the two surfaces, the third re-renders the second's own list, and the fourth answers one field of the same issue with `422 ISSUE_TRASHED` instead of `404` (§2.6.3). No other route passes it: the row and the card never do, and the asymmetry — page, panel, history fragment and field wrapper pass it, collection members do not — is the rule and not an implementation detail. **The one route that shares the panel's call is not a third surface**: `GET /_/issues/{identifier}/history` (§2.1.4) re-renders the panel's own timeline with the panel's two `include` names and its keyword, so it answers what the panel already answers and adds no surface over the trash — a `Show all` on a trashed issue's panel is the panel talking, and its `200` is that `200`. What it forbids: a third surface over the trash; any page, count, chip or link that **enumerates** it (enumerating is the CLI's `hifa issue list --trashed`, SPEC-050 §2.5.3); a trashed row inside a collection fragment; and every write but `restore` on a trashed issue — `edit`, `move`, an `assignee` or label write, `archive` and a comment refuse with `ISSUE_TRASHED` (`422`, §6) while `delete` is the no-op row beside it — so the page the trash gets offers the one write it accepts and tells the truth about the rest. `is_trashed` is `True` only on a detail read with the keyword, which is what lets a template gate the action group **once** on `vm.can_write` and pick the branch inside it: an issue in **either** layer — archived or trashed — fills the group with `Restore` alone, and a visible issue fills it with the move control, the archive block and the delete block (§2.3.4, §2.6.9, D-059). → **REQ-F070-17** |
| **R30** | **A reference written in the text is a link only when the caller says what it resolves to.** `render_markdown(text, links=None)` takes a **mapping** — candidate folded to upper case → canonical `(team_key, identifier)` — and never a callable, so a surface pays the one statement the caller already made instead of one lookup per match; `None` is today's rendering byte for byte. The anchor is a plain `href`, `<a class="issue-ref" href="/{TEAM}/issue/{CANONICAL}">{as written}</a>`, with the canonical team key and identifier and with the author's own spelling as its text, because a non-canonical spelling is the same page reached by another path — `GET /HIFA/issue/hifa-103` and `GET /ENG/issue/HIFA-103` both answer `303` to the canonical path — and R6's rule that two URLs never serve the same content with `200` is what leaves a second spelling no page to serve, which is what makes the canonical `href` mandatory. Nothing inside `code_inline`, `fence`, `code_block` or an existing markdown link is ever linked, and the candidate is the maximal run, so `HIFA-1034` and `XHIFA-103` never fire as `HIFA-103`. A candidate the mapping does not carry — every `SPEC-*` and `SQL-7` — stays the text it is today: never an anchor, never a 404. An issue row that no longer exists cannot occur in v1, because nothing physical-deletes one — `delete` **is** `trash_issue` (D-015, SPEC-050 §2.5.3) — while a **trashed** issue does resolve and does link, its page and panel answering `200` (R29), as does an archived one. The grammar is `domain/identifier.py`'s — `IDENTIFIER_PATTERN` for the pattern and `parse` for the format, the two entries of SPEC-020 §2.3's published list this rule borrows, with the candidate the maximal run that pattern accepts — read by `issue_ref_candidates` in `src/hifa/web/markdown.py`, which both the renderer and the collector call; the fold is `str.upper()`, the same `upper(:ref)` of `issue_id_by_ref` and `issue_ref_links`, applied in Python so each element stays an index seek. The candidates of the description **and of every comment** of a surface are resolved by one call to `issue_ref_links(conn, refs)`, one statement over `issue JOIN team ON team.id = issue.team_id` projecting `identifier` and `team.key` (SPEC-030 §2.6); `build_issue_vm` stays pure and receives the mapping as a keyword, built beside the detail read by `_issue_detail_vm` (§2.3.6, D-081). → **REQ-F070-18** |

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
1. blur/Enter on a text control, change on a select, Cmd/Ctrl+Enter or Save on the description
   ⇒ htmx serializes the field's own form (urlencoded, `title=...`) and does
   PATCH /_/issues/ENG-13?view=field&field=title (§2.6.3, §2.1.3, D-056). The list row sends the same
   request to ?view=list.
2. Router: resolve ref (404 `partials/fragment_error.html`, appended to `#toasts` under
   `HX-Retarget: #toasts`/`HX-Reswap: beforeend` — a `/_/` answer never renders `error.html`, §2.1.3, R18),
   resolve `field` (404 `partials/fragment_error.html` appended to
   `#toasts`, under `HX-Retarget: #toasts` and `HX-Reswap: beforeend`, §2.1.3), validate body (422).
3. `app.issues.update_issue(uow, UpdateIssue(issue=ref, title=...), actor)` → UPDATE issue +
   INSERT issue_history (one transaction).
4. Serialize the element `view` names — the field wrapper (§2.6.3), the `<tr>` or the panel — and answer
   200 with HX-Trigger: issue-updated.
5. Domain error ⇒ 422 with the same element, the control `open` and `.is-invalid`, the message beside it.
   `409` ⇒ the issue is **archived**: the same element with the server's value, the message and neither a
   control nor a `<details>` (`writable: False`, §2.3.4). `422 ISSUE_TRASHED` ⇒ the issue is **in the
   trash**: the same element `open` and marked, with the message and no write control
   (`writable: False, open: True`), and that answer exists only because the read behind it passed
   `include_trashed=True` (§2.6.3, §6). All three answers fill `form_errors` under the key of the field the
   request named — `{requested_field: refusal_sentence(refusal, verb="edit", identifier=…)}` — and never under
   `refusal.field`: `ISSUE_ARCHIVED`/`ISSUE_TRASHED` name `ref`/`trashed`, which is none of the seven
   controls, and the wrapper prints only its own `fields[<name>].message` (§2.3.4), so a key off the error's
   field would leave the `409` and the trash's `422` mute. The two statuses are not interchangeable and the
   trash is never a `409`. Both are put in place by the `htmx:beforeSwap` listener, which enables `422`
   and `409` — the `409` is the one HIFA-114 added (§2.1.3). A `404` has no
   element to keep and answers
   `partials/fragment_error.html` appended to `#toasts`, under `HX-Retarget: #toasts` and
   `HX-Reswap: beforeend` (§6).
```

### 5.4 Drag & drop (move)

```
1. sortablejs onEnd builds {state_id, before, after} from the neighbors' data-identifier. No arithmetic.
2. POST /_/issues/ENG-13/move  {"state_id": "...", "before": "ENG-12", "after": "ENG-15"}
3. Router validates the payload (422), resolves issue and state (404) and calls:
   - different state ⇒ app.issues.move_issue(uow, MoveIssue(...), actor)
   - same state      ⇒ app.issues.reorder_issue(uow, ReorderIssue(..., scope="board"), actor)
4. Validation (open children) ⇒ 422 ⇒ same recovery; InvalidTransition (a **drop** of an issue that is
   already archived, R20 — not the archive write, which is a no-op on such an issue, §5.7) ⇒ 409 ⇒
   the client reloads /_/board/{team} and shows a toast.
5. RANKING_EXHAUSTED after the server's rebalance+retry ⇒ 422 ⇒ same recovery.
6. 204 + HX-Trigger: issue-moved ⇒ the counters refresh; the DOM is untouched. This is the **drop's**
   flow, and the drop is the client that renders nothing on success: the route's other client is the
   panel footer's form, whose success ends the panel and re-reads the collection (§5.7.1).
```

**One body for each of `move`'s two refusals, whichever client sent the drop.** `move` has a **single**
answer for each of its two refusals: `422` (`Validation` — open children, a bad neighbour,
`RANKING_EXHAUSTED` after the rebalance) and
`409` (`InvalidTransition` — the issue is archived, R20) both answer with the **panel re-rendered** from the
read model plus `form_error`, the same element and the same body every other refusal of this layer uses
(§2.1.3), and never an empty body or a second shape picked by the caller. The **board drop ignores that
body**: app.js reads the status, puts the card back where it was and reloads `/_/board/{team}` — the
`page["refresh_url"]` of §5.6, D-054 — because the collection is what the drop changed and a panel is the one
element the drop never opened. The contract is therefore one contract with two consumers: the route always
answers the panel, and the difference lives entirely in what the client does with it.

**The route's third answer is not one of those two bodies, and it is the drop's alone.** A `state_id` that
resolves to no state of the issue's team is the `404` `text/plain` `workflow state does not exist` of §6 —
a resolution that failed, with no field to mark and no element to re-render — not a `422` and not a second
body of this paragraph. Of the route's two `/_/` clients only the drop can produce it: the panel footer's
`<select name="state_id">` is rendered from the team's states (§5.7.1), so a footer post names one the
server had just offered, while the drop's payload carries a `state_id` and two neighbours the client
assembled from the DOM. A footer post reaches this row only across a stale panel — a state deleted by
another tab since the panel was rendered — which is the staleness every write of this layer answers with
a refusal rather than a re-render.

### 5.5 Column drag & drop (board reorder)

```
1. sortablejs onEnd (column instance) builds {before, after} from the neighbours' data-state-id. No arithmetic.
2. POST /_/states/<state_id>/position  {"before": "<id>", "after": "<id>"}
3. Router: state (404) → neighbours belong to the same team and are not the moved state (422) →
   list_states(team) → new position = ranking.between(prev.position, next.position) →
   app.teams.update_state(uow, UpdateState(state=<id>, position=<float>), actor)
4. RANKING_EXHAUSTED (no float room between the neighbours) ⇒ 422 ⇒ the client reloads /_/board/{team}.
5. Read-only (can_write=False) ⇒ 403 partials/fragment_error.html; the instance is not
   initialized in that mode.
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
   and the page adopts it in the htmx afterRequest hook **only if that answer replaced the
   collection**: step 4 is already true on the next tick, so the user's own action never causes a
   second render of what it just produced. A write whose answer rendered something else — a panel, a
   row, the palette, the counters — does not adopt: its number is the one the server observed,
   foreign commits included, and believing it would spend the difference between the page and the
   database without updating the board (D-050).
9. The wait for the answer is bounded by RELOAD_MS (10 s, src/hifa/web/static/js/app.js). When it
   expires the request is abandoned and asked again: the client clears its in-flight flag, aborts
   the stale request on document.body — the element htmx keyed it on, because htmx.ajax was called
   without a source — and only then flushes the pending revision through the same single path. The
   abort is what makes the retry leave the browser: without it htmx queues the second call behind
   the slot the hung one still holds, resolves its promise at once and sends nothing.
```

### 5.7 A layer write (archive, delete, restore)

```
1. The reader opens the panel (/_/issues/ENG-13/panel) and tabs to its footer: the action group ends
   with the <details> blocks of §2.6.9. Enter or Space opens one and reveals its two lines, the
   `confirm` checkbox and the button; the <summary> itself is the action. Without JS the same blocks
   are the ones of the detail page's action group.
2. With htmx: POST /_/issues/ENG-13/archive, HX-Request: true, body confirm=1. The page form posts to
   POST /ENG/issue/ENG-13/archive with the same body.
3. Router: resolve the reference (404 ⇒ error.html / the panel), refuse a body without confirm=1 with
   422 confirmation_required and the element re-rendered with the <details> open and `form_error` — the
   edge's form-level refusal of §2.1.2: no use case is called and no transaction opens, so this branch is
   answered from the block plus §6's builder and never from an exception —
   then call app.issues.archive_issue(uow, ArchiveIssue(issue=ref), Actor(...)).
4. Validation(CLOSE_WITH_OPEN_CHILDREN) or Validation(ISSUE_TRASHED) ⇒ 422 with the element
   re-rendered and `form_error`. An **already-archived** issue is not a refusal: the use case returns
   the stored row unchanged and writes nothing (SPEC-040 §5.5, REQ-F040-33), so the answer is the
   success of step 5 with no history row and `archived_at` exactly where it was. `InvalidTransition`
   is not reachable from this route at all — its only producer is a field write or a transition *on*
   an archived issue (SPEC-040 §6, R20), and §2.1.3 exempts archive, delete and restore from that rule.
   Nothing is written in any of the refusal branches.
5. Success: 204, HX-Trigger: issue-archived, HX-Revision: the number the request observed. The page
   form answers 303 to /ENG/list instead.
6. app.js on `issue-archived`: closes the panel and re-reads page["refresh_url"] into
   #issues-tbody/#board (§2.6.8), which is the answer that adopts the revision (D-050). The archived
   issue is no longer in the rows, and the rows are the server's (D-054).
7. `delete` is the same flow with issue-deleted and trash_issue. An issue **already in the trash** is
   the same shape and not a refusal: `trash_issue` returns the stored row unchanged and reads no clock
   (SPEC-040 §5.5, `issue.trashed => return issue`), so `delete` answers the same `204` — HX-Trigger and
   HX-Revision included — with no history row, `trashed` and `updated_at` exactly where they were, and
   the panel closes and the collection is re-read like any other success. `ISSUE_TRASHED` belongs to the
   writes that cannot operate on a trashed issue at all (edit, move, a label or `assignee` write,
   archive), never to this one (§6). `restore` is the same flow without a
   confirmation and with issue-restored — the same 204, the same closing of the panel, the same re-read
   of the collection: it changes what the collection contains too, and a panel answer would not adopt
   the revision, so the restored row comes back from the collection and not from a node the client
   kept (D-054). The focus lands on that row (§2.6.9). On a visible issue the use case writes nothing
   and the answer is the same 204 with no history row (SPEC-040 §5.5).
8. Without JS every step is a form and a 303: no panel, no toast, and the page the reader lands on is
   the list for an archive or a delete and the detail for a restore (§2.1.2).
```

### 5.7.1 `move` from the panel footer: the one client of the route that swaps the collection

`move` has two clients and **one** answer. The drop sends `{state_id, before, after}` and renders
nothing, because the node is already where the reader put it (§2.6.4). The panel footer's form sends
`state_id` alone — no neighbour, no position, so the server makes it `position="last"` — and the node is
**not** where the reader put it: the issue was chosen in the footer's `<select>`, the collection behind
the panel still shows the old column, and the panel itself now carries a stale state. What that client
does with the answer is declared here, once, and §2.6.4 and §2.6.9 point at it instead of restating it.

```
1. The reader opens the panel, picks a state in the footer's <select name="state_id"> and presses Move:
   POST /_/issues/ENG-13/move with HX-Request: true and the body state_id=<id> — the payload of
   §2.6.4 without its neighbours, form-encoded like every write of /_/ (D-056), which the server reads
   as position="last".
2. Router: resolve the reference and the state (404, §6), refuse an archived issue (409) and a trashed
   one (422), then run the command the payload selects between — move_issue when the state changes,
   reorder_issue when it does not (§2.6.4, §5.4) — with no arithmetic in this layer (R9).
3. Success: 204, no body, HX-Trigger: issue-moved, HX-Revision: the number the request observed
   (§2.1.3, §2.6.8). No element is rendered, by construction: the route answers no body to either
   client.
4. app.js on issue-moved closes the panel and re-reads page["refresh_url"] into #issues-tbody/#board
   (§2.6.8), which is the answer that adopts the revision (D-050). The listener is the
   **htmx-dispatched** `issue-moved`: the header rides a response htmx made, so htmx fires the event
   and this one handler runs. The drop is the exception §5.4 declares — it posts with `fetch`, reads
   `HX-Trigger` off the response itself and dispatches nothing — so no `issue-moved` reaches this
   handler after a drop and no re-read follows one: the node the drop left in place stays where the
   reader put it, which is what keeps §5.4 step 6's "the DOM is untouched" and this step from being
   two behaviours of one event. The card is in its new column because the server put it there, not
   because the client moved it (D-054).
5. Focus lands on #issue-{identifier} / #card-{identifier} of the collection just re-read, or on that
   container when the page's filters leave the moved issue out (§2.6.9).
6. A refusal is the panel's: 422/409 render the panel again with form_error, carried by HX-Retarget:
   #panel and HX-Reswap: innerHTML (§2.6.9), because the form's hx-swap is none and the marked panel
   would otherwise be fetched and thrown away. Nothing on the board moved, so nothing is put back.
7. Without JS the same form is the page form of §2.1.2: it posts /{team}/issue/{identifier}/move and
   answers 303 to the detail, with the issue at the end of the target column.
```

**Why the panel closes, and why the collection is what is re-read.** A `204` has no body, so there is no
element for the route to hand back (§2.1.3) and no way to re-render the `<select>` the reader just used;
an answer that rendered the panel would have to be a `200` of some other route, and it would still leave
the board behind it showing the old column. The open panel is one of the blocking states of §2.6.8:
keeping it open would hold the very collection the reader is about to look at, while the panel went on
showing a state the issue no longer has. Closing it releases that re-read and costs nothing the reader
did not already accept — the same close, the same HX-Trigger and the same re-read the three layer writes
of §5.7 already perform, so `move` adds no second convention to the footer: one event, one close, one
re-read, whichever of the footer's writes it was.

**What it is not.** Not a panel swap, and not a node the client moves: the footer sends one identifier
and the column comes back from the read (D-054). Not a second payload: the footer sends no neighbour, so
its `Move` always appends, and the drag remains the only control that names neighbours (§2.6.4). Not the
drop: the drop re-renders nothing and takes its own recovery on a refusal (§5.4), and the two clients
differ in exactly the step above, not in the route, the status or the header.

## 6. Errors

| Situation | Status | Response | Effect on the UI |
|---|---|---|---|
| nonexistent `{team}` or `{identifier}` | 404 | `error.html` (`message`, `back_url`) | error page with a link to the list |
| `/search` without `q` | 200 | `search.html` in its empty state (§2.1.8, D-063) | the search box, focused, and no result list — never an error page |
| `/search` with a query SPEC-090 §6 refuses (`query_too_long`, `invalid_filter`, `invalid_limit`) | 422 | `search.html` re-rendered with `message` = the §6 sentence (its own key, **not** `form_errors`: this page has no field to mark, and §2.3.5 declares `message`), the query unchanged in the box, no hits | the reader fixes the query in place; nothing was searched |
| Fragment without `HX-Request` | 400 | `text/plain` `fragment requires htmx` | not applicable (it is a malformed client) |
| `/_/revision` without `HX-Request` | 400 | `text/plain` `fragment requires htmx` (R5, §2.1.3.1) | the tick is skipped and the page keeps what it has |
| Validation on a page (form without JS) | 422 | same template + `form_errors` | errors next to the field |
| Validation in a fragment / inline | 422 | the element the request named — the field wrapper for `view=field`, the `<tr>`, the card or the panel — with `.is-invalid` on the control and the message beside it; `HX-Retarget` only when there is no element to keep | field marked, control `open`, message next to it — the answer is swapped in place and reaches no toast (§2.1.3) |
| A value of a control in `view=field` that resolves to no row (`state` or `member` naming none — the two references `EDIT_COMMAND_FIELDS` sends, §2.1.2) | 422 | the **field wrapper** of the control that was edited, re-rendered `open` with `.is-invalid` on that control and the `invalid value: ` sentence the catalogue's last row gives the domain's message — a `NotFound` about a value and not about the route, so the wrapper stays; the page route `POST /{team}/issue/{identifier}` keeps its `404` (`error.html`) for the same body, because a page has no wrapper to keep | nothing is written; the field stays open, marked, with the sentence beside it and the stored value in the control |
| `Conflict` (label in use, etc.) | 409 | partial with message | the message is swapped in where the reader is, no destructive swap — a `409` to a fragment request is swapped in place, never toasted; the drop's own `409` keeps its recovery and its toast (§2.6.4) |
| `Validation` (drop with open children) | 422 | the panel re-rendered + `form_error` (§2.1.3, §5.4); the drop ignores the body | the card returns to its place + toast, the client reloads `/_/board/{team}` |
| `InvalidTransition` (drop on an archived issue, R20) | 409 | the panel re-rendered + `form_error`, the same body as the row above (§2.1.3, §5.4) | the card returns to its place + toast, the client reloads `/_/board/{team}` |
| `archive` / `delete` without `confirm=1` | 422 | the page (or the panel) with the `<details>` block rendered `open` and `form_error` | nothing is written; the block stays open |
| `archive` of a parent with open sub-issues (`CLOSE_WITH_OPEN_CHILDREN`) | 422 | the page (or the panel) with the message | nothing is written; `archived_at` unchanged |
| `edit` (the page, the panel and the `?view=field` wrapper), `move`, a label or `assignee` write, a comment, or `archive` of an issue in the trash (`ISSUE_TRASHED`) | 422 | the page, the panel or the **field wrapper** with the message: the wrapper comes back `open`, marked and **without** a write control, because R29 leaves `Restore` alone (§2.6.3) — and that answer exists only because the read behind `?view=field` passes `include_trashed=True`, as the panel's does; a comment sent by a client that still holds a stale form gets the message and **no** composer, because the gate of §2.6.10 renders none for a trashed issue | nothing is written |
| `delete` of an issue already in the trash | 204 (`/`), 303 (page) | no body; `/{team}/list` | a no-op: no history row, `trashed` and `updated_at` unchanged (SPEC-040 §5.5, §5.7 step 7) |
| `move`, an edit or a comment on an archived issue (`InvalidTransition`, R20) | 409 | the page (or the panel) with the message and the way out; an edit through `?view=field` answers that **field wrapper**, with the server's value, the message and no control (§2.6.3) | nothing is written; the panel offers `Restore` |
| `PATCH …?view=field` naming a `field` that is not one of the seven controls of §2.1.2 | 404 | `partials/fragment_error.html` with `HX-Retarget: #toasts` and `HX-Reswap: beforeend` (§2.1.3) — a resolution that failed, like the unknown `state_id` below, but one that has a sentence to render and no element to keep: the partial's message names the field, and that is how this `404` is told from an unknown issue | the field is untouched; the partial is appended to the toast area and the sentence is read there |
| `restore` of a visible issue | 204 (`/`), 303 (page) | no body; the detail | a no-op: no row, no history (SPEC-050 §2.5.3) |
| `archive` of an already-archived issue | 204 (`/`), 303 (page) | no body; `/{team}/list` | a no-op: no history row, `archived_at` unchanged (SPEC-040 §5.5, REQ-F040-33) |
| A comment with no body, or only spaces (`BODY_EMPTY`) | 422 | the composer with `a comment needs a body` as its `form_error`, the textarea marked `.is-invalid` | nothing is written |
| A reply that would sit deeper than three (`COMMENT_DEPTH_EXCEEDED`, `field="parent_id"`) | 422 | the composer with `the thread is 3 deep; reply to a shallower comment`, the parent control marked `.is-invalid` | nothing is written; the dashboard's own composer sends no parent (D-060), so this row is the fragment's contract, not a path its UI can take |
| A comment whose `parent` names a comment of another issue (`COMMENT_PARENT_MISMATCH`) | 422 | the composer with `that comment belongs to another issue`, the parent control marked `.is-invalid` | nothing is written; the dashboard's own composer sends no parent (D-060), so this row is the fragment's contract, not a path its UI can take |
| Unknown `{state_id}` on `/_/states/{id}/position` | 404 | `text/plain` `workflow state does not exist` | the client reloads `/_/board/{team}` |
| Unknown `{state_id}` on the `move` drop (`/_/issues/{identifier}/move`) | 404 | `text/plain` `workflow state does not exist` | the client reloads `/_/board/{team}`; the card returns to its place + toast |
| Neighbour of the column drop from another team, or the moved state itself | 422 | `text/plain` `invalid neighbour` | same reload; the columns return to the server order |
| A state-changing request from another origin (§2.1.7, R28) | 403 | `text/plain` `cross-site write refused` + `Cache-Control: no-store`, returned by the middleware before routing | nothing is written and no page is rendered: the tab shows the sentence |
| Write with `read_only` | 403 | `error.html` from a page route; `partials/fragment_error.html` from a `/_/` route | read-mode banner (a fragment never swaps a document) |
| `POST /_/states/{state_id}/position` with `read_only` | 403 | `text/plain` `server is read-only` — **the exception the implementation owes a fix**: every other `/_/` write answers `partials/fragment_error.html` and is contracted to the catalogue's `server in read-only mode` (the row above), so this route drifts on both the media type and the sentence (`PATCH /_/issues/{identifier}` already carries the same wrong sentence, so the second drift is older than this route) | read-mode banner; the sentence reaches the toast area as text, not as the fragment's markup |
| `/_/revision` when the probe connection cannot answer | 503 | `application/json` `{"revision": null}` + `Cache-Control: no-store` | the body is not a number ⇒ the client does nothing; no swap, and the next tick retries on its own |
| The activity timeline cannot be assembled (`history` requested, the collection missing) | 200 | the page or the panel, with the timeline's place carrying `history unavailable` — dim, one line, no `form_error` (§2.5.7.3) | the rest of the issue renders: a history that cannot be read does not take the issue with it |
| Unmapped exception | 500 | generic `error.html` (no traceback) | same |

**One answer for one `state_id`, on both drops.** The two `404` rows above are one row with two routes:
`state_id` on the wire is a column — the word §2.1.2 gives it — so a value that resolves to no state in the
team is answered identically by `move` and by the column drop, `404` `text/plain` `workflow state does not
exist`, and neither route invents a status of its own for it. This row is **not** §2.1.2's `422`: that one is
a body that failed to parse or validate, re-rendered with `form_errors` next to the field, while a `state_id`
naming no state is a resolution that failed and carries no field to mark (SPEC-040 §6, `NOT_FOUND` from
`move_issue`). The page form of the same write resolves the reference the same way and answers the `404` of
the first row of this table (`error.html`), not the `422` of a rejected form.

Catalog of visible messages (exact text, lowercase initial, no trailing period):

| Case | Text |
|---|---|
| 404 issue | `issue 'ENG-999' does not exist` |
| 404 team | `team 'OPS' does not exist` |
| 404 state (`move` or `/_/states/{id}/position`) | `workflow state does not exist` |
| 404 field (`?view=field`) | `field 'duedate' is not a field of ENG-13` — the request's `field` and the resolved identifier; this is the sentence that tells an unknown field from an unknown issue, since both are `404` |
| move with open children | `cannot move ENG-13: it has 2 open sub-issues` |
| write on an archived issue | `ENG-13 is archived; restore it first` |
| write on an issue in the trash | `ENG-13 is in the trash; restore it first` |
| archive with open children | `cannot archive ENG-13: it has 2 open sub-issues` |
| missing confirmation | `confirm the delete of ENG-13` (`confirm the archive of ENG-13`) |
| read-only | `server in read-only mode` |
| write from another origin (§2.1.7, R28) | `cross-site write refused` |
| invalid field | `invalid value` + the domain `error.message` |

**Every refusal of a write is composed by one builder, and the subject is the identifier of the row the
route resolved.** The sentences this catalogue puts in a `form_error` — one for each layer, the two
`CLOSE_WITH_OPEN_CHILDREN` verbs', the confirmation's and the invalid-field fallback — are built by one
function of this edge,
`refusal_sentence(refusal: HifaError | None, *, verb: str | None = None, identifier: str | None = None)
-> str`, in `src/hifa/web/app.py`: the single place in the tree that turns a refusal into text (A7, P4).
Nothing else composes them — not a handler of `_install_error_handlers`, not a route branch, and not a
template (§2.3.7: a template prints the `form_error` it is handed and never assembles one) — so each
string exists once in the spec, here, and once in the code, in that function. `refusal` is the
`HifaError` the route caught, or `None` for the one refusal of this layer that no use case produces: the
missing `confirm=1` of §2.1.2.

**The builder has two arms, and the second one exists because a query has no identifier.**
`verb` and `identifier` are keywords with no default *value* of their own — the write arm cannot be called
without them — and the function **raises** if a rule whose sentence names an issue arrives with
`identifier=None`. The **request arm** is `refusal_sentence(refusal)` with both keywords omitted: it is
legal only for a refusal about the request and not about a row, it selects no rule sentence, and it
returns `invalid value: ` plus `refusal.message` — the last row of the catalogue below, which is
`invalid value: nothing to search for`, `invalid value: limit must be between 1 and 200`, and so on for
SPEC-090 §6's four messages. Its one caller today is `GET /search`: the route catches `SearchError`,
`build_search_vm` calls the builder with no keywords (§2.3.5) and stores the text in `message`. A
`SearchError` never reaches the write arm, because no write route can raise one, and the arm cannot be
reached with an issue rule and no identifier, because the function refuses it.

Each argument has exactly one source:

- `refusal.rule` is the rule id and the only thing that selects a sentence — never the exception's class,
  which is `Validation` for two of these rules and `InvalidTransition` for the third, and never two
  conditions checked in sequence. `None`, which has no rule, selects the confirmation's sentence and
  nothing else. A rule outside the catalogue falls through to `invalid value: ` plus `refusal.message`,
  the last row of the table.
- `identifier` is the `identifier` of the issue the route resolved, **not** the path segment (a page route
  also accepts the ULID, R6) and **not** `refusal.value`: `ISSUE_ARCHIVED` happens to carry the identifier
  there (`field='ref'`), `ISSUE_TRASHED` carries the issue's ULID (`field='trashed'`) and
  `CLOSE_WITH_OPEN_CHILDREN` carries the target state's type (`field='state_id'`, `value='canceled'`), so a
  builder that read `value` would print two different subjects for three sentences that must read alike.
  The row is in hand by construction: the route resolves the reference before it writes, and the `404`
  precedes every `422`/`409` of this layer (§5.7 step 3).
- `verb` is the route's own word, and one rule reads it: `CLOSE_WITH_OPEN_CHILDREN` is the rule of the
  catalogue's only two sentences that differ in a word and in nothing else, `cannot move ENG-13: it has 2
  open sub-issues` and `cannot archive ENG-13: it has 2 open sub-issues`. The confirmation's sentence reads
  it too (`confirm the archive of ENG-13`, `confirm the delete of ENG-13`). A `CLOSE_WITH_OPEN_CHILDREN`
  raised by a field write — `update_issue`'s `state`, SPEC-040 §6, `field='state_id'` — is a control of no
  verb in this catalogue and falls through to the row above rather than borrowing the word of a write it
  did not perform.
- `refusal.details['open_children']` is the count and the only number any sentence carries, printed as the
  rule counted it. The noun is `sub-issues` for every count: the sentence has one form and not two, and no
  scenario of §7 asks for a singular.

The page form and the panel call it with the same three arguments, so a refusal reads identically in
`issue_detail.html` and in `partials/issue_panel.html`; they differ in the element that carries the
`form_error`, never in the text (§2.6.9). The fallback's prefix is the builder's too: `_refusal`, the
per-field arm this module already carries for the inline edit of §2.6.3, keeps the control it resolves and
takes its message from the builder, so `invalid value: ` is written in one place and the two cannot drift.
The `text/plain` rows of this table are not `form_error`s and are not built here: `fragment requires htmx`,
`workflow state does not exist`, `invalid neighbour`, `cross-site write refused` and `server in read-only
mode` are single constants or the fragment partial, and none of them is a sentence about an issue.

In fragments, the error travels in the HTML (`.form-error`, the line beside the control, §2.6.3), **not**
in JSON: the client of this
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
    And the HTML contains 7 columns in position order
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
    And the body is the row with the field marked

  @REQ-F013 @REQ-F035
  Scenario: the detail page renders each field as an inline control and no pooled Edit form
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a GET to "/ENG/issue/ENG-13"
    Then the status is 200
    And the HTML contains "hx-patch"
    And the HTML contains "id=\"field-title-ENG-13\""
    And the HTML contains "?view=field&field=title"
    And the HTML does not contain "form-title"
    And the HTML does not contain "class=\"issue-edit\""

  @REQ-F013 @REQ-F035
  Scenario: an inline title edit on the detail page answers the field it changed
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=field&field=title" with the header "HX-Request: true" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the HTML contains "id=\"field-title-ENG-13\""
    And the HTML contains "Fix SSO login"
    And the HTML does not contain "<html>"
    And the HTML does not contain "<aside"
    And the response carries the header "HX-Trigger" with "issue-updated"

  @REQ-F013 @REQ-F035
  Scenario: a refused inline edit keeps the control and the message beside it
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=field&field=title" with the header "HX-Request: true" and the body {"title":""}
    Then the status is 422
    And the HTML contains "is-invalid"
    And the HTML contains "<details open"
    And the HTML contains "class=\"form-error\""
    And the title of "ENG-13" is "Fix login"
    And no row was added to "issue_history" for "ENG-13"

  @REQ-F013 @REQ-F035
  Scenario: the description is edited in place and Ctrl+Enter saves it
    Given the issue "ENG-13" in the state "Todo" with description "first draft"
    When I execute a PATCH to "/_/issues/ENG-13?view=field&field=description" with the header "HX-Request: true" and the body {"description":"**final** draft"}
    Then the status is 200
    And the HTML contains "<strong>final</strong>"
    And there is exactly 1 row in "issue_history" for "ENG-13" with field "description"

  @REQ-F013 @REQ-F035
  Scenario: the description's control is a textarea with a Save and no blur trigger
    Given the issue "ENG-13" in the state "Todo" with description "first draft"
    When I execute a GET to "/ENG/issue/ENG-13"
    Then the HTML contains "<textarea"
    And the description field's markup carries "name=\"description\""
    And the description field's markup carries "field-save"
    And the description field's markup carries "?view=field&field=description"
    And the description field's markup carries no "blur changed"

  @REQ-F013 @REQ-F035
  Scenario: the panel renders one inline control per field and no Edit heading
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the status is 200
    And the HTML does not contain "form-title"
    And the HTML contains "hx-patch=\"/_/issues/ENG-13?view=field&field=title\""
    And the HTML contains "hx-patch=\"/_/issues/ENG-13?view=field&field=estimate\""
    And the HTML does not contain "<dt>State</dt>"
    And the HTML does not contain "<dt>Assignee</dt>"

  @REQ-F013 @REQ-F035
  Scenario: a refused inline edit on the panel answers that field and not the aside
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=field&field=state" with the header "HX-Request: true" and the body {"state":"No Such State"}
    Then the status is 422
    And the HTML contains "id=\"field-state-ENG-13\""
    And the HTML contains "is-invalid"
    And the HTML does not contain "<aside"

  @REQ-F013 @REQ-F035
  Scenario: an inline edit of an archived issue is refused with the message and no control
    Given the issue "ENG-13" in the state "Todo" with title "Fix login" archived at "2026-11-10T12:00:00Z" and not in trash
    When I execute a PATCH to "/_/issues/ENG-13?view=field&field=title" with the header "HX-Request: true" and the body {"title":"Fix SSO login"}
    Then the status is 409
    And the HTML contains "id=\"field-title-ENG-13\""
    And the HTML contains "archived"
    And the HTML contains "data-readonly"
    And the HTML does not contain "<details"
    And the HTML does not contain "hx-patch"
    And the title of "ENG-13" is "Fix login"

  @REQ-F013 @REQ-F035
  Scenario: without JavaScript each inline control still saves through the page form
    Given the issue "ENG-13" in the state "Todo" with estimate 1
    When I execute a POST to "/ENG/issue/ENG-13" with the form {"estimate":"3"}
    Then the status is 303
    And the "Location" header is "/ENG/issue/ENG-13"
    And the estimate of "ENG-13" is 3

  @REQ-F013 @REQ-F035
  Scenario: the list row keeps its own inline title control and is not a field wrapper
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a PATCH to "/_/issues/ENG-13?view=list" with the header "HX-Request: true" and the body {"title":"Fix SSO login"}
    Then the status is 200
    And the HTML contains "<tr id=\"issue-ENG-13\""
    And the HTML contains "closest tr"
    And the HTML does not contain "field-inline"
    And the HTML does not contain "view=field"

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
    And the columns appear in the order "Triage", "Todo", "Backlog", "In Progress", "In Review", "Done", "Canceled"
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

  Scenario: the detail page of a trashed issue is served with Restore as its only control
    Given an issue ENG-13 in the state "Todo" with title "Fix login", in the trash and not archived
    When GET /ENG/issue/ENG-13 is requested
    Then the status is 200
    And the HTML contains "Fix login"
    And the HTML contains "/ENG/issue/ENG-13/restore"
    And the HTML does not contain "action=\"/ENG/issue/ENG-13\""
    And the HTML does not contain "name=\"state_id\""
    And the HTML does not contain "/ENG/issue/ENG-13/archive"
    And the HTML does not contain "/ENG/issue/ENG-13/delete"

  Scenario: the panel of a trashed issue is served with Restore as its only control
    Given an issue ENG-13 in the state "Todo" with title "Fix login", in the trash and not archived
    When GET /_/issues/ENG-13/panel is requested with HX-Request true
    Then the status is 200
    And the HTML contains "<aside id=\"panel\""
    And the HTML contains "Fix login"
    And the HTML contains "/_/issues/ENG-13/restore"
    And the HTML does not contain "name=\"state_id\""
    And the HTML does not contain "/_/issues/ENG-13/archive"
    And the HTML does not contain "/_/issues/ENG-13/delete"
    And the HTML does not contain "<html>"

  Scenario: the ULID alias of a trashed issue answers 303 to its canonical page
    Given an issue with identifier ENG-13 in the trash and not archived
    When GET /ENG/issue/<its ULID> is requested
    Then the status is 303
    And the Location header is "/ENG/issue/ENG-13"

  Scenario: a trashed issue is absent from the board, the list, the search page, the palette and the counters
    Given an issue ENG-13 in the trash and a visible issue ENG-80 in the state "Todo"
    When GET /ENG/board, /ENG/list, /search?q=ENG-13, /_/palette?q=ENG-13 and /_/counts?team=ENG are requested
    Then none of the answers contains "ENG-13"
    And the counters count 1 issue for "ENG"

  Scenario: the search page without a query is the empty state and not an error
    Given a workspace with one team ENG and an issue ENG-13 titled "Fix login"
    When GET /search is requested
    Then the status is 200
    And the HTML contains "search the workspace"
    And the HTML does not contain "Fix login"

  Scenario: the search page renders the fields of every hit
    Given an issue ENG-13 titled "Fix login" in the state "In Progress" with priority 2
    When GET /search?q=login is requested
    Then the status is 200
    And the HTML contains "class=\"hit\""
    And the HTML contains "ENG-13"
    And the HTML contains "Fix login"
    And the HTML contains "In Progress"
    And the HTML contains "H"
    And the HTML contains "<mark>login</mark>"
    And the HTML contains "\"/ENG/issue/ENG-13\""
    And the HTML does not contain "<table"

  Scenario: a query with no matches is the no-results state
    Given a workspace with one team ENG and an issue ENG-13 titled "Fix login"
    When GET /search?q=zzz is requested
    Then the status is 200
    And the HTML contains "nothing matches"
    And the HTML does not contain "Fix login"

  Scenario: a query the search refuses is answered on the page with a 422
    Given a workspace with one team ENG
    When GET /search with a 600-character query is requested
    Then the status is 422
    And the HTML contains "invalid value"
    And the search box still holds the submitted query

  Scenario: the snippet is escaped exactly once
    Given an issue ENG-13 whose title is "Fix <script>alert(1)</script> login"
    When GET /search?q=login is requested
    Then the status is 200
    And the HTML contains "&lt;script&gt;"
    And the HTML contains "<mark>login</mark>"
    And the HTML does not contain "<script>alert(1)</script>"

  Scenario: the sidebar entry survives and is active on its own page
    Given a workspace with one team ENG
    When GET /search is requested
    Then the HTML contains "href=\"/search\""
    And the search link carries "class=\"side-link is-active\""

  Scenario: the row and card fragments keep the trash out while the page serves it
    Given an issue ENG-13 in the state "Todo" with title "Fix login", in the trash and not archived
    When GET /_/issues/ENG-13/row?view=list and /_/issues/ENG-13/card are requested with HX-Request true, then its page
    Then the first status is 404
    And the second status is 404
    And the third status is 200
    And the third response contains "Fix login"

  Scenario: a move naming no state of the team answers the catalogue's 404
    Given an issue ENG-13 in the state "Todo" and a team OPS whose state is "Todo"
    When POST /_/issues/ENG-13/move is requested with HX-Request true and {"state_id":"<the OPS Todo id>"}
    Then the status is 404
    And the body mentions "workflow state does not exist"
    And the state of ENG-13 is still "Todo"
    And no row was added to issue_history

  Scenario: a move from the panel footer answers 204 and hands the client the collection
    Given an issue ENG-13 in the state "Todo"
    When POST /_/issues/ENG-13/move is requested with HX-Request true and {"state_id":"<the In Progress id>"}
    Then the status is 204
    And the body is empty
    And the HX-Trigger header contains "issue-moved"
    And the HX-Revision header carries the revision the request observed
    And the state of ENG-13 is "In Progress"
    And ENG-13 is the last issue of the column "In Progress"

  Scenario: a move refused from the panel re-renders that panel and marks it for the reader
    Given an issue ENG-13 in the state "Todo", in the trash and not archived
    When POST /_/issues/ENG-13/move is requested with HX-Request true and {"state_id":"<the In Progress id>"}
    Then the status is 422
    And the HTML contains "<aside id=\"panel\""
    And the HTML contains "ENG-13 is in the trash; restore it first"
    And the HX-Retarget header is "#panel"
    And the HX-Reswap header is "innerHTML"
    And the state of ENG-13 is still "Todo"

  Scenario: a delete of an issue already in the trash is a no-op that still answers
    Given an issue ENG-12 in the state "In Progress", in the trash and not archived, updated at "2026-11-10T12:00:00Z"
    When POST /_/issues/ENG-12/delete is requested with HX-Request true and {"confirm":"1"}
    Then the status is 204
    And the body is empty
    And the HX-Trigger header contains "issue-deleted"
    And no row was added to issue_history
    And ENG-12 still has trashed 1
    And the updated_at of ENG-12 is "2026-11-10T12:00:00Z"

  Scenario: the delete confirmation block renders its own three strings and its refusal
    Given an issue ENG-13 in the state "Todo", visible and not archived
    When POST /ENG/issue/ENG-13/delete is requested with an empty form
    Then the status is 422
    And the HTML contains "<details class=\"confirm\" open>"
    And the HTML contains "<summary class=\"btn\">Delete</summary>"
    And the HTML contains "This sends ENG-13 to the trash. Restore brings it back."
    And the HTML contains "Yes, delete ENG-13"
    And the HTML contains "btn btn-danger\">Delete</button>"
    And the HTML contains "confirm the delete of ENG-13"
    And the HTML contains "is-invalid"
    And ENG-13 still has trashed 0
    And no row was added to issue_history

  Scenario: an archive sends the reader to the list and leaves the detail reachable
    Given an issue ENG-12 in the state "In Progress", visible and not archived
    When POST /ENG/issue/ENG-12/archive is requested with {"confirm":"1"} and then GET /ENG/issue/ENG-12 is requested
    Then the first status is 303
    And the Location header is "/ENG/list"
    And the second status is 200
    And the second response contains "/ENG/issue/ENG-12/restore"
    And the second response does not contain "name=\"state_id\""

  Scenario: a visible issue's page and panel carry the four controls of the action group
    Given an issue ENG-13 in the state "Todo" with title "Fix login", visible and not archived
    When GET /ENG/issue/ENG-13 is requested and then GET /_/issues/ENG-13/panel is requested with HX-Request true
    Then the first status is 200
    And the second status is 200
    And the first response contains "name=\"state_id\""
    And the second response contains "name=\"state_id\""
    And the first response contains "/ENG/issue/ENG-13/archive"
    And the second response contains "/ENG/issue/ENG-13/archive"
    And the first response contains "/ENG/issue/ENG-13/delete"
    And the second response contains "/ENG/issue/ENG-13/delete"
    And the first response contains "action=\"/ENG/issue/ENG-13\""
    And the second response contains "action=\"/ENG/issue/ENG-13\""
    And none of the answers contains "/ENG/issue/ENG-13/restore"

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

  # A `\n` inside the quoted description is a line break: a table needs three lines and the step
  # takes one string.
  Scenario: a markdown table in a description is served as a table
    Given an issue with description "| Name | Qty |\n|:--|:-:|\n| Bolt | 4 |"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains "<table>"
    And the HTML contains a "<th>" with the text "Name"
    And the HTML contains a "<td>" with the text "Bolt"
    And the HTML contains "text-align:center"

  # The reference autolink (R30, D-081). The mapping is the caller's, so a token the read did not
  # resolve is never a link: the steps create the issue a reference names before naming it.
  @REQ-F035
  Scenario: an issue reference in a description links to the issue it names
    Given a team ENG with the issues ENG-12 and ENG-13, ENG-13 describing "follows ENG-12"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains a link of class "issue-ref" to "/ENG/issue/ENG-12"
    And the link's text is "ENG-12"

  @REQ-F035
  Scenario: a reference typed in another case links to the canonical URL
    Given a team ENG with the issues ENG-12 and ENG-13, ENG-13 describing "follows eng-12"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains a link of class "issue-ref" to "/ENG/issue/ENG-12"
    And the link's text is "eng-12"

  @REQ-F035
  Scenario: a token that names no issue stays text
    Given a team ENG with the issue ENG-13 describing "SPEC-050, SQL-7 and SPEC-070"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains no link of class "issue-ref"
    And the HTML contains the text "SPEC-050"
    And the HTML contains the text "SQL-7"

  @REQ-F035
  Scenario: a reference inside code is not linked
    Given a team ENG with the issues ENG-12 and ENG-13, ENG-13 describing "`ENG-12` in backticks"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains "<code>ENG-12</code>"
    And the HTML contains no link of class "issue-ref"

  @REQ-F035
  Scenario: a reference glued to other characters does not link
    Given a team ENG with the issue ENG-13 describing "HIFA-1034, XHIFA-103 and HIFA-103", and the issue HIFA-103 existing on team HIFA
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains a link of class "issue-ref" to "/HIFA/issue/HIFA-103"
    And the HTML contains exactly 1 link of class "issue-ref"
    And the HTML contains the text "HIFA-1034"
    And the HTML contains the text "XHIFA-103"

  @REQ-F035
  Scenario: a reference in a comment body links to the issue it names
    Given a team ENG with the issues ENG-12 and ENG-13, and a comment on ENG-13 saying "blocked by ENG-12"
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains a link of class "issue-ref" to "/ENG/issue/ENG-12"

  @REQ-F035
  Scenario: a reference to another team's issue links to that team's page
    Given a team ENG with the issue ENG-13 describing "blocked by HIFA-1", and the issue HIFA-1 existing on team HIFA
    When GET /ENG/issue/ENG-13 is requested
    Then the HTML contains a link of class "issue-ref" to "/HIFA/issue/HIFA-1"
    And the link's text is "HIFA-1"

  @REQ-F035
  Scenario: the references of a surface are resolved in one statement
    # 14 references, one statement: the resolution reads the `issue` table joined to `team` once for
    # all of them, so N references do not cost N statements.
    Given a team ENG with 14 issues, ENG-13's description naming 12 of them and its 3 comments naming the other 2
    When GET /ENG/issue/ENG-13 is requested with the statements counted
    Then exactly 1 statement reads the "issue" table joined to "team" to resolve the references
    And the HTML contains 14 links of class "issue-ref"

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

  Scenario: the page and the panel both carry the composer for a visible issue
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "name=\"body\""
    And the second response contains "name=\"body\""
    And the first response contains "action=\"/ENG/issue/ENG-13/comment\""
    And the second response contains "/_/comments/ENG-13"

  Scenario: a parked issue offers no composer
    Given the issue "ENG-13" in the trash
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "/ENG/issue/ENG-13/restore"
    And the second response contains "/ENG/issue/ENG-13/restore"
    And none of the answers contains "name=\"body\""

  Scenario: an archived issue offers no composer either
    Given the issue "ENG-13" in the state "Todo" archived at "2026-11-10T12:00:00Z"
    When I execute a GET to "/ENG/issue/ENG-13" and then a GET to "/_/issues/ENG-13/panel" with the header "HX-Request: true"
    Then the first status is 200
    And the second status is 200
    And the first response contains "/ENG/issue/ENG-13/restore"
    And the second response contains "/ENG/issue/ENG-13/restore"
    And none of the answers contains "name=\"body\""

  Scenario: a comment posted without JavaScript lands on the comments of the page
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13/comment" with the form {"body":"First look at this"}
    Then the status is 303
    And the location is "/ENG/issue/ENG-13#comments"
    And the HTML of "/ENG/issue/ENG-13" contains "First look at this"

  Scenario: the comment fragment answers the one list item it wrote
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/_/comments/ENG-13" with the form {"body":"From the panel"} and the header "HX-Request: true"
    Then the status is 200
    And the response carries the header "HX-Trigger" with "comment-created"
    And the HTML contains "<li class=\"comment\""
    And the HTML contains "From the panel"

  Scenario: a comment with no body is refused and the box comes back
    Given the issue "ENG-13" in the state "Todo" with title "Fix login"
    When I execute a POST to "/ENG/issue/ENG-13/comment" with the form {"body":"   "}
    Then the status is 422
    And the HTML contains "a comment needs a body"
    And the HTML contains "name=\"body\""
  Scenario: the timeline reads as sentences, oldest first, and shows no key
    Given issue ENG-13 with a created row and two state moves, Backlog to Todo and Todo to Done
    When GET /ENG/issue/ENG-13 is requested
    Then the status is 200
    And the first history row is "ginkelte created the issue"
    And the second history row is "ginkelte moved from Backlog to Todo"
    And the third history row is "ginkelte moved from Todo to Done"
    And no history row contains a 26-character ULID
    And no history row contains "state_id"

  Scenario: the panel shows eight rows and offers Show all
    Given issue ENG-13 with 12 history rows
    When GET /_/issues/ENG-13/panel is requested
    Then the status is 200
    And the history list has 8 items
    And the page contains "Show all 12"

  Scenario: Show all expands the timeline in place
    Given issue ENG-13 with 12 history rows
    When GET /_/issues/ENG-13/history?limit=100 is requested
    Then the status is 200
    And the history list has 12 items
    And the response contains no "Show all"

  Scenario: a full window is a floor and the page says so
    Given issue ENG-13 with 120 history rows
    When GET /ENG/issue/ENG-13 is requested
    Then the status is 200
    And the page shows 100 history rows
    And the page contains no "Show all" control
    And the panel of the same issue contains "Show all 100"
    And the page contains "the last 100 events"
    When GET /_/issues/ENG-13/history?limit=100 is requested
    Then the status is 200
    And the history list has 100 items
    And the response contains "the last 100 events"
    And the response contains no "Show all"

  Scenario: an issue whose history is empty renders no timeline
    Given issue ENG-13 whose history collection is empty
    When GET /ENG/issue/ENG-13 is requested
    Then the status is 200
    And the page contains no "history-list"
    And the page contains no "No activity"

  Scenario: a row whose state is gone falls back to the kind and the word
    Given issue ENG-13 with a state history row whose old value is not a state of the team
    When GET /ENG/issue/ENG-13 is requested
    Then the history row contains "state (deleted)"
    And the history row contains no 26-character ULID
    And the response contains no "→"

  Scenario: unknown fields and unknown actors are named, not raised
    Given issue ENG-13 with a history row of field "mood" and a row whose actor was deleted
    When GET /ENG/issue/ENG-13 is requested
    Then the history contains "someone"
    And the history contains "changed mood"
    And the status is 200

  Scenario: the timeline is a list of times, not a table
    Given issue ENG-13 with two history rows
    When GET /ENG/issue/ENG-13 is requested
    Then the timeline is an "ol" element with two "li" children
    And each history row contains a "time" element with a "datetime" attribute
    And the page contains no "FROM" and no "TO" column header

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
7b. `web/markdown.py`: `issue_ref_candidates` and `render_markdown(text, links=None)` — the mapping
   from folded candidate to canonical `(team_key, identifier)`, the anchor, the skip list and the
   `a.issue-ref` class — with `markdown-it-py` + the in-house allowlist of §2.3.6, and
   `markdown-it-py` declared in the base group of `pyproject.toml` (D-020).
8. Templates of §2.2 — start with `base.html` with the 5 blocks and the `<noscript>`.
9. `pytest tests/e2e/web -q` green (99 scenarios of §7). The four scenarios marked manual in §7, the
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
  without guarantees. The write actions are the exception and declare their 320 px behaviour in
  §2.6.9; the whole-page floor is T-19.
- **Inline editing of an issue on a board card.** A card is a one-line summary owned by the drag of
  §2.6.4/§2.6.5; a `DblClick` editor there is its own ticket with its own scenarios (HIFA-114 leaves
  the card alone).
- **Inline editing of `labels`** as chips with a menu: the writes land with HIFA-61/HIFA-62. The chips
  render read-only in this layer (§2.6.3).
- **Inline editing of `project`, `cycle` and `parent`**: no write use case carries them (HIFA-106); they
  stay read-only rows of the detail page's `dl.meta` (§2.6.3).
- **Editing a comment**: the composer of §2.6.10 writes one and no route of this layer rewrites one.

## 11. Notes

- spec_revision 45 (2026-11-24): **an identifier written in rendered markdown becomes a link to the
  issue it names, and only when it names one (HIFA-112, D-081).** `version` 12 → 13 is §2 gaining a
  parameter, a permission and one read; the corpus moves **251 → 259** scenarios (the seventeen
  files, `grep -cE '^  (Scenario|Scenario Outline):'`, each outline once) and §7 of this file
  **91 → 99**, and `DECISIONS.md` gains its **81st** entry. §2.3.6 gains the second parameter of
  `render_markdown` — a **mapping** from the candidate as written, folded to upper case, to the
  canonical `(team_key, identifier)`, never a callable — the one scanner
  `issue_ref_candidates(text: str | None) -> list[str]` that both the renderer and the collector of
  `src/hifa/web/app.py` call, the anchor
  `<a class="issue-ref" href="/{TEAM}/issue/{CANONICAL}">{as written}</a>`, the skip list
  (`code_inline`, `fence`, `code_block`, the text of an existing link), the maximal-run boundary, the
  resolution rule that leaves `SPEC-*` as text, the one-statement-per-surface rule with its
  `issue JOIN team ON team.id = issue.team_id` projection, and the `a.issue-ref` CSS contract; R30
  states the same contract and REQ-F070-18 is the id its arrow mints, since the component table
  carries `REQ-N`/`REQ-S` rows only; R15's scheme sentence gains the one anchor whose `href` may be
  site-relative, and §3 records the one `issue`/`team` statement the detail surface now makes.
  **SPEC-030 §2.6 contracts `issue_ref_links(conn, refs)`**, the batch twin of `issue_id_by_ref`
  (`spec_revision` 25 → 26, `version` 10 → 11: a function joins §2's closed export list), and that
  statement is what lets the surface pay one read; D-081's `Touches` names those two files too, and
  its amendment block below records that correction. The measured reason the link is
  resolution-checked and not pattern-only: **308 occurrences of 71 distinct tokens**, of which **42**
  name an existing issue, against four teams (a snapshot of `$HIFA_DB`, measured 2026-11-24).
  Eight scenarios land in §7 and in `16-dashboard.feature` — the block R30 states plus the cross-team
  case — each `@deferred(HIFA-112)` until its test exists; the ticket is registered in
  `scripts/spec_claims_debt.json` with a cluster of **no keys**, because a key for a scenario a
  `@deferred` already accounts for is stale by construction.
- spec_revision 44 (2026-11-23): **the round-three quality review's four minors are settled, and none of them
  moves a contract.** `version` stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**) and
  `DECISIONS.md` stays **80** decisions: no route, status, value of `view` or scenario moves. (a) **The twelve
  `§` citations this ticket had left in the code are gone** — `app.py` (five), `readmodels.py` (three) and
  `app.js` (four), each rewritten as the rule it named; the five `app.py` already carried are untouched, and
  revision 43's note (i) now says it removed six of the eighteen and that these twelve fall here. (b) **§2.6.3's
  aside and revision 43's note (b) named the wrong commit**: the `409` and its `HX-Retarget` entered in
  `79a6f80`, the ticket's implementation commit, and not in `3ddafc1`; both sentences now carry the right SHA.
  (c) **§6's `Conflict` row generalised a `409` the drop does not swap**: the cell now scopes the swap to a
  fragment request — where the message is read in place and never toasted — and leaves the drop's own `409`
  with its recovery and its toast, which the `InvalidTransition` row of the same table and §2.6.4 already said.
- spec_revision 43 (2026-11-23): **the second quality review's two findings and seven minors are settled, and
  the two that mattered were a shortcut whose scope the text had narrowed and an aside that claimed a fix the
  tree had already made.** `version` stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**)
  and `DECISIONS.md` stays **80** decisions: no route, status, value of `view` or scenario moves. (a) **§2.6.2's
  `Cmd/Ctrl+Enter` row named one control while the client accepts three scopes**: the row reads "any control of
  a `.field-inline` wrapper, the comment composer of §2.6.10, or the quick-add", and a new implementation rule
  states that the submitted form must sit inside one of the three (`closest`, the client's `SUBMIT_SCOPE`) and
  that a `<details class="confirm">` form of §2.6.9 is never submitted by the key. The code is not narrowed to
  the description: it scoped the three already, and its comment now says so. (b) **§2.6.3's `409` aside was
  still written in the future tense** — "adding 409 to it is new work of this ticket" — against §2.1.3 and
  against the tree: it now says the listener enabled `422` alone until HIFA-114 added the status and the
  `HX-Retarget`, carried since `79a6f80`, and spec_revision 42's note, which claimed that aside as done, says
  what that revision did and did not touch. (c) **The citations of §2.6.2 had drifted six lines**: the branch
  of `requestSubmit()` is `app.js:328-335` and the `Escape` field branch is `app.js:337-347`, here and in
  spec_revision 41's note. (d) **§2.6.3's "the whole file, exactly" snippet of `field_title.html` was missing
  an attribute**: it now carries `hx-disabled-elt` and the `hx-disinherit` of the rule below. (e) **The
  double-send guard fired on requests it cannot cover**: inherited by a control, `hx-disabled-elt="find
  button"` matched nothing on a `blur changed`/`change` save and htmx logged the miss, so the seven forms
  declare `hx-disinherit="hx-disabled-elt"` and §2.6.3 states the rule — the guard covers the submits and
  the control's own saves keep their `hx-sync`. (f) **§6's `422` row and its `409` `Conflict` row still
  promised a toast** for answers §2.1.3 swaps in place: both now say the message is read where it is swapped
  in, and the toast keeps the `500` and the retargeted `404`. (g) **`_field_refusal` gained the `NotFound` its
  re-read can raise** — the issue gone between the refusal and the render. (h) **The static client test now
  bites the rule and not the prose**: it asserts `.field-inline`, `.comment-composer` and `#quick-add` in the
  scope, the absence of `confirm` from it and the exclusion the comment above it states, all read from `app.js`
  with its comments stripped; `_form_of` fails with a sentence when a `<form>` carries no `method`/`action`.
  (i) **Six of the `§` citations this ticket had added to the code are gone** — eighteen of them stood in
  `app.py`, `readmodels.py` and `app.js`, and this revision replaced six with the rule each one named; no
  citation older than this ticket is touched, and the twelve that remain are withdrawn in spec_revision 44. (j) **Two
  sentences of §2.6.3 and §5.3 told the same story in the future tense** — the `description` bullet
  ("this ticket implements", "the branch is new work") and the field flow's "gains `409` in this ticket" — and
  both now say what the tree does.
- spec_revision 42 (2026-11-23): **the quality review's two spec findings are settled, and neither moves a
  contract.** `version` stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**) and
  `DECISIONS.md` stays **80** decisions: no route, status, value of `view` or scenario moves. (a) **§6's
  `422` row and the tree disagreed about a reference nobody reads**: the row said `parent` sat in
  `_FIELD_REFERENCE_RESOURCES` with no control to send it, and the set carried it anyway; `parent` is gone
  from both, so the row names `state` and `member` — the two references `EDIT_COMMAND_FIELDS` sends — and the
  comment above the set says why `parent` is not one of them. (b) **§2.1.3 described an early return the
  client does not make.** The aside said `htmx:responseError` returned for a `.field-inline` on a `409`; the
  handler has no such guard, and one that mattered could not exist: htmx re-reads `isError` after
  `htmx:beforeSwap`, so a `409` never reaches `responseError` at all. The paragraph now says what the listener
  really does — `422`, `409` and every `HX-Retarget` answer swapped in place, only a `500` left to the toast.
  §2.6.3's aside, which still called the `409` "new work of this ticket", was **not** touched here: this
  revision settled the paragraph of §2.1.3 only, and spec_revision 43 rewrites that aside in the past tense.
- spec_revision 41 (2026-11-23): **the round-2 review's three minors are settled, and the one that mattered
  was a focus rule promising a return the client does not make.** `version` stays **12**, the corpus stays
  **251** scenarios (§7 of this file: **91**) and `DECISIONS.md` stays **80** decisions: no route, status,
  value of `view` or scenario moves. (a) **§2.6.2's focus bullet sent every swap through `data-identifier`**,
  but the panel's own `htmx:afterSwap` focuses the panel and returns before that branch
  (`app.js:267-275`): the bullet now names the two destinations — `data-field` for the saved field's
  wrapper, `data-identifier` for the row or card — and says that the swap which opens the panel focuses the
  panel itself (§2.6.9) and that closing it leaves focus on `<body>`, with no restoration invented for the
  row that asked. (b) **Three asides still described HIFA-114's work as unwritten while `app.js` shipped
  it** — the `409` and the `HX-Retarget` (`app.js:213-220`), `requestSubmit()` for `Cmd/Ctrl+Enter`
  (`app.js:328-335`) and the `Escape` field branch before the panel (`app.js:337-347`): they are past tense
  now, with the ticket and the reason each one gave kept, and spec_revision 39's sentence that quoted them
  by name is reworded with them. (c) **§6's `422` row enumerated `parent` among the control values that
  resolve to no row**, though the `PATCH …?view=field` builds its `UpdateIssue` from `EDIT_COMMAND_FIELDS`
  alone (`app.py:1480-1498`), which carries no `parent`: the row names `state` and `member` and says that
  `parent` sits in `_FIELD_REFERENCE_RESOURCES` (`app.py:1363`) with no control to send it, which is
  §2.1.2's seven. Clause (d) of spec_revision 40 is corrected here.
- spec_revision 40 (2026-11-23): **the compliance review's five findings are settled, and the one that
  mattered was a focus with no destination.** `version` stays **12**, the corpus stays **251** scenarios (§7
  of this file: **91**) and `DECISIONS.md` stays **80** decisions: no route, status, value of `view` or
  scenario moves. (a) **§2.6.9's table sent the focus of an opened panel to a heading the ticket deleted**:
  the row said `<h2 tabindex="-1">` while §2.6.3 retired that `<h2>` with the wrappers, so the row now names
  the panel itself — `<aside id="panel" tabindex="-1">` in `partials/issue_panel.html`, focused once by the
  `htmx:afterSwap` of `app.js` when the swap answered the panel request; the other rows of the table are
  untouched. (b) **Two rules outlived their only consumer**: `.detail-title` and `.panel-title` go, and
  §2.5.8 carries the grep, which now answers `static/css/app.css` and nothing else. (c) **§2.5.8 declared
  `.field-text` as a `<span>` and the description prints a `<div>`**: the row now says the class is the
  value's element — a `<span>` for the six single-line fields — so the template and the markdown-of-block
  `description` agree. (d) **§6 had no row for a control value that does not resolve**: `state`, `member` and
  `parent` are rows the domain resolves, so a value that names none is that field's `422` — open wrapper,
  `.is-invalid`, `invalid value: ` — while the page route keeps its `404`; one row, in the `422` block.
  (e) **Two refusals had no test**: an unknown `field` on `?view=field` (`404`, `#toasts`, `is not a field of`)
  and a field write on an issue in the trash (`422`, open wrapper, `data-readonly`, no `hx-patch`) are pinned
  in `tests/e2e/web/test_dashboard_fields.py`, which the corpus does not have to grow for: neither is a
  scenario, both are rows §6 already carried.
- spec_revision 39 (2026-11-23): **the field contract is implemented and its ten deferrals are paid.**
  `version` stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**) and `DECISIONS.md`
  stays **80** decisions: no route, status, value of `view` or scenario moves — this revision records that
  the tree now answers what §2.1.3, §2.3.4, §2.5.8 and §2.6.3 declared, so the asides of §2.1.3 and §2.6.2
  that had carried the work as not yet written describe the client and the route that ship. `readmodels.build_issue_vm`
  builds the seven-entry `fields` dict (`writable` is `can_write` and neither layer, `open` is the field a
  `422` hands back and the trash's, `invalid`/`message` are that control's entry of `form_errors`), the
  `PATCH` answers `?view=field&field={name}` with one wrapper and keys its refusals on the requested field,
  and the seven `partials/field_*.html` replace the pooled `issue_form_fields.html` on both surfaces. The
  ten scenarios of §7 and of `16-dashboard.feature` lose their `@deferred(HIFA-114)`: each is pinned by the
  test whose name is its title, in `tests/e2e/web/test_dashboard_fields.py`. The `HIFA-114` cluster of
  `scripts/spec_claims_debt.json` keeps its ticket and holds no keys, which is what
  `scripts/spec_claims_check.py` reports: `0 new, 0 broken`.
- spec_revision 38 (2026-11-23): **the focus return is one sentence with three branches, and the third branch
  moves nothing.** `version` stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**) and
  `DECISIONS.md` stays **80** decisions. The bullet of "the server is the truth" said focus went "to the
  wrapper itself when the answer has no control to focus", which is unimplementable on the archived issue's
  `409`: that wrapper is a `<div class="field-value" data-readonly data-invalid>` with no `tabindex`, so it is
  not focusable and there is no jump to make. The sentence now names the three answers one by one — the
  `<summary>` of that field's `[data-field="{name}"]` wrapper on a save, the marked `.is-invalid` control on a
  `422` or a `409` that carries one, and no movement at all on the archived refusal, where the message arrives
  by the toast of `partials/fragment_error.html` and the wrapper's `.form-error` and the reader hears it as a
  content change. Nothing else of §2.6.3 changes.
- spec_revision 37 (2026-11-23): **the closing notes on the field contract: two states under one word, one
  taxonomy, and the row's markup.** `version` stays **12**, the corpus stays **251** scenarios (§7 of this
  file: **91**) and `DECISIONS.md` stays **80** decisions. (a) **§2.6.3's refusal bullet gave one answer for
  two states**: the trash's `422` comes back `open` as the `<details open>` whose `<summary>` is the marked
  value and whose content is the message and no control, and the archived issue's `409` comes back with **no**
  `<details>` — a `<div class="field-value" data-readonly data-invalid>` — which is what §2.3.4's table and
  §7's archived scenario already said. (b) **"No stop at all" was true of the `409` alone**: the trash's open
  `<details>` contributes its `<summary>` to the tab order, and the keyboard bullet says so on both branches.
  (c) **§2.1.2 called `move`, `archive`, `delete` and `restore` "the layer forms"**, a third name for a set
  §2.6.9 already names; they are **the four writes that are not the field edit**, and the three layer writes
  of §5.7 and R27 are `archive`, `delete` and `restore` — §2.6.9 and §5.7 are untouched. (d) **The row's
  markup is now quoted verbatim** from `partials/issue_row.html` (its `<td class="cell-title">`): the block
  omitted `type`, the `class` with `is-invalid`, `aria-label`, `autocomplete` and `aria-invalid`, so the copy
  presented as "unchanged — attribute for attribute" would have changed the row.
- spec_revision 36 (2026-11-23): **the third review round closed seven findings on the field contract, and the
  one that was not cosmetic was a refusal that printed nothing.** `version` stays **12** (no route, status,
  value of `view` or scenario moves), the corpus stays **251** scenarios (§7 of this file: **91**) and
  `DECISIONS.md` stays **80** decisions. (a) **The `409` and the trash's `422` came back mute.**
  `ISSUE_ARCHIVED`/`ISSUE_TRASHED` carry `field="ref"`/`"trashed"`, which is none of the seven controls, so
  `_refusal` resolves no control and the sentence landed in `form_error` — the key the field wrapper never
  prints. §2.3.4 and §5.3 step 5 now say a `view=field` refusal keys its message on the field the request
  named, `form_errors={requested_field: refusal_sentence(refusal, verb="edit", identifier=…)}`, which is what
  makes §7's archived-issue scenario (`contains "archived"`) producible. (b) **§2.3.4 no longer states a count
  for the action group**: it said "the four", the group is the move control, the archive block and the delete
  block, and rev 34 (j) already declared that no prose of this spec counts it. (c) **Both control-less
  wrappers print `data-readonly` and `data-invalid`.** §2.5.8 and §2.6.3 agreed with each other and not with
  the trash's `<summary>`, which printed neither; the value element of both states carries both attributes
  now — a `<div>` with no `<details>` on the `409`, the `<summary>` of the open one on the `422`. (d)
  **`.panel-error` does not exist**: the panel's line for a refusal with no field of its own is a
  `<p class="form-error">` (`partials/issue_panel.html`), and §2.6.3 and §2.6.9 say so. (e) **§2.6.9's "one of
  the five" is one of the four** — the section's own opening counts move, archive, delete and restore.
  (f) **A `/_/` `404` is never `error.html`**: §5.3 step 2 answers `partials/fragment_error.html` for the
  unresolved ref, as §2.1.3 and R18 already said; the page routes keep `error.html`. (g) **`restore` is a
  layer form too**: §2.1.2 listed three of the four forms that carry `method`/`action` and no `hx-*`.

- spec_revision 35 (2026-11-23): **the second review round on the field contract closed eleven findings: the
  contract gains one key and loses three claims that were not true.** `version` stays **12**: no route, no
  status, no value of `view` and no scenario is added or removed — §2.1.2, §2.1.3 and §2.6.3 keep the surface
  version 12 declared — and the one read-model key this revision adds (`writable`, §2.3.4) is a flag on the
  same `fields` dict version 12 introduced, needed so the third wrapper §2.6.3 already owed on an archived
  issue can be selected without a client branch. The corpus stays **251** scenarios (§7 of this file: **91**)
  and `DECISIONS.md` stays **80** decisions. What changed, and why:
  (a) **`field_control.html` could not do what §2.2 said.** A partial handed `name="title"` has to choose
  between three controls and seven value keys, i.e. `{% if name == "title" %}…`, while the paragraph claimed
  "the file never decides *which* field it is". §2.2 now declares **seven partials** (`field_title.html`
  through `field_estimate.html`), each with its field's name, key, value expression and
  `?view=field&field=…` written literally, included in order from `issue_detail.html` and
  `partials/issue_panel.html`; the `{% with %}` include is gone, and §2.3.7's second row forbids a binding
  that tells one field from another. A `{% call %}` macro was the alternative and does not fit: a field's
  value state and its control are two different expressions in two different elements, and one `caller()`
  carries one of them. (b) **The third state of the wrapper was not selectable.** §7's `409` scenario
  requires a wrapper with the value and **no** `<details>`, while `fields[]` only said open/closed and §2.2
  called the partial "the two states". §2.3.4 gains `writable` and states the three combinations —
  `True` (closed or open), `False` + `open` (the trash's `422`: `<details open>`, marked, no control) and
  `False` + closed (the archive's `409`: value and message, no `<details>`, no control) — and §2.2, §2.6.3's
  markup, §2.6.9 and §7 say the same. (c) **`409` is the archived issue; the trash is `422 ISSUE_TRASHED`.**
  §5.3 step 5 said "409 (an archived **or a trashed** issue)" and then built the trash's answer under it,
  while §6 and §2.6.9 keep the two apart; the step now names `409` = archived and `422` = trashed and leaves
  no sentence that mixes them. (d) **`HX-Reswap` is `beforeend`, not `innerHTML`.** `_fragment_error`
  (`app.py:280`) appends the partial to `#toasts`; the five mentions of the field `404` (§2.1.3, its header
  table, §5.3 twice, §6) say so now, and the panel's `innerHTML` retarget (§2.6.9) is untouched: it is
  another header on another target. (e) **`.field-input` does not retire.** It has a consumer this ticket
  does not touch — `partials/comment_composer.html:17`, whose `textarea.field-input` (`app.css:444`) is the
  composer's 96 px box and mono — so §2.5.8 keeps the class and its rule, retires only `.form-title`,
  `.issue-edit`, `.form-actions` and `.field`, and carries the grep that decides each one. (f)
  **`.inline-input` keeps the rule it already has.** §2.5.8 declared a new full rule for it, which would have
  given the list row a border and a background and taken away its 360 px cap, on the one control
  `partials/issue_row.html:21` owns; the table now names `app.css:431`'s rule, unchanged, and adds nothing,
  so requirement 9 ("the row does not change") is literal. (g) **`issue_row.html` does not lose
  `data-original`.** The wrapper never prints it, so nothing is added there; the row keeps the one it prints
  today (`issue_row.html:20`), because deleting it would be the change §2.6.3's ninth rule forbids. (h)
  **`Esc` on an open field is new work of this ticket.** §2.6.2 stated the ordering as if it ran; the shell's
  `keydown` (`app.js:270-275`) answers `Escape` with `closePanel()` and returns, and nothing looks for a
  `details[open]` — the bullet declares the branch, as its `Cmd/Ctrl+Enter` neighbour already did. (i)
  **`.field-error` does not exist.** §6 said the fragment's error travels in it; the wrapper's markup, §2.6.3
  and `app.css:427` all use `.form-error`, and §6 says that now. (j) **The scenario's name is its test's, and
  the steps are the authority.** `a visible issue's page and panel carry the four controls of the action
  group` is not renamed — the revision below says why — and the comment in `16-dashboard.feature` no longer
  counts the edit form among the group's controls: the group is the move `<select>`, the archive block and
  the delete block, and the edit is the fields themselves (§2.6.9). No prose of this spec states a count for
  that group. (k) **The revision-33 entry's `?view=panel` sentence is corrected where it stands**, not
  deleted: the value has no HTTP client of its own any more, and the layer refusals call
  `_issue_edit_fragment(view="panel")` internally (§2.1.3).

- spec_revision 34 (2026-11-23): **the review of the field-per-request delta closed eleven findings, and the
  contract does not change shape — it stops saying things that are not true or not implementable.** `version`
  stays **12**, the corpus stays **251** scenarios (§7 of this file: **91**) and `DECISIONS.md` stays **80**
  decisions: no route, no status, no read-model key and no scenario is added or removed. What moved, and why:
  (a) **R27 counted a set that no longer exists.** It said "the seven page forms" and then "one route and seven
  forms per surface"; §2.1.2 as it stands has **thirteen** page forms — the quick add, the seven field edits,
  `move`, the comment and the three layer writes — and the rule enumerates them now. (b) **`?view=field` had two
  contractual answers for one failure** — `partials/fragment_error.html` into `#toasts` in §2.6.3 and `text/plain`
  in §6 and §5.3 — and now has one body and one media type: the partial, under `HX-Retarget: #toasts` and
  `HX-Reswap: innerHTML`, with a message that names the field (`field 'duedate' is not a field of ENG-13`), which
  is what tells an unknown field from an unknown issue. (c) **The `409` was described as if it already replaced
  the wrapper.** htmx swaps no `4xx` unless a listener says so, the listener in the tree enables `422` alone and
  `htmx:responseError` turns a `409` into `could not save: the server rejected the change`; §2.1.3 now prescribes
  the listener — `422`, `409`, and every `/_/` answer that carries `HX-Retarget` — and §2.6.3 states the flow as
  this ticket's work, with R11 moving with it. (d) **`changed` compares against htmx's `lastValue`**, the value it
  records when it processes the node, not against `data-original`: no script of the tree reads that attribute
  (`grep -rn "data-original" src/` answers one printed line in `partials/issue_row.html`), so both shapes stop
  printing it instead of carrying it. (e) **`Cmd/Ctrl+Enter` is new work**: the shell's `keydown` returns on
  `metaKey`/`ctrlKey` before it looks at a key and nothing under `src/hifa/web/` calls `requestSubmit()`, so
  §2.6.2 and §2.6.3 declare the branch instead of presenting the rule as implemented. (f) **A trashed issue's
  field is a `422` on the wrapper**, and the answer needs `include_trashed=True` for `view="field"` where the tree
  passes it for `view="panel"` alone: §6's `ISSUE_TRASHED` row, §2.6.3 and §2.1.4's `PATCH` row say so, and R29
  names that fourth read and keeps it a non-surface the way it keeps the history fragment one. (g)
  **`description_invalid` left `row_vm`**: a row has one control, so `title_invalid` is its only flag, and the two
  detail surfaces read `fields[<name>].invalid`. (h) **`partials/field_control.html` is defined**: `name` and
  `field` arrive as literals, the seven wrappers are seven `{% with %}` includes, and no template indexes `fields`
  with a loop variable — §2.3.7 now says which lookup is an access and which is the read model's decision. (i)
  **§2.5.8 declares the eight classes the wrapper prints** and retires the ones the delta orphans, with the grep
  that decides each: `.form-actions` (its only consumers were the two pooled forms, and its `.confirm` variant
  matches nothing), `.field` and `.field-input` (only `partials/issue_form_fields.html`), while `.field-label`
  stays because the value state prints it. (j) **The scenario `a visible issue's page and panel carry the four
  controls of the action group` keeps its name**, for the reason D-080 already gives for the two titles this
  change narrows: a scenario's title is its test's name, and that test exists. The group the steps assert is the
  move control, the archive block and the delete block — and **the steps are the authority**, not the title: the
  last assertion pins `action="/ENG/issue/ENG-13"`, which the seven field forms carry and no grouped control
  does. The comment inside the scenario says exactly that, and no count of that group is stated in this spec. (k) **`view=panel`
  names no HTTP client of the UI any more**: the layer refusals render the aside server-side through
  `_issue_edit_fragment(..., view="panel")`, and the only request that still carries the value is §7's
  `the panel edit answers the panel`.
  **Correction, carried by spec_revision 35:** four statements of the entry above are superseded, and the
  body of this spec is the authority. (1) (b)'s `HX-Reswap: innerHTML` for the field `404` is
  **`beforeend`** — `_fragment_error` appends the partial to `#toasts` (`app.py:280`), §2.1.3, §5.3 and §6
  say it. (2) (d)'s "both shapes stop printing `data-original`" is wrong: the wrapper never has it and
  `issue_row.html` keeps the one it prints (`issue_row.html:20`); the row does not change (§2.6.3,
  requirement 9). (3) (h)'s `field_control.html` and its seven `{% with %}` includes are replaced by seven
  per-field partials (§2.2). (4) (i)'s `.field-input` does **not** retire — `partials/comment_composer.html:17`
  consumes it — and (j)'s "three" is not a count this spec states: the scenario's title is its test's name
  and its steps are the authority (§2.5.8, §2.6.9).

- spec_revision 33 (2026-11-22): **the field is the control: every editable field of an issue edits in place,
  on both detail surfaces (HIFA-114, D-080).** The detail page and the panel rendered the same pooled
  `<form class="issue-edit">` with one `Save`, so a reader who wanted to fix a word in the title edited the
  seven fields at once; measured on the served dashboard (`127.0.0.1:8823`, commit `bb7ccf5`),
  `curl -s http://127.0.0.1:8823/HIFA/issue/HIFA-98 | grep -c hx-patch` answered `0` and the panel carried
  **one** `hx-patch`, on its form. Six changes, one decision. (1) **§2.6.3 is rewritten** around the seven
  fields — `title`, `description`, `priority`, `state`, `assignee`, `due_date`, `estimate` — each in its own
  wrapper and each edited **alone**, with the exact markup of both states: a `<details>` whose `<summary>` is
  the value and whose content is that field's own `<form method="post" action="/{team}/issue/{identifier}">`
  with the control. The reveal is the browser's and not a script's — no Alpine component, no `x-data`, no
  client flag — and `vm.fields[<name>]` (§2.3.4) is what decides which state a wrapper renders; the
  `<details>` is also why the editor still works with JS off, since the control state is always in the
  document. (2) **`description` is inline, deliberately.** §2.6.3 said the opposite, in so many words, and
  §2.1.3 justified `?view=panel` by it. It is now a field like the others with the one difference that
  matters: `Enter` in a `<textarea>` is a newline, so it saves on **`Cmd/Ctrl+Enter`** (§2.6.2's rule,
  extended to this control) **or** on the `Save` that sits inside the field, and **never** on `blur`. (3)
  **`PATCH /_/issues/{identifier}?view=field&field={name}`** is the fourth value of `view`, and its answer is
  **one field wrapper** — not the page, not the aside, not the row — so a save on either surface replaces the
  node the reader is standing on. `view=list`, `view=board` and `view=panel` keep their bodies and their
  swaps; `view=panel` keeps its clients, which are now the **refusals** of the layer writes and not a
  successful edit. *(Superseded — correction carried by spec_revision 35: those are not clients of the wire.
  The layer refusals are internal calls to `_issue_edit_fragment(view="panel")` (`app.py:1590`), not
  requests that name `?view=panel`, and §2.1.3 is the authority: the value has no HTTP client of its own any
  more.)* (4) **`assignee` stays a text input holding the member id**, and the new paragraph says
  why in one place: `build_issue_vm` in `src/hifa/web/readmodels.py` builds `"assignees": []`, and
  `IssueDetail.members` is the comment-authors and history-actors map of SPEC-030 §2.6 SQL-7 row 13, not a
  roster. A member picker is another ticket. (5) **The pooled form and its heading leave the tree**, with
  `.form-title` and `.issue-edit` from `src/hifa/web/static/css/app.css` (their only two consumers were those
  two forms, `grep -rn "form-title\|issue-edit" src/`), and the `dl.meta` of both surfaces stops echoing a
  field the body now controls: the page keeps `Project`, `Cycle`, `Parent`, `Labels`, `Created` and
  `Updated`, the panel keeps none. (6) **The fragment `GET /_/issues/{identifier}/field/{field}` is
  withdrawn**: it was declared in §2.1.3 since the first commit, no route under `src/hifa/web/app.py` and no
  template ever served it, and its only possible client — a click that swaps the value for the control — is
  the one the no-JS rule forbids. §7 and `specs/acceptance/001-mvp-v1/16-dashboard.feature` gain the same
  **ten** scenarios, `@deferred(HIFA-114)` until their tests land, so §7 moves **81 → 91** and the corpus
  **241 → 251 (239 `Scenario` + 12 `Scenario Outline`)** — the `grep -cE '^  (Scenario|Scenario Outline):'`
  of the seventeen files — and every statement of the count moves with it: `specs/001-mvp-v1.md` §7,
  `specs/001-mvp-v1.tasks.md` and `specs/INDEX.md`. **`version` 11 → 12**: §2 changes — a new value of `view`
  and a new query parameter are the public contract of two routes this layer already served, and the HTML of
  both surfaces changes with them.

- spec_revision 32 (2026-11-22): **a markdown table is served as a table (HIFA-113, D-079).** `GET
  /HERMES/issue/HERMES-1` answered a GFM table as one `<p>` of pipe characters, and the two layers
  that produced it are both additive to fix. (1) **§2.3.6 enables the one rule.** The `commonmark`
  preset ships `table` and leaves it disabled, so the module gains `MD.enable("table")` and keeps the
  preset: `gfm-like` would also turn on strikethrough, linkify and task lists, i.e. more tags and a
  checkbox `input` in the allowlist, for no requirement. (2) **The allowlist gains the structure.**
  `table`, `thead`, `tbody`, `tr`, `th` and `td` join `ALLOWED_TAGS`, which R15 enumerates and
  REQ-S003 names as the sanitizer contract. (3) **The alignment is the one `style` kept.** markdown-it
  writes `style="text-align:left|center|right"` on the `th`/`td` of a column declared `:--`, `:-:` or
  `--:`; `(th, "style")` and `(td, "style")` are accepted on an exact match of those three values and
  on nothing else, any other value drops the attribute and keeps the cell text, and no other tag
  carries a `style`. (4) **The wrapper is the sanitizer's own.** Every `table` the walker emits is
  wrapped in `<div class="table-scroll">…</div>`, written by the walker and never passed through from
  user input — with `html=False` markdown-it cannot emit a `div` and a written one is escaped — so
  `div` does not enter `ALLOWED_TAGS`. (5) **The CSS contract moves with it.** The base rules style
  `.description table` and its cells, and the `@media (max-width: 900px)` overflow rule moves from
  `.description table` to `.description .table-scroll`, because `display: block` on the table breaks
  the alignment of its own header. §7 and
  `specs/acceptance/001-mvp-v1/17-dashboard-hardening.feature` gain the one scenario
  (`@deferred(HIFA-113)` until its test lands), so §7's count moves **80 → 81** scenarios and the
  corpus is **241 (229 `Scenario` + 12 `Scenario Outline`)**, and every statement of the count in
  `specs/001-mvp-v1.md` §7, `specs/001-mvp-v1.tasks.md` and `specs/INDEX.md` moves with it.
  **`version` 10 → 11**: §2 changes — an allowlist that R15 enumerates and a `.feature`-pinned
  rendering are the public contract of this layer — and the change adds elements to the HTML two
  routes already served.

- spec_revision 30 (2026-11-20): **the activity section is declared — its grammar, its layout, its
  states, its density and its accessibility — and the panel gets the one fragment D-C needed
  (HIFA-46, D-074).** `version` 9 → 10: §2.1.3 gains a fragment, §2.3.4 changes what a view-model key
  holds, and §2.5.7 is a new normative UI section; every one of them is a surface this layer publishes.
  (1) **The detail page rendered `history` from `issue_history` rows and printed a ULID pair.** §2.5.7
  states the sentence grammar per `field` (38 rows covering the **21** values SPEC-030 §2.4 enumerates
  with their writers), the three-part row, the four fallback rules, the layout, the states of the
  section and the density/a11y rules that keep a row one sentence on a 320 px screen. The wording lives
  **here** and nowhere else: SPEC-030 §2.4 quotes it beside each writer, SPEC-050 §2.5.3.3 prints it in a
  terminal.
  (2) **The formatter is `app/`'s and this layer owns layout only (P2/P4).** `history_rows`
  (SPEC-040 §2.2) answers the sentence, the three labels, the dot colour and the one-line summary; the
  templates iterate. §2.3.4's `history` element is therefore **that formatter's `HistoryRow`** — eleven
  keys, the raw pair among them because P7 keeps it for `data-*` — plus `created_label`, the one relative
  stamp the edge's clock produces. `history_panel` is the panel's slice and is layout; `history_window`,
  `history_hidden` and `last_activity` are `HistoryPage`'s (`window_size`, the flag this layer derives from
  it, and `last_activity`), because a template may not count, slice or compose them (§2.3.7).
  (3) **`Show all N` had no fragment to call, and no row in §2.1.4.** §2.1.3 gains
  `GET /_/issues/{identifier}/history?limit={n}` returning the whole `<ol>`, §2.1.4 the port call it makes
  — the panel's two `include` names and its `include_trashed`, so a trashed issue's expansion is `200` and
  R29 keeps its two trash-reading surfaces — and §2.2 the two partials that render it; the control expands
  in place, which is what D-C binds. The implementation PR adds the `issue.history` capability row
  (`cli="issue history"`, `web` this fragment: both edges, so no `debt=`/`reason=`) and the route's
  `FRAGMENT_ALLOWLIST` entry in the same commit — the checker fails until both exist.
  (4) **Order is oldest-first (D-D) and nothing collapses (D-B).** The panel's 8-row cut is a cut, not a
  summary: `history_panel` is `history[:8]` and the button shows the rest of **the window**. A
  `board_order` row that a move produced is not in the window at all — the read drops it (SPEC-030 §2.6) and
  the move's own sentence is the event — so this layer renders every row it is given and filters none.
  (5) **`Show all N` and the page claimed more than the read can reach.** With `history_total` = 12 the
  button read `Show all 12` and the page said it showed every row; at 120 rows the window is 100, so the
  button reads `Show all 100`, 20 rows are unreachable, and "nothing is hidden" was false. `N` is now
  `history_window` — the read's window, never a total — and a full window prints one dim line,
  `the last 100 events`, which is the only sentence on the section that admits a floor. §7 carries the
  120-row scenario that asserts it.
  (6) **Counts.** §7 goes **72 → 79** (seven scenarios: the sentences with no key, the panel's eight and
  `Show all 12`, the fragment, the empty section, the deleted-state fallback, the unknown field and actor,
  and the `<ol>`/`<li>`/`<time>` contract), and **79 → 80** for the 120-row window scenario; §8 says 80.
  The `.feature` corpus is untouched, no route of §2.1.1 changes, and `issue_vm` loses no key.

- spec_revision 29 (2026-11-20): **the detail page stops publishing a collection nothing can fill, and its
  `include` stops naming it (HIFA-49, round three).** `version` 8 → 9: `issue_vm` loses a key, §2.1.4's
  two detail rows pass a different `include` set, R16 is rewritten and REQ-S070-05 withdrawn, and §7 loses
  one scenario — the read model and the acceptance surface of this layer, which is the ground rev 28 stood
  on for 7 → 8.
  (1) **`attachments` left `issue_vm`.** The key was built from `IssueDetail.attachments`, which v1 no
  longer carries (SPEC-060 §2.4.2 is **37 keys**, four collections) and which nothing can fill:
  `add_attachment` is declared in SPEC-040 §2.2 and implemented nowhere (SPEC-050 §2.3.1's GAP-07). A
  template branch on an always-empty list is the payload D-071 §2 refuses, one layer down — the list was
  `[]` on every issue and the `<section class="attachments">` never rendered.
  (2) **The detail stops passing the whole constant.** §2.1.4 passed
  `include=app.boards.DETAIL_INCLUDE`, whose third name is `attachments`: that gate runs SQL-7 row 12 for a
  collection no envelope carries, and this layer drops the result. The two rows now pass
  `frozenset({"comments", "history"})` — **the names of `DETAIL_INCLUDE` whose collection `IssueDetail`
  carries**, the same set SPEC-060 §2.5.1's route publishes and SPEC-050 §2.5.3.2's flag maps, and the
  same rule, not a fourth formulation of it. `/_/issues/{id}/row` keeps the empty `include`.
  (3) **R16's rule had no subject and REQ-S070-05 had no test.** `attachments[].url` limited to `http`,
  `https`, `file` is a rule about a key this layer does not carry; both are withdrawn — R16 now says there
  is no attachment surface in v1 and names the revision that returns it, and REQ-S070-05 leaves §4 with
  the row that pointed at it. The prohibition row of §2.3.7 ("`{{ url }}` without a scheme check") and §7's
  scenario go with them: a scenario whose `Given` cannot be built is not a contract, it is a fixture
  nobody can write.
  (4) **§4's table stops claiming a table.** The detail's row listed `attachment` among the tables the
  page reads; with the key and the include name gone, nothing on that page reaches it.
  (5) **Counts.** §7 goes **73 → 72** (the attachment scenario leaves) and §8 says **72**: it said 67
  while §7 carried 73, a drift this revision had to touch because it moves the number anyway.
  (6) **What this demands of the code**, none of it written here: `src/hifa/web/readmodels.py`
  (`build_issue_vm` drops the `attachments` list and `_attachment_url`), `src/hifa/web/views.py` (the two
  detail routes pass the two names, not the constant) and `templates/issue_detail.html` (the
  `<section class="attachments">` block). Until that lands the tree and this document disagree about one
  key, and the document is the contract.

- spec_revision 28 (2026-11-20): **a hit carries no colour, and the one rule the palette inherits is
  named where it is decided (HIFA-7, second review round).** `version` 7 → 8, because §2.1.8's results
  row, §2.1.9 and §2.3.5 all change.
  (1) **`state_color` had no producer on this page's hit.** §2.3.5's `search_vm` required it while
  `SearchHit` (SPEC-090 §2.2) and `SearchRow` (§2.4 of that document) carry `state_name`/`state_type`
  only, and this spec declares no second read that could fetch a colour per hit — so the key asked for a
  field nothing returns. It is gone: the hit prints `state_name` with `data-state-type` for the
  stylesheet, and the bullet says why no colour exists here while `row_vm` keeps its own (the board and
  the list read the colour in the query that already joins `workflow_state`).
  (2) **R21 constrained a route §2.1.9 declares out of scope.** R21 said "both compose the `SearchQuery`
  … call `search_issues(db, query, now=…)`" while §2.1.9 said the palette "is a separate ticket — this
  revision contracts the page alone", and no parameter of that call was ever named here. R21 is now
  explicit that it is a rule for **every edge that composes a query**, that this revision ships one of
  them, and that the palette is one when it ships; §2.1.9 carries the matching sentence, so the two
  sections no longer exclude and constrain the same route in silence.
- spec_revision 27 (2026-11-20): **the review round of HIFA-7 closed four holes around the `/search` page
  and corrected the record of what the revision before it changed (HIFA-7).** `version` 6 → 7, because
  §2.1.4, §2.1.8, §2.2, §2.3.5, §6 and R21 all change:
  (1) **The `422` had no legal producer.** §2.3.5 said the error state's `message` was "never a sentence
  built here", §2.3.7 forbade the template to compose one, and the one builder of §6 —
  `refusal_sentence(refusal, *, verb, identifier)` — required an issue identifier that a query does not
  have. §6 now declares the builder's **two arms**: the write arm (a rule sentence, `verb` and
  `identifier` required, and a hard refusal if a rule arrives without an identifier) and the **request
  arm** — `refusal_sentence(refusal)` with both keywords omitted, legal only for a refusal about the
  request, returning `invalid value: ` plus SPEC-090 §6's message. `GET /search` is its one caller:
  the route catches `SearchError`, `build_search_vm` asks the builder, and the template prints `vm.message`
  as it always did. One owner, reachable by the template, and no branch composes text.
  (2) **The page composes its query and passes its instant.** R21 and §2.1.4/§2.1.8's use-case rows say
  what SPEC-090 §2.3 now says: `parse_query(q)` parses text, the route composes the `SearchQuery` with its
  `team` parameter, and `search_issues(db, query, now=…)` receives the instant this layer's clock read —
  the same `now` `updated_label` uses — because `app/` has no clock and a relative `due:` window needs one.
  (3) **The sidebar's two classes are declared.** §7 pinned `class="view-link is-active"` and no section of
  this document declared either name; the markup of `partials/sidebar.html` is `side-link` with
  `is-active` appended by the flag of §2.3.1, so §2.2 declares both, the scenario asserts the class the
  template really carries, and the paragraph says what does *not* mark the active view.
  (4) **The write guard is a `403` in this document too.** §2.1.1 and §2.1.8 listed the cross-site guard
  among "the `400`s of this layer" while R28 and §6 answer `403`; both sentences now say so, and the only
  `400` left is the `HX-Request` one of R5.
  §7 does not move — **73** scenarios before and after — and no route, state or read-model key is added
  or removed in this round.
- spec_revision 26 (2026-11-20): **the `/search` page is contracted, and the dead end it used to be is
  gone** (D-063). `version` 5 → 6: §2.1.8 and §2.1.9 are new, §2.3.5 gains four keys and every hit gains
  four, and one row of §6 changes its status. The route exists and the **template does not**: `web/app.py`
  declares `GET /search` and answers `400` (empty `q`) or `501` (`search is not implemented yet`), the tree
  has no `templates/search.html`, and `partials/empty_state.html` hardcodes the sentence `Nothing here yet`
  with a fixed `class="empty"`. What this revision contracts is the page those two facts become: its four
  states, its read model, its template and the two class names its sidebar link wears — not a page that
  was already there. (1) **the empty state is a state, not a refusal.** `/search`
  without `q` is `200` with the box focused and `search the workspace` in the result area — the sidebar
  links to it from every page, and the `400` of §2.1.1/§6 turned that link into an error page. The `400`
  is gone from this spec: a missing query is the empty state and a query SPEC-090 refuses is a `422` on
  the same template with the reader's query still in the box, which is the one answer that lets them fix
  it. (2) **The page has four states and the read model carries them**: `search_vm` gains `state`,
  `message`, `message_class` and `autofocus`, the four values `empty|results|no_results|error` with the
  copy declared once in the read model (R2) and the template comparing nothing — two truthiness
  branches and one class string — and each hit gains `state_type`, `priority`, `priority_glyph` and
  `updated_label` — the six
  fields `hifa search` prints, so the CLI table and the page show the same hit. (3) **The snippet is
  escaped exactly once and R17 says where.** SPEC-090 hands the read model the raw text wrapped in
  `<mark>`/`</mark>` (REQ-S090-03); `build_search_vm` splits on the pair, escapes each piece and rejoins,
  which is the only order that works when the markers already exist — "escape first, then mark" as rev 25
  wrote it cannot be done at this edge without re-finding the terms, and the raw-`|safe` variant is the
  XSS. A scenario pins both halves. (4) **320 px and the keyboard path are declared**: a `<ul>` of three
  -line blocks instead of the CLI's six-column table (which pans at 320 px), and a page that is operable
  with `Tab`, `Enter`, `/` and `Esc` and needs no JS at all. (5) `page` gains the three derived flags
  `is_board`/`is_list`/`is_search` that `base.html` marks the view links with and that §2.3.1 did not
  declare (R3), and `nav` gains the `"issue"` value the detail page already passes. (6) §2.1.4 and R21
  call `parse_query` + `search_issues` instead of the `SearchService` class (D-064), and §3 names the
  tables of SPEC-090 §3.2 that the search reads instead of `v_issue`. **The palette is untouched**:
  `/_/palette` keeps its row, its fragment and R19 as they were, and this revision contracts the page
  alone — no key, class or route of the palette moved.
- spec_revision 25 (2026-11-17): **the comment composer exists as markup, and its two fragments have
  two shapes** (D-060). §2.6.10 is new: the box, on both templates that render `issue_vm`, gated on
  `can_write` and neither layer, outside the action group of §2.6.9 because it belongs to the comments
  under it rather than to the footer. §2.1.3's two comment rows were the defect this round was opened
  for — both named `#comments`, one swapping an `<ol>` in by `innerHTML` and the other appending an
  `<li>` by `beforeend`, which is invalid whichever way the browser is told to do it. Now `#comments` is
  the `<section>` and `#comment-list` the `<ol>`, both fragments target the list — the `GET` swapping it
  `outerHTML` so the heading and the composer beside it survive a refresh — the `POST` answers one item
  with `200`, and the no-JS `303` lands on the section. §6 gains the depth refusal the new rule raises
  and names the comment write in the two layer rows, and the `HX-Trigger` row of §2.1.3 finally lists
  `comment-created`, the event R12 already counted and the table did not name.

- spec_revision 24 (2026-11-17): **the refusals say where their sentences are built, the confirmation says
  whose it is, and the `204` keeps the header it already had.** Three description gaps closed before the
  write routes were implemented; none of them is a trade-off between two contracts, so no `D-0NN` was opened
  and `DECISIONS.md` is untouched. (1) **The sentences existed only in this spec (§6).** §6 contracted
  `ENG-13 is archived; restore it first`, `ENG-13 is in the trash; restore it first` and `cannot archive
  ENG-13: it has 2 open sub-issues`, and nothing in the tree composed them: the domain and the use cases
  raise `Validation(ISSUE_TRASHED)`, `InvalidTransition(ISSUE_ARCHIVED)` and
  `Validation(CLOSE_WITH_OPEN_CHILDREN)` with their own `message` (`issue is in the trash`, `issue is
  archived`, `cannot close an issue with open sub-issues`), the handler that catches them prints
  `invalid value: ` plus that message, and no branch anywhere turned a rule id into one of the three
  sentences. §6 now declares the one builder — `refusal_sentence(refusal, *, verb, identifier)` in
  `src/hifa/web/app.py` — and the one source of each part of a sentence: the rule id from `refusal.rule`,
  the subject from the `identifier` of the row the route resolved (**never** the path segment, which may be
  a ULID, and **never** `refusal.value`, which is the identifier for `ISSUE_ARCHIVED`, the issue's ULID for
  `ISSUE_TRASHED` and the target state's type for `CLOSE_WITH_OPEN_CHILDREN` — the trap the paragraph
  exists for), the word from the route's own verb (the only difference between the catalogue's two
  `CLOSE_WITH_OPEN_CHILDREN` sentences), and the count from `details['open_children']`, in one form for
  every count. §2.3.7 forbids a template to compose one, `invalid value: ` is the same function's fallback,
  and a `CLOSE_WITH_OPEN_CHILDREN` from a field write (`update_issue`'s `state`) falls through to that
  fallback instead of borrowing a verb it did not perform. (2) **The confirmation had no home (§2.1.2,
  §5.7).** It lived in the CLI edge as `CliError(code="confirmation_required")` and this spec asserted only
  that the two forms carry `confirm=1`. §2.1.2 now says which kind of thing it is — a **form-level refusal
  of the edge**: the body is read before any use case, no transaction opens, nothing is written, and the
  `422` is the route's answer, not an exception — which is why `ArchiveIssue`/`TrashIssue` carry no
  `confirm` (SPEC-040 §2.2 and its `spec_revision` are untouched) and why `confirmation_required` names a
  form contract and no `Validation`. The other choice was priced and rejected: the guard in `app/` would
  move a UI affordance into SPEC-040 §2.2 and bump `version` in both specs, give the CLI a second gate
  beside the prompt it already asks (SPEC-050 R6), and turn that edge's documented refusal (`hifa: operation
  canceled`, exit `2`) into the `validation_error` a `Validation` prints. The price this revision accepts
  is named in the same paragraph: the destructive set is declared once per edge, kept in step by P1/P2 and
  the CLI's verb contract, and a third destructive verb would need both tables rather than one command.
  (3) **`HX-Revision` on a `204` (§2.1.3).** The header row already declared it — the four bodyless writes
  carry it, and a `204` renders no element for the number to land on, so the collection re-read afterwards
  is what reports the number the page keeps (§5.7 steps 5-7, D-050) — so no clause was added. The drift was
  in its first words: "the revision the response was rendered at" describes an answer with a body, and the
  middleware sets the header on `200`/`201` only today. The cell now reads "the number the request
  observed — for a write, the number read after it committed", which is what `READ_METHODS` and
  `_install_security_headers` already implement and what §5.7 step 5 and §2.6.8 already said; the `204` arm
  of that middleware is the implementation's, and no spec sentence stood in its way. **`version` stays 5**:
  no endpoint, body, status, header or command moved — the three changes name where an existing string and
  an existing precondition live — and the page that carried `confirm=1` before carries it now.
- spec_revision 23 (2026-11-17): **the dashboard serves a trashed issue, and `move` says where a bad state
  goes (D-059).** Five changes, one decision. (1) **The read surface (§2.1.1).**
  `GET /{team}/issue/{identifier}` and `GET /_/issues/{identifier}/panel` answer `200` for an issue the trash
  layer holds, rendering the issue with `Restore` as the only control — no edit form, no `state_id` control, no
  archive and no delete — and the `{ulid}` alias answers `303` to that same page. Every other surface keeps the
  trash out: board, list, search, palette, counters and the `row`/`card` fragments never return a trashed row,
  so those two fragments answer `404` for exactly the identifier whose page answers `200`. The asymmetry is
  declared instead of implied: the page and the panel pass `issue_detail(..., include_trashed=True)` (SPEC-040
  §2.2, R21, `spec_revision` 24 of the same decision) and nothing else does. (2) **The `Restore` gate
  (§2.6.9).** The templates that exist gate the action group on
  `{% if vm.can_write and not vm.is_archived and not vm.is_trashed %}`, so a `200` page for a
  trashed issue would have shipped with **no control at all** — the sentence this section already had would
  have been markup no route could produce, one layer lower than before. §2.6.9 now states the gate **once**:
  `vm.can_write` gates the action group and the layer picks the branch inside it — an issue in either layer
  fills the group with `Restore` alone, a visible one fills it with the four, because a refusal that prints
  `ENG-13 is in the trash; restore it first` needs a page with something to press and a visible issue needs
  the four writes it accepts. Corrected inside this same revision, with no bump: the first wording of that
  paragraph put the layer on the container and its negation on the four children, two disjoint conditions
  under which a **visible** issue rendered no control at all — the mirror of the bug the paragraph was
  written to close, and the reason the `.feature` now asserts the four controls on a visible page.
  (3) **The `303` reason, split (§2.1.2).** The paragraph said archive and delete both
  answer `303` to the list because "a redirect back to the detail would be a redirect into a `404`". The shared
  reason is right — both left every listing — but the `404` half was true of `delete` and of nothing else (an
  archived detail has always answered `200`), and D-059 retires it for the trash too. The three sentences now
  say what each case answers and where its `Restore` lives, and keep the list as the target for the reason that
  survives. (4) **`move` with an unresolvable `state_id` (§6).** A `state_id` that resolves to no state in the
  team answers the **same** `404` `text/plain` `workflow state does not exist` that the column drop already
  declared, mirrored rather than invented, and the message catalogue says the one sentence belongs to both
  routes. §2.1.2's `422` is named as a different row: a body that failed to parse or validate, not a reference
  that did not resolve. (5) **`move`'s single answer for `409`/`422` (§5.4, §6).** §6 said "no body" for both
  while §2.1.3 said the same refusals answer the element re-rendered. §5.4 now closes with the clause that
  settles it: the route **always** answers the panel plus `form_error`, and the board drop ignores that body and
  reloads the collection. **`version` moves 4 → 5 and not only `spec_revision`,** because a **successful
  status** of two declared routes changes (`404` → `200` for the detail and the panel of a trashed issue):
  `specs/README.md`:37 is exactly that, and rev 21 argued the other way — `version` stays — only because it
  added a refusal that changed no successful answer. §2.1.4's three detail rows carry the keyword with it, so
  the mapping an implementer copies and §2.1.1 do not disagree. The rest of D-059's surface landed in the
  same revision, and this is the whole list: **R29** in §4, the new **§5.7.1**, **§2.3.4**'s read-model
  note, **§2.6.4 and §5.7**'s one-flow `move` wording, the **seven** write page forms of §2.1.2 (§2.5
  counted five), the four writes' **`HX-Revision` on the bodyless `204`** beside `HX-Trigger` (§2.1.3,
  §2.6.8, §5.7 step 5), the **delete confirmation block's three strings** and its `422` sentence
  (§2.6.9), the **`Restore` control's markup** — a bare `class="action-restore"` form whose single
  `<button class="btn">` reads `Restore`, with no `<details>` and no `confirm` (§2.6.9) — **§2.6.3**'s
  panel copy of the inline-edit form, which keeps `method="post"` and
  `action="/{team}/issue/{identifier}"` beside its `hx-patch` to `?view=panel`, **§2.6.6**'s `Move
  state` row, whose "with JS" cell now ends at §5.7.1, **§2.6.9**'s focus-table row for a `move` from
  the panel footer, and **two new §6 rows** — the `delete` no-op beside a narrowed `ISSUE_TRASHED` row
  (`edit`, `move`, a label or `assignee` write, `archive`; never `delete`), and the
  `/_/states/{id}/position` read-only refusal named as the exception to the fragment-plus-catalogue
  answer. Completing it moved neither header: `spec_revision` remains **23** and `version` remains
  **5**, because none adds a route, a verb or a field and none changes a successful status — R29 and
  §5.7.1 write down behaviour §2.1.1 and §2.6.4 already implied, and the §6 rows make the table agree
  with §5.7. §7's inline copy of the trash block's twelve scenarios landed in the same revision, so both
  halves of the acceptance criteria — `specs/acceptance/001-mvp-v1/16-dashboard.feature` and §7 — say
  the same thing, and nothing of D-059 is owed to a later round.
- spec_revision 22 (2026-11-16): **the board a new team gets is seven columns (D-058).** The template
  gains one shared `In Review` between `In Progress` and `Done` (SPEC-100 §2.5), so §7's two column
  assertions move from six to seven and list the new name in place; nothing of this layer's contract
  moves with them. The columns were already derived from `workflow_state.position` (§2.3.2, R25), so
  the board renders the new column with no template, read-model or fragment change, and the drag of
  §2.6.5 reorders it like any other. A team seeded before the decision gets it through that same drag or
  through `hifa state update` (SPEC-050 §2.5.6), never through a migration (D-058).
- spec_revision 21 (2026-11-16): **the write guard — a state-changing request from another origin is
  refused before the route runs, and a request carrying no origin information is allowed (D-057).**
  The layer could be told no in two ways and neither was about the caller: R18's `403` is the *process*
  being read-only, and R5's `400` is the *client* not being htmx — while the page forms of §2.1.2 take a
  plain urlencoded `POST` with no header from any origin. Over a cloudflared quick tunnel that means a
  page the reader visits can write, and after T-20.1 can archive or delete. §2.1.7 declares one HTTP
  middleware installed by `create_app` inside `_headers`, keyed on the method, **returning** `403
  cross-site write refused` when `Sec-Fetch-Site` is `cross-site`/`same-site`, or when it is absent and
  `Origin` names another authority or is `null`; `same-origin`, `none`, an `Origin` whose authority is
  `Host`'s, and a request with neither header are served, and the authority is compared with the scheme
  dropped so the tunnel's `https` browser over a plain `http` socket still writes. R28 states the rule,
  and §6 gains the refusal as its own row and its own exact text, next to the read-only `403` it must not
  be confused with. The refusal is returned and not raised: a handler of `@app.exception_handler` sits
  inside the user middleware stack and would never see it, so there is no exception type and no handler —
  the edge's only such pair remains `FragmentRequiresHtmx`. It answers before the route, before the
  `HX-Request` dependency and before the read-only `403`, so a cross-site write to an unknown identifier
  is a refusal and not a `404`. No route, verb, field or successful status changes: `version` stays 4 and
  only `spec_revision` moves (20 → 21). The rule's own limits are in §2.1.7: no token, no session, no TLS,
  a same-origin page can write, the tunnel URL stays a capability, and the API edge of SPEC-060 is out.
- spec_revision 20 (2026-11-14): **the inline edit declares the body the browser really sends —
  form-urlencoded, no JSON, and the hidden form inside the cell (D-056).**
  T-20.1 implemented §2.6.3 and the first run that reached the wire proved the prescription wrong:
  §2.6.3 said the `PATCH` body was JSON, carried a JSON request shim in `app.js` and put the
  hidden `<form>` outside the `<td>` with `hx-include="closest form"`. Three failures, all verifiable.
  (1) No fragment route parses JSON: every `/_/` write goes through `_parse_form` (urlencoded), and
  the only JSON body under `/_/` is the revision probe of §2.1.3.1 (D-049), so a JSON body is
  `422 invalid request`. (2) The shim cannot exist: the event it hooks is not cancelable, so htmx
  has already encoded the body as form data when the listener runs and a header swapped to
  `application/json` over that body is a request that is neither — which is the `422` a browser
  produced before the override was deleted. (3) A `<form>` as a child of `<tr>` is foster-parented
  out of the table, so the declared position cannot work; the form lives inside the `<td>`, is
  `hidden`, and the control reaches it by `id` (`form="edit-{identifier}"`,
  `hx-include="#edit-{identifier}"`). §2.6.3 is corrected in its three places — the exchange block,
  the markup, and the rule under it — `Enter` is declared as the implicit submission of the form
  owner instead of a second `hx-trigger`, and §5.3 step 1 stops describing the payload as an object.
  **Nothing in `src/` was changed to fit this revision**: the working behaviour is what the section
  now describes, and the browser probe that proved it is named in D-056.
- spec_revision 19 (2026-11-13): **the dashboard's write paths are contracted — five capabilities,
  seven page forms, five fragments — and `version` 4 because §2 gains routes (T-20, D-053, D-054,
  D-055).**
  The parity checker of T-18 names five capabilities the CLI has and the dashboard did not
  (`issue.edit`, `issue.move`, `issue.archive`, `issue.delete`, `issue.restore`). Reading §2.1 against
  `src/hifa/web/app.py` showed that two of them had **no declared route at all** and that the three
  that had one were declared in a shape this spec could not defend: §2.1.2 carried `POST …/archive`
  with an `undo?` field and no `delete` and no `restore`, i.e. a capability reachable only through the
  body of another capability's route — uncountable by the map, unmappable by the checker — while
  `delete` and `restore` had no route at all, so their page forms had no redirect target to name.
  (1) §2.1.2 gains `…/delete` and `…/restore`,
  loses `undo?`, and states the redirect per route: the list for the two writes that take the issue out
  of every listing, the detail for the three that leave it where the reader was. (2) `archive` and
  `delete` require `confirm=1` — the web form of the CLI's `--yes` (P6), refused with `422`
  `confirmation_required` — and `restore` never asks, because undoing is not destructive (D-053).
  (3) §2.1.3 gains the three `204` fragments, keeps them bodyless so that no client decides what the
  collection contains, declares `?view=panel` as the answer the description editor needs — and states
  the swap per `view` (`outerHTML` for a row or a card, `innerHTML` for the panel) in the row itself,
  which is the public contract, not only in the prose under it; §5.7 is
  the flow, step by step, with the do-not-interrupt behaviour of §2.6.8 spelled out and with the two
  no-ops the corpus already declares (an archive of an already-archived issue, a restore of a visible
  one) kept as no-ops instead of being turned into `409`s. (4) §2.6.9 is the
  interface behaviour P3 demands: the two places the actions live (panel and detail, never a row and
  never a card), the
  tab order — no hover-only control and no single-letter shortcut that trashes an issue — the focus
  table, the refusal as text next to the field with the server's value (R7), the confirmation as a
  `<details>` block with the form inside it instead of a modal or a `<dialog>` — script-free,
  route-free and field-free (D-055) — and the 320 px layout of the action group. (5) §6 gains five
  refusals and
  three messages, §3 gains the `trash`/`restore` writes, and R27 states the rule once for every write:
  identifiers on the wire, `confirm=1` before a layer write, the page's own element as the answer.
  §2.1.2 also fixes the two names that could be "corrected" into one: `state` is a *reference* (edit)
  and `state_id` is a *column* (move and drop), and neither is a position — the move form sends
  `state_id`, like the drop payload, because a `<select name="state">` in a move form was a third
  wire name for the same column. (6) `acceptance:` pointed at
  `specs/acceptance/070-web-dashboard.feature`, a file that does not exist; it now points at
  `16-dashboard.feature`, where the twenty-two scenarios of the write paths landed. §10's mobile bullet
  stops contradicting this revision: the write actions declare their 320 px behaviour here and the
  whole-page floor stays T-19. **Nothing in `src/hifa/web/` was changed to fit this revision** — the
  five routes and seven of their forms are unimplemented and stay so until T-20.1 ships them; the
  `Version` bump follows `specs/README.md` (a route is a public contract) and the precedent of rev 14.
- spec_revision 18 (2026-11-13): **the client adopts only the collection, and the bound is named.**
  Three things the previous revision described in prose or not at all, stated where the contract is
  read. (1) The `Adopt` row of §2.6.8 still said the poller stored the `HX-Revision` of *every*
  answer; only an answer whose target is `#board` or `#issues-tbody` adopts it, and a panel, a row,
  the palette or the counters adopt nothing — they answer with the number the server observed,
  foreign commits included, and none of them updated the board, so believing theirs spends the
  difference between the page and the database without updating the page and leaves a stale board
  behind a poller that has gone quiet (D-050). R26 and §5.6 step 8 carry the same rule. (2) "The wait
  is bounded" named no bound and no mechanism: §2.6.8 and §5.6 now name `RELOAD_MS` (10 s) in
  `src/hifa/web/static/js/app.js`, and state why clearing the client's own flag is not enough — htmx
  keys the request in flight on the element that acts (`document.body`, because the reload calls
  `htmx.ajax` without a `source`), queues the next call while that slot is taken and resolves its
  promise without sending anything, so the stale request is aborted on the body with `htmx:abort`
  before the reload is asked again through the same single path. A browser measured the failure: after
  one hung reload every later tick resolved in the same millisecond with no XHR sent. (3) §2.4 said
  `gzip -9` in the column header and nowhere that the two own-file ceilings are enforced at that
  compression level; they are, by `test_asset_budget_is_under_75_kb_gzip`, at **≤ 20 KB for
  `app.css` and ≤ 12 KB for `app.js`**, and the vendored `sortable.min.js` row declared 10 KB where
  1.15.2 measures 14.4 KB gzip — corrected to 15 KB. The **≤ 75 KB** total does not move.
- spec_revision 17 (2026-11-13): **the client contract is the one D-050 records.** The panel became a
  blocking state like the others and stopped being re-rendered under the reader: the revision is one
  global number, so *"re-render the panel only if its issue changed"* is not a condition that can be
  evaluated, and replacing the markup of a panel a person is reading costs more than waiting for them
  to close it. The same revision said that **only an answer which replaced the collection adopts its
  number** — in the §2.6.8 prose and in §5.6, because the panel, a row, the palette and the counters
  answer with the number the server observed and none of them updated the board. It added the two
  paragraphs that are not about the panel as well: the reload carries the values typed in the filter
  bar, since the page's query is not what the reader is looking at; a request that never answers is
  given up on and asked again, so one hung reload cannot silence the page; and the collection
  fragment stops shipping the quick-add form out of band, which replaced the form under the cursor on
  every automatic reload. §5.6 steps 6 and 8 followed so the loop stops re-rendering an open, clean
  panel and stops spending the pending value on it. The `Adopt` row of the table was left saying the
  poller stores the number of every answer, which is what rev 18 corrects.

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
  happens in an explicit user action (D-029). *(Superseded at `spec_revision` 29: the panel passes the
  two names of that constant `IssueDetail` carries — §2.1.4 — because the constant's third name is
  `attachments`, which no v1 envelope carries.)* (2) R3's ban on defensive `default()` is now checked:
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
  now uses `SearchService` from SPEC-090, which is the authority for the search contract (D-016). (Rev 26
  re-settles this: SPEC-040 §2.2 does declare `search_issues`, so R21 calls `parse_query` +
  `search_issues` and the class stays inside `app/search.py` — D-064.)
- **Pending for another round:** the board does not paginate (`limit_per_group` +
  `truncated`/`more_url`); enabling infinite scroll per column requires a new decision, because
  D-011 only allows `offset` in the list.
