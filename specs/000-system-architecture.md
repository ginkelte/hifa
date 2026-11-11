---
id: SPEC-000
title: System architecture
component: support
status: approved
version: 3
spec_revision: 9
depends_on: []
implements:
  - pyproject.toml
  - .importlinter
acceptance: specs/acceptance/000-system-architecture.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
---

# SPEC-000 — System architecture

## 1. Responsibility and boundaries

`hifa` is a local issue tracker: CLI + SQLite + web dashboard, built on a complete issue-tracker
model (teams with key/emoji, issues with identifier, priority 0-4, labels, assignee, due date,
sub-issues, cycles, projects). No mandatory server, no account, no cloud.

**Does NOT do (explicit):**

- It is not truly multi-user: there is no auth and no network by default.
- It does not sync with anything, has no AI, does not manage files (attachments are links).
- It does not define the schema (SPEC-010), nor the commands (SPEC-050), nor the endpoints (SPEC-060), nor the
  views (SPEC-070): it enumerates them by reference in §2.

## 2. Public contract

This is the contract every edge implements identically. Changing it is a `version` change.

```toml
# pyproject.toml
[project.scripts]
hifa = "hifa.cli.main:app"

[project.optional-dependencies]
web = ["fastapi", "uvicorn[standard]", "jinja2"]
```

| Surface | Owner | Contract |
|---|---|---|
| CLI commands and flags | SPEC-050 §2.5 | names, arguments, `--json`, `--quiet` |
| HTTP endpoints | SPEC-060 §2 | `/api/v1/*` (including `/health`) and Pydantic schemas |
| Dashboard routes, fragments and `/healthz` | SPEC-070 §2.1 | `/{team}/board`, `/{team}/list`, `/_/*`, `/healthz` (D-028) |
| Database schema | SPEC-010 §3 | tables, constraints, views, migrations |
| Domain errors → edge | **§6 of this spec** | exit code and HTTP status |
| Resource budgets | §4.4 | RSS, latency, size |

Exit codes (D-014, normative for every edge except `hifa doctor`):

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | **warn** — reserved for `hifa doctor` (there are warnings, nothing broken) |
| 2 | validation / invalid configuration |
| 3 | not found |
| 4 | conflict (uniqueness, version) |
| 5 | invalid state transition |
| 6 | internal error, corrupt database or unusable environment |
| 130 | interrupted (SIGINT) |

`hifa doctor` **extends** this map (`0` ok, `1` warn, `2` fail, `3` not diagnosable) and is the
only documented exception (D-001, SPEC-100 §6). No command uses `1` to report a bug: that
is `6`.

## 3. Data it touches

**N/A in this component.** SPEC-000 neither reads nor writes tables. It governs two files:

| File | What this spec owns | What it does not own |
|---|---|---|
| `pyproject.toml` | the packaging keys: `[build-system].build-backend`, `hatchling` in `requires`, `[project]` name/description/readme/`requires-python`/dependencies/extras/scripts, `[tool.hatch.build.targets.wheel]`, `[tool.ruff]`, `[tool.pytest.ini_options]` | the version-producing keys — `[project].version` (the **literal**, written only by the release job) and the absence of `dynamic`, of `hatch-vcs` and `setuptools-scm` in `[build-system].requires`, of `[tool.hatch.version]`, of the `python-semantic-release` row in `[dependency-groups].dev` and of `[tool.semantic_release]`, which belong to SPEC-110 §2.1/§2.5/§2.8 by **D-038** and **D-039** |
| `.importlinter` | all of it | nothing |

- **`src/hifa/__init__.py` is not governed here at all**: by D-038 the module is SPEC-110's contract in
  full (SPEC-110 §2.1), which is why it is absent from this spec's `implements:`.
- **`implements:` is glob-based**, so `pyproject.toml` legitimately appears in the `implements:` of
  both specs. The attribution of a diff is by **key**, not by file: a change inside a key listed in
  SPEC-110 §2.9 needs a SPEC-110 `spec_revision` bump, any other change in the file needs a SPEC-000
  `spec_revision` bump, and moving a key from one column to the other needs both (D-038). The
  `specs/INDEX.md` row of each spec repeats which part it claims, so the rule is applied without
  reading prose.

The schema, its invariants and the migrations belong to SPEC-010 §3; data access exists only in
`adapters/sqlite/` (A3).

## 4. Business rules

### 4.1 Architectural rules (A1-A8, normative)

- **A1 — Unidirectional dependency.** `cli/`, `api/`, `web/` → `app/` → `domain/` ← `adapters/`.
  Nobody imports "upward". Verified by `import-linter` in CI (contract `forbidden`).
- **A2 — `domain/` is pure.** Importing `sqlite3`, `fastapi`, `typer`, `click`, `jinja2`,
  `httpx`, `os`, `time`, `datetime.now` is forbidden (time enters through the `Clock` port).
- **A3 — A single persistence engine.** SQLite with WAL, `foreign_keys=ON`, `busy_timeout=5000`.
  Data access exists **only** in `adapters/sqlite/`.
- **A4 — The CLI works without a server.** No `hifa` command (except `hifa serve`) opens the network.
- **A5 — The dashboard is optional.** FastAPI/uvicorn live in the `[web]` extra; the core and the CLI
  do not import them.
- **A6 — A single output formatter.** Table / `--json` / `--quiet` are resolved in
  `cli/output.py`; no command prints on its own.
- **A7 — A single error mapping.** `domain.errors` → exit codes (CLI) and HTTP status (API) in
  `cli/errors.py` and `api/errors.py`, with the same table: the one in **§6**. No edge invents codes.
- **A8 — No ORM, no event bus, no DI container, no queues, no cache.** Any exception
  requires an ADR and a bump of this spec's `version`.

### 4.2 Layers and responsibilities

| Layer | Module | Responsibility | Forbidden |
|---|---|---|---|
| CLI entry | `hifa/cli/` | parse args, call use cases, format | business logic, SQL |
| HTTP entry | `hifa/api/` | validate payload, map errors, serialize | business logic, SQL |
| HTML entry | `hifa/web/` | render read-models, htmx fragments | business logic, SQL |
| Application | `hifa/app/` | use cases, transactions, composite invariants | raw SQL, HTTP, print |
| Core | `hifa/domain/` | entities, enums, rules, ranking, errors | IO of any kind |
| Ports | `hifa/ports.py` | Protocols for repos, UoW, Clock, IdGen | implementations |
| Persistence | `hifa/adapters/sqlite/` | connection, UoW, repos, migrations, reads | business rules |

### 4.3 File structure (contract)

```
src/hifa/
├── domain/{enums,entities,ranking,identifier,rules,errors}.py
├── ports.py
├── app/{commands,issues,teams,labels,projects,cycles,boards,search,transfer}.py
├── adapters/sqlite/{connection,uow,migrate,issue_repo,team_repo,label_repo,
│                    project_repo,cycle_repo,comment_repo,history_repo,reads}.py
├── adapters/sqlite/migrations/00X_*.sql
├── cli/{main,output,errors,context,refs}.py + cli/commands/*.py
├── api/{app,schemas,deps,errors}.py + api/routers/*.py
├── web/{app,readmodels,markdown}.py + web/templates/** + web/static/{css,js,vendor}/**
└── config.py
```

The three `web/` modules, one responsibility each (D-028):

| Module | Responsibility |
|---|---|
| `web/app.py` | the application factory (`create_app`, `install`) and the router: one handler per route of SPEC-070 §2.1, the `HX-Request` guard, `StaticFiles` at `/static` and `/healthz`; it mounts on the FastAPI app of SPEC-060, it does not create one |
| `web/readmodels.py` | pure edge read models (`build_board_vm`, `build_list_vm`, `build_issue_vm`, `build_row_vm`, `build_card_vm`): dicts for the templates, no IO, no SQL, no `sqlite3` |
| `web/markdown.py` | markdown render + own allowlist for descriptions and comments (REQ-S003, D-020): `markdown-it-py` with `html=False`, then the tag/attr/scheme filter |

There is **no separate `views.py`**: the route handlers live in `web/app.py`, next to the factory that
mounts them (D-028). Where SPEC-001's task list or an older revision of another spec still says
`web/views.py`, that is the old name of this module.

Changing this structure is a contract change: `version` bump. Specs 010-100 and their
`implements:` use exactly these paths.

### 4.4 Non-functional requirements

- **REQ-N001** — The CLI process must not exceed **120 MB RSS** in normal operations (listing
  10,000 issues included). SPEC-001 declares 80 MB for v1: the stricter one wins.
- **REQ-N002** — `hifa serve` must not exceed **150 MB RSS** with the app loaded (measured: FastAPI +
  uvicorn idles at 56 MB on the reference host). SPEC-001 declares 120 MB: the stricter one wins.
- **REQ-N003** — CLI startup < **250 ms** (p50) warm; < 500 ms cold. SPEC-001 measures
  p95 per command.
- **REQ-N004** — All functionality (except `serve`) must operate **offline**, without network.
- **REQ-N005** — No external assets: CSS/JS vendored in `web/static/vendor/`, zero CDN.
- **REQ-N006** — The UI works without JavaScript for reading and navigation (progressive enhancement).
- **REQ-N007** — Python ≥ 3.11. **Base** dependencies (the core and the CLI import nothing else):
  `typer`, `pydantic`, `markdown-it-py`, `python-ulid`. Rationale: `markdown-it-py` renders
  the markdown of descriptions and comments (REQ-S003, D-020: it goes in the base group, not in `[web]`,
  because the dashboard is not the only consumer) and the resulting HTML is filtered with
  `web/markdown.py`'s own allowlist (~60 lines, no new dependency — location fixed by D-028: it is a
  helper of the render edge, not of `app/`); `python-ulid` is the
  ID generator of SPEC-010 D1. `fastapi`, `uvicorn[standard]` and `jinja2` live **only** in the
  `[web]` extra (A5).
- **REQ-N008** — Listing 10,000 issues with labels in < **300 ms** (one query, no N+1).

### 4.5 Security requirements

- **REQ-S001** — `hifa serve` listens on `127.0.0.1` by default; exposing it on `0.0.0.0` requires
  an explicit `--host` and emits a warning on `stderr`. There is no additional confirmation flag and no exit
  code other than 0 (D-014 reserves the codes for errors).
- **REQ-S002** — All SQL uses bound parameters (`?`); interpolating strings into SQL is forbidden.
- **REQ-S003** — The markdown of descriptions is rendered with `markdown-it-py` and filtered with
  `web/markdown.py`'s own allowlist (allowed tags and attributes, `href` only `http`/`https`/
  `mailto`, no `javascript:` nor `on*`), in that order and only once (D-020; module location per
  D-028 — the allowlist and the order are unchanged). No third-party
  sanitizer is added.
- **REQ-S004** — File paths (import/export, attachments) are resolved and validated against the
  allowed directory; rejection of `..` and of symlinks that escape.
- **REQ-S005** — `hifa serve` does not implement auth in v1: hence the local bind by default, the
  warning when exposing and `--read-only` being available. There is also no CSRF token or `Bearer`: the only
  guard on dashboard writes is `HX-Request` (SPEC-070 R5).

## 5. Flow

Nominal traversal of an operation, identical for the three edges:

```
1. The edge (cli/ | api/ | web/) parses and validates the shape of the input. No business logic here.
   Shape error ⇒ Validation/InvalidOption ⇒ §6 (exit 2 / 422).
2. The edge builds the UoW (adapters/sqlite/uow.py) and calls a use case from app/.
3. app/ resolves entities through the Protocols of ports.py, applies the domain/ rules,
   opens the transaction (BEGIN IMMEDIATE) and calls the repos.
4. domain/ decides (enums, ranking, identifier, transitions). It performs no IO and knows no SQL.
5. adapters/sqlite/ executes the parameterized SQL and returns models.
6. app/ does COMMIT (or ROLLBACK if something raises). Retries live outside the contextmanager
   (D-018: app/retry.py::with_write_retry).
7. The edge formats: cli/output.py (table | --json | --quiet), api/schemas.py, or the Jinja
   template. A single representation per entity (SPEC-050 R2).
8. Any HifaError is translated in the edge's mapper with the §6 table.
```

Startup: `hifa --version` does not open the database nor import `fastapi`. `hifa serve` imports `uvicorn`
deferred inside the command (A5) and does not migrate: the schema is administered by the CLI (SPEC-010 §5).

## 6. Errors

Normative map (A7). The CLI column is the exit code; the HTTP column the status of SPEC-060.

| Domain error | When | CLI | HTTP | `error.code` |
|---|---|---|---|---|
| `Validation` | invalid input (flag, date, status of another team) | 2 | 422 | `validation_error` |
| `InvalidOption` | incompatible flags (`--json` + `--quiet`, `--before` + `--after`) | 2 | 422 | `invalid_option` |
| `MissingContext` | unresolvable team/member | 2 | — | `missing_context` |
| `InvalidConfig` | invalid `.hifa.toml` | 2 | — | `invalid_config` |
| `NotFound` | nonexistent entity | 3 | 404 | `not_found` |
| `DatabaseNotFound` | missing SQLite file | 3 | 503 | `database_not_found` |
| `Conflict` | uniqueness, references in use, `schema_too_new` | 4 | 409 | `conflict` |
| `InvalidTransition` | illegal state change | 5 | 409 | `invalid_transition` |
| `MissingWebExtra` | `serve` without the `[web]` extra | 6 | — | `missing_web_extra` |
| `CorruptDatabase` | `SQLITE_CORRUPT` / `SQLITE_NOTADB`, `integrity_check` fails | 6 | 500 | `corrupt_database` |
| unmapped `Exception` | bug | 6 | 500 | `internal_error` |
| `KeyboardInterrupt` | Ctrl-C | 130 | — | `interrupted` |
| **`hifa doctor`** | diagnostic findings (exception D-001) | 0 ok / 1 warn / 2 fail / 3 not diagnosable | — | see SPEC-100 §6 |

Map rules: the exit code never depends on the output format; with `--json` the error goes to
`stderr` as `{"error":{"code","message","field"}}` and `stdout` stays empty (SPEC-050 §2.4.5). The
`error.code` codes are stable for scripts: they are not renamed within a `version`.

## 7. Acceptance Criteria

```gherkin
@SPEC-000
Feature: Architectural rules

  Scenario: domain does not import infrastructure
    Given the package installed in editable mode
    When import-linter is run with the "domain is pure" contract
    Then the contract passes without violations

  Scenario: the CLI works without the web extra installed
    Given an environment with only the base dependencies
    When "hifa issue list" is run
    Then the command responds with exit code 0
    And the process does not import fastapi or uvicorn

  Scenario: serve listens only on loopback by default
    Given a migrated database and the web extra installed
    When "hifa serve" is run without flags
    Then the socket listens on 127.0.0.1
    And the default port is 7777
```

Coverage of the requirements that have no scenario of their own (they are verified in the owning spec):

| Requirement | Verifies |
|---|---|
| REQ-N001, REQ-N002, REQ-N003, REQ-N008 | SPEC-001 §4.2 (benchmarks of T-17) |
| REQ-N004, REQ-N005, REQ-N006 | SPEC-070 §7 and SPEC-001 §4.2 (REQ-N003) |
| REQ-N007 | §8 step 1 (`pyproject.toml`) + AC "the CLI works without the web extra" |
| REQ-S001, REQ-S005 | SPEC-000 AC + SPEC-070 §7 (bind, `HX-Request`) |
| REQ-S002 | SPEC-090 §7 (hostile query) and SPEC-030 (parameterized SQL) |
| REQ-S003 | SPEC-001 REQ-S004 (sanitization) and SPEC-070 §7 |
| REQ-S004 | SPEC-080 §7 (import/export paths) and SPEC-001 REQ-S001 |

## 8. Regeneration checklist

1. `pyproject.toml` with `requires-python = ">=3.11"`, entrypoint `hifa = "hifa.cli.main:app"`,
   extras `web = [fastapi, uvicorn[standard], jinja2]`, base deps `typer`, `pydantic`,
   `markdown-it-py`, `python-ulid` (REQ-N007; no third-party sanitizer, D-020). The distribution
   `version` is a **literal** in `[project]` written only by the release job
   (`chore(release): X [skip ci]`): its value, its bump and the ban on deriving it from git belong to
   SPEC-110 §2.1/§2.5.3 (D-038, **D-039**) — the backend must not carry `hatch-vcs`, `setuptools-scm`,
   `dynamic` or `[tool.hatch.version]`.
2. Create the tree of §4.3 with empty modules that respect the allowed imports.
3. `.importlinter` with the `domain`, `app` and `web` contracts (A1, A2, P8/D-032).
4. `scripts/spec_contract_check.py` implemented before writing features.
5. CI: ruff + pytest + import-linter + spec_contract_check. The pytest gate also **executes** the governed
   shell files of the release pipeline (`.github/release/tag-message.sh`,
   `.github/release/assert-outcome.sh`), because P8 is satisfied by a test that observes behaviour, not by a
   test that reads a document (SPEC-110 §2.9, D-040).

## 9. Provenance

- Data model: the domain vocabulary defined in SPEC-010 (storage) and SPEC-020 (domain).
- RSS benchmark: real measurement on the host (Flask 58 MB / FastAPI 56 MB / Django 67 MB).
- Stack decision and "Pydantic as single entity" decision: author's decision, 2026-11-08.
- Exit codes 0/1/2/3/4/5/6 + 130 and the `doctor` exception: D-014 and D-001 (2026-11-08).
- `markdown-it-py` + own allowlist in `web/markdown.py` in the base group: D-020 (2026-11-08);
  module location corrected in D-028 (2026-11-11), behaviour unchanged.

## 10. Out of scope

Multi-tenant, auth, sync, plugins, AI, binary attachments, notifications, webhooks.

## Notes

- spec_revision 9 (2026-11-11): **the CI gate of §8 step 5 now names the executable contracts it runs.**
  SPEC-110 `version: 3` moves the release component's two shell files from prose into governed,
  pytest-executed artefacts (`.github/release/tag-message.sh`, `.github/release/assert-outcome.sh`, D-040),
  and a regenerated system must keep them executable and tested — a checklist that only says "pytest" does
  does not say that. **Nothing else moves:** §3's ownership table is untouched (the packaging keys, the
  `[project].version` literal and the version-producing absences are still SPEC-110's by D-038/D-039), and
  **no public contract change: `version` stays at 3** — same backend, same base dependencies, same extras,
  same entrypoint. §8 step 1 now points at SPEC-110 **§2.5.3** for the bump mapping: §2.5.2 is engine
  behaviour and §2.5.3 is the table this key follows.
- spec_revision 8 (2026-11-11): **the version key is a literal again, and this spec's packaging contract
  follows (D-039).** SPEC-110 `version: 2` replaces `python-semantic-release` with the npm
  `semantic-release` engine and re-adds `[project] version = "X.Y.Z"` as the only product version
  literal, written by the release job. The consequence for this spec is a *narrowing of what it must
  not own*: the version-producing keys are now the literal plus the **absence** of `dynamic`,
  `hatch-vcs` and `setuptools-scm` in `[build-system].requires`, `[tool.hatch.version]`,
  `[dependency-groups].dev` and `[tool.semantic_release]` — all of them SPEC-110 §2.1/§2.5/§2.8. §3's
  table and §8 step 1 say so, and the drift rule of D-038 is unchanged (a diff inside a SPEC-110 key needs a SPEC-110
  `spec_revision` bump; any other diff needs one here). **No public contract change: `version` stays at
  3** — same backend (`hatchling`), same base dependencies, same extras, same entrypoint.
- spec_revision 7 (2026-11-11): **the version keys of the packaging contract belong to SPEC-110
  (D-038).** §3 no longer claims all of `pyproject.toml` / `src/hifa/__init__.py`: SPEC-000 keeps the
  backend, the base dependencies, the extras, the entrypoint, `wheel.packages` and the tool config,
  and `src/hifa/__init__.py` leaves `implements:` altogether (D-038 gives the module to SPEC-110 in
  full, so claiming it here was a second owner of the same file);
  SPEC-110 §2.1/§2.9 owns `[project].dynamic` (and the absence of `[project].version`), `hatch-vcs`,
  `[tool.hatch.version]`, `[dependency-groups].dev`, `[tool.semantic_release]` and the `__version__`
  symbol. §8 step 1 now says so explicitly and points at SPEC-110 §2.1. Drift on the shared file is
  attributed by key (D-038). **No public contract change: `version` stays at 3** — same backend, same
  dependencies, same extras, same entrypoint. *(The version-key column was rewritten by
  spec_revision 8 / D-039; the split itself stands.)*
- spec_revision 6 (2026-11-11): **the dependency contract covers the web edge (D-032).** §8 step 3
  names three `.importlinter` contracts — `domain`, `app` and `web` — so the dashboard can never
  import the CLI (P8, D-032). No public contract change: `version` stays at 3.
- spec_revision 5 (2026-11-11): **the file tree is contract again (D-028).** (1) §4.3 gains the three
  `web/` modules the dashboard shipped — `app.py` (factory + router + `/healthz`), `readmodels.py`
  (pure edge read models), `markdown.py` (render + allowlist) — each with one line of responsibility,
  and `views.py` is dropped from the tree because that module never existed: the handlers live in
  `app.py`. (2) REQ-N007 and REQ-S003 point at `web/markdown.py`: **a change of location, not of
  behaviour** — same `markdown-it-py` + `html=False` + own allowlist, same order, D-020 untouched.
  (3) §3's surface table is split so that `/healthz` belongs to SPEC-070 §2.1 and `/api/v1/*` +
  `/health` to SPEC-060, instead of one row claiming both. (4) §9's provenance follows the path.
  **`version` 2 → 3:** the tree is contract.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
- **spec_revision 3 (2026-11-08, Fix Agent, spec-review-03):** closure of the contradiction around
  markdown sanitization. REQ-N007 (§4.4), REQ-S003 (§4.5), the checklist of §8 and the provenance of
  §9 land on **a single** render dependency (`markdown-it-py`) plus the own allowlist of
  `app/markdown.py` (D-020; path since moved to `web/markdown.py`, D-028); the third-party sanitizer
  disappears from the base group. This aligns the spec
  with SPEC-070 §2.5, which already declared it that way. No public contract change: `version` stays at 2.
- **spec_revision 2:** gaps R1-02 (normative error map in §6), R1-03 (`When` in the
  `serve` scenario), R1-25 (§2 Public contract, §3 N/A data, §5 Flow and §6 Errors added; the
  rules A1-A8, layers, structure and REQ-N/S stay in §4), R1-26 (REQ → spec coverage table),
  R1-42 (`component: soporte`: the template enum has no `sistema`). Version 2 due to REQ-N007
  (base deps with `markdown-it-py` + `python-ulid`, D-020) and to the exit code map of
  D-014. Gaps R1-18/R1-19 were resolved by subordination: REQ-S001 (no ack flag, warning and
  exit 0) and REQ-S005 (no CSRF/Bearer; only `HX-Request` in the dashboard) are the ones this
  spec mandates, and SPEC-001 was aligned to them.
