---
id: SPEC-060
title: REST API — endpoints, schemas, pagination and HTTP errors
component: entry
status: review
version: 8
spec_revision: 15
depends_on: [SPEC-000, SPEC-010, SPEC-040]
implements:
  - src/hifa/api/**
  - tests/e2e/api/**
acceptance: specs/acceptance/060-api-rest.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-20
---

# SPEC-060 — REST API

## 1. Responsibility and boundaries

**Does:** exposes the `hifa` model as HTTP+JSON over FastAPI: it validates payloads with Pydantic v2,
serializes entities, paginates with keyset, maps domain exceptions to HTTP statuses, and publishes a
stable `/openapi.json`. It is the edge for **agents and scripts**, and the substrate that `hifa serve`
mounts next to the dashboard.

**Does NOT do (explicit):**
- **The dashboard does not use these endpoints from the browser.** The views of SPEC-070 are rendered
  server-side and updated with HTML fragments; the UI's JS never calls `/api/v1/*`.
  There is no CORS, there are no "for the front end" endpoints, there is no JSON consumed by htmx.
- It holds no business logic: every handler validates shape, calls a use case and serializes.
  Computing `number`, `identifier`, `board_order` or validating transitions here is forbidden.
- It opens no SQLite connections on its own: it uses the injected `UnitOfWork`.
- It neither authenticates nor authorizes (REQ-S005): there are no tokens, sessions, API keys or rate limiting. The
  mitigation is the loopback bind of `hifa serve`.
- It serves no product HTML, no static files, no `/docs` and no `/redoc` (see R7: they load assets from a CDN).
- It does not render markdown: `description` travels as raw text; sanitized HTML belongs to SPEC-070.
- It implements no GraphQL, batch, webhooks, SSE or long-polling.

## 2. Public contract

### 2.1 App factory and mounting

```python
# src/hifa/api/app.py
from fastapi import FastAPI


def create_app(*, db_path: str, read_only: bool = False, mount_web: bool = True) -> FastAPI: ...
```

```python
app = FastAPI(
    title="hifa",
    version=__version__,
    openapi_url="/openapi.json",
    docs_url=None,  # Swagger UI carga JS desde CDN: prohibido por REQ-N005
    redoc_url=None,  # idem
    default_response_class=JSONResponse,
)
```

Registration: `app.include_router(issues.router, prefix="/api/v1")` and the analogous calls for `teams`,
`labels`, `projects`, `cycles`, `members`, `search`; `health` without a prefix. With
`mount_web=True` (the default of `hifa serve`), after registering the API, `hifa.web.install(app)` is
called (SPEC-070 §2.1: pages, `/_/*` fragments and `StaticFiles` at `/static`).
The order is fixed: **first** `/api/v1` + `/health` + `/openapi.json`, **then** the web router;
a web route can never shadow an API one. `create_app(mount_web=False)` is what the tests
of this spec use and returns an app with exactly the routes of §2.5 (R2).

`hifa.api` may **only** import `hifa.app.*`, `hifa.domain.*`, `hifa.config`,
`hifa.adapters.sqlite.uow`, `fastapi`, `pydantic`, stdlib. Importing `hifa.cli.*` and
`hifa.web.*` is forbidden (A1). No handler imports `sqlite3`.

### 2.2 Conventions

| # | Convention |
|---|---|
| C1 | Resource prefix: `/api/v1`. Changing the shape of a payload ⇒ `/api/v2`, not a flag. |
| C2 | All handlers are declared `def`, **never** `async def` (the core is synchronous and uses SQLite). FastAPI runs them in the AnyIO threadpool. |
| C3 | Path `REF` = `identifier` (`ENG-123`, case-insensitive) **or** a 26-char ULID. It is resolved in that order. |
| C4 | The API is **id-first**: it accepts ULIDs in `label_ids`, `project`, `cycle`, `assignee`. Accepting names is the CLI's prerogative (SPEC-050), not the API's. `state` accepts an id or a name because the name is unique per team. **Single exception:** the `/issues/{ref}/labels` subresource (§2.5.1) accepts an id **or** a name, because `add_label`/`remove_label` (SPEC-040) resolve both. A label name is **not** unique per team in the sense this sentence used to claim: the indexes compare binary, so identical spellings at one level are possible and are **refused** by SPEC-040 R22 (`LABEL_AMBIGUOUS`, 422) instead of being picked; the name resolves case-insensitively in the issue's team and then in the workspace. |
| C5 | Request `Content-Type: application/json` (or no body). Any other type ⇒ 415. Body > 1 MiB ⇒ 413. |
| C6 | Input models are `extra="forbid"`: an unknown field ⇒ 422 with `field` = the field name. |
| C7 | Response: UTF-8 JSON, no `{"data": ...}` wrapper. Lists use the envelope of §2.6. |
| C8 | Fields are serialized **all** of them, in the order declared in the schema; explicit `null`, never omission. |
| C9 | `description` and `body` travel as raw markdown in a string; the API never returns HTML. |
| C10 | A `POST` that creates returns `201` + a `Location` header with the resource path. |
| C11 | `read_only=True`: any method other than `GET`/`HEAD` ⇒ 403 `forbidden`. |
| C12 | No CORS, no cookies, no `Set-Cookie`. A client other than loopback is not a supported case. |

### 2.3 UnitOfWork injection

```python
# src/hifa/api/deps.py
def get_uow(request: Request) -> Iterator[UnitOfWork]:
    conn = connect(request.app.state.db_path, readonly=request.app.state.read_only)
    try:
        yield UnitOfWork(conn, clock=clock, idgen=idgen)
    finally:
        conn.close()
```

`connect` and `UnitOfWork` are those of SPEC-030 §2.1/§2.2 (exact names: `readonly=`, not
`read_only`; `clock`/`idgen` are keyword-only). The process's `Clock` and `IdGen` are built
once in `create_app` and stored in `app.state`.

Threading rule (mandatory, it is the classic bug of this stack): since C2 mandates `def` handlers,
FastAPI may run the dependency and the handler in **different threads** of the same threadpool.
That is why `open_connection` opens with `check_same_thread=False` and one connection **per request**,
closed in the `finally`. Sharing a global connection between requests is forbidden: SQLite serializes
writes and a shared connection turns contention into `database is locked`.

### 2.4 Pydantic v2 schemas

**`model_config = ConfigDict(from_attributes=True)` is for the models that validate a use case's
dataclass**, i.e. the ones a handler builds with `Model.model_validate(<frozen dataclass>)` out of
`app/` or `adapters/`: `IssueOut`, `TeamOut`, `StateOut`, `LabelOut`, `ProjectOut`, `CycleOut`,
`MemberOut`, `CommentOut`, `RelationOut`, `AttachmentOut`, `SearchHit` and `SearchPage`. **Every input
model** (the `*Create`/`*Patch`/`*Move`/`*Add` set) uses `ConfigDict(extra="forbid")`. No loose `dict`s:
the shape of the JSON is the schema.

A model that is **composed here and not validated from a dataclass** carries neither config, and that is
deliberate rather than an omission: the nested references of §2.4.1 (`TeamRef`, `StateRef`, `MemberRef`,
`ProjectRef`, `MilestoneRef`, `CycleRef`, `ParentRef`, `LabelRef`), the envelopes of §2.6 (`IssuePage`,
`TeamList`, the eight bounded lists and their `items`), the aggregates computed in the handler
(`HistoryEntry`, `TeamProgress`), and `HealthOut` are built field by field, so `from_attributes` would
declare a coercion path nothing uses. **The error bodies carry no config either** — `ErrorBody`,
`ErrorEnvelope` — and on purpose: they are built from a `DomainError`, whose `code`/`message`/`field`
are copied into the schema by `api/errors.py`, so they must not accept an arbitrary object as their
source. Adding `from_attributes` there would make any exception with those attribute names
envelope-shaped, which is the one place of this layer where that freedom is a hazard.

```python
# src/hifa/api/schemas.py
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Priority = Literal[0, 1, 2, 3, 4]
StateType = Literal["triage", "backlog", "unstarted", "started", "completed", "canceled"]
Visibility = Literal["public", "private", "restricted"]
EstimationType = Literal["notUsed", "exponential", "fibonacci", "linear", "tShirt"]
ProjectStatus = Literal["backlog", "planned", "started", "paused", "completed", "canceled"]
MilestoneStatus = Literal["unstarted", "next", "overdue", "done"]
RelationType = Literal["blocks", "duplicate", "related", "similar"]
GroupType = Literal["multiSelect", "singleSelect"]
ArchivedFilter = Literal["exclude", "include", "only"]
OrderField = Literal[
    "created_at", "updated_at", "priority", "due_date", "sort_order", "board_order", "number"
]
Direction = Literal["asc", "desc"]
```

#### 2.4.1 Nested references

```python
class TeamRef(BaseModel):
    id: str
    key: str
    name: str
    icon: str | None


class StateRef(BaseModel):
    id: str
    name: str
    type: StateType
    color: str


class MemberRef(BaseModel):
    id: str
    name: str
    display_name: str
    initials: str | None


class ProjectRef(BaseModel):
    id: str
    name: str


class MilestoneRef(BaseModel):
    id: str
    name: str


class CycleRef(BaseModel):
    id: str
    number: int
    name: str | None


class ParentRef(BaseModel):
    id: str
    identifier: str
    title: str


class LabelRef(BaseModel):
    id: str
    name: str
    color: str
```

#### 2.4.2 `IssueOut` (33 fields, same order as SPEC-050 §2.4.2)

```python
class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    identifier: str
    number: int
    team: TeamRef
    title: str
    description: str | None
    priority: Priority
    priority_label: str
    estimate: float | None
    due_date: str | None  # 'YYYY-MM-DD'
    state: StateRef
    assignee: MemberRef | None
    creator: MemberRef | None
    project: ProjectRef | None
    milestone: MilestoneRef | None
    cycle: CycleRef | None
    parent: ParentRef | None
    labels: list[LabelRef]  # ordenado por name ASC
    sort_order: float
    board_order: float
    sub_issue_sort_order: float
    started_at: str | None
    completed_at: str | None
    canceled_at: str | None
    snoozed_until_at: str | None
    archived_at: str | None
    auto_archived_at: str | None
    trashed: bool
    branch_name: str | None
    is_open: bool
    is_visible: bool
    created_at: str
    updated_at: str


class HistoryEntry(BaseModel):
    id: int
    issue_id: str
    actor_id: str | None
    field: str
    old_value: str | None
    new_value: str | None
    created_at: str


class IssueDetail(IssueOut):
    comments: list["CommentOut"] = []
    relations: list["RelationOut"] = []
    attachments: list["AttachmentOut"] = []
    history: list[HistoryEntry] = []
    children: list[ParentRef] = []
```

`IssueDetail` adds keys **at the end**; it never reorders the 33 of `IssueOut`.

#### 2.4.3 Issue inputs

```python
class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str | None = None  # id o key; None ⇒ team por defecto del server
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    priority: Priority = 0
    state: str | None = None  # id o name
    assignee: str | None = None  # member id
    label_ids: list[str] = []
    project: str | None = None
    cycle: str | None = None
    estimate: float | None = Field(default=None, ge=0)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    parent: str | None = None
    actor: str | None = None


class IssuePatch(BaseModel):
    """PATCH parcial: campo ausente = sin cambio; campo null = borrar (si es nullable)."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: Priority | None = None
    estimate: float | None = Field(default=None, ge=0)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    assignee: str | None = None
    project: str | None = None
    milestone: str | None = None
    cycle: str | None = None
    parent: str | None = None
    branch_name: str | None = None
    snoozed_until_at: str | None = None
    actor: str | None = None


class IssueMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    after: str | None = None
    before: str | None = None
    sort_order: float | None = None

    @model_validator(mode="after")
    def _one_anchor(self) -> "IssueMove":
        if self.after is not None and self.before is not None:
            raise ValueError("after y before son mutuamente excluyentes")
        return self


class LabelAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str  # ULID o nombre; una sola etiqueta por llamada (SPEC-040 AddLabel)
```

Fields **not** nullable in a patch (§R5): `title`, `priority`. Sending `null` for them ⇒ 422 with
`field` = the field name, because the database `CHECK` forbids it
(`title NOT NULL CHECK(length(trim(title)) > 0)`, `priority NOT NULL`).

#### 2.4.4 Remaining schemas

```python
class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    name: str
    icon: str | None
    color: str | None
    description: str | None
    timezone: str
    visibility: Visibility
    parent_id: str | None
    cycles_enabled: bool
    cycle_duration: int
    cycle_start_day: int
    issue_estimation_type: EstimationType
    default_issue_estimate: float | None
    triage_enabled: bool
    auto_close_period: int | None
    archived_at: str | None
    created_at: str
    updated_at: str


class TeamProgress(BaseModel):
    open_count: int
    closed_count: int
    urgent_open: int


class TeamDetail(TeamOut):
    progress: TeamProgress
    states: list["StateOut"]


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # normalized (strip + upper) before the pattern check: SPEC-020 §2.2 owns KEY_PATTERN (D-030)
    key: str = Field(min_length=2, max_length=10, pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(min_length=1)
    icon: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = None
    timezone: str = "America/Santiago"
    visibility: Visibility = "public"


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    icon: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = None
    timezone: str | None = None
    visibility: Visibility | None = None
    cycles_enabled: bool | None = None
    cycle_duration: int | None = Field(default=None, ge=1, le=8)
    cycle_start_day: int | None = Field(default=None, ge=0, le=6)
    issue_estimation_type: EstimationType | None = None
    default_issue_estimate: float | None = None
    triage_enabled: bool | None = None
    auto_close_period: int | None = None


class StateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    type: StateType
    color: str
    position: float
    is_default: bool
    archived_at: str | None


class StateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    type: StateType
    color: str = Field(default="#6B6F76", pattern=r"^#[0-9A-Fa-f]{6}$")
    position: float | None = None  # None ⇒ max(position)+1
    is_default: bool = False


class StatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    position: float | None = None
    is_default: bool | None = None
    archived: bool | None = None  # True ⇒ archived_at = now, False ⇒ NULL


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    color: str
    description: str | None
    team_id: str | None
    parent_id: str | None
    is_group: bool
    group_type: GroupType | None
    retired_at: str | None


class LabelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    color: str = Field(default="#6B6F76", pattern=r"^#[0-9A-Fa-f]{6}$")
    team: str | None = None
    description: str | None = None
    parent: str | None = None
    is_group: bool = False
    group_type: GroupType | None = None


class LabelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    icon: str | None
    color: str | None
    team_id: str | None
    status_type: ProjectStatus
    priority: Priority
    lead_id: str | None
    start_date: str | None
    target_date: str | None
    sort_order: float
    archived_at: str | None
    created_at: str
    updated_at: str


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str | None = None
    team: str | None = None
    icon: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    status_type: ProjectStatus = "planned"
    priority: Priority = 0
    lead: str | None = None
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    target_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    status_type: ProjectStatus | None = None
    priority: Priority | None = None
    lead: str | None = None
    start_date: str | None = None
    target_date: str | None = None
    sort_order: float | None = None


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: int
    name: str | None
    starts_at: str
    ends_at: str
    completed_at: str | None
    team_id: str


class CycleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str | None = None
    starts_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    ends_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    display_name: str
    email: str | None
    initials: str | None
    avatar_url: str | None
    timezone: str | None
    is_admin: bool
    is_active: bool
    created_at: str


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    display_name: str | None = None  # None ⇒ slug(name)
    email: str | None = None
    initials: str | None = None
    timezone: str | None = None
    is_admin: bool = False


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    email: str | None = None
    initials: str | None = None
    timezone: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    issue_id: str
    parent_id: str | None
    author_id: str | None
    body: str
    resolved_at: str | None
    edited_at: str | None
    created_at: str
    updated_at: str


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1)
    parent: str | None = None
    actor: str | None = None


class RelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    issue_id: str
    related_issue_id: str
    type: RelationType
    created_at: str


class RelationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    related: str  # REF del otro issue
    type: RelationType


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    issue_id: str
    title: str
    subtitle: str | None
    url: str
    source_type: str | None
    metadata: dict | None
    created_at: str


class AttachmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    subtitle: str | None = None
    source_type: str | None = None
    metadata: dict | None = None


class SearchHit(BaseModel):
    """The flat hit of SPEC-090 §2.2 — eleven fields, in this order, and no `issue` wrapper (D-064)."""

    model_config = ConfigDict(from_attributes=True)
    issue_id: str
    identifier: str
    title: str
    state_name: str
    state_type: StateType
    priority: int
    due_date: str | None
    assignee_display_name: str | None
    updated_at: str
    snippet: str  # carries <mark>/</mark> literally: no edge escapes it (R15, SPEC-090 REQ-S090-03)
    score: float  # higher = better; 0.0 when backend="like" (SPEC-090 §2, D-016)


class ErrorBody(BaseModel):
    code: str
    message: str
    field: str | None = None
    details: list[dict] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
```

The `attachment` `metadata` is `TEXT` in the database (SPEC-010): the schema exposes it as a JSON object and
the adapter does `json.dumps`/`json.loads`. If the stored text is not valid JSON, `null` is returned
(the response is not broken).
### 2.5 Endpoints

Columns: `Request` = schema/params; `Response` = schema; `Errors` = error statuses (outside `2xx`).

#### 2.5.1 Issues

| Method | Route | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/v1/issues` | query: `team`, `state`\*, `state_type`\*, `priority`\*, `assignee`, `unassigned`, `label`\*, `project`, `cycle`, `no_cycle`, `parent`, `ids`\*, `q`, `open` (default `true`), `archived` (`exclude\|include\|only`, default `exclude`), `trashed` (default `false`), `order`, `limit`, `cursor` | `IssuePage` | 422 |
| GET | `/api/v1/issues/{ref}` | query: `include`\* ∈ `{comments,relations,attachments,history,children}` | `IssueOut` \| `IssueDetail` | 404, 422 |
| POST | `/api/v1/issues` | `IssueCreate` | 201 `IssueOut` + `Location` | 409, 422 |
| PATCH | `/api/v1/issues/{ref}` | `IssuePatch` | 200 `IssueOut` | 404, 409, 422 |
| DELETE | `/api/v1/issues/{ref}` | — (no query params; `?hard` **does not exist**, D-015) | 200 `IssueOut` (`trashed: true`) | 404, 409, 422 |
| POST | `/api/v1/issues/{ref}/restore` | — | 200 `IssueOut` | 404 |
| POST | `/api/v1/issues/{ref}/move` | `IssueMove` | 200 `IssueOut` | 404, 409, 422 |
| POST | `/api/v1/issues/{ref}/labels` | `LabelAdd` (`{"label": "<id\|name>"}`: **one** label per call) | 200 `IssueOut` | 404, 409, 422 |
| DELETE | `/api/v1/issues/{ref}/labels/{label}` | — (`label` = ULID or name, in the path) | 200 `IssueOut` | 404, 422 |
| GET | `/api/v1/issues/{ref}/comments` | — | `CommentList` | 404 |
| POST | `/api/v1/issues/{ref}/comments` | `CommentCreate` | 201 `CommentOut` + `Location` | 404, 422 |
| GET | `/api/v1/issues/{ref}/relations` | — | `RelationList` | 404 |
| POST | `/api/v1/issues/{ref}/relations` | `RelationCreate` | 201 `RelationOut` | 404, 409, 422 |
| DELETE | `/api/v1/issues/{ref}/relations/{relation_id}` | — | 204 | 404 |
| GET | `/api/v1/issues/{ref}/attachments` | — | `AttachmentList` | 404 |
| POST | `/api/v1/issues/{ref}/attachments` | `AttachmentCreate` | 201 `AttachmentOut` | 404, 422 |

\* repeatable parameter (`?priority=1&priority=2`).

**Labels subresource: one label per call.** `POST` and `DELETE` map 1:1 to the use cases
`add_label` / `remove_label` of SPEC-040 §2 (D-012), which operate on **one** label. There is no
`PUT` that replaces the whole set: SPEC-040 has no use case that receives a complete set, and
such an endpoint would be a port with no function behind it. To leave the issue with a different set,
N endpoints are called (the edge iterates, just like the CLI: SPEC-040 GAP-05). `add_label` is idempotent
(SPEC-040 R10): repeating a label already present ⇒ 200 with no change and no history; `DELETE` of a label that is not
linked ⇒ 404 `not_found` with `field: "label_link"`, and a **reference** that resolves nowhere ⇒ 404
`not_found` with `field: "label"` — `field` is the `resource` of the `NotFound` (SPEC-020 §2.6), so the
two 404s differ and the CLI publishes the same pair for the same input (SPEC-050 §2.4.5). The label is
resolved the same way as `ref`: ULID first, name after — case-insensitively in the **issue's team and
then in the workspace**, the order `add_label`/`remove_label` declare (SPEC-040 R22, §5.6); a level that
answers the name with two spellings is 422 `validation_error` with `field: "ref"`
(`LABEL_AMBIGUOUS`); a label from another team ⇒ 422 `validation_error` (`rule='LABEL_TEAM_MISMATCH'`,
SPEC-040 §6), a retired label ⇒ 409 `conflict` (`rule='LABEL_RETIRED'`). Neither route creates labels:
`create_if_missing=False` always.

Filter semantics (identical to SPEC-050 §2.5.3, translated to HTTP):

| Param | Effect |
|---|---|
| `state` | `workflow_state.id` or name (case-insensitive) within the team; several = `IN` |
| `state_type` | filters by `workflow_state.type`; `state` and `state_type` combine with `AND` |
| `priority` | `IN` over `issue.priority`; `0` included |
| `assignee` | `member.id`; `assignee=none` ⇒ `assignee_id IS NULL` (alias of `unassigned=true`) |
| `label` | label id; several labels ⇒ **AND** (the issue must have all of them) |
| `project`, `cycle` | id; `cycle=none` ⇒ `cycle_id IS NULL` (alias of `no_cycle=true`) |
| `parent` | `issue.id` or `identifier`; `parent=none` ⇒ `parent_id IS NULL` (root issues only) |
| `ids` | list of `identifier`/ULID, `IN`; it wins over the rest of the filters |
| `q` | the **title filter** of D-025 (SPEC-030 §2.6): `i.title LIKE '%term%' ESCAPE '\'` over `ListFilters.q`, case-insensitive, `%`/`_`/`\` escaped inside the term, an empty or blank `q` dropped. It declares no `MATCH`, no `backend` and no ranking, and it does not touch `order`; the workspace search is `GET /api/v1/search` (§2.5.4, SPEC-090 §2.5) |
| `open` | `true` ⇒ `is_open = 1`; `false` ⇒ no `is_open` filter |
| `archived` | `exclude` ⇒ `archived_at IS NULL`; `include` ⇒ no filter; `only` ⇒ `archived_at IS NOT NULL` |
| `trashed` | `true` ⇒ `trashed = 1` (implies `open=false` and `archived=include`) |

`assignee=none` and `unassigned=true` at the same time: not an error, it is applied once.
`project` with a simultaneous `no_cycle=true`: 422 `validation_error`, `field: "no_cycle"`.

#### 2.5.2 Teams and workflow states

| Method | Route | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/v1/teams` | query: `include_archived` (default `false`) | `TeamList` | — |
| POST | `/api/v1/teams` | `TeamCreate` | 201 `TeamOut` + `Location` | 409, 422 |
| GET | `/api/v1/teams/{ref}` | `ref` = key or ULID | `TeamDetail` | 404 |
| PATCH | `/api/v1/teams/{ref}` | `TeamPatch` | 200 `TeamOut` | 404, 422 |
| POST | `/api/v1/teams/{ref}/archive` | query: `undo` (default `false`) | 200 `TeamOut` | 404, 409 |
| GET | `/api/v1/teams/{ref}/states` | query: `include_archived` (default `false`) | `StateList` | 404 |
| POST | `/api/v1/teams/{ref}/states` | `StateCreate` | 201 `StateOut` + `Location` | 404, 409, 422 |
| PATCH | `/api/v1/teams/{ref}/states/{state_id}` | `StatePatch` | 200 `StateOut` | 404, 422 |

`GET /teams/{ref}` returns `TeamDetail`: `TeamOut` + `progress` (from `v_team_progress`) + `states`
sorted by `position` ASC. Archiving a team with visible issues (`archived_at IS NULL AND
trashed = 0`) ⇒ 409 `conflict` (D7: no silent cascade).

#### 2.5.3 Labels, projects, cycles, members

| Method | Route | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/v1/labels` | query: `team`, `include_retired` (default `false`) | `LabelList` | 404 |
| POST | `/api/v1/labels` | `LabelCreate` | 201 `LabelOut` + `Location` | 404, 409, 422 |
| GET | `/api/v1/labels/{id}` | — | `LabelOut` | 404 |
| PATCH | `/api/v1/labels/{id}` | `LabelPatch` | 200 `LabelOut` | 404, 409, 422 |
| DELETE | `/api/v1/labels/{id}` | — (retires: `retired_at = now`) | 200 `LabelOut` | 404, 409 |
| GET | `/api/v1/projects` | query: `team`, `status_type`, `include_archived` | `ProjectList` | 404 |
| POST | `/api/v1/projects` | `ProjectCreate` | 201 `ProjectOut` + `Location` | 404, 422 |
| GET | `/api/v1/projects/{id}` | — | `ProjectOut` | 404 |
| PATCH | `/api/v1/projects/{id}` | `ProjectPatch` | 200 `ProjectOut` | 404, 422 |
| DELETE | `/api/v1/projects/{id}` | — (archives) | 200 `ProjectOut` | 404 |
| GET | `/api/v1/cycles` | query: `team`, `include_completed` (default `true`) | `CycleList` | 404 |
| POST | `/api/v1/cycles` | `CycleCreate` | 201 `CycleOut` + `Location` | 404, 409, 422 |
| GET | `/api/v1/cycles/{id}` | — | `CycleOut` | 404 |
| POST | `/api/v1/cycles/{id}/close` | — | 200 `CycleOut` | 404, 409 |
| GET | `/api/v1/members` | query: `include_inactive` (default `false`) | `MemberList` | — |
| POST | `/api/v1/members` | `MemberCreate` | 201 `MemberOut` + `Location` | 409, 422 |
| GET | `/api/v1/members/{id}` | — | `MemberOut` | 404 |
| PATCH | `/api/v1/members/{id}` | `MemberPatch` | 200 `MemberOut` | 404, 409, 422 |

`DELETE /labels/{id}` retires the label (`retired_at`); it does not delete rows: `issue_label` would still
point at it. If the label is in use ⇒ 409 `conflict` (`rule='LABEL_IN_USE'`), and in use means **any**
`issue_label` row, whatever layer the issue is in — an archived or trashed issue counts, because a
restored issue comes back wearing the chip and a retired label cannot be re-attached (SPEC-040 §4.1 R22,
D-061). The `409` is the status it always was; what this revision settles is the set that triggers it.
`DELETE /projects/{id}` archives (`archived_at`); it does not delete.

#### 2.5.4 Search and meta

| Method | Route | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/v1/search` | query: `q` (optional; `min_length=1` when present, `max_length=512`; no `q` and no filters ⇒ 422 `query_empty`), `team`, `state`, `assignee`, `label`, `priority`, `due`, `is`, negation `-field:` (`team`/`state`/`assignee`/`label`/`priority`), `limit` (`1..200`, default `50`), `offset`, `all`, `count`, `sort ∈ {relevance,updated,created,priority,due}` | `SearchPage` (envelope of SPEC-090 §2) | 422 |
| GET | `/health` | — | `HealthOut` | 503 |
| GET | `/openapi.json` | — | OpenAPI 3.1 | — |

**Search declares no contract of its own (D-016).** The endpoint is the query-string translation of
`hifa search` (SPEC-050 §2.5.1) and its authority is **SPEC-090 §2**: the **11 filters** (`team`,
`state`, `assignee`, `label`, `priority`, `due`, `is` and the negated `-field:` form of SPEC-090 §4
R8), `limit` `1..200` (default **50**), `offset`, `all` (`is:all`), `count` and the envelope
`{hits, total, has_more, backend, took_ms}`, whose elements are the **eleven flat fields** of
`SearchHit` (§2.2) and not a nested `Issue`. `score` is **higher = better** (the `-bm25` is normalized
inside SPEC-090); with `backend="like"` it is `0.0`.

**A refused query is published as SPEC-090 §6 publishes it: one refusal, one envelope.** An invalid
filter value, a `limit` out of range, an `offset < 0`, a query over the limits or an empty `q` ⇒ `422`
with `error.code` = the code SPEC-090 §6 gives the refusal (`invalid_filter`, `invalid_limit`,
`query_too_long`, `query_empty`), `error.message` = that section's sentence for it and `error.field` = the
**input** that carried it — `limit`, `offset`, `priority`, `due` or `q`. The code never travels in
`error.field`: `field` names the parameter a client fixes, and the rule name is the code (§6.1). The
endpoint's parameters are the query-string translation of `hifa search`'s flags, so the edge composes the
`SearchQuery` on this side too — `parse_query(q)` plus the parameters — and passes the instant its own
clock read (`search_issues(db, query, now=…)`, SPEC-090 §2.3).

```python
class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    schema_version: int
    db: Literal["ok", "missing", "corrupt"]
    uptime_s: float
```

`/health` responds 200 with `status="ok"` if `PRAGMA integrity_check` returns `ok` and
`user_version` == the expected version; 503 with `status="degraded"` and a `db` other than `ok`
otherwise. It is the only endpoint that opens its own verification connection (it does not use the request's UoW).

**`/docs` and `/redoc` are disabled** (`docs_url=None`, `redoc_url=None`): Swagger UI and ReDoc
load JS/CSS from `cdn.jsdelivr.net`, which violates REQ-N005 and REQ-N004 (offline). The readable
contract is `/openapi.json` + this spec.

### 2.6 Keyset pagination

```python
class IssuePage(BaseModel):
    items: list[IssueOut]
    next_cursor: str | None
    has_more: bool
    total_estimate: int | None = None  # siempre null en v1


# Listas acotadas (teams, labels, projects, cycles, members, comments, relations,
# attachments, states): same envelope, no real pagination.
class TeamList(BaseModel):
    items: list[TeamOut]
    next_cursor: None = None
    has_more: bool = False
    total_estimate: int | None = None


# Idem: LabelList, ProjectList, CycleList, MemberList, StateList,
#       CommentList, RelationList, AttachmentList


# Search: envelope of SPEC-090 §2 (D-016), NOT the list envelope of R3.
# The five keys and the eleven-field SearchHit are SPEC-090 §2.2: this class repeats them and
# defines neither. `score` is higher = better, never negative; `total` is null without `count`.
class SearchPage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hits: list[SearchHit]
    total: int | None
    has_more: bool
    backend: Literal["fts", "like", "none"]
    took_ms: float
```

Both validate a dataclass — `search_issues` returns the **frozen dataclasses** of SPEC-090 §2.2 — and both
carry `model_config = ConfigDict(from_attributes=True)`, which §2.4 requires of exactly that family of
models. That is not decoration: `SearchPage.model_validate(page)` and `SearchHit.model_validate(hit)`
are the conversion — every field is read by attribute, the nested hits included, and no edge builds a
`dict` or calls `model_dump()` on the way in. A schema without the config rejects the use case's own
return value.

| Param | Contract |
|---|---|
| `limit` | int `1..200`, default `50`. `0`, negative or `> 200` ⇒ 422, `field: "limit"` |
| `order` | `{field}[.asc\|.desc]`; `field` ∈ `manual | created | updated | priority | due` (D-011), no suffix = `.asc`. Default `manual.asc`. Invalid (field outside the vocabulary or unknown direction) ⇒ 422, `field: "order"` |
| `cursor` | opaque: `base64url("<order>:<asc\|desc>\|<ULID>")` without padding, the direction in lowercase (D-011, SPEC-030 §2.6.2 `encode_cursor`). The client neither interprets nor builds it |

Rules:

- **Total and stable order.** Every query sorts by `(key, id)`; `id` (ULID) is the final
  tiebreaker. `NULL` goes **always last**, in `asc` and in `desc`, comparing
  `(key IS NULL)` first.
- **Keyset, not offset.** The server translates the cursor into the predicate SPEC-030 §2.6.1
  declares, in the form that corresponds to the anchor: with a value, the three branches (every row
  without a value, a later value, or the same value with a later `id`); without a value,
  `key IS NULL` passed by `id` alone. A comparison against a `NULL` anchor matches no row, so the
  tuple comparison `(key, id) > (k, id)` is not the clause here. `OFFSET` is forbidden in any paginated
  query.
- `has_more` is computed by requesting `limit + 1` rows and trimming; `next_cursor` is emitted only if
  `has_more`.
- A cursor that does not parse (no `|`, empty `sort_key`, malformed ULID — anything but 26 characters) or
  whose `sort_key` is not the canonical `<order>:<asc|desc>` of the requested `order` and direction
  (SPEC-030 §2.6.2) ⇒ 422 with `code: "validation_error"`, `field: "cursor"`. It is never silently
  ignored.
- The cursor **does** encode the direction, inside its `sort_key`: `order=due.desc` is served by a token
  whose `sort_key` is `due:desc` (lowercase, SPEC-030 §2.6.2). A cursor from the previous page presented
  with the other direction ⇒ 422 `validation_error`, `field: "cursor"` — the `INVALID_CURSOR` that
  SPEC-040 §5.3 rejects — instead of an incoherent page. The client keeps `order` stable while
  paginating because a changed order is an error, not a silent restart at the first page.
- The cursor is not a security token: it is not signed. A tampered cursor can only produce a
  422 or a shifted page.
- `q` does **not** change the order, and no order is refused because `q` is present. The title filter of
  D-025 is one predicate among many in `ListFilters` (SPEC-030 §2.6): it produces no score, no band and
  no `relevance`, and `order` stays the one the client sent or `manual.asc` by default (D-011). The
  keyset is the same `(key, id)` with and without `q`, because the filter narrows the rows and never
  reorders them. There is no `relevance` in this route's vocabulary: §2.4's `OrderField` carries
  `created_at`, `updated_at`, `priority`, `due_date`, `sort_order`, `board_order` and `number`, and the
  order vocabulary of the row of §2.5.1 is `manual`, `created`, `updated`, `priority` and `due`. The
  search that ranks by `bm25` is `GET /api/v1/search` (§2.5.4, SPEC-090 §2).

Equivalence with the CLI (SPEC-050 §2.5.3), so that a script can migrate from one to the other without
guessing:

| CLI | API |
|---|---|
| `--sort manual` (default) | `order=manual` (equivalent to `manual.asc`) |
| `--sort priority` | `order=priority.asc` |
| `--sort created` | `order=created.asc` |
| `--sort updated` | `order=updated.asc` |
| `--sort due` | `order=due.asc` |
| `--reverse` | flips the direction of `order` (`manual` → `manual.desc`) |
| `--limit N` / `--cursor X` | `limit=N` / `cursor=X` |

The default **does** match: `manual` on both edges (and in the dashboard), because D-011 fixes a
single default for CLI, API and web. The edge does not reorder: it translates to `ListFilters.order` of SPEC-030 and
nothing more.

### 2.7 Examples

```console
$ curl -s localhost:7777/health
{"status":"ok","version":"0.1.0","schema_version":1,"db":"ok","uptime_s":12.4}
```

```console
$ curl -s -X POST localhost:7777/api/v1/issues \
    -H 'content-type: application/json' \
    -d '{"team":"ENG","title":"Fix login","priority":2,"label_ids":[],"due_date":"2026-11-20"}'
{"id":"01JCV3X9K2M4P6R8S0T2V4W6Y8","identifier":"ENG-13","number":13,
 "team":{"id":"01JCV3...","key":"ENG","name":"Engineering","icon":"🧪"},
 "title":"Fix login","description":null,"priority":2,"priority_label":"High",
 "estimate":null,"due_date":"2026-11-20",
 "state":{"id":"01JCV3...","name":"Todo","type":"unstarted","color":"#E2E2E2"},
 "assignee":null,"creator":null,"project":null,"milestone":null,"cycle":null,
 "parent":null,"labels":[],"sort_order":1024.0,"board_order":1024.0,
 "sub_issue_sort_order":0.0,"started_at":null,"completed_at":null,"canceled_at":null,
 "snoozed_until_at":null,"archived_at":null,"auto_archived_at":null,"trashed":false,
 "branch_name":null,"is_open":true,"is_visible":true,
 "created_at":"2026-11-08T14:03:11Z","updated_at":"2026-11-08T14:03:11Z"}
```
(Real response on one line; it is split here for readability. `Location:
/api/v1/issues/ENG-13`.)

```console
$ curl -si -X POST localhost:7777/api/v1/issues -H 'content-type: application/json' \
    -d '{"title":"no team in a multi-team workspace"}'
HTTP/1.1 422 Unprocessable Entity
{"error":{"code":"validation_error","message":"team required: the workspace has 2 teams",
          "field":"team","details":null}}
```

```console
$ curl -s 'localhost:7777/api/v1/issues?team=ENG&open=true&priority=1&priority=2&order=priority.asc&limit=2'
{"items":[{…},{…}],"next_cursor":"eyJ2IjoxLCJvIjoicHJpb3JpdHkiLCJkIjoiYXNjIiwia1siOlsyLDEwMjQuMF0sImlkIjoiMDFKQ1YzWDlLMo",
 "has_more":true,"total_estimate":null}
```

```console
$ curl -s 'localhost:7777/api/v1/issues?team=ENG&cursor=eyJ2IjoxLCJvIjoicHJpb3JpdHkiLCJkIjoiYXNjIiwia1siOlsyLDEwMjQuMF0sImlkIjoiMDFKQ1YzWDlLMo'
{"items":[{…}],"next_cursor":null,"has_more":false,"total_estimate":null}
```

```console
$ curl -s localhost:7777/api/v1/issues/ENG-13?include=comments\&include=children
{"id":"01JCV3X9...","identifier":"ENG-13",…,"comments":[],"relations":[],"attachments":[],
 "history":[{"id":1,"issue_id":"01JCV3X9...","actor_id":null,"field":"state_id",
             "old_value":null,"new_value":"01JCV3...","created_at":"2026-11-08T14:03:11Z"}],
 "children":[]}
```

```console
$ curl -s -X PATCH localhost:7777/api/v1/issues/ENG-13 -H 'content-type: application/json' \
    -d '{"due_date":null,"priority":1}'
{"id":"01JCV3X9...","identifier":"ENG-13",…,"priority":1,"priority_label":"Urgent",
 "due_date":null,…}
```

```console
$ curl -s -X PATCH localhost:7777/api/v1/issues/ENG-13 -H 'content-type: application/json' \
    -d '{"title":null}'
{"error":{"code":"validation_error","message":"title cannot be null","field":"title",
          "details":null}}
```

```console
$ curl -si -X POST localhost:7777/api/v1/issues/ENG-13/move \
    -H 'content-type: application/json' -d '{"state":"Done"}'
HTTP/1.1 422 Unprocessable Entity
{"error":{"code":"validation_error","message":"cannot complete ENG-13: it has 2 open sub-issues",
          "field":"state","details":null}}
```

```console
$ curl -si -X POST localhost:7777/api/v1/issues/ENG-13/move \
    -H 'content-type: application/json' -d '{"state":"In Progress","after":"ENG-10"}'
HTTP/1.1 200 OK
{"identifier":"ENG-13",…,"state":{"name":"In Progress","type":"started",…},"board_order":1536.0,…}
```

```console
$ curl -s -X POST localhost:7777/api/v1/issues/ENG-13/labels \
    -H 'content-type: application/json' -d '{"label":"auth"}'
{"identifier":"ENG-13",…,"labels":[{"id":"01JCV5AAA","name":"auth","color":"#5E6AD2"},
 {"id":"01JCV5BBB","name":"bug","color":"#EB5757"}],…}

$ curl -s -X DELETE localhost:7777/api/v1/issues/ENG-13/labels/bug
{"identifier":"ENG-13",…,"labels":[{"id":"01JCV5AAA","name":"auth","color":"#5E6AD2"}],…}
```

```console
$ curl -si -X POST localhost:7777/api/v1/issues -H 'content-type: application/json' \
    -d '{"title":"x","colr":"#fff"}'
HTTP/1.1 422 Unprocessable Entity
{"error":{"code":"validation_error","message":"unknown field: colr","field":"colr","details":null}}
```

```console
$ curl -s 'localhost:7777/api/v1/search?q=login&limit=2'
{"hits":[{"issue_id":"01JCV5AAA","identifier":"ENG-13","title":"SSO login (Okta)",
          "state_name":"In Progress","state_type":"started","priority":2,"due_date":null,
          "assignee_display_name":"Ginkelte","updated_at":"2026-11-18T09:12:00Z",
          "snippet":"<mark>login</mark> with <mark>SSO</mark> (Okta)","score":0.82},
         {"issue_id":"01JCV5BBB","identifier":"ENG-02","title":"legacy login without SSO",
          "state_name":"Done","state_type":"completed","priority":0,"due_date":null,
          "assignee_display_name":null,"updated_at":"2026-11-02T16:40:00Z",
          "snippet":"<mark>login</mark> legacy without <mark>SSO</mark>","score":0.37}],
 "total":15,"has_more":true,"backend":"fts","took_ms":4.2}
```

`score` is **higher = better** (D-016): the `-bm25` of FTS5 is normalized inside SPEC-090, so
no value comes out negative. The envelope is the one of SPEC-090 §2 — `hits, total, has_more, backend,
took_ms` —, not the list envelope of R3, and each hit is the eleven flat fields of §2.2: the `snippet`
carries the literal `<mark>`/`</mark>` pair of SPEC-090 R15 as text, unescaped and unrewritten at this
edge (R15), because the markers are the only markup the search produces and an API client that wants a
different emphasis reads them and renders its own.

```console
$ curl -si localhost:7777/api/v1/issues/ENG-999
HTTP/1.1 404 Not Found
{"error":{"code":"not_found","message":"issue 'ENG-999' does not exist","field":"issue","details":null}}
```

```console
$ curl -si -X POST localhost:7777/api/v1/teams -H 'content-type: application/json' \
    -d '{"key":"ENG","name":"Engineering"}'
HTTP/1.1 409 Conflict
{"error":{"code":"conflict","message":"team with key 'ENG' already exists","field":"key","details":null}}
```

```console
$ curl -si localhost:7777/api/v1/issues -H 'content-type: text/csv'
HTTP/1.1 415 Unsupported Media Type
{"error":{"code":"unsupported_media_type","message":"content-type must be application/json",
          "field":"content-type","details":null}}
```
## 3. Data it touches

The API executes no SQL: it delegates to the use cases of SPEC-040 over the `UnitOfWork`. This table
declares the observable effect per endpoint; the invariants are those of the SPEC-010 schema.

| Resource | Tables | Operation | Invariant |
|---|---|---|---|
| `/issues` | `issue`, `issue_label`, `issue_history` | SELECT / INSERT / UPDATE | `UNIQUE(team_id, number)`, `UNIQUE(identifier)`, composite FK `(team_id, state_id)` |
| `/issues/{ref}/comments` | `comment` | SELECT / INSERT | `CHECK(length(trim(body)) > 0)` |
| `/issues/{ref}/relations` | `issue_relation` | SELECT / INSERT / DELETE | `UNIQUE(issue_id, related_issue_id, type)`, `CHECK(issue_id <> related_issue_id)` |
| `/issues/{ref}/attachments` | `attachment` | SELECT / INSERT | `url NOT NULL`; `metadata` TEXT with JSON |
| `/teams`, `/teams/{ref}/states` | `team`, `workflow_state`, `team_membership` | SELECT / INSERT / UPDATE | `UNIQUE(workspace_id, key)`, `UNIQUE(team_id, name)` |
| `/labels` | `label`, `issue_label` | SELECT / INSERT / UPDATE `retired_at` | `UNIQUE(workspace_id, team_id, name)`, `CHECK(is_group = 0 OR parent_id IS NULL)` |
| `/projects` | `project`, `project_milestone` | SELECT / INSERT / UPDATE `archived_at` | `priority BETWEEN 0 AND 4` |
| `/cycles` | `cycle` | SELECT / INSERT / UPDATE `completed_at` | `UNIQUE(team_id, number)`, `CHECK(ends_at > starts_at)` |
| `/members` | `member`, `team_membership` | SELECT / INSERT / UPDATE `is_active` | `email UNIQUE` |
| `/search` | `issue_fts`, `v_issue` | SELECT | index maintained by triggers (SPEC-090) |
| `/health`, `GET /teams/{ref}` | `PRAGMA integrity_check`, `v_team_progress`, `v_priority` | SELECT | read-only |

Nothing is ever written to `v_*` or to `issue_fts`.

## 4. Business rules

| ID | Rule |
|---|---|
| **R1** | **No handler is `async def`** (C2). An introspection test walks `app.routes` and fails if any endpoint is a coroutine. Plus one connection per request with `check_same_thread=False` (§2.3): sharing a connection between requests is a bug, not an optimization. → **REQ-F060-01** |
| **R2** | **Closed surface.** The routes registered under `/api/v1` are exactly those of §2.5 (method + path), with no extras. The scope is `/api/v1` because `create_app(mount_web=True)` mounts the dashboard of SPEC-070 in the same process (§2.1): web routes are not a contract of this spec. A test filters `route.path.startswith("/api/v1")` and compares `{(route.path, method)}` against the table; it fails on any difference. → **REQ-F060-02** |
| **R3** | **A single envelope for lists.** Every collection response has the 4 keys `items, next_cursor, has_more, total_estimate`. Bounded collections return `next_cursor: null` and `has_more: false`, never a bare array. **Single exception: `/search`**, which uses the envelope of SPEC-090 §2 (`hits, total, has_more, backend, took_ms`) per D-016; it is not a keyset-paginable list. → **REQ-F060-03** |
| **R4** | **Pure keyset.** `OFFSET` is forbidden. The order is total (`key, id`) and `NULL` goes last in both directions, because the absence flag ascends in both (D-044, SPEC-030 §2.6.1). `limit+1` decides `has_more`. → **REQ-F060-04** |
| **R5** | **PATCH is genuinely partial.** Three states are distinguished per field: absent (untouched), `null` (cleared if nullable), value (written). It is implemented with `model_fields_set`; `model_dump(exclude_unset=True)` is the only admitted way to build the `changes`. `null` in `title` or `priority` ⇒ 422. → **REQ-F060-05** |
| **R6** | **REF is resolved at the app-layer edge, not in the router.** The router passes the string; `identifier` case-insensitive first, ULID after; no match ⇒ `NotFound`. `LIKE` is never used and nothing is guessed by prefix. → **REQ-F060-06** |
| **R7** | **No remote assets.** `docs_url=None`, `redoc_url=None`, no `StaticFiles` under `/api`, no CDN in any response. `GET /docs` ⇒ 404. → **REQ-N060-03** |
| **R8** | **Creation and deletion codes.** A `POST` that creates a top-level resource ⇒ 201 + `Location`. `DELETE /issues/{ref}` is **soft only**: 200 with the resource and `trashed: true`; **there is no physical delete and no `?hard`** (D-015 — the only compaction is `hifa db vacuum`), and a `hard` in the query string is rejected with 422 `validation_error`, `field: "hard"`. The labels subresource **does not** create top-level resources and is idempotent (SPEC-040 R10): `POST /issues/{ref}/labels` and `DELETE /issues/{ref}/labels/{label}` ⇒ **200** with the complete issue, never 201 and never 204. → **REQ-F060-07** |
| **R9** | **Search: contract of SPEC-090 §2 (D-016).** `q` requires `min_length=1` and `≤ 512` chars; `limit` `1..200` default `50`; the 11 filters are those of SPEC-090 §2, with no parameters of its own. `score` is **higher = better** and **never negative** (`0.0` with `backend="like"`); the default order is `score DESC, id ASC` — which here is `sort=relevance`, the default of the `sort` parameter of §2.5.4. **`q` does not change the order, and no `order`/`sort` value is refused because `q` is present**: this endpoint declares `sort` (the five values of SPEC-090 §2.5, all valid with or without `q`) and no `order` parameter at all, so there is nothing to refuse — a `sort` with `q` is a search ordered by that key, and the ranking of SPEC-090 R9/R12 is what `relevance` means. `snippet` arrives as-is from SPEC-090 §2: FTS5 `snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12)`, with `<mark>`/`</mark>` around the terms and no escaping in `app/` (REQ-S090-03). → **REQ-F060-09** |
| **R10** | **Single-direction relations.** `POST /relations` with `type=blocks` creates `issue_id → related_issue_id`. `blocked_by` is not persisted: the inverse is derived on read. → **REQ-F060-10** |
| **R11** | **Read-only.** With `read_only=True`, any method ≠ GET/HEAD ⇒ 403 `forbidden` with the `message` `"server in read-only mode"`, before touching the database. → **REQ-F060-11** |
| **R12** | **A single error format.** Pydantic's 422s and those of `RequestValidationError` are rewritten into the envelope of §6.3; FastAPI's `{"detail": [...]}` format **never** goes out. → **REQ-F060-12** |
| **R13** | **Mapping by type, not by message.** The exception→status mapping is a `dict[type[DomainError], int]` in `api/errors.py`; `if "does not exist" in str(e)` is forbidden. → **REQ-F060-13** |
| **R14** | **No internals in the response.** 500 returns a generic `internal_error`; the traceback goes to the process log only if `HIFA_DEBUG=1`. No table names, file paths or SQL in `error.message`. → **REQ-S060-03** |
| **R15** | **No generated HTML.** No response field contains HTML built by the API; `description` and `body` are raw markdown. **Single exception:** the `snippet` of `/search`, which preserves the `<mark>`/`</mark>` markers of SPEC-090 §2 (REQ-S090-03) as text inside the JSON: they are neither escaped nor sanitized again at this edge. `Content-Type` is always `application/json`. → **REQ-S060-02** |
| **R16** | **The API does not authenticate.** No `Authorization` required, no CORS, no cookies. `create_app` does not register `CORSMiddleware`; a test verifies that the middleware list does not include it. → **REQ-S060-01** |
| **R17** | **Stable OpenAPI.** Every endpoint declares an explicit `operation_id` `f"{resource}_{action}"` (`issues_list`, `issues_move`); `summary` and `tags` fixed. The generated document does not change between runs over the same data (no unordered `dict`, no `datetime.now`). `spec_contract_check` compares routes and operationIds against §2.5. → **REQ-F060-08** |

Requirements specific to this component:

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N060-01** | `GET /api/v1/issues?limit=50` p95 < **50 ms** with 10 000 issues in the database (no `q`). | pytest-benchmark or `oha` in the perf test |
| **REQ-N060-02** | Process with the app loaded ≤ **150 MB** RSS (REQ-N002), 1 uvicorn worker. | `/usr/bin/time -v` over `hifa serve` |
| **REQ-N060-03** | Zero network requests from the served process: no remote `<script src>`, `/docs` disabled. | template grep + `/docs` 404 test |
| **REQ-N060-04** | Body > **1 MiB** ⇒ 413 `payload_too_large`; `limit` > 200 ⇒ 422. | parameterized test |
| **REQ-S060-01** | No auth, no CORS, loopback bind by default (delegated to `hifa serve`, REQ-S001). | middleware test + bind test |
| **REQ-S060-02** | No response contains HTML or `text/html`; rejection of `X-Forwarded-*` for building `Location` URLs (a relative path is used). | `Location` and content-type test |
| **REQ-S060-03** | `error.message` includes no traces, SQL, absolute paths or table names. | test with a forced exception (500) |
| **REQ-S060-04** | Non-JSON Content-Type ⇒ 415; method not allowed ⇒ 405 with the same envelope. | parameterized test |
| **REQ-S060-05** | The API imports no `sqlite3` and contains no SQL in strings: everything goes through repos. | static AST test + `import-linter` |

## 5. Flow

### 5.1 Lifecycle of a request

```
1. uvicorn recibe la request y la pasa a la app ASGI.
2. Router resuelve (method, path). Sin match ⇒ 404/405 por el handler de Starlette,
   normalizado al sobre §6.3.
3. read_only and the method is not GET/HEAD ⇒ 403 forbidden (before any data dependency).
4. get_uow dependency: opens the connection (per-connection PRAGMA: WAL, foreign_keys, busy_timeout),
   construye UnitOfWork. Se cierra en finally.
5. Pydantic validation of path/query/body. Failure ⇒ RequestValidationError ⇒ 422 with
   error.code="validation_error", error.field = primer campo del error (`body.title`, `query.limit`).
6. Handler (def, threadpool): resuelve refs, arma el comando, llama el caso de uso.
7. Caso de uso: BEGIN IMMEDIATE si muta; COMMIT o ROLLBACK.
8. Serialization: `IssueOut.model_validate(row)` and the same key order as §2.4.2.
9. Domain exception ⇒ per-type handler (api/errors.py) ⇒ status + envelope §6.3.
10. Connection close (generator finally). Structured log entry: method, path, status, ms.
```

### 5.2 Exception handlers (exact registration)

```python
app.add_exception_handler(NotFound, not_found_handler)  # 404
app.add_exception_handler(Conflict, conflict_handler)  # 409
app.add_exception_handler(Validation, validation_handler)  # 422
app.add_exception_handler(InvalidTransition, invalid_transition_handler)  # 409
app.add_exception_handler(RequestValidationError, request_validation_handler)  # 422
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # 404/405/413/415
app.add_exception_handler(Exception, internal_handler)  # 500
```

The registration order is irrelevant (FastAPI resolves by MRO), but the `Exception` one must exist or
Starlette returns a plain-text 500, breaking R3 and R14.

### 5.3 Exception-to-status mapping

```
NotFound            -> 404  not_found                (field = e.field, the `resource`)
Conflict            -> 409  conflict
Validation          -> 422  validation_error        (field = e.field; a lowercase e.code wins, §6.1)
InvalidTransition   -> 409  invalid_transition      (field = e.field)
RequestValidationError -> 422 validation_error      (field = "body.<campo>" | "query.<param>")
HTTPException(405)  -> 405  method_not_allowed
HTTPException(413)  -> 413  payload_too_large
HTTPException(415)  -> 415  unsupported_media_type
sqlite3.OperationalError (locked) -> 503 database_unavailable
Exception           -> 500  internal_error
```

## 6. Errors

### 6.1 Catalog

| `error.code` | HTTP | When | `field` |
|---|---|---|---|
| `validation_error` | 422 | invalid payload/query, unknown field, null `title`, `limit` out of range on a listing (`/issues`), invalid cursor | yes |
| `not_found` | 404 | nonexistent ref/resource | the `resource` of the `NotFound` (`issue`, `label`, `label_link`, …) |
| `conflict` | 409 | uniqueness, label in use, team with issues, member with open issues, duplicate relation | yes |
| `invalid_transition` | 409 | writing an issue with `archived_at` not null (SPEC-040 §6, R20) | `ref` |
| `forbidden` | 403 | `read_only=True` and a write method | `null` |
| `method_not_allowed` | 405 | method not declared for the route | `null` |
| `payload_too_large` | 413 | body > 1 MiB | `null` |
| `unsupported_media_type` | 415 | `Content-Type` other than JSON | `content-type` |
| `database_unavailable` | 503 | `database is locked` after `busy_timeout` | `null` |
| `internal_error` | 500 | unmapped exception | `null` |
| `query_empty`, `query_too_long`, `invalid_limit`, `invalid_filter` | 422 | a `GET /api/v1/search` the search contract refuses (SPEC-090 §6) | the **input** that carried it: `q`, `limit`, `offset`, `priority`, `due` |

**The search refuses with its own four codes, and that is the one deliberate exception to
`validation_error`.** A search refusal is already spelled — `invalid_limit`, `invalid_filter`,
`query_too_long`, `query_empty` — by the component that raises it (SPEC-090 §6), it is the code the CLI
publishes for the same input (SPEC-050 §6), and re-badging it as `validation_error` on the way out would
give one refusal two names depending on the transport. The same sentence is the message, and `field`
names the parameter: this is the rule of §2.5.4 and the last row of the catalog above. Every other
`Validation` of this layer is `validation_error`, unchanged.

`InvalidTransition` and `Conflict` share a status (409) but **not** the `code`: the client distinguishes
"retryable with different data" (conflict) from "the issue is archived" (invalid_transition). The map is
the one D-042 records for the three edges: `invalid_transition` has a single producer, R20 of SPEC-040 §6,
and every state-machine refusal that SPEC-020 raises — `CLOSE_WITH_OPEN_CHILDREN`, `STATE_ARCHIVED`,
`STATE_TEAM_MISMATCH`, `ISSUE_TRASHED` — is `validation_error` 422.

### 6.2 Messages

In English, lowercase initial, no trailing period, with the identifier in single quotes and no
internal data. The exact text is contract: the tests assert it.

| Situation | `message` |
|---|---|
| nonexistent issue | `issue 'ENG-999' does not exist` |
| nonexistent team | `team 'OPS' does not exist` |
| duplicate key | `team with key 'ENG' already exists` |
| label in use | `label 'bug' is in use by 12 issues` |
| completing with open children | `cannot complete ENG-13: it has 2 open sub-issues` |
| writing an archived issue | `issue 'ENG-13' is archived; restore it first` |
| unknown field | `unknown field: colr` |
| null in non-nullable | `title cannot be null` |
| read-only | `server in read-only mode` |
| body limit | `body exceeds 1 MiB` |
| content-type | `content-type must be application/json` |

### 6.3 Error envelope (normative)

```json
{
  "error": {
    "code": "not_found",
    "message": "issue 'ENG-999' does not exist",
    "field": "issue",
    "details": null
  }
}
```

- `error` is mandatory and is the only top-level key.
- `code`, `message` mandatory; `field` and `details` **always present** (`null` if they do not apply)
  so that the client's parser does not change shape.
- `details` is `null` or a list `[{"field": "...", "message": "..."}]` with **all** the validation
  errors when there is more than one; `field` repeats the first one.
- `LABEL_AMBIGUOUS` is the case worth naming: its `details` is **`null`**, and the spellings (and, when
  the rows span levels, the team keys) travel in `message` — `label bug is ambiguous: 'Bug', 'bug'`,
  `label bug is ambiguous: it exists in ENG, OPS`. The mapping that carries the same names is the
  exception's accessor (`exc.details`, SPEC-020 §2.6); no edge publishes it, so `details` keeps its two
  shapes and gains no third (SPEC-040 R22, D-062).
- `Content-Type: application/json; charset=utf-8`. Never `text/plain`, never HTML.

## 7. Acceptance Criteria

```gherkin
@SPEC-060
Feature: hifa REST API

  Scenario: health responds ok with an up-to-date schema
    Given a database migrated to user_version 1
    When GET /health is performed
    Then the status is 200
    And the body has status "ok"
    And the body has schema_version 1
    And the body has db "ok"

  Scenario: openapi.json declares exactly the endpoints of the spec
    Given the app created with create_app
    When GET /openapi.json is performed
    Then the status is 200
    And the declared routes include "/api/v1/issues" with get, post
    And the declared routes include "/api/v1/issues/{ref}/move" with post
    And no route of the document starts with "/api/v2"

  Scenario: interactive documentation is disabled
    Given the app created with create_app
    When GET /docs is performed
    Then the status is 404

  Scenario: redoc is disabled
    Given the app created with create_app
    When GET /redoc is performed
    Then the status is 404

  Scenario: creating an issue returns 201 with Location
    Given a team ENG with no issues
    When POST /api/v1/issues is performed with body {"team":"ENG","title":"Hello"}
    Then the status is 201
    And the Location header is "/api/v1/issues/ENG-1"
    And the body has identifier "ENG-1"
    And the body has number 1
    And the body has 33 keys

  Scenario: an unknown field is rejected with 422
    Given a team ENG
    When POST /api/v1/issues is performed with body {"title":"x","colr":"#fff"}
    Then the status is 422
    And error.code is "validation_error"
    And error.field is "colr"

  Scenario: search returns the SPEC-090 envelope and no negative score
    Given an issue ENG-13 with description "login with SSO"
    And an issue ENG-02 with description "legacy login without SSO"
    When GET /api/v1/search?q=login&limit=2 is performed
    Then the status is 200
    And the body has the keys hits, total, has_more, backend, took_ms
    And the body does not have the keys items, next_cursor, total_estimate
    And each hit has the keys issue_id, identifier, title, state_name, state_type, priority, due_date, assignee_display_name, updated_at, snippet, score
    And no hit has the key issue
    And all scores are greater than or equal to 0
    And the hits come sorted by score descending

  Scenario: a refused search publishes SPEC-090's code and names the input
    Given a team ENG
    When GET /api/v1/search?q=login&limit=201 is performed
    Then the status is 422
    And error.code is "invalid_limit"
    And error.field is "limit"
    And error.message is "limit must be between 1 and 200"

  Scenario: keyset pagination neither duplicates nor loses rows
    Given a team with 7 issues
    When GET /api/v1/issues?limit=3 is paginated until has_more is false
    Then 7 unique identifiers are obtained
    And the last page has next_cursor null

  Scenario: a cursor from another order is rejected
    Given a cursor obtained with order "priority.asc"
    When GET /api/v1/issues?order=created.asc is performed with that cursor
    Then the status is 422
    And error.field is "cursor"

  Scenario: the reference by identifier and by ULID returns the same object
    Given an issue with identifier "ENG-13"
    When GET /api/v1/issues/ENG-13 and GET /api/v1/issues/<its id> are performed
    Then both statuses are 200
    And both bodies are identical

  Scenario: PATCH does not touch absent fields
    Given an issue ENG-13 with priority 2 and due_date "2026-11-20"
    When PATCH /api/v1/issues/ENG-13 is performed with body {"priority":1}
    Then the status is 200
    And the body has priority 1
    And the body has due_date "2026-11-20"

  Scenario: PATCH with null clears a nullable field
    Given an issue ENG-13 with due_date "2026-11-20"
    When PATCH /api/v1/issues/ENG-13 is performed with body {"due_date":null}
    Then the status is 200
    And the body has due_date null

  Scenario: PATCH with null in a non-nullable field is 422
    Given an issue ENG-13
    When PATCH /api/v1/issues/ENG-13 is performed with body {"title":null}
    Then the status is 422
    And error.field is "title"

  Scenario: soft DELETE marks trashed without deleting the row
    Given an issue ENG-13
    When DELETE /api/v1/issues/ENG-13 is performed
    Then the status is 200
    And the body has trashed true
    And a subsequent GET to /api/v1/issues/ENG-13 with archived=include returns 200

  Scenario: DELETE never deletes the row and ?hard is an unknown parameter
    Given an issue ENG-14
    When DELETE /api/v1/issues/ENG-14?hard=true is performed
    Then the status is 422
    And error.code is "validation_error"
    And error.field is "hard"
    And the issue ENG-14 still exists with trashed false
    And `SELECT COUNT(*) FROM issue` does not change

  Scenario: move to Done with open sub-issues is 422 validation_error
    Given an issue ENG-13 with two sub-issues in "Todo"
    When POST /api/v1/issues/ENG-13/move is performed with body {"state":"Done"}
    Then the status is 422
    And error.code is "validation_error"
    And the state of the issue did not change

  Scenario: a state from another team is 422
    Given an issue of team ENG and a state of team OPS
    When POST /api/v1/issues/ENG-13/move is performed with that state
    Then the status is 422
    And error.code is "validation_error"

  Scenario: POST adds a label to the issue
    Given an issue ENG-13 with the labels ["bug"]
    When POST /api/v1/issues/ENG-13/labels is performed with {"label":"auth"}
    Then the status is 200
    And the body has exactly 2 labels and one is named "auth"
    And the label "auth" appears only once in the body

  Scenario: DELETE removes a label from the issue
    Given an issue ENG-13 with the labels ["bug","auth"]
    When DELETE /api/v1/issues/ENG-13/labels/bug is performed
    Then the status is 200
    And the body has exactly 1 label named "auth"

  Scenario: a duplicate relation is 409
    Given a blocks relation from ENG-13 to ENG-7
    When POST /api/v1/issues/ENG-13/relations is performed with {"related":"ENG-7","type":"blocks"}
    Then the status is 409
    And error.code is "conflict"

  Scenario: read-only rejects writes
    Given the app created with create_app(read_only=True)
    When POST /api/v1/issues is performed with a valid body
    Then the status is 403
    And error.code is "forbidden"
    And no issue was created

  Scenario: a non-JSON content-type is 415
    Given the app running
    When POST /api/v1/issues is performed with Content-Type "text/csv"
    Then the status is 415
    And error.code is "unsupported_media_type"

  Scenario: the 404 uses the error envelope
    Given a workspace without the issue ENG-999
    When GET /api/v1/issues/ENG-999 is performed
    Then the status is 404
    And the body has exactly the key "error"
    And error.code is "not_found"
    And error.field is "issue"
    And error.details is null

  Scenario: no endpoint is async
    Given the app created with create_app
    When every API-type route is inspected
    Then no endpoint function is a coroutine

  Scenario: a body larger than 1 MiB is rejected
    Given the app running
    When POST /api/v1/issues is performed with a 2 MiB title
    Then the status is 413
    And error.code is "payload_too_large"
```

## 8. Regeneration checklist

1. Read `SPEC-000`, `SPEC-010` and this spec. Do not read `src/hifa/api/`.
2. `api/app.py`: `create_app(*, db_path, read_only)` with `docs_url=None`, `redoc_url=None`,
   `openapi_url="/openapi.json"`. No CORS. `app.state.db_path`/`app.state.read_only`.
3. `api/deps.py`: `get_uow` as a **sync** generator with `check_same_thread=False` and closing in
   `finally`.
4. `api/schemas.py`: transcribe §2.4 without renaming fields. The 33 of `IssueOut` in the given order
   (a test compares the key list against SPEC-050 §2.4.2).
5. `api/errors.py`: `STATUS: dict[type, int]` + the 7 handlers of §5.2, all returning
   `ErrorEnvelope` with the 4 keys of `error`.
6. `api/routers/{issues,teams,labels,projects,cycles,members,search,health}.py`: one router per
   resource, `def` handlers, explicit `operation_id` `f"{resource}_{action}"`.
7. Implement keyset in `api/pagination.py`: `encode_cursor`, `decode_cursor`, `apply_cursor`;
   property test with 1 000 rows and 4 `order` combinations.
8. `pytest tests/e2e/api -q` green (the 24 scenarios of §7), then
   `python scripts/spec_contract_check.py --spec specs/060-api-rest.md` (compares routes and
   operationIds of `/openapi.json` against §2.5).
9. `curl` of §2.7 against `hifa serve` on a database with 10 000 issues; verify REQ-N060-01.

## 9. Provenance

- `/api/v1` prefix and plural resources: standard REST convention; path versioning (not header
  versioning) is the only form an agent's `curl` can exercise without extra state.
- Keyset over offset: measured in the prototype — `LIMIT 50 OFFSET 9950` over 10 000 issues takes
  31 ms vs 3.8 ms for keyset, and offset duplicates rows if something is inserted between pages.
- `def` and not `async def`: the core is synchronous (SQLite, no async driver) and SPEC-000 A8 forbids
  extra dependencies; the AnyIO threadpool already gives enough concurrency for a local process.
  Measured: 56 MB RSS with 1 worker (REQ-N002).
- `/docs` disabled: Swagger UI 5 loads `swagger-ui-bundle.js` from `cdn.jsdelivr.net`, which
  contradicts REQ-N004/REQ-N005; vendoring it would cost ~1.1 MB for a marginal use case.
- `409` shared between `conflict` and `invalid_transition`: both are the same family — the server
  refusing a write because the current state does not allow it — and 409 is the status clients
  already retry differently from a 400.
- `total_estimate: null`: `COUNT(*)` violates the REQ-N008 budget; the envelope reserves the key
  so as not to break clients in v2.
- `error.field`/`error.details`: the shape of Stripe/GitHub validation errors, chosen over
  FastAPI's `{"detail": [...]}` because an agent needs a stable field to retry.
- The API/dashboard separation (the browser never calls `/api/v1`) is the author's decision,
  2026-11-08: it removes CORS, tokens and a serialization layer from the UI's critical path.

## 10. Out of scope

- Auth, API keys, rate limiting, quotas, multi-tenant (REQ-S005).
- CORS, cookies, CSRF: there is no browser client for this API.
- Batch/bulk endpoints, mass `PATCH`, `POST /issues/bulk`.
- Webhooks, SSE, long-polling, GraphQL.
- `/docs` and `/redoc` (see R7), vendored documentation client.
- Computation of `progress`/`scope`/`velocity` beyond what `v_team_progress` exposes.
- File upload: `attachments` are links (SPEC-000 §1).

## 11. Notes

- spec_revision 15 (2026-11-20): **C4's "unique per team" is false for a label name, and `details` keeps
  two shapes (HIFA-14, round two of the label review, D-070's amendment).** `version` 7 → 8: §2.2's C4 is
  a convention of the public contract and its label clause changes.
  (1) **C4 justified accepting a name with a uniqueness the schema does not impose.** `uq_label_team`
  and `uq_label_ws` compare **binary**, so `Bug` and `bug` coexist at one level; the sentence now says
  the name resolves case-insensitively in the issue's team and then in the workspace, that identical
  spellings at one level are refused by SPEC-040 R22 (`LABEL_AMBIGUOUS`, 422) instead of picked, and
  §2.5.1 publishes the same 422 for `POST`/`DELETE /issues/{ref}/labels`.
  (2) **`details` was described with two shapes and one section implied a third.** §6.3 now names
  `LABEL_AMBIGUOUS` explicitly: its `details` is `null`, the spellings and level keys travel in
  `message`, and the mapping that carries them is the exception's accessor (`exc.details`) that this edge
  does not serialize (SPEC-020 §2.6, SPEC-040 R22, D-062). No envelope key is added or removed; §7 stays
  at 26 scenarios.

- spec_revision 14 (2026-11-20): **`q` does not move the order, and §2.4's config rule now says what the
  models actually do (HIFA-7, third review round).** `version` 6 → 7: §2.4's normative instruction
  changes (which models carry `from_attributes`) and R9 is the rule a client reads.
  (1) **R9 refused something this document does not declare.** It said "an explicit `order` with `q` ⇒
  422", against SPEC-090 §2.5 — which accepts `sort` with `q` — and against §2.6 of this document, whose
  own `order` rule is the listing's keyset order and has nothing to do with a search. The sentence is
  replaced: `q` does not change the order and no `order`/`sort` value is refused because `q` is present;
  the endpoint declares `sort` (the five values of SPEC-090 §2.5, valid with or without `q`) and no
  `order` parameter at all. `relevance` is what `score DESC, id ASC` means.
  (2) **§2.4's blanket rule was false.** "All output models use `model_config =
  ConfigDict(from_attributes=True)`" was not true of `IssuePage`, `TeamList`, `HistoryEntry`,
  `TeamProgress`, `HealthOut`, `ErrorBody`/`ErrorEnvelope` or the eight nested references, and revision
  13 restated it as "mandatory for every output model". The rule is now the truthful one — the config
  belongs to the models that validate a use case's dataclass, named one by one — with the reason the
  others carry neither config (composed field by field, so `from_attributes` would declare a coercion
  path nothing uses) and the reason the error bodies are declared without it on purpose (they must not
  accept an arbitrary exception as their source, which would make any object with those attribute names
  envelope-shaped). No model gains or loses a field, and no error body gains config. §2.6's sentence
  under `SearchPage` follows.
- spec_revision 13 (2026-11-20): **`q` on the issue listing has one meaning, and it is not the search's
  (HIFA-7, second review round).** `version` 5 → 6, because §2.5.1's filter table and §2.6's order rule
  both change.
  (1) **§2.5.1 and §2.6 published an FTS5 search on a route that has none.** They said `q` was "FTS5
  search delegated to SPEC-090" and that "with `q` the default `order` becomes `relevance`", which
  contradicts SPEC-090 §2.5, D-025, SPEC-030 §2.6 and SPEC-040 §4 — every one of them says the listing's
  `q` is a `LIKE '%term%'` over `title` — and `relevance` is not even a member of this document's own
  `OrderField` (§2.4). `q` is now the title filter of D-025 and nothing else: no `MATCH`, no `backend`, no
  ranking, no band, and no effect on `order`, with the sentence pointing at the route that does search
  (§2.5.4).
  (2) **`SearchHit` and `SearchPage` now declare `from_attributes`.** §2.4 as it then read made it
  mandatory for every output model (revision 14 narrows that sentence to the models that validate a
  dataclass, which is exactly this pair) and these two, added in revision 12, carried no `model_config` —
  while the use case returns SPEC-090's frozen dataclasses, so the schemas could not validate what they
  were written to publish. One sentence under them states that `model_validate` over the dataclasses
  **is** the conversion.
  (3) **A citation of the note below pointed at §2.2 for a class §2.4.4 declares**; it now says §2.4.4.
- spec_revision 12 (2026-11-20): **the search hit this edge publishes is the one SPEC-090 declares, flat
  and eleven fields, and a refused search keeps its own code (HIFA-7 review round, D-064).** `version`
  4 → 5, because the `SearchHit` of §2.4.4 changes shape:
  (1) **`SearchHit(issue: IssueOut, snippet, score)` is gone.** It was the nested shape D-064 retired and
  this document never heard of the decision — D-064's `Touches:` did not name SPEC-060, so it got no
  `spec_revision` bump in that round. It now publishes the eleven flat fields in the order of SPEC-090
  §2.2 — `issue_id, identifier, title, state_name, state_type, priority, due_date,
  assignee_display_name, updated_at, snippet, score` — with no `issue` wrapper, because every consumer
  (the CLI table of SPEC-050 §2.4.1.1 and the page's read model of SPEC-070 §2.3.5) reads those eleven
  and nothing a nested 31-key `Issue` would add. §2.6's comment and §7's assertion follow: the scenario
  now asserts the eleven keys **and** that no hit carries `issue`.
  (2) **One refusal, one envelope.** §2.5.4 published `422 validation_error` with `field` ∈
  `{invalid_filter, invalid_limit, query_empty}` — a code list in the `field` position — while SPEC-090
  §6 publishes those four as `error.code` with `field` naming the input. §2.5.4, §5.3 and §6.1 now say the
  same thing: the code is `invalid_limit` / `invalid_filter` / `query_too_long` / `query_empty`,
  `error.message` is SPEC-090 §6's sentence for it, and `error.field` is the **input** that carried the
  refusal (`q`, `limit`, `offset`, `priority`, `due`). §6.1 gains the row, §5.3's mapping says when a
  `Validation` passes its own lowercase code through, and §2.5.4 says that the edge composes the
  `SearchQuery` and passes its clock's instant (`search_issues(db, query, now=…)`, SPEC-090 §2.3). The
  first row of §6.1 still owns `limit` out of range **on a listing**, which is `validation_error` and is
  not the search.
  (3) **The §2.7 sample showed markers no layer produces and a shape the contract no longer has.** It
  rendered `**login** with **SSO**` — bold markdown, which contradicts R15 — and nested an `issue` object
  per hit. It is now the flat hit with the literal `<mark>`/`</mark>` of SPEC-090 R15, unescaped at this
  edge, with a sentence saying why. §7 goes 25 → **26** scenarios (the 422 one is new) and none is
  removed.
- spec_revision 10 (2026-11-20): **this edge publishes transport codes, and its 404s publish the
  resource (HIFA-14, D-061).** (1) §2.5.3 named `label_team_mismatch` and `label_retired` in the
  position where §6.1's catalog holds `error.code`, and that catalog has no such codes: the paragraph
  now publishes 422 `validation_error` with `rule='LABEL_TEAM_MISMATCH'` and 409 `conflict` with
  `rule='LABEL_RETIRED'` — the transport code in `error.code`, and the rule as the **name SPEC-040 §6
  gives it**, quoted the way §2.5.3 already quotes `LABEL_IN_USE` and rendered to the client in
  `error.message` (in `error.details[].message` when one refusal reports several). No section of this
  document declares a `rule` member: §5.3 maps exception classes to statuses and codes, and §6.3's
  envelope carries `code`, `message`, `field` and `details` — so the name travels as text, never as a
  key. (2) The same paragraph published only
  `field: "label_link"` for the labels subresource while SPEC-050 §2.4.5 declared both of its 404s with
  `field: "label"`; the values are settled by **what is missing** — a reference that resolves nowhere
  is `label`, a label the issue does not wear is `label_link` — and both edges read them from the
  derived `NotFound.field` (SPEC-020 §2.6, §5.3) instead of choosing one. That makes `field` of a
  missing issue the resource `issue` instead of the `ref` §6.1 used to publish, so §5.3, §6.1's
  `not_found` row, §6.3's envelope sample and §7's 404 scenario say so. `version` 4: no route, status
  code or schema changes, but §2.5.3's `error.code` for two inputs of `POST|DELETE
  /api/v1/issues/{ref}/labels` moves — `label_team_mismatch` → `validation_error` and `label_retired` →
  `conflict` — and a code a client branches on is §2 contract: that is the bump this entry owed and did
  not take until revision 11.

- spec_revision 11 (2026-11-20): **round 3 over this delta: the 404 example, the address of the rule and
  the counts (HIFA-14, D-061, D-062).** (1) §7's "the 404 uses the error envelope" still asserted
  `ref` as the `error.field` of `GET /api/v1/issues/ENG-999`, while §5.3, §6.1, §6.3 and §2.7 of this same
  document — and SPEC-020 §2.6, which D-062 records — derive that value from the resource: the scenario
  now asserts `issue`, and it was the one assertion the revision-10 cascade missed. The only `ref` this
  document still publishes is §6.1's `invalid_transition` row, which is the explicit
  `InvalidTransition(rule='ISSUE_ARCHIVED', field='ref')` of SPEC-040 §6 and not a 404. (2) The
  revision-10 note said the rule name sat "where §5.3 and §6.3 put it" and neither section declares a
  `rule`: the name is the one SPEC-040 §6 gives the rule and it reaches the client in `error.message`.
  (3) `version` 4, for the reason the entry above now carries.

- spec_revision 9 (2026-11-20): **"label in use" is the rule the use case applies, not a narrower one
  (HIFA-14, D-061).** §2.5.3 said `issue_label` ≥ 1 row *and the issue not archived*, while
  `retire_label` refuses when `LabelRepo.count_uses > 0` — every layer, archived and trashed issues
  included (SPEC-040 R22). The parenthetical described a set the port cannot see and no use case
  computes; it is gone, the `409` stays, and what the revision settles is the set that triggers it.
  `version` 3: the condition of a published `409` is §2 contract, and a client that archived its issues
  to free a label now learns it cannot.

- spec_revision 8 (2026-11-13): **the cursor carries its direction, and the token is the one SPEC-030
  §2.6.2 defines.** §2.3 said the cursor "does not encode the direction" and that a `.asc`/`.desc` switch
  was undetectable, while SPEC-030 §2.6.2 and SPEC-040 §5.3 both reject a token from the other direction.
  The parameter row and the rule now publish `base64url("<order>:<asc|desc>|<ULID>")` with the direction
  in lowercase, so switching direction with a stale cursor is 422 `INVALID_CURSOR`, and the causes of
  that error list a malformed ULID, as SPEC-030 §2.6.2 does. No parameter, status code or schema
  changed, so `version` stays at 2.
- spec_revision 7 (2026-11-13): **the keyset predicate at the `NULL` edge is the one SPEC-030 §2.6.1
  declares (D-044).** §2.3 already required `NULL` last in `asc` and in `desc`; it then described the
  cursor as the tuple comparison `(key, id) > (k, id)` plus a rule for `NULL`, which returns no row
  when the anchor has no value and ends the listing silently at the first undated issue. The bullet now
  names the two forms — the valued anchor with its three branches and the `NULL` anchor passed by `id`
  alone — and says explicitly that the tuple comparison is not the clause. R4 carries the same reading.
  No parameter, status code or schema changed, so `version` stays at 2.
- spec_revision 6 (2026-11-13): **the error map is the one SPEC-040 §6 publishes (D-042), so `version` 2.**
  `invalid_transition` describes its only producer — a write on an issue whose `archived_at` is not null
  (R20) — and carries `field: ref`, the name of the parameter on every surface. A refused state machine,
  which SPEC-020 expresses as `Validation` (`CLOSE_WITH_OPEN_CHILDREN`, `STATE_ARCHIVED`,
  `STATE_TEAM_MISMATCH`, `ISSUE_TRASHED`), is `validation_error` 422: the `move` example of §6.3, the
  message catalog of §6.2 and the `move`-to-Done scenario of §7 say 422, and §6.2 gains the message of the
  archived case.
- spec_revision 5 (2026-11-11): **`TeamCreate.key` stops disagreeing with the domain (D-030).** The
  field carried a private copy of the key pattern, `^[A-Z][A-Z0-9]{1,4}$` (2-5 characters), while
  SPEC-020 §2.2 owns `KEY_PATTERN` = `^[A-Z][A-Z0-9]{1,9}$` (2-10) and SPEC-100 §2.3 seeds its
  default key from the same range. A 6-character key was therefore legal for `hifa team create` and
  a `422` through `POST /api/v1/teams`. The field now references the owner's pattern (with the
  explicit `min_length`/`max_length` that make the intent readable), normalizes before matching
  (`strip` + `upper`, so `ops` and `OPS` are the same team) and drops `parent`, which `CreateTeam`
  of SPEC-040 does not carry: an API field the use case cannot apply is not a contract, it is a
  field that silently disappears.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
- **spec_revision 3 (2026-11-08, Fix Agent, spec-review-03):** application of **D-015** and **D-016**
  to the 2 live blockers of the previous round.
  - **D-015 — deletion.** Physical deletion was removed from §2 (R8, line 876) and from §2.5.1 (the row
    `DELETE /api/v1/issues/{ref}`, line 538): `DELETE` is **soft only** ⇒ 200 `IssueOut` with
    `trashed: true`, with no query params and no `204`; `?hard` is rejected with 422 `validation_error` and
    `field: "hard"` (compaction is `hifa db vacuum`). The §7 scenario that tested
    `DELETE ?hard=true` ⇒ 204 was rewritten to the real behavior (§7, line 1112) without losing
    scenarios: it now asserts the invariant `COUNT(*)` and the 422 of the unknown flag.
  - **D-016 — search.** The §2.7 example (lines 815-819) uses the envelope of **SPEC-090 §2**
    `{hits, total, has_more, backend, took_ms}` with a **positive** `score` (`0.82`, `0.37`, higher =
    better) instead of the invented envelope `{items, next_cursor, total_estimate}` with a negative `score`.
    `SearchPage` (§2.6, line 671) and the `GET /api/v1/search` row (§2.5.4, line 620) were
    aligned to SPEC-090: `limit` **1-200 default 50** (it was 1-100/20), `q` up to 512 chars, the 11
    filters of SPEC-090 §2 (`team`, `state`, `assignee`, `label`, `priority`, `due`, `is` +
    negations), `offset`, `all`, `count` and `sort`; R9 defers to SPEC-090 as the authority and R3
    declares `/search` as the single exception to the list envelope. A scenario was added that asserts the
    envelope and that no `score` is negative (§7, line 1057).
  - §7 scenario count: **24** (23 previous, none removed; the search one was added).
