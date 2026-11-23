# DECISIONS.md — cross-spec resolutions

**Append-only** record of decisions that affect more than one spec. Every entry states what was
resolved, who wins and which files were touched. When a spec changes because of a decision, its
`spec_revision` is bumped and its §Notes points to the corresponding `D-0NN`.

> Rule: a conflict between specs **is not resolved "in the code"**. It is resolved here, the losing
> spec is adjusted, and only then is it implemented.

---

## D-001 — `hifa doctor` has its own exit-code space
**Date:** 2026-11-08 · **Wins:** SPEC-100 §6 · **Touches:** SPEC-050 §2.5.1 and §6, SPEC-100

SPEC-050 assigned `0` (everything ok or warn) and `6` (some `fail`). SPEC-100 defines `0` ok, `1` warn,
`2` fail, `3` not diagnosable (linter-style scheme, lets `doctor` be used in CI as a gate).

**Resolution:** `doctor` is a **documented exception** to the global map of SPEC-000 A7: it uses
`0/1/2/3`. The rest of the commands keep `2` validation, `3` not found, `4` conflict,
`5` invalid transition, `6` internal. SPEC-050 references SPEC-100 and does not redefine the table.

## D-002 — SPEC-090 is the authority for the search contract
**Date:** 2026-11-08 · **Wins:** SPEC-090 §2 · **Touches:** SPEC-050 (command table, inline contract, `search_issues` signature)

There were two contracts for `hifa search`:

| | SPEC-050 (before) | SPEC-090 |
|---|---|---|
| `--limit` | default 20, max 100 | default 50, range 1-200 |
| `--json` envelope | `{items: [{issue,snippet,score}], next_cursor, has_more, total_estimate}` | `{hits: [{issue,snippet,score}], total, has_more, backend, took_ms}` |

**Resolution:** 090 wins (it owns the semantics and the ranking). SPEC-050 adopts
`--limit` 1-200 default 50 and the 090 envelope; the items keep `{issue, snippet, score}`.
`backend` (`fts|like|none`) and `took_ms` are mandatory in the output.

## D-003 — `hifa db reindex` exists and is the only corrective action for the index
**Date:** 2026-11-08 · **Wins:** SPEC-090 + SPEC-100 · **Touches:** SPEC-050 (command tree, `doctor` hint)

SPEC-050 declared its command tree "exhaustive and closed" and offered `hifa search --reindex`.
SPEC-090 and SPEC-100 assume `hifa db reindex`.

**Resolution:** the rebuild lives in `hifa db reindex` (database maintenance, not a search flag).
The `hifa doctor` hint points there. SPEC-050's command tree is no longer "closed":
adding a command is a contract change with a `version` bump.

## D-004 — Requirement ID space
**Date:** 2026-11-08 · **Wins:** this decision · **Touches:** all specs (convention, without renumbering content)

Three formats coexisted: `REQ-F001` (001), `REQ-F010…` (020/030/040) and `REQ-F050-01` (050-100).

**Resolution:**
- Canonical format: **`REQ-<F|N|S><NNN>-<NN>`**, where `NNN` = component number and `NN` = sequence.
- The **global** IDs `REQ-N001…` and `REQ-S001…` are **defined once in SPEC-000** and are
  referenced from any spec without redefining them (that is why they appear repeated: they are references).
- `SPEC-001` (vertical) uses `REQ-F001…REQ-F037` for the MVP scope.
- IDs already written are not renumbered: renumbering 300 IDs introduces more risk than value. New
  specs use the canonical format.

## D-005 — SPEC-001 (vertical) does not define contracts
**Date:** 2026-11-08 · **Touches:** SPEC-001 (header), SPEC-INDEX

`001-mvp-v1.md` describes the whole MVP (scope + 37 REQ-F + 106 scenarios) and includes a consolidated
DDL. On any divergence with a component spec, **the component wins** and the vertical is corrected.

## D-006 — Migration ledger
**Date:** 2026-11-08 · **Wins:** SPEC-010 · **Touches:** SPEC-010

`schema_migration(version, name, applied_at, checksum)` was added as **migration 002**; a `.sql`
edited after being applied aborts the migration because of the checksum. `team_previous_key` moved to **003**.
`PRAGMA user_version` remains the fast cursor.

## D-007 — Label commands added to the CLI
**Date:** 2026-11-08 · **Wins:** SPEC-050 · **Touches:** SPEC-050

`hifa issue label` needs existing labels and SPEC-060 exposes `/labels`, so SPEC-050
incorporated `hifa label add|list|archive` (they were not in the component's original assignment).

---

## Known pending items (they are not conflicts, they are debt)

| # | Debt | Owner | When |
|---|---|---|---|
| P-01 | **The per-component `.feature` files are missing** (`specs/acceptance/0NN-*.feature`): only the 001 one exists. Today the Gherkin lives inline in each spec. | Spec Reviewer / T-01 | before implementing each component |
| P-02 | `020`/`030`/`040` use flat IDs (`REQ-F010…`); they coexist with the canonical format per D-004. | — | on the next change to those specs |
| P-03 | `scripts/spec_contract_check.py` does not exist yet (SPEC-000 and SPEC-050 declare it). | T-01 | first implementation task |
| P-04 | `010` has 8 sections instead of the template's 10 (Public contract / Flow / Errors are missing). | — | when approving 010 |

---

# Round 2 — resolutions for the 104 gaps of the Spec Review

**Date:** 2026-11-08 · Review result: 3 FAIL (spec-review-01: 42 gaps / 24 blocking;
02: 28/9; 03: 34/18). The decisions here are **normative** for the Fix Agent: they are not re-discussed,
they are applied. Each one states which spec wins.

## D-008 — A single DDL: SPEC-010 is the only source
**Wins:** SPEC-010 · **Touches:** 001, 010

`001-mvp-v1.md` carried a parallel DDL that diverged in 12 points (invented fields
`team.last_issue_number`, `issue.previous_identifiers`; `issue`/`project` without `workspace_id`;
`workflow_state` without `UNIQUE(team_id, id)`; views with other names; `identifier` indexed in FTS;
`schema_migration` as migration 001 vs 002).

**Resolution:** **the DDL block in 001 is removed** and replaced by a reference to SPEC-010.
In 010 the following is fixed: `workspace_id` present in `issue` and `project`; `UNIQUE(team_id, id)` in
`workflow_state` (the composite FK of R4 depends on it); canonical views `v_issue`, `v_priority`,
`v_team_progress`; `identifier` as `UNINDEXED` in `issue_fts`; `schema_migration` = migration 002 and
`team_previous_key` = migration 003 **with its `CREATE TABLE`**; the count becomes **16 tables**.

## D-009 — Real validation of `due_date`
**Wins:** SPEC-010 · **Touches:** 010, 020

The `GLOB` accepts `2026-13-45`. `date(due_date) IS due_date` is added to the CHECK (SQLite returns NULL
for impossible dates) along with calendar validation in `domain.rules`. The AC of 010 is corrected to
test a real impossible date.

## D-010 — `label` uniqueness with `team_id` NULL
**Wins:** SPEC-010 · **Touches:** 010

`UNIQUE(workspace_id, team_id, name)` does not prevent duplicates when `team_id` is NULL (SQLite treats
NULLs as distinct). It is replaced by **two partial unique indexes**:

```sql
CREATE UNIQUE INDEX uq_label_team  ON label(workspace_id, team_id, name) WHERE team_id IS NOT NULL;
CREATE UNIQUE INDEX uq_label_ws    ON label(workspace_id, name)          WHERE team_id IS NULL;
```

## D-011 — Canonical keyset pagination
**Wins:** this decision (single contract for 030/050/060/070) · **Touches:** 030, 050, 060, 070

- **Order**: `manual | created | updated | priority | due` (default `manual`). Each order defines its
  `sort_key` and its final tie-breaker by `issue.id`.
- **Cursor**: `base64url(sort_key + "|" + issue_id)`, **opaque** to the client. Without a valid `sort_key`
  ⇒ `Validation("invalid_cursor")`.
- **`limit`**: 1-200, default 50, identical in CLI, API and web.
- **`has_more`**: `limit + 1` is requested and then trimmed (a `COUNT(*)` is never done).
- **`offset`**: allowed **only** in the web (scroll paging) with a cap of 500; the CLI and the API use
  a cursor. `ListFilters(limit, cursor, order, include_archived)` in 030; 050/060/070 do **not** redefine it.
- The edges declare `limit ≤ 200`; the value 500 of web paging never leaves `070`.

## D-012 — SPEC-040 is the authority for the application port
**Wins:** SPEC-040 · **Touches:** 040, 050, 060, 070

050 declared ~25 functions that 040 did not have (`create_member`, `assign_issue`, `add_attachment`,
`delete_issue`, `search_issues`, `rebalance`, …). **They are added to 040** (the edges really need them)
with an exact signature, and 050/060/070 are adjusted to the names and signatures of 040. The direction of
the fix is always *toward* 040, never inventing signatures at the edge.

## D-013 — Canonical bootstrap (`hifa init`)
**Wins:** SPEC-100 · **Touches:** 050, 100

Exactly this is seeded: 1 workspace, 1 team, **6 workflow_states** — `Triage`(triage),
`Backlog`(backlog), `Todo`(unstarted, `is_default=1`), `In Progress`(started), `Done`(completed),
`Canceled`(canceled) —, 4 labels, 1 member + membership.
Single command: `hifa init [--name] [--team-key] [--team-name] [--icon] [--timezone] [--no-labels] [--force]`.
050 and 100 declare **the same** JSON envelope and the same flags; `.hifa.toml` has a single format
(the one in 100 §2.3).

## D-014 — Definitive exit-code map
**Wins:** this decision · **Touches:** 000, 020, 040, 050, 060, 100

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | **warn** — reserved for `hifa doctor` (there are warnings, nothing broken) |
| 2 | validation / invalid configuration |
| 3 | not found |
| 4 | conflict (uniqueness, version) |
| 5 | invalid state transition |
| 6 | internal error, corrupt database, or unusable environment |

`doctor` **extends** this map: `0` ok, `1` warn, `2` fail, `3` not diagnosable (D-001).
No spec may use `1` for "bug": that is `6`.

## D-015 — There is no physical deletion
**Wins:** SPEC-010/030 · **Touches:** 050, 060

`--hard` is removed from `hifa issue delete` and from the API. Deleting = `trash` (soft), archiving = `archived_at`.
The only compaction is `hifa db vacuum`, which does not delete logical rows.

## D-016 — Search contract: only SPEC-090
**Wins:** SPEC-090 · **Touches:** 050, 060, 070

050, 060 and 070 do **not** declare their own search flags or envelopes: they reference SPEC-090 §2
(`--limit` 1-200 default 50, 11 filters, envelope `{hits,total,has_more,backend,took_ms}`).
`score` is **higher = better** in every layer; `-bm25` is normalized inside 090 and no example may show a
negative `score`.

## D-017 — `src/hifa/ports.py` belongs to SPEC-030
**Wins:** SPEC-030 · **Touches:** 030, 020, 040

`ports.py` (repo Protocols, `UnitOfWork`, `Clock`, `IdGen`) is governed by 030:
`Clock.now() -> datetime` (UTC aware), `IdGen.new() -> str` (ULID), and the UoW exposes
`clock` / `idgen` as properties. 020 and 040 consume them without redefining them.

## D-018 — Retries outside the `@contextmanager`
**Wins:** SPEC-030 · **Touches:** 030, 040

`uow.write()` is a `@contextmanager` **without** internal retry (a generator cannot be re-executed).
The retry lives in `app/retry.py::with_write_retry(fn, attempts=3, backoff=ms)`, which reopens the UoW.
`next_number` retries **inside** the use case when it catches `UNIQUE(team_id, number)`.

## D-019 — Read models without shadowing
**Wins:** SPEC-020/030 · **Touches:** 020, 030, 070

`IssueView` uses `visible` (not `is_visible`): the homonymous field shadowed the base model's property and
produced two different values for the same object. The four read models declared by 070
(`board_view`, `list_view`, `issue_detail`, `agenda`) must exist with that signature in 030.
`IssueRepo.positions` accepts `state_id` to read positions of the destination state (drag & drop).

## D-020 — Gherkin and markdown
**Wins:** this decision · **Touches:** 001 (+ acceptance), 070, pyproject

- `# language: es` is **forbidden**: keywords go in English (`Feature/Scenario/Given/When/Then`).
- **One `.feature` file = one `Feature`**; the 17 areas of the MVP are expressed as `Rule:` inside the
  same Feature, or split into `specs/acceptance/001-mvp-v1/*.feature`. It must compile with
  `gherkin-official`.
- Description markdown is rendered with **`markdown-it-py`** + its own allowlist sanitization;
  the dependency is declared in `pyproject.toml` (base group, not in `[web]`).

---

# Round 3 — decisions from the group C review and cut-off criterion

**Date:** 2026-11-08 · Result: the 18 blocking findings of review 03 ended up with the fix applied
(verified by script), but **7 new regressions** appeared, 4 of them blocking.

## D-021 — The labels API follows the 040 port
**Wins:** SPEC-040 (§2) · **Touches:** 060

`PUT /issues/{ref}/labels` (full replacement) had no use case behind it: 040 only exposes
`add_label` / `remove_label`. It changes to `POST /issues/{ref}/labels` and
`DELETE /issues/{ref}/labels/{label}`. General rule: **no endpoint is added without a use case**;
if the operation seems missing, the use case is added in 040 first (D-012).

## D-022 — A single markdown renderer: `markdown-it-py`
**Wins:** SPEC-000 · **Touches:** 000, 070

`000` said `nh3` and `070` said `markdown-it-py`. Canonical: **`markdown-it-py`** (`html=False`) +
own allowlist of tags/attrs/schemes, declared in the base dependency group. `nh3` is forbidden
(D-020 stands).

## D-023 — Per-batch transaction in the import
**Wins:** SPEC-080 · **Touches:** 080

`COMMIT` inside the row loop contradicts the per-batch transaction. Canonical:
`BEGIN IMMEDIATE` on entering the batch → `SAVEPOINT` per row → `COMMIT` at the end; with `--strict` and
errors, full `ROLLBACK` and exit 2. `--dry-run` walks the same path and ends in `ROLLBACK`.

## Spec loop cut-off criterion (circuit breaker)

The pipeline has already done **3 review rounds and 4 fix rounds**. Rule decided by the orchestrator:

- A **structural blocker** (contract impossible to implement, two owners of the same symbol,
  operation without a port, prose that contradicts its own pseudocode) → **blocks** and is fixed.
- A **minor traceability or form issue** (missing `@REQ` tags, outdated counts, snippet marker,
  cross references) → **accepted debt**, recorded here and paid during the implementation of the task
  that touches that file.
- Specs must be **sufficient to regenerate the component**, not perfect. That is the standard of
  `specs/README.md`.

### Debt accepted on approval (P-05 … P-09)

| # | Debt | Owner | When |
|---|---|---|---|
| P-05 | The 6 specs of group C (050-100) have no `@REQ-F…` tags on their 134 scenarios: the REQ→scenario mapping is positional, not traceable by tag. | implementer of each component | when implementing each task the tag is added |
| P-06 | `050:1113` says "20 scenarios" (there are 26); minor count errors in other specs. | docs pass | when closing the MVP |
| P-07 | Snippet marker in 3 forms (`<mark>` in 090/070, `**` in 050/060) → unify to `<mark>`. | implementer of 090 | with T-11 |
| P-08 | `spec_contract_check.py` does not exist yet | T-01 | first code task |
| P-09 | Per-component `.feature` files (outside the MVP) do not exist; the Gherkin lives inline | — | if a component is regenerated |

## P-10 — Empty `--team-key ""` falls back to the default instead of failing (minor, non-blocking)
**Date:** 2026-11-08 · **Touches:** SPEC-050/SPEC-100 (in the future)

Detected in the T-04b Controller Gate: `hifa init --team-key ""` returns exit 0 and uses the
default `ENG`, when R15 requires the key to match `^[A-Z][A-Z0-9]{1,9}$` (an empty string does not
match). Genuinely invalid keys (`1NG`, `E`, `ENG-X`, `TOOLONGKEY123`) do fail with exit 2
and a correct message, and `eng` is normalized to `ENG` as R15 mandates.

Proposed fix: distinguish "flag absent" from "flag empty" in Typer (sentinel `None` vs `""`).
Low priority: it affects no data, only the error message.

## D-024 — All repository artifacts are in English
**Date:** 2026-11-11 · **Decision by:** the project owner

The whole repository switches to English: code, docstrings, comments, error messages, CLI
output, specs, README, `AGENTS.md`, commit messages and PR descriptions. This replaces the
previous convention (prose in Spanish, identifiers in English).

Rationale: a single language removes the mix that had already produced Spanish error strings
asserted by tests, and keeps the project usable for anyone reading only the code or only the
specs.

Scope: the conversation with the owner stays in Spanish, and historical review reports under
`specs/reviews/` keep their original language as dated evidence.

## P-11 — Config walk-up is poisoned by a stray `.hifa.toml` in an ancestor
**Date:** 2026-11-11 · **Touches:** SPEC-100 (§2.2), `hifa.config.find_config_file`

Symptom found while validating T-04b: a `.hifa.toml` left in `/tmp` made every project whose
working directory lives under `/tmp` resolve to that file's database, and 8 otherwise healthy
tests failed because of it. The walk-up climbs up to `/` and takes the first `.hifa.toml` it
finds, regardless of whether that directory is a project root.

Status: **fixed in T-05** — the walk-up stops at the first directory that is a project root
(contains `.git`) and never climbs past it, with a regression test using a decoy
`.hifa.toml` in a parent directory.

## D-025 — `q` in `ListFilters` is a title filter, not search
**Date:** 2026-11-11 · **Wins:** SPEC-070 §2.1.1 · **Touches:** SPEC-030 §2.6, SPEC-040 §5 and §6, SPEC-070 §2.1.1

The dashboard routes `GET /{team}/board` and `GET /{team}/list` accept `?q=` (SPEC-070 §2.1.1) as the
filter box of the board and of the list. SPEC-040 §5 rejected that parameter outright with
`Validation(rule='FILTER_UNSUPPORTED')`, so the two specs could not be implemented at the same time.

**Resolution:** `q` is a **title filter** and belongs to `ListFilters` (SPEC-030 §2.6):
`q: str | None = None`. Semantics, now written in the spec: `i.title LIKE ? ESCAPE '\'` with the term
wrapped as `%term%`, case-insensitive (SQLite `LIKE` on ASCII), and the `%`, `_` and `\` characters of
the user's term **escaped** before wrapping, so a literal `%` does not become a wildcard. An empty or
blank `q` is dropped (no filter applied), never an error. It filters **titles only**: descriptions,
comments, identifiers and the `field:value` grammar are workspace search, owned by SPEC-090 /
`search_issues`.

SPEC-040 loses the prohibition in its three places — the §5 pseudocode comment, the §5 flow rule
("`q` is not handled here …") and the `FILTER_UNSUPPORTED` row of the §6 error table — and the rule
disappears from the §6 use-case matrix, because no use case can raise it any more. In its place the
§5 flow says: "`q` is a title filter, not search: workspace search is SPEC-090 / `search_issues`".
SPEC-070 §2.1.1 states the semantics next to the route and its Notes point here.

**Rationale:** the alternative — filtering the board's box in client JS — breaks the one-query rule
(REQ-N008) and the no-JS fallback (REQ-N006); the alternative of sending the box to `/search` changes
an URL contract that SPEC-070 had already frozen. Keeping the rule `FILTER_UNSUPPORTED` alive with no
filter left to reject would have left a dead error code in the edge maps.

**Files:** `specs/DECISIONS.md`, `specs/030-sqlite-adapter.md` (§2.6), `specs/040-application-services.md`
(§5, §6), `specs/070-web-dashboard.md` (§2.1.1, Notes).

## D-026 — The read-model fields of §2.6 are declared and append-only
**Date:** 2026-11-11 · **Wins:** SPEC-030 §2.6 · **Touches:** SPEC-030 §2.6, SPEC-070 §2.3 (templates and read models)

`IssueListItem` grew `state_color`, `sort_order`, `board_order` and `created_at`, and `BoardGroup`
grew `color` and `truncated`, without §2.6 declaring them: a public contract did not describe the
dataclasses the edges were already consuming, so a regeneration from the spec would have produced a
board without colours and a listing without `created_at`.

**Resolution:** the six fields are **declared** in SPEC-030 §2.6 with their type and their origin:
`state_color` and `BoardGroup.color` come from the `JOIN` with `workflow_state` (`s.color`);
`sort_order` and `board_order` are the two existing columns of `issue` — backlog order and board
order, which are different orders of the same data (D-011); `created_at` is `issue.created_at`;
`truncated` is `len(items) > limit_per_group`, computed by the adapter with the same `limit + 1`
trick as the keyset page.

All six are **additive and carry a default** (`state_color="#6B6F76"`, `sort_order=0.0`,
`board_order=0.0`, `created_at=None`, `color=None`, `truncated=False`) and are appended at the end of
the dataclass, so a consumer that does not know them keeps working. They change no behaviour of
`list_view`, `board_view` or `issue_detail`, but the shape of a §2 dataclass **is** contract:
SPEC-030 goes to `spec_revision` 4 and `version` 2.

**Rationale:** a colour per state is a data-model fact (SPEC-010 `workflow_state.color`), not a
presentation invention, and the board ordering by `board_order` versus the listing ordering by
`sort_order` is also a data-model fact. Resolving either inside a template would put SQL semantics in
the Jinja layer, which SPEC-070 R3 forbids.

**Files:** `specs/DECISIONS.md`, `specs/030-sqlite-adapter.md` (§2.6, Notes),
`specs/070-web-dashboard.md` (Notes), `specs/INDEX.md`.

## D-027 — `list_board` keeps the SPEC-040 signature; the new edge functions are declared
**Date:** 2026-11-11 · **Wins:** SPEC-040 §2.2 · **Touches:** SPEC-040 §2.2, SPEC-030 §2.6, SPEC-070 §2.1.4

The dashboard implementation had replaced
`list_board(db, *, team, filters: ListFilters | None = None, group_by='state', limit_per_group=50)`
with six loose keyword arguments (`state`, `assignee`, `label`, `cycle`, `project`, `q`), moving filter
resolution up into the edges, and had shipped `require_team`, `resolve_filters`, `board_counts` and
`DETAIL_INCLUDE` without any of them existing in the contract.

**Resolution:** SPEC-040 wins and the signature is the one §2.2 already wrote:
`list_board(db, *, team: str, filters: ListFilters | None = None, group_by: Literal[...] = 'state',
limit_per_group: int = 50) -> Board`. Filters — `q` included (D-025) — travel inside **one**
`ListFilters`; the six loose kwargs are not the contract and no edge may reintroduce them, because
a signature that grows one parameter per filter cannot be extended without touching every caller and
duplicates the dataclass that SPEC-030 already owns. `reads.board_view` takes the same shape:
`board_view(conn, *, team_id: str, filters: ListFilters | None = None, group_by=..., limit_per_group=50)`.

The **edge functions** are declared in §2.2 because they were shipped with no contract at all:

| Function | Contract |
|---|---|
| `require_team(db, ref) -> Team` | team by key or ULID; `NotFound(team, key=ref)` |
| `resolve_filters(db, team, *, state=None, assignee=None, label=None) -> dict[str, str \| None]` | name or ULID to id (`state_id`, `assignee_id`, `label_id`); an unknown value is **dropped**, not an error — the dashboard's filter box must not 422 on a stale link |
| `board_counts(db, team) -> dict[str, int]` | `{"open", "urgent", "total"}` of one team, one aggregate query |

All three are read-only helpers of `app/boards.py`: one `ro_connection`, no `BEGIN IMMEDIATE`, no
business rule, no writes — they stay inside SPEC-040 §3.1 ("reads do not open a write transaction").

`DETAIL_INCLUDE` is declared as a **public constant** of `app/boards.py`:
`DETAIL_INCLUDE: frozenset[str] = frozenset({"comments", "attachments", "history"})`. It is
deliberately **not** the default of `include`: `issue_detail(db, ref)` keeps `frozenset()` so the panel
and the row/card fragments do not pay for the heavy collections (SPEC-070 §2.1.4), and the full-page
detail route passes `include=DETAIL_INCLUDE` explicitly. The constant is the single source of "what
the detail page shows" and both forms stay inside the already declared validation
`include ⊆ {comments, relations, attachments, history, children}` ⇒ `Validation(INCLUDE_INVALID)`.

SPEC-030 §2.6 also declares the read functions the edge resolves through: `team_by_ref`,
`team_counts`, `local_member`, `list_teams` and `issue_detail(conn, ref, *, include=...)`.

**Rationale:** an edge that resolves names to ids itself would need a connection and knowledge of
`WHERE name = ?` — SQL in `web/` violates A1/A3. A signature with six filter kwargs forked a second
filter contract next to `ListFilters`, which is exactly the duplication D-011 and §2.6 exist to
prevent.

**Files:** `specs/DECISIONS.md`, `specs/040-application-services.md` (§2.2, Notes),
`specs/030-sqlite-adapter.md` (§2.6), `specs/070-web-dashboard.md` (§2.1.4, Notes).

## D-028 — `web/` modules and `/healthz` are part of the architecture contract
**Date:** 2026-11-11 · **Wins:** SPEC-000 §4.3 · **Touches:** SPEC-000 §3, §4.3, §4.4, §4.5, SPEC-070 §2.1

Three defects in SPEC-000, all of them contract-shaped:

1. **§4.3 (file structure, "changing it is a `version` bump") listed `web/{views}.py` alone.** The
   dashboard shipped `web/app.py` (application factory, router, guards, `StaticFiles`),
   `web/readmodels.py` (pure edge read models) and `web/markdown.py` (render + own allowlist). None of
   the three existed in the tree, so the delivered code contradicted the contract that owns it.
2. **REQ-N007 (and REQ-S003) placed the markdown sanitizer in `app/markdown.py`;** it lives in
   `web/markdown.py`.
3. **`/healthz` was served by the web layer and declared nowhere** — not in §4.3, not in SPEC-070.

**Resolution:**
- §4.3 gains the three modules with one line of responsibility each, and `version` goes 2 → 3: the
  tree is contract, so adding to it is a contract change.
- REQ-N007 and REQ-S003 point at `web/markdown.py`. This is a **change of location, not of
  behaviour**: `markdown-it-py` with `html=False` plus the in-house allowlist (~60 lines, no new
  dependency) and D-020 stand untouched; only the module that hosts it moves to the edge that renders
  it (`web/`), which is also where its only consumer lives, so `app/` and `domain/` do not import a
  renderer (A1).
- `/healthz` is **declared in SPEC-070 §2.1.6** as a route of the web layer: `GET /healthz` ⇒ `200`
  with `{"status": "ok"}`; it verifies **liveness only** — the process answers and the web router is
  mounted. No database access, no template, no read model, no `PRAGMA`. It is **not** a duplicate of
  `/health` of SPEC-060 §2.5, which is the readiness check (it opens its own connection, runs
  `PRAGMA integrity_check` and compares `user_version`, answering 200 `ok` or 503 `degraded`): two
  different questions, and a probe that must run without touching the file cannot be the one that
  opens the database. The path is kept because SPEC-001 §2 and the `hifa serve` contract already
  publish `/healthz` without prefix. §3's surface table is split accordingly, so every endpoint has
  exactly one owning spec.

**Rationale:** the alternative (dropping `/healthz` and keeping only `/health`) would force the
liveness probe to hit the database on every check and would break a path that the MVP scope already
publishes; the alternative of leaving the three modules undeclared keeps a tree that cannot regenerate
the component — the standard of `specs/README.md`.

**Files:** `specs/DECISIONS.md`, `specs/000-system-architecture.md` (§3, §4.3, §4.4, §4.5, Notes),
`specs/070-web-dashboard.md` (§2.1, Notes), `specs/INDEX.md`.

## P-12 — `hifa init` outside a project writes a config that hijacks every sibling

**Date:** 2026-11-11 · **Kind:** known debt · **Status:** open
**Blocks:** no (workaround: delete the stray `.hifa.toml`)

`hifa init` writes `.hifa.toml` in the current directory unconditionally. Run it once from
`/tmp` (or any shared scratch directory) and the file-resolution walk-up of SPEC-100 §2.3 hands
that database to **every** project created below it, silently. It cost 8 red tests earlier today
and 10 more in a later run; both times the cause was a stray `/tmp/.hifa.toml`, not a code bug.

P-11 (walk-up stops at the first `.git`) reduces the blast radius but does not remove the class:
directories without a `.git` ancestor still inherit whatever config sits above them.

**Proposed fix:** `hifa init` writes `.hifa.toml` only when the working directory is inside a git
work tree. Outside one it must not write a project config: it uses the global database
(`$XDG_DATA_HOME/hifa/hifa.db`) and says so, because a project config with no project is a lie
with consequences. Add a test that runs `init` in a scratch directory and asserts that no
`.hifa.toml` was created and that the resolved path is the global one.

## D-029 — The detail panel pays for its comments; `DETAIL_INCLUDE` is the one set
**Date:** 2026-11-11 · **Wins:** SPEC-070 §2.1.4 · **Touches:** SPEC-070 §2.1.4, §2.3, Notes; SPEC-040 §2.2,
Notes; SPEC-030 §2.6, Notes

SPEC-070 §2.1.4 said `GET /_/issues/{id}/panel` called `issue_detail(db, ref)` with an empty
`include`, "without paying the heavy collections". The code passed
`include=app.boards.DETAIL_INCLUDE`, and `partials/issue_panel.html` renders the comment list —
which is the reason the panel exists. Implemented literally, the spec would have shipped a detail
panel with no comments and no way to say why.

**Resolution:** the code wins. The panel row declares
`issue_detail(db, ref, include=app.boards.DETAIL_INCLUDE)`. What keeps the cost honest is not the
absence of the argument but **where it appears**:

- `DETAIL_INCLUDE = frozenset({"comments", "attachments", "history"})` is the single constant that
  fixes the set. It lives next to `issue_detail` in `adapters/sqlite/reads.py`, the function that
  enforces it, and `app/boards.py` re-exports it so the edge keeps one name; `issue_detail` rejects
  any other name with `Validation(field="include", rule="INCLUDE_INVALID")` (SPEC-040 §2.2, §6),
  so the constant cannot drift from what the adapter can actually load.
- The board (`list_board`) and the row fragment (`/_/issues/{id}/row`) still pass no collections:
  the panel is an **explicit user action** on one issue, the board is the hot path. The extra read
  is paid once per opened panel, never per rendered board.
- The detail page (`GET /{team}/issue/{identifier}`) keeps the same constant, so the full page and
  the panel show the same comments.

**Rationale:** a detail panel without comments is not a detail panel; the alternative (a second
round trip from the client to fetch the comments) adds a request and a loading state to avoid one
indexed query inside a read that is already opening the row.

**Files:** `specs/DECISIONS.md`, `specs/070-web-dashboard.md` (§2.1.4, Notes),
`specs/030-sqlite-adapter.md` (§2.6, Notes), `src/hifa/adapters/sqlite/reads.py`,
`src/hifa/app/boards.py`, `src/hifa/web/app.py`.

## D-030 — Multiple teams are reachable from the CLI and the sidebar refreshes per request
**Date:** 2026-11-11 · **Wins:** SPEC-050 §2.5.2 + SPEC-070 §2.1.1 · **Touches:** SPEC-050 §2.3.1,
§2.4.1, §2.5.2, §2.7, §7, Notes; SPEC-070 §2.1.1, §2.1.4, §2.2, §2.3.1, §2.3.8, §4, §7, Notes;
SPEC-001 REQ-F005, Notes and `specs/acceptance/001-mvp-v1/02-teams-workflow-states.feature`;
SPEC-060 §2.4, Notes; `specs/INDEX.md`

A user can have more than one team, but the product had no way to create the second one and no way
to see it. Two independent holes, one decision:

1. **`create_team` existed as contract and as nothing else.** SPEC-040 §2.2 declares it, and
   REQ-F040-14 fixes its transaction (create the workspace if missing, insert the team and its 6
   default states). No entry surface published it: `hifa --help` offered only `init`, `serve` and
   `issue` because those are the only commands implemented, and SPEC-050 §2.5.2 declared
   `hifa team add --key --name`, a shape nobody built. A use case with no edge is not a feature.
2. **The dashboard could not grow a team while running.** `create_app` did
   `app.state.teams = teams` once at startup and the sidebar read from that attribute on every
   request. Measured today with a second team inserted by hand: `/{team}/board`, `/{team}/list` and
   `/{team}/issue/{id}` for the new team answered correctly and in isolation (the routes resolve the
   team per request), but the sidebar did not list it **until the process was restarted**.

**Resolution.**

- **The CLI surface is `hifa team create KEY NAME`** (plus `--icon`, `--color`, `--description`,
  `--timezone`, `--visibility`), with `hifa team list` and `hifa team show [KEY]`. One form, one
  handler: `team add` is deleted rather than kept as an alias, because an alias is a second place
  where the same command can drift (SPEC-050 R5 already treats "the idempotent" as contract; two
  names for one mutation is the same class of problem). The command is *parse → delegate*: `KEY` is
  validated by `create_team` (`TEAM_KEY_INVALID` ⇒ exit 2), a duplicate key is
  `Conflict(UNIQUE_TEAM_KEY)` ⇒ exit 4, and the workspace+team+6-states transaction of
  REQ-F040-14 is executed by SPEC-040, not re-implemented at the edge. `--parent` disappeared with
  the row because `CreateTeam` has no such field: the edge cannot offer a flag the use case cannot
  carry (D-012).
- **The sidebar reads teams per request.** `page["teams"]` is built from the database on every
  request (SPEC-070 §2.1.4: `app.teams.list_teams(db)`), never captured in `create_app` and never
  memoized on `app.state`. A team created by `hifa team create` while `hifa serve` is running
  appears on the next request (SPEC-070 R23). This is the point: **a local tracker has two writers**
  — the web process and the CLI against the same SQLite file — so any team list held in process
  memory is a cache that the other writer cannot invalidate. The cost of the correct behaviour is
  one indexed read of a table that holds a handful of rows, on a page that already opens several
  connections; the cost of the cache is a restart the user cannot know about.
- **`GET /` with several teams and no default renders a picker** (`team_picker.html`, `200`): every
  team with its name, key and issue count, each linking to `/{team}/board` (SPEC-070 §2.3.8, R24).
  The alternatives were both wrong: choosing "the first one by `created_at`" silently decides for
  the user and is the exact thing SPEC-050 R4 forbids (explicit context or error); answering `404`
  hides a selector behind an error page and teaches the user that the root is broken. The picker is
  a normal page — no form, no JS, no htmx — so it works before the bundle loads (R4).
- **`issues_count` and `active` are additive read-model keys with defaults** (D-026): `0` and
  `False`. The count is `open_count + closed_count` of `v_team_progress` (SPEC-010 §3.3), and a team
  with no issues legitimately has no row in that view, so the default is also the real value. They
  are declared in SPEC-070 §2.3.1 and echoed in `team list --json`, which keeps one vocabulary for
  "how many issues does this team have" across the two edges.
- **`KEY_PATTERN` gets one owner.** SPEC-020 §2.2 owns it (`^[A-Z][A-Z0-9]{1,9}$`, 2-10 characters)
  and `validate_key` normalizes (`strip` + `upper`) before matching. SPEC-001, SPEC-050 and SPEC-060
  had each copied `{1,4}` (2-5 characters), so a 6-character key was legal for the SPEC-100 seed and
  illegal at the other three edges, and `Eng` was a `422` through the API while it created `ENG` from
  the CLI. The three copies are gone: the edges reference the owner (SPEC-001 REQ-F005, SPEC-060 §2.4
  `TeamCreate`) or delegate to it (`hifa team create` reaches `validate_key` inside `create_team`).

**Rationale:** the three pieces are one story — creating a second team is only useful if the UI
shows it without a restart, and the UI can only show it if the read is per request. Documenting
them separately would have produced a spec that says "a team can be created" and an app that keeps
serving the snapshot taken at boot.

**Files:** `specs/DECISIONS.md`, `specs/050-cli.md` (§2.3.1, §2.4.1, §2.5.2, §2.7, §7, Notes),
`specs/070-web-dashboard.md` (§2.1.1, §2.1.4, §2.2, §2.3.1, §2.3.8, §4, §7, Notes),
`specs/001-mvp-v1.md` (REQ-F005, Notes), `specs/001-mvp-v1.tasks.md`,
`specs/acceptance/001-mvp-v1/02-teams-workflow-states.feature`, `specs/060-api-rest.md` (§2.4,
Notes), `specs/INDEX.md`.

## D-031 — `team archive` is deferred with `update_team`, and lists have one envelope
**Date:** 2026-11-11 · **Wins:** SPEC-040 §2.2 + SPEC-050 §2.4.3/§2.5.2 · **Touches:** SPEC-040
§2.2, Notes; SPEC-050 §2.3.1, §2.4.3, §2.5.2, §6, §5, §7, Notes; SPEC-070 §2.1.1, §2.3.8, R24,
§7, Notes; `specs/INDEX.md`

Two edges published a contract that no code produces, and in the `archive` case the published
semantics also contradicted each other. One decision with two parts, because the failure is the
same one twice: the spec describes a shape the implementation cannot emit, and the reader has no
way to tell which half is the truth.

**1. `hifa team archive [KEY] [--undo] [--yes]` and `archive_team(uow, cmd, actor)` are deferred.**

Neither exists: `hifa team --help` offers `create`, `list` and `show`, and `src/hifa/app/teams.py`
has `create_team` and `list_teams` — there is no `ArchiveTeam` command object and no
`archive_team` function. The surface was declared in SPEC-050 §2.3.1, §2.5.2 and §7 and in
SPEC-040 §2.2, and the same operation was described three incompatible ways:

| Source | Open issues | Already archived |
|---|---|---|
| SPEC-040 §2.2 / §6 | `Validation(TEAM_HAS_OPEN_ISSUES)` ⇒ exit 2 | `Conflict(TEAM_ALREADY_ARCHIVED)` ⇒ exit 4 |
| SPEC-050 §2.5.2 | `Conflict` ⇒ exit 4 | (unstated) |
| SPEC-050 R5 | (unstated) | idempotent ⇒ exit 0 |

`Validation` and `Conflict` are different classes with different exit codes (SPEC-000 A7), and an
operation cannot be both a `409` and an idempotent `200`.

**Resolution.** `archive_team` is deferred **with** `update_team`, not on its own: both are the
"mutate a team that already exists" family, both need the same ownership and history decisions, and
`--undo` (which SPEC-050 declared as `UpdateTeam` with no fields) is unimplementable until
`update_team` exists. The `team archive` row leaves SPEC-050 §2.5.2 and §2.3.1 — the command is not
part of the published surface until the use case exists. That is D-012 applied to the other
direction: an edge does not declare a flag the use case cannot carry, and it does not declare a
command the use case cannot serve either. SPEC-040 keeps the signature and its `# raises:` line
(the function is contract for the day it is written) and gains a **status** note saying it is
declared and not implemented; the error table in §6 is untouched, because those codes are the
contract of that function, not of a command.

The semantic contradiction is **recorded, not resolved**: SPEC-040 is the authority on error classes
(D-012), so the candidate reading is open issues ⇒ `Validation` ⇒ exit 2 and already archived ⇒
`Conflict` ⇒ exit 4, which makes R5's idempotency claim inapplicable to `archive`; the final answer
belongs to the revision that implements it, together with whether `--undo` is `UpdateTeam` or a
second use case. Until then SPEC-050 R5 stops listing `team archive` as one of the idempotent
operations: an unimplemented command cannot be the example of a rule.

**2. The list envelope is `{items: [...]}` and nothing else.**

SPEC-050 §2.4.3 required `{items, next_cursor, has_more, total_estimate}` for every list, while
§2.5.2 declared `{items: Team[]}` for `team list`, and `src/hifa/cli/commands/team.py` emits exactly
`output.emit(rows, json_obj={"items": rows})`. Three sources, two shapes: a script written against
the normative envelope would look for `next_cursor` on a command that has no cursor, and a script
written against the code would fail a contract check.

**Resolution: the implemented shape wins, and it is the only normative one.** `items` is the whole
base envelope; `next_cursor`, `has_more` and `total_estimate` are **not** part of it and appear
only in commands that paginate with `--cursor` — today only `issue list` (§2.5.3), whose row already
declares them. Every other list (`team list`, `project list`, `cycle list`, `member list`,
`label list`, `issue attach --list`) emits `{"items": [...]}`, which is also what `--quiet` and the
table renderer consume: one record per element. The alternative — keeping the four-key envelope
everywhere and making the code emit `has_more: false` and `next_cursor: null` on `team list` —
would have been a code change to satisfy a shape nobody asked for, and it would advertise pagination
on a table with a handful of rows. The `total_estimate: null` rationale of §2.4.3 still holds where
it applies (`issue list`), and it is not weakened: it is now scoped to the command that has the
problem.

**Rationale:** a deferred command is honest debt; a command declared with contradictory semantics
is a trap, because the implementer picks one reading and the reader picks another. The envelope was
the same trap in miniature — the spec had a "right" answer that the code could not have produced
without inventing keys. Both were fixed by making the spec describe what ships and recording what
does not, rather than by writing code to match prose.

**Files:** `specs/DECISIONS.md`, `specs/050-cli.md` (§2.3.1, §2.4.3, §2.5.2, §6, §5, §7, Notes),
`specs/040-application-services.md` (§2.2, Notes), `specs/070-web-dashboard.md` (§2.1.1, §2.3.8,
R24, §7, Notes), `specs/INDEX.md`.

## D-032 — Design principles P1-P9 are normative and edge parity is a contract
**Date:** 2026-11-11 · **Wins:** AGENTS.md §5 · **Touches:** AGENTS.md §5, SPEC-000 §8,
SPEC-070 §1 (reading reaffirmed, no text change), `.importlinter`

The repository had architecture rules (A1-A8) but no design principles: nothing stated which edge
owns a capability first, that the dashboard must reach parity with the CLI, that a spec carries its
UI/UX, or that mobile is a first-class target. Every one of those questions was answered ad hoc, and
the answers disagreed with each other.

The gap was measurable. SPEC-050 defines 43 verbs (§2.5, one row per `hifa <verb>` command) and
6 are implemented (`init`, `serve`,
`issue create`, `team create`, `team list`, `team show`), while the dashboard had already exposed
reads that no CLI verb offered: it rendered `list_board`, `list_issues` and `issue_detail` before any
`hifa issue list` or `hifa issue show` existed. One edge outran the other, with no rule saying which
one was the reference.

**Resolution:**

- AGENTS.md §5 declares P1-P9 normative and binding, exactly like A1-A8; a PR that violates a
  principle is rejected.
- P1 makes the CLI the reference surface: a capability is implemented, tested, documented and
  verified as a `hifa <verb>` before any dashboard route consumes it.
- P2 (edge parity) is executable rather than aspirational: `.importlinter` gains the `web` contract
  in this change, so the web edge can never reach back into the CLI, and a CLI-vs-web capability
  parity checker is its own follow-up task, because bounding imports and counting verbs/routes are
  two different checks.
- P3 adds the UI/UX section to the definition of a complete spec in `specs/README.md`: empty,
  loading and error states, keyboard path, breakpoints and mobile layout.
- P6 and P7 fix reversibility and machine-readability as defaults: archive, soft-delete and
  `--undo` instead of deletion, an explicit `--yes` for anything destructive, a stable `--json`
  envelope and a canonical URL for every view.

SPEC-070 §1 keeps the dashboard out of the REST API, and this decision fixes that reading for good:
`depends_on: SPEC-060` means the dashboard REFINES the semantics of SPEC-060 (PATCH rules, keyset
pagination, `/health` vs `/healthz`), never that it CALLS it. The dashboard's server-side routes
call `app/` directly; there is no HTTP hop inside one process.

**Rationale:** in a single-process local app the web edge and the CLI are two transports over one
core, and the rules that keep them honest are the ones that can be executed. A principle that lives
only in prose is a preference, not a contract (P8).

**Files:** `specs/DECISIONS.md`, `AGENTS.md` (§5), `specs/README.md`, `README.md`, `specs/INDEX.md`,
`specs/000-system-architecture.md` (§8, Notes), `.importlinter`, `src/hifa/cli/commands/issue.py`,
`tests/unit/cli/test_help_contract.py`.

## D-033 — The eleven existing specs stay in Spanish as accepted debt of the D-024 migration
**Date:** 2026-11-11 · **Wins:** AGENTS.md §9 · **Touches:** AGENTS.md §9, `specs/README.md`,
`specs/INDEX.md`

D-024 (2026-11-11) made the repository English-only: code, comments, docstrings, commit messages,
docs and specs. The code followed — the modules, the tests and the CLI output are English. The
specs did not: the eleven files under `specs/` (`000` … `100`) are still written in Spanish, and so
are `001-mvp-v1.md` and its task breakdown, because they are the frozen contracts the v1 work is
being implemented against.

That left the rule in a state where it was true on paper and false in the tree: a reader of
`AGENTS.md` §9 was told "English everywhere" and then opened eleven Spanish specs, which is exactly
the kind of documentation debt §8 calls a bug. Rewriting 700 KB of normative prose in one commit
would have been a translation project disguised as a cleanup, and it would have invalidated every
line reference the two review rounds produced (`SPEC-030 §2.6`, `SPEC-050 §2.5`, `R17`…).

**Resolution:** the eleven specs are **accepted debt**, declared as such:

- Existing specs and docs are translated **as they are revised**: whoever bumps a `spec_revision`
  translates the section they touch, in the same commit.
- **New files, and any modified text, are English from now on** (D-024). A newly added section,
  task, decision or paragraph is English even inside a Spanish spec.
- The debt is registered here instead of remaining an invisible exception, and §9 of `AGENTS.md`
  carries the clause so no reader is misled again.

**Rationale:** a language rule that is falsified by the repository it governs teaches agents that
rules are advisory. Naming the exception, bounding it (a fixed list of eleven files, decremented by
revision) and keeping new text English is the only version of D-024 that is both honest today and
convergent tomorrow. Translating on revision also keeps the translation next to the change that
makes it necessary — the section is being read and rewritten anyway.

**Files:** `specs/DECISIONS.md`, `AGENTS.md` (§9), `specs/INDEX.md` (decision counter),
`specs/README.md` (language note).
## D-034 — The canonical state order is `Triage · Todo · Backlog · In Progress · Done · Canceled`
**Date:** 2026-11-11 · **Wins:** SPEC-100 §2.5 · **Touches:** SPEC-100 §2.5, §2.7, Notes; SPEC-050
§2.5.1, §2.5.6, §2.7, §7, Notes; SPEC-070 §2.3.2, §2.5.4, §2.6.5, §5.5, R25, §7, Notes;
SPEC-001 REQ-F001; `specs/acceptance/001-mvp-v1/01-init.feature`,
`specs/acceptance/001-mvp-v1/02-teams-workflow-states.feature`; `specs/INDEX.md`

The seed every new team receives had `Backlog` at `position 2.0` and `Todo` at `3.0`, so a fresh
board read `Triage | Backlog | Todo | In Progress | Done | Canceled`. The owner of the tracker does
not read those two columns that way: for him **`Backlog` is "some day"** — real work, not scheduled —
and **`Todo` is "the next thing"**, the column he looks at when he sits down. With `Backlog` first,
the leftmost column of the board was the one he uses least, and the drag he does most often (pull
something out of the backlog into the immediate queue) moved cards *right to left*.

**Resolution: the canonical order is `Triage(1.0) · Todo(2.0, is_default=1) · Backlog(3.0) ·
In Progress(4.0) · Done(5.0) · Canceled(6.0)`.** Same six names, same types, same colors, same
`is_default`; only the positions of `Todo` and `Backlog` swap.
`DEFAULT_STATES` in SPEC-100 §2.5 is the single template, so the change reaches `hifa init`,
`create_team` (REQ-F040-14 seeds from that same tuple) and every surface that reads it
(SPEC-050 §2.5.1, SPEC-070 §2.5.4).

**Existing teams are not migrated.** `workflow_state.position` is a user-facing order, and a
database migration that renumbered it would (a) silently reorder boards somebody arranged by hand,
(b) need a rule for teams where the owner already moved things, and (c) turn a presentation
preference into a schema concern. The existing team is reordered with the same verb that reorders a
new one — `hifa state update Backlog --after Todo` (SPEC-050 §2.5.6) — or by dragging the column on
the board (SPEC-070 §2.6.5). Both write the same column through the same use case
(`update_state`), so there is one mechanism and no second, hidden one. A fresh workspace is correct
by construction; an old one is one command away, and the acceptance scenarios say so explicitly
(`02-teams-workflow-states.feature`, "an existing team is reordered with a command, not with a
migration").

**Why `Todo` keeps `is_default = 1`:** a new issue with no `--state` still lands in the column the
user works from, and the invariant SPEC-100 §3.1 (exactly one default) is untouched. Moving `Todo`
to `2.0` does not move the default, only its place on the board.

## D-035 — Archiving or deleting a workflow state is deferred; `hifa state` publishes no such verb
**Date:** 2026-11-11 · **Wins:** SPEC-050 §2.5.6 · **Touches:** SPEC-050 §2.5.6, §7, Notes;
`specs/acceptance/001-mvp-v1/02-teams-workflow-states.feature`; `specs/INDEX.md`

`hifa state` ships `list`, `create` and `update` — create, rename, recolor, reorder. It does **not**
ship `state archive` or `state delete`, and SPEC-040 §2.2 declares no `delete_state` /
`archive_state` use case for it to call.

The reason is the same one that deferred `team archive` in D-031, one level down: **there is no
defined semantic for the issues that live in the state being removed.** The candidates are mutually
exclusive and each one changes data:

| Reading | What happens to the issues | Cost |
|---|---|---|
| move them to the team default (`Todo`) | history rows, `updated_at`, `board_order` recomputed in the target column | a bulk write the user did not ask for |
| refuse while the state has issues (like `archive_team` / `TEAM_HAS_OPEN_ISSUES`) | nothing moves; the user must `hifa issue move` first | a refusal the user cannot satisfy in one step for 200 issues |
| archive the state and keep the issues in it (like `label.retired_at`) | the issues stay in a column the board no longer renders, and `is_open`/`v_team_progress` start disagreeing with reality | an orphan column that `create_issue` may still resolve to |
| block them like `archive_team` with `--yes` | the issues become unwritable | the harshest option, and the hardest to undo |

Picking one is a product decision with data consequences, not a formatting one, and the iteration
that ships `hifa state` did not need it: creating, renaming, recoloring and reordering cover the
stated request ("customizable states per team"). **So the verb is not published**, exactly as
`team archive` was not (D-012 applied in both directions: an edge does not declare a command the
use case cannot serve).

The two `@REQ-F009` scenarios of `02-teams-workflow-states.feature` — delete a state with issues ⇒
`409`, delete reassigning its issues in one transaction — were written when `hifa state delete`
was assumed; they are now tagged `@deferred` with a comment pointing here, so the runnable set does
not claim a behaviour that no command produces. The day the semantic is decided, it is written as
a new decision (the losing spec adjusted, `spec_revision` bumped), `delete_state` / `archive_state`
appear in SPEC-040 §2.2 with its `# raises:` line, and the `hifa state` row comes back in
SPEC-050 §2.5.6 — in that order.

**Files:** `specs/DECISIONS.md`, `specs/100-config-bootstrap.md` (§2.5, §2.7, Notes),
`specs/050-cli.md` (§2.3, §2.4.1, §2.5.1, §2.5.6, §2.7, §7, Notes),
`specs/070-web-dashboard.md` (§2.1.3, §2.3.2, §2.5.4, §2.6.5, §3, R12, R25, §5.5, §6, §7, Notes),
`specs/001-mvp-v1.md` (REQ-F001), `specs/001-mvp-v1.tasks.md` (T-04),
`specs/acceptance/001-mvp-v1/01-init.feature`,
`specs/acceptance/001-mvp-v1/02-teams-workflow-states.feature`, `specs/INDEX.md`.

## D-036 — The repository is public and `main` is enforced PR-only by a ruleset
**Date:** 2026-11-11 · **Wins:** SPEC-000 §"repository conventions" (implicit until now) ·
**Touches:** `AGENTS.md` §6, `README.md` "Contributing", `specs/INDEX.md`

Until today the rule "never commit directly to `main`" was a convention in `AGENTS.md` §6, and P8
(*contracts, not conventions*) says a rule that is not executable is not a rule. It was measurably
not a rule: nothing stopped a direct push.

**Resolution.** `ginkelte/hifa` is a **public** repository, and the ruleset `main is PR-only`
(`current_user_can_bypass: never`) makes the convention executable:

| Rule | Effect |
|---|---|
| `pull_request` | every change to `main` through a PR; squash merges only |
| `required_status_checks` | `test` and `firmas` green before the merge button works |
| `non_fast_forward` | `main` cannot be force-pushed |
| `deletion` | `main` cannot be deleted |

Required approvals are `0` **on purpose**: a solo maintainer cannot approve their own PR, so
requiring one would deadlock every merge. The two status checks are the gate.

**Verified, not assumed.** A direct push is rejected by GitHub with *"Changes must be made through
a pull request"* and *"2 of 2 required status checks are expected"*. The PR that documents this
decision (and every PR since) was merged under the ruleset itself, so the path is exercised and not
just configured.

**History.** The published history starts at a single signed initial commit dated 2026-11-11, which
carries the complete tree of the project. A public repository is a release, not a diary: the history
that starts here is the one a reader can navigate.

## D-037 — A release is a signed tag; the pipeline writes no commit to `main`
**Date:** 2026-11-11 · **Wins:** SPEC-110 §2 (new component) · **Touches:** `specs/110-release.md`,
`specs/INDEX.md`, `pyproject.toml`, `src/hifa/__init__.py`, `.github/workflows/release.yml`,
`.github/signers/allowed_signers`, `AGENTS.md` §6, `README.md` "Contributing"

HIFA-22 defines the release flow as: derive the version from the commit log, **bump the version in
`pyproject.toml` and commit it**, update the changelog, tag, and publish a GitHub Release. The second
clause cannot execute on this repository. `main` is enforced PR-only by the ruleset `main is PR-only`
with `current_user_can_bypass: never` (D-036): a push of a commit to `main` is rejected by GitHub
with *"Changes must be made through a pull request"* and *"2 of 2 required status checks are
expected"*, there is no force-push and no deletion, and only squash merges are accepted. A job that
commits and pushes a version bump therefore fails at the push, on every release, after having already
computed the version.

The second measured contradiction is inside the repository: the project carried **two** version
literals in different places and with different owners — `pyproject.toml:7` (`version = "0.1.0"`, the
distribution version) and `src/hifa/__init__.py:5` (`__version__ = "0.1.0"`, the value that
`hifa --version` prints, imported by `cli/main.py`). Both agreed by luck, not by construction:
nothing derived one from the other, and a release that bumped one would have left the other behind,
so the tag and `hifa --version` could disagree with the wheel.

**Candidates, evaluated against the ruleset:**

| Design | Mechanism | Verdict |
|---|---|---|
| **(a) tag-only, dynamic version** | `pyproject.toml` goes `dynamic = ["version"]` + `hatch-vcs`; the tag is the only source; the job creates a signed tag and the GitHub Release, and commits nothing; `hifa --version` reads `importlib.metadata.version("hifa")` | **chosen** |
| (b) push the bump to `main` | the job writes `version` and pushes to `main` | rejected: the ruleset rejects the push (*"Changes must be made through a pull request"*), and every release would end red after doing the work |
| (c) bot PR with the bump | the job opens a PR with a signed bump commit, a human merges it, a second run tags | rejected as the default: two steps and two runs per release, a second signed commit per release, a PR whose only content is a number that the tag already contains, and a merge gate that can stay open indefinitely while the version is decided |

**Resolution.** SPEC-110 defines design (a):

- **The tag is the single source.** `vX.Y.Z` (annotated, SSH-signed) is the only artefact that
  carries a version; `pyproject.toml` declares `dynamic = ["version"]` and the build backend derives
  the distribution version from the tag; `src/hifa/__init__.py` derives `__version__` from the
  installed metadata and contains no literal. The two-literal state is gone, not synchronised.
- **The pipeline writes no commit.** The release workflow triggers on `push` to `main` and on manual
  dispatch, and the only ref it pushes is `refs/tags/vX.Y.Z`. No `git commit`, no branch push, no run
  of the release tool in write mode (R11 of SPEC-110).
- **The changelog is the GitHub Release body.** A `CHANGELOG.md` in the tree would require a bot
  commit and a PR per release — the same wall as (b). The notes are generated from the commit range
  with the sections and bullet shape fixed by SPEC-110 §2.6, so "the changelog groups by type" stays
  true without a file that can drift.
- **`python-semantic-release` is a calculator, not a publisher.** It is a development dependency
  (never runtime), used read-only (`--print*` with `--no-commit --no-tag --no-changelog --no-push
  --no-vcs-release`); the tag and the Release are created by the job, because the tool would write the
  version into the tree and produce an unsigned tag.
- **`allow_zero_version = true` is mandatory.** Measured on `python-semantic-release 10.7.0`: with the
  key unset, a `feat`, a `fix` and a `feat!` on the `0.x` line all resolve to `1.0.0`; with it set,
  they resolve to `0.2.0`, `0.1.1` and `1.0.0`. The issue's acceptance criteria (`feat` ⇒ `0.x.0`,
  `fix` ⇒ patch, breaking ⇒ major) only hold with that key, so the default configuration contradicts
  the requirement.
- **The first tag is manual and signed.** The published history is a single non-conventional commit,
  which yields `0.0.0` — no release, forever. The maintainer creates `v0.1.0` once, on that commit,
  with the same SSH key (§5.2 of SPEC-110). The job never invents a first tag: a tag is an assertion
  about what shipped, and a bot has nothing to assert on a history it cannot read semantically.
- **Signing stays SSH and single-allowlist.** The private key lives in the repository secret
  `HIFA_RELEASE_SIGNING_KEY`; its public half is a line in `.github/signers/allowed_signers` with
  principal `hifa-release`. The `firmas` job verifies commits with that file and the release job
  verifies tags with it: one allowlist, two checks. A missing secret fails the run; an unsigned tag
  is never created as a fallback.

**What loses, and why.** HIFA-22 loses its "bump the version in `pyproject.toml` and commit" clause:
the mechanism it prescribes is the one the repository forbids, so the issue is wrong about the *how*
and right about the *what* (a version derived from the log, a changelog, a tag, a Release). The
version literal in `pyproject.toml` loses to the tag (SPEC-000 §8 step 1 keeps its `version` mention
until that spec is revised; the split of ownership — SPEC-000 keeps backend, deps, extras, entrypoint;
SPEC-110 keeps everything that produces a version — is recorded in SPEC-110 §Notes). The literal in
`src/hifa/__init__.py` is deleted, not kept in sync. Nothing in SPEC-050 loses: `--version` still
prints `hifa <version>`; only where the value comes from changes.

**Verified.** Every claim above is executable, and SPEC-110 §7.2 carries the commands: `git verify-tag`
against `allowed_signers` for the signature; `git describe --tags --exact-match` ⇔ `hifa --version` for
the tag/version tie; `semantic-release version --print` on a range with a `feat` for the bump table;
`grep` for the absence of a version literal; `gh run view --log` for `last=` / `next=` / `no release`;
the ruleset itself rejects any attempt to push a commit to `main`, which is the constraint this
decision is built around and not a hypothesis.

**Files:** `specs/110-release.md` (new, SPEC-110), `specs/DECISIONS.md`, `specs/INDEX.md`;
implementation: `pyproject.toml`, `src/hifa/__init__.py`, `.github/workflows/release.yml`,
`.github/signers/allowed_signers`, `AGENTS.md` §6, `README.md` "Contributing".

## D-038 — SPEC-110 owns the version keys of `pyproject.toml` and `__version__`; SPEC-000 keeps the rest
**Date:** 2026-11-11 · **Wins:** SPEC-110 §2.1, §2.9 (new component) · **Touches:** SPEC-000 §3,
§8 step 1, Notes; SPEC-110 §2.1, §2.9, §2.11, §12, Notes; `specs/INDEX.md`; `specs/DECISIONS.md`

D-037 settled the release design and left one conflict **declared but not resolved**: SPEC-000 §3
says SPEC-000 "governs `pyproject.toml`, `.importlinter` and `src/hifa/__init__.py`", while SPEC-110
removes `version` from `[project]` and rewrites `src/hifa/__init__.py`. Two specs claiming one file
is exactly the case `AGENTS.md` §3 covers — *"a conflict between specs is not resolved by writing
code: it is recorded in `DECISIONS.md`, the losing spec is adjusted with a `spec_revision` bump, and
its §Notes points at the decision"* — and `specs/INDEX.md` §Rules repeats it. A split written inside
the new spec's §Notes is not a decision: it has no number, no owner and nothing to point at.

D-037 also carried a **false premise**, inherited by revision 1 of SPEC-110: it claims "SPEC-000 §8
step 1 keeps its `version` mention". §8 step 1 lists `requires-python`, the entrypoint, the extras
and the base dependencies — it has never mentioned `version`. The real overlap is §3, one paragraph
longer than the decision assumed, so the deferral rested on a text that does not exist.

**Corrigendum to D-037 — premise and pointer.** D-037 is **append-only and is not edited**; this
paragraph is where its two defects are corrected, and it is the pointer that survives:

1. *Premise*: the sentence "SPEC-000 §8 step 1 keeps its `version` mention until that spec is
   revised" describes a text that never existed (above). §8 step 1 of SPEC-000 has now been revised
   anyway — it states that the distribution `version` is dynamic and owned by SPEC-110 §2.1 — so the
   deferral it announced has no object left.
2. *Pointer*: D-037 says the split of ownership "is recorded in SPEC-110 §Notes". A note inside the
   winning spec is not a decision (`AGENTS.md` §3, `specs/README.md` §Drift): the split is **this
   decision**, and the pointers live here. Both SPEC-000 §Notes and SPEC-110 §12 point at D-038, and
   SPEC-000's `implements:` no longer claims `src/hifa/__init__.py`, so a reader who follows D-037
   forward lands on this decision instead of on a §Notes paragraph with no number.

**Resolution: the ownership of the two shared artefacts is a split by key, and it is recorded here.**

| Artefact | SPEC-000 owns | SPEC-110 owns |
|---|---|---|
| `pyproject.toml` | `[build-system].build-backend`, `hatchling` in `requires`, `[project]` name/description/readme/`requires-python`/dependencies/extras/scripts, `[tool.hatch.build.targets.wheel]`, `[tool.ruff]`, `[tool.pytest.ini_options]` | `[project].dynamic` and the **absence** of `[project].version`, `hatch-vcs` in `[build-system].requires`, `[tool.hatch.version]`, `[dependency-groups].dev` (the pinned PSR), the whole `[tool.semantic_release]` table, and the absence of `version_toml`/`version_variables`/`changelog`/publish targets |
| `src/hifa/__init__.py` | nothing (the module is SPEC-110's contract in full, §2.1) | the `__version__` symbol and its fallback |
| `.importlinter` | all of it | nothing |

**Drift attribution for a shared file.** `implements:` is glob-based, so both specs legitimately list
`pyproject.toml`; the rule that removes the ambiguity is: *a change inside a key listed in SPEC-110
§2.9 requires a SPEC-110 `spec_revision` bump; any other change in the file requires a SPEC-000
`spec_revision` bump; a change that moves a key from one column to the other requires both.* The
`INDEX.md` row of each spec now says which part it claims, so a reader — and a future drift check —
attributes a diff to one owner without guessing.

**SPEC-000 is the losing spec and is adjusted here:** `spec_revision 6 → 7` (no `version` bump: the
packaging contract keeps the same backend, dependencies, extras and entrypoint, so no public
contract changed). Its §3 excludes the version-producing keys, its §8 step 1 points at SPEC-110 §2.1
for the version, and its §Notes carries the pointer to this decision. `INDEX.md` carries the
decision counter and the per-spec claim.

**Secondary clause — the `0.1.0` examples of SPEC-050/060/070/080.** **Six example blocks in four
specs** read `__version__` (`hifa --version` at 050:908, `hifa doctor --json` at 050:913, the
`hifa serve` banner at 050:1326, `/health` at 060:833, the web read-model at 070:237 and the export
`app_version` at 080:333), and removing `[project].version` makes an off-tag development install
report a dev version. The example blocks at `050-cli.md:908,913,1326`,
`060-api-rest.md:833`, `070-web-dashboard.md:237` and `080-transfer-csv-json.md:333` are
**illustrative, not contractual**: none of those specs asserts equality with a literal, and the AC
at `050-cli.md:1326` is an ordering assertion. SPEC-110 §2.11 states the rule. Refreshing those
six blocks to the current tag needs a `spec_revision` bump of each spec and is **accepted debt**:
paid when each spec is next revised, never as a drive-by edit in this change.

**Why not the alternatives.** (a) *Let SPEC-110 take the whole file*: SPEC-000 §8 step 1 is the only
place that declares the backend, the extras and the entrypoint, and moving them would rewrite an
approved spec's packaging contract for a version change. (b) *Let SPEC-000 keep the version keys*:
it cannot — SPEC-000 does not define a release, a tag or a parser, so those keys would be a contract
with no owner of the behaviour. (c) *Declare the split in SPEC-110 §Notes only*: rejected by
`AGENTS.md` §3 and by `specs/README.md` §Drift, and it leaves `INDEX.md` with two unqualified rows
for one file, which is the state this decision repairs.

**Files:** `specs/DECISIONS.md`, `specs/000-system-architecture.md` (§3, §8, Notes and
`implements:`, which no longer claims `src/hifa/__init__.py`), `specs/110-release.md` (§2.1, §2.9,
§2.11, §12), `specs/INDEX.md`; implementation: `pyproject.toml`, `src/hifa/__init__.py`.

## D-039 — The release engine is npm `semantic-release`; the admin role is the bypass; `v0.1.0` is the bootstrap
**Date:** 2026-11-11 · **Wins:** SPEC-110 §2 (rewritten as `version: 2`, `spec_revision: 4`) ·
**Touches:** `specs/110-release.md` (all of §2, §3-§10, §12), `specs/000-system-architecture.md`
(§3, §8 step 1, Notes), `specs/INDEX.md`; implementation: `pyproject.toml`, `package.json`,
`package-lock.json`, `.releaserc.json`, `.github/workflows/release.yml`,
`.github/release/tag-message.sh`, `CHANGELOG.md`, `AGENTS.md` §6/§9, `README.md` "Contributing"

D-037 chose `python-semantic-release` (PSR) as a **read-only calculator** and forbade the pipeline any
commit to `main`, because the ruleset `main is PR-only` rejected every push (D-036: "no bypass"). Two
things changed the premise, and the third is a measurement:

1. **The ruleset now has a bypass actor.** It carries
   `bypass_actors: [{actor_id: 5, actor_type: "RepositoryRole", bypass_mode: "always"}]` and GitHub
   reports `current_user_can_bypass: always`, while `pull_request`, `required_status_checks` (`test`,
   `firmas`), `non_fast_forward` and `deletion` stay enabled. The maintainer authorised this
   explicitly: *"pass a token from an admin user and add the admin as a bypass to that rule, or use a
   GitHub App"*.
2. **A proven engine exists.** The user's `Vichoko/python-semantic-release-cookiecutter` solves the
   same problem with the npm package `semantic-release` and six plugins; its `.releaserc.json` was read
   from the repository, not remembered.
3. **PSR's cost was measured in workarounds.** On this repository PSR 10.7.0 needs
   `allow_zero_version = true` (unset, every releasable commit on a `0.x` line resolves to `1.0.0`),
   `major_on_zero = true`, `add_partial_tags = false`, three `--no-*` flags per invocation and the
   seven-row truth table of SPEC-110 rev 3 (rows A-G) to read `--print` unambiguously. That table was
   the price of a calculator that answers with a number and nothing else. The npm engine answers with
   the release itself: it computes, writes, commits, tags and publishes, and it is idempotent by
   construction (it computes from the last tag and prints `There are no relevant changes` when there
   is nothing to release).

**Resolution (a) — the engine.** SPEC-110 `version: 2`, `spec_revision: 4` replaces PSR with npm
`semantic-release@21`, pinned with its plugins in a **versioned** `package.json` +
`package-lock.json` and installed with `npm ci`:

| Kept from the reference setup | Corrected (the three defects of the reference) |
|---|---|
| plugin set and order: `commit-analyzer` → `release-notes-generator` → `changelog` → `exec` → `git` → `github`; `tagFormat: "v${version}"`; the `sed` `prepareCmd` that rewrites the literal | **config vs documentation**: the reference mapped `style`/`ci`/`chore`/`build` to `patch` in `.releaserc.json` and to "no release" in its own docs. SPEC-110 §2.5.4 is the single table and `AGENTS.md` §9 must be identical (§2.10) |
| `@semantic-release/git` committing `CHANGELOG.md` + `pyproject.toml` with `chore(release): X [skip ci]` | **`CHANGELOG.md` corruption**: a hand-written `# Changelog` header ended up *below* the generated `# [1.0.1](…)`. The file is machine-owned and must not exist before the first release (§2.6) |
| the workflow skeleton: `push: [main]`, `workflow_dispatch`, `fetch-depth: 0`, `setup-node` lts, `[skip ci]` job condition | **non-reproducible install**: an inline `package.json` with `^` ranges and no lockfile, in a job holding `contents: write`. `npm ci` + a versioned lockfile is mandatory (§2.8); `persist-credentials: false` is kept and the push credential travels in `GIT_CONFIG_*` env vars (§2.4) |

The version is a **literal again**: `@semantic-release/exec`'s `sed` writes `version = "X.Y.Z"` in
`pyproject.toml`, in the release commit. `dynamic = ["version"]`, `[tool.hatch.version]`,
`hatch-vcs`, `[tool.semantic_release]` and the `python-semantic-release` dev dependency are removed;
`src/hifa/__init__.py` keeps reading `importlib.metadata` with the `0.0.0+unknown` fallback, so
`hifa --version` still has one origin. The seven-row guard A-G is deleted: its outcome space is now
one assertion on the run — a release XOR the engine's `no relevant changes` token, plus four
post-conditions (annotated signed tag, signed release commit, literal == tag, changelog mentions the
version).

**Resolution (b) — the bypass, and its exact scope against D-036.** D-036 is **not edited** (append-
only); this is its corrigendum:

- *What D-036 said*: `current_user_can_bypass: never`; every push to `main` is rejected; a release
  commit is impossible. *What holds now*: the same ruleset exempts **one actor class**, the
  `RepositoryRole` admin, with `bypass_mode: always`. The four rules remain, the required checks
  remain `test` + `firmas`, and every other actor — collaborator, fork, bot without bypass,
  fine-grained token — is still rejected with *"Changes must be made through a pull request"*.
- *The exception is role-based, not token-based.* `HIFA_RELEASE_TOKEN` (classic PAT of an admin, scope
  `repo` **only**, ≤ 90 days — `workflow` would only matter to push `.github/workflows/**`, which no step
  does, so carrying it is a widening that must be justified: SPEC-110 §2.7.2, REQ-S110-03) authenticates a
  job that exercises a power the admin already has; it
  grants no new power, and any admin could push to `main` without it. That is the trade-off the
  maintainer accepted to get a one-run release instead of a two-step bot PR (D-037 candidate (c),
  still rejected).
- *Scope.* May be used for: the single `chore(release): X [skip ci]` commit, the `vX.Y.Z` tag, and
  re-running a failed release. Must never be used for: merging a feature branch, editing
  `.github/workflows/**` by hand, pushing any other commit to `main`, force-pushing, deleting `main`,
  moving or deleting a tag, or changing the ruleset and its bypass actors. SPEC-110 §2.7.1 is the
  authoritative table; R11 and R16 are the rules derived from it.
- *Blast radius, stated.* A leaked token can push code that bypasses review. Mitigations in the
  contract: 90-day expiry, rotation, secret scanning, and the audit trail (the release commit is
  always `chore(release): … [skip ci]` and is verifiable as a signed commit).
- *The GitHub App alternative is deferred, not rejected.* It would scope the exemption to one identity
  with its own audit trail; it costs an App registration, a private key, an installation-token exchange
  and a second secret. Out of scope for `version: 2` (SPEC-110 §10), recorded here as the upgrade path.
- *Not closed by this decision*: GitHub has no tag rule in this ruleset, so `refs/tags/v*` is not
  protected against a force-move by a human with write access. A second ruleset targeting
  `refs/tags/v*` is declared debt (SPEC-110 §2.5.4 point 3).

**Resolution (c) — the bootstrap `v0.1.0`.** Measured on `semantic-release 21.1.2`: with no tag at all
the engine answers *"There is no previous release, the next release version is 1.0.0"* for the first
`feat:`. HIFA-22 requires a `feat` to produce `0.x.0`, so the maintainer creates a **signed** `v0.1.0`
tag once, on the **merge commit of the PR that restores the literal `0.1.0`** (§5.2's PR-A), and pushes it
**before** the workflow is enabled (SPEC-110 §5.2, R12). The first automated release is then `0.2.0`. The
tag is manual because it is the one version no commit can derive — the import commit is not conventional,
and PR-A only restores a value a human chose — and because a tag asserts what shipped. That commit must
carry `version = "0.1.0"`: tagging one whose version is derived (`dynamic = ["version"]`) would make the
baseline itself violate R10.

**Resolution (d) — what this corrects in D-037 and D-038.** Both are append-only and are not edited:

1. *D-037, "the pipeline writes no commit"* — false from now on: the pipeline writes **exactly one**
   commit per release and pushes it through the bypass. The invariant that survives is *one commit per
   release, always `chore(release): X [skip ci]`, signed, and no other write* (SPEC-110 R11).
2. *D-037, "`python-semantic-release` is a calculator, not a publisher"* — the engine is now the
   publisher: it writes the version, the changelog, the commit, the tag and the Release.
3. *D-037, "the changelog is the GitHub Release body"* — there is now a **versioned
   `CHANGELOG.md`**, machine-owned, and the Release body is the same notes. The reason D-037 gave for
   avoiding the file (a bot commit and a PR per release) no longer applies: there is no PR.
4. *D-037, "the tag is the single source of the version"* — the **literal** is the value an install
   reports and the tag is its record; the two are written by the same commit, and the job asserts they
   agree. The tag remains the only marker of *what shipped*.
5. *D-038, the ownership split* — the split stands; the column changes. SPEC-110 owns
   `[project].version` (the literal) and the **absence** of `dynamic`, `[tool.hatch.version]`,
   `hatch-vcs`, `[tool.semantic_release]` and the PSR row of `[dependency-groups] dev`; SPEC-000 keeps
   the backend, the base dependencies, the extras, the entrypoint and the tool config. The drift rule
   is unchanged: a diff inside a SPEC-110 key needs a SPEC-110 `spec_revision` bump, any other diff
   needs a SPEC-000 one.

**What loses.** HIFA-22's clause "bump the version in `pyproject.toml` and commit it" now *holds* — as
a bypassed commit, not as a PR; the part that loses is the assumption that the bump travels through
review like any other change. The "dynamic version" design of D-037 loses on the packaging side:
`hatch-vcs` and `dynamic` are removed, so a development install no longer reports `0.N.devM+g<sha>`;
it reports the last released version (SPEC-110 §2.2), which is the accepted cost of a literal. Nothing
in SPEC-050 loses: `--version` still prints `hifa <version>`.

**Verified (measured on this host, `semantic-release 21.1.2`, Node 22.22.3, scratch repo with a bare
`origin`).** `git tag` is called without `-m` by the engine (`lib/git.js:225-227`); `tag.gpgSign=true`
alone makes it abort; `tag.gpgSign=true` plus a `GIT_EDITOR` helper produces an annotated, SSH-signed
tag (`git cat-file -t` ⇒ `tag`, `git verify-tag` ⇒ *Good "git" signature for hifa-release*); the
release commit created by `@semantic-release/git` verifies (`%G?` ⇒ `G`) with only
`commit.gpgSign=true`; the engine pushes the commit to the branch; `feat` ⇒ `0.2.0` from `v0.1.0`,
`fix`/`chore`/`ci` ⇒ patch, a `BREAKING CHANGE:` footer ⇒ `1.0.0`, `feat!` **without** the footer ⇒
`no relevant changes`, `docs:`-only ⇒ exit 0 with no release, no tag at all ⇒ `1.0.0`; `--dry-run`
leaves the tree untouched. SPEC-110 §9 is the full table.

**Why not the alternatives.** (a) *Keep PSR and fix its flags*: the ambiguity is in the tool's output,
not in the configuration — that is why rev 3 needed seven rows; PSR can compute a version but cannot
write a changelog file, commit it and publish without losing the signature and the guard. (b) *Bot PR
with the bump*: two runs and two signed commits per release, and a merge gate that can stay open while
the version is decided (D-037 (c), still rejected). (c) *GitHub App as the bypass actor now*: better
scoping, but it adds an App, a key and a token exchange to a pipeline that does not exist yet — it is
the documented upgrade path, not the first version. (d) *No bootstrap tag*: the first release would be
`1.0.0`, violating HIFA-22's `0.x.0` criterion on a product that is not 1.0.

**Files:** `specs/DECISIONS.md`, `specs/110-release.md` (`version` 1 → 2, `spec_revision` 3 → 4),
`specs/000-system-architecture.md` (`spec_revision` 7 → 8), `specs/INDEX.md`; implementation:
`pyproject.toml`, `package.json`, `package-lock.json`, `.releaserc.json`,
`.github/workflows/release.yml`, `.github/release/tag-message.sh`, `CHANGELOG.md`,
`.github/signers/allowed_signers`, `AGENTS.md` §6/§9, `README.md` "Contributing".

**Corrigendum (pre-publication, 2026-11-11).** This entry was written and corrected before it was ever
committed, so the two adjustments below are made **in place** instead of appending a second decision: the
append-only rule protects published entries and none of this text has been published. *(1) PAT scopes:*
`repo` **only** — `workflow` authorises nothing this pipeline does and any additional scope is a widening
that has to be justified on its own (SPEC-110 §2.7.2, REQ-S110-03). *(2) Bootstrap target:* `v0.1.0` names
the **merge commit of the PR that restores the literal `0.1.0`**, never the initial commit — the import
commit is not conventional and its version would be derived, so tagging it would make the baseline itself
violate R10 (SPEC-110 §5.2, R12).

**Corrigendum (pre-publication, 2026-11-11) — the scopes of the installed token, as measured.** Item (1)
above states the requirement (`repo`, nothing else); this records what `HIFA_RELEASE_TOKEN` actually
carries, and supersedes that item as a description of the token that exists. The token was created with the
**`gh` CLI device flow**, and the CLI's OAuth app appends `gist` and `read:org` to every token it issues:
they cannot be removed one by one, only `repo` and wider scopes are selectable on the authorisation screen.
GitHub reports the effective set as `x-oauth-scopes: gist, read:org, repo`, so the token carries **`repo`
(required) + `gist` + `read:org`** — the **accepted ceiling** of this deviation, not an instance of least
privilege. What keeps the blast radius where SPEC-110 §2.7.2 puts it is what is **absent**:
`admin:ssh_signing_key` (it would let the token replace the account's signing keys, i.e. forge a signed
release) and `workflow` (it would authorise exactly the write §2.7.1 forbids, `.github/workflows/**`).
`gist` and `read:org` authorise nothing this pipeline does. Creating the token as a classic PAT with `repo`
alone removes the deviation; until then SPEC-110 §2.7.2 carries the normative text and this line records
the gap between it and the installed token.

## D-040 — The outcome classifier is an executable, tested script; a contract that lives only in YAML is not a rule
**Date:** 2026-11-11 · **Wins:** SPEC-110 §2.4.2 (rewritten as `version: 3`, `spec_revision: 6`) ·
**Touches:** `specs/110-release.md` (§1, §2.1.1, §2.3.3, §2.4, §2.4.1, §2.4.2, §2.4.3, §2.5.1, §2.5.2,
§2.7, §2.9, §4 R8/R17-R20, §5, §6, §7.1, §7.2, §8, §9, §10, §12), `specs/000-system-architecture.md`
(§8 step 5, Notes), `specs/INDEX.md`; implementation: `.github/release/assert-outcome.sh` (**new**),
`.github/release/tag-message.sh`, `.github/workflows/release.yml`, `.releaserc.json`,
`tests/e2e/test_release_outcome.py` (**new**), `tests/fixtures/release/**` (**new**),
`tests/unit/test_version.py`, `tests/e2e/test_release_contract.py`

P8 states the rule this decision applies: *a rule that is not executable is not a rule*. SPEC-110 rev 5
declared the outcome space of a release run — three greens and eleven error codes with a precedence — **as a
block of YAML inside `release.yml`**, and verified it with `tests/e2e/test_release_contract.py`, a suite that
reads files and asserts the presence of strings. The table was therefore documentation in a test-shaped
wrapper: nothing in the harness could observe what the block *does*.

**The gap, as measured.** Six semantic mutations of the pipeline, each of them keeping every asserted string
byte-identical, passed that suite **31/31** — among them a raw PAT written into the header instead of the
basic-auth pair, and the release-commit signature check turned off. That is not a flaw of the assertions;
it is what a document-shape linter is for. It matters here because the release job is the only writer to
`main` that no CI run validates (§2.4): the first execution of the classifier is the first release, and the
first release is exactly what a wrong classifier corrupts.

**Resolution (a) — the classifier becomes a file with a contract.** SPEC-110 §2.4.2 is rewritten around
`.github/release/assert-outcome.sh` (precedent: `.github/release/tag-message.sh`, §2.3.3):

| Property | Value |
|---|---|
| Step 9 | `if: always()` and **one command**: `./.github/release/assert-outcome.sh`. The workflow carries no classification logic |
| Inputs | `RELEASE_LOG`, `RELEASE_EXIT_CODE` (sentinel `255` when unset, never `0`), `RELEASE_DRY_RUN`, `PRE_RUN_HEAD`/`PRE_RUN_MAIN`/`PRE_RUN_TAGS`, `RELEASE_CODE_MARKER`, and the checkout as working directory |
| Output | **exactly one** classification line on `stdout` — `green: …` or `release_<code>` — and the exit status: `0` only for G1/G2/G3 |
| Determinism | **no network writes**: `git ls-remote` is read only (two queries, `refs/heads/main` and `refs/tags/*`), there is no `fetch` and no `push`; no write to the repository; the same inputs produce the same line and the same exit status |
| Coverage | pytest executes it from `tests/e2e/test_release_outcome.py`, against fixture logs in `tests/fixtures/release/` and a throwaway git repository in `$TMPDIR`: **one fixture per row** of §2.4.2 and **at least one negative fixture per row**, so the precedence is asserted and not just the branches |

**Resolution (b) — the other seven claims that had to move with it**, because a behaviour-level test makes
false claims visible:

1. **The version test asserts shape, never a value** (§2.1.1, R17): a test pinned to `0.1.0` is red after the
   first release, whose commit no CI run sees.
2. **Secrets are per step, and the install runs `npm ci --ignore-scripts`** (§2.4.3, R18): a workflow-level
   `env:` gave 616 packages' lifecycle scripts the admin PAT and the release key.
3. **The push credential never enters `$GITHUB_ENV`** (§2.4.1): it is exported inside the shell of step 8,
   and its absence from the environment file is asserted *after* the write (code id 12,
   `release_credential_persisted`), because a check that runs before the write proves nothing about it.
4. **One code per run, by ownership** (§2.4.2, R19): rows 1 and 2 belong to the gates, which print the code
   and write a mark in `$RUNNER_TEMP`; every other row belongs to the classifier, which prints nothing when
   the mark exists. Previously rows 1 and 2 were printed by both.
5. **G1 and G3 require `RELEASE_EXIT_CODE == 0` and a positive signal** (§2.4.2): a dry run that computed
   nothing is no longer "green: nothing written".
6. **The literal guard runs in `verifyConditions`, and `prepareCmd` fails unless the substitution is exactly
   one line** (§2.5.1): the guard used to run at tag time, after the commit had been pushed.
7. **The tag message is the name git is creating** (§2.3.3), read from the message file git seeds, not the
   literal in the tree; and `PRE_RUN_*` are required non-empty, so a failed capture is not a baseline.

**What this corrects in SPEC-110 rev 5, stated instead of rewritten.** Rev 5 said row 3 is printed by step 8
— it is printed by the classifier of step 9, once; it said the job-level `if:` is "a second net" — it is
gone, because `[skip ci]` is the loop breaker and a `contains()` copy of it also *skips* a legitimate release
whose squash body quotes the phrase; it put both secrets in the workflow-level `env:` and the base64 header
in `$GITHUB_ENV`; it had the message helper read the tree literal; and its §2.4.2 had eleven codes, no owner
column and a dedupe wired only for row 3. The catalogue now has **twelve** codes. **D-039 is not edited**
(append-only) and nothing in it loses: the engine, the bypass and the `v0.1.0` bootstrap stand. What D-039
claimed and D-040 narrows is the *verification*, not the design: the outcome table was documented, not
tested.

**How it is verified.** `pytest tests/e2e/test_release_outcome.py` (one fixture per row plus a negative per
row, on a scratch repository), `bash -n` and an executable bit on the script, `tests/e2e/test_release_contract.py`
for the document-level contract that remains (plugin order, the thirteen `releaseRules` rows, the guard in
`verifyConditionsCmd`), the workflow parsed as YAML for the per-step `env:` map (§7.2), and the simulation of
§7.2 that runs `tests/unit/test_version.py` against `version = "0.2.0"`. The regression that motivates the
decision is a criterion, not a memory: **the six mutations that passed the old suite must turn this one
red**, and a mutation that only rewrites a value while keeping a string identical is now a change of
behaviour by construction.

**Why not the alternatives.** (a) *Keep the YAML and add greps*: a grep cannot observe behaviour, and the six
mutations are the demonstration. (b) *A script without fixtures*: an untested script is the same act of
trust one level lower. (c) *Run the workflow with a local runner (`act`)*: heavyweight, needs Docker, and it
mocks precisely the runner semantics the defects touch — `$GITHUB_ENV` propagation, step-level `env`,
`if: always()`. (d) *A Python classifier instead of `bash`*: the job already depends on shell semantics
(`PIPESTATUS`, `git`, `grep -c`), and `tag-message.sh` sets the precedent of a governed shell file executed
by pytest.

**Files:** `specs/DECISIONS.md`, `specs/110-release.md` (`version` 2 → 3, `spec_revision` 5 → 6),
`specs/000-system-architecture.md` (`spec_revision` 8 → 9), `specs/INDEX.md`; implementation as listed in
**Touches**.

**Corrigendum — the five-axis review of the same pull request (SPEC-110 `spec_revision` 6 → 7).** Two of
the guarantees above were *declared* and not *observed*, and both were demonstrated by running them:
(1) evaluation order 1 matched names and literals, so a `$GITHUB_ENV` value holding
`base64("x-access-token:<PAT>")` — exactly what step 8 builds — still printed `green: released v0.2.0`,
and the row that exists to make that impossible passed it; it now decodes base64-shaped values and matches
a token by prefix, with a fixture per shape. (2) The contract suite compared the **set** of evaluation
orders, so exchanging two blocks (5↔6, 9↔10) with each marker keeping its own body left it green; the
precedence is the **sequence** in the file, and that mutation is now a case in the suite. Three narrower
corrections travel with them: rows 9 and 10 are narrowed to the states they can observe (a rejected tag
push is not a missing note, and `failed to push some refs` alone is not proof that the *tag* was refused),
step 9 tests that the classifier is on disk before invoking it, and the classifier refuses an argument
instead of classifying `--help` as a release. Nothing in the design moves: this is the criterion of R20
applied to the decision that wrote it.

## D-041 — `hifa init` writes `.hifa.toml` only inside a git work tree; P-12 is closed
**Date:** 2026-11-11 · **Wins:** SPEC-100 §4.2 R13 · **Touches:** SPEC-100 §2.1, §2.7, R3, R12, R13,
§5.2, §7, Notes; `src/hifa/config.py`; `src/hifa/cli/commands/init.py`; `tests/unit/config/`,
`tests/e2e/cli/`; `README.md`; `specs/INDEX.md`

`hifa init` used to write `.hifa.toml` in the cwd unconditionally. A single run in a shared scratch
directory therefore put a workspace file above everything created below it, and the walk-up of R3
handed that database to every one of those projects, silently. P-11 (the walk-up stops at the first
`.git`) narrowed the blast radius but not the class: a directory with no `.git` ancestor still
inherits whatever config sits above it — and the file is written *before* anybody knows whether a
project exists there.

**Resolution:** the write is conditional on the working directory being inside a git work tree, and
nothing else changes. The guard lives in `config.py` as `find_project_root(start)` — the nearest
ancestor of `start`, itself included, that contains `.git`, whether that path is a directory (a
clone) or a file (`gitdir: …`, a linked work tree or a submodule). It is the **same** notion of
project root that bounds `find_config_file`'s walk-up, and `find_config_file` is now expressed in
terms of it, so the write guard and the resolution guard cannot drift apart. `init` consumes it:
`_write_config` returns "no path" plus the status `outside a git work tree` when the guard
suppresses the write, and the report prints `config: none (outside a git work tree)` — the fourth
shape of a field that already had `created`, `existing` and `rewritten`.

What the guard deliberately does **not** do:

- **It does not touch resolution.** `--db`, `HIFA_DB` and an ancestor `.hifa.toml` keep deciding the
  database exactly as R1 says; outside a work tree the effective base is the global one of R5
  (`$XDG_DATA_HOME/hifa/hifa.db` or `~/.local/share/hifa/hifa.db`) when no other source speaks. A
  guard on the *path* would have broken `hifa init --db /tmp/x.db` from a scratch directory, which
  is a legitimate way to seed a database on purpose.
- **It does not fail and does not change the exit code.** Writing no file is the correct outcome,
  not an error: exit stays `0` and the seeding is complete, so the report has to say which base was
  used — otherwise the user cannot tell a project-local run from a global-DB run.
- **`--force` does not lift it.** There is no file inside the tree to rewrite, and a flag that means
  "re-seed what is missing" is not a licence to invent a project. No `.bak` appears either.
- **It is not a git call.** `Path.exists()` on `.git` is enough for "is there a work tree here", and
  `config.py` stays stdlib-only (REQ-N100-04): no `git` subprocess, no `subprocess` import, no
  work-tree parsing, so `hifa init` keeps working with git absent from `PATH`.

**Why not the alternatives.** (a) *Write the config only when the directory already has a `.git`,
and otherwise print a warning*: a warning does not undo a file that poisons every sibling below it —
the class of failure is "a stray ancestor file", and the only reliable cure is not creating it.
(b) *Drop `.hifa.toml` from `init` altogether*: it is the only place where `[team]`, `[cli]` and
`[web]` are materialised for a project (SPEC-100 §2.3, R16 `0600`), and every other command reads it;
removing the write would leave `[web] host/port` and `default_team` with no file to live in.
(c) *A `--no-config` flag*: it makes the safe behaviour opt-in, and the default is the one that
poisons the tree.
(d) *Put the guard in `init.py`*: the notion of project root already lives in `config.py` next to
`find_config_file`, and a second copy in the CLI edge is exactly the duplication P4 forbids.

**Verification.** Unit tests cover the helper (the repo directory, a subdirectory of the repo, a
`.git` that is a file, and a tree with no `.git` ancestor); the CLI tests cover the four states of
the `config` field and the two guards — outside a work tree no `.hifa.toml` and no `.hifa.toml.bak`
appear, the resolved database is the XDG one, `HIFA_DB`/`--db` still win, and the report carries
`none (outside a git work tree)`; inside a work tree the file is written, a second run reports
`existing` and `--force` reports `rewritten` with a `.bak`. The outside-a-work-tree test is required
to fail against the previous implementation: it asserts the absence of the file, which the old
code created on every run, and it was run red before the fix landed.

P-12 is closed by this decision and its entry is left as written (append-only); nothing in P-12's
description loses, the proposed fix it recorded is what R13 now states.

## D-042 — `invalid_transition` has exactly one cause: writing an archived issue
**Date:** 2026-11-13 · **Wins:** SPEC-040 §6 (R20) · **Touches:** SPEC-040 §2.2, §4.1, §5.5, §6;
SPEC-050 §2.5.4, §2.6, §2.6.1, R5; SPEC-060 §6.1, §6.2, §6.3, §7; SPEC-070 §5.4

The three edges publish the same five buckets of D-014 for the errors of a write, and one of them
carries a single `code`: `invalid_transition`. The map has to be the same map, because the CLI, the API
and the dashboard are three transports of one application layer (A7, P4): a client that learns the
rule on one edge must not have to relearn it on the next.

**Resolution.** In v1 `invalid_transition` — exit **5** in the CLI, **409** in the API — has exactly
one producer: R20 of SPEC-040 §6, `ISSUE_ARCHIVED`, raised when a command writes an issue whose
`archived_at` is not null. Everything else that reads like a state-machine refusal is `Validation` —
exit **2**, **422**, `validation_error` — because that is the class SPEC-020 §2.5 raises:
`CLOSE_WITH_OPEN_CHILDREN` (a terminal target or an archive with open sub-issues), `STATE_ARCHIVED`,
`STATE_TEAM_MISMATCH` and `ISSUE_TRASHED`. A parent with open sub-issues is 422, not 409, on every
edge.

Consequences, recorded in the specs:

- **`cycle close` loses exit 5.** `complete_cycle` declares `NotFound(cycle)` and
  `Conflict(CYCLE_ALREADY_COMPLETED)` and no clock-based refusal (SPEC-040 §2.2, §6), so a cycle whose
  `ends_at` is in the future is closed like any other and `hifa cycle close` exits 0, 3 or 4. A refusal
  that no use case can raise is a rule the code cannot obey, so the condition is removed instead of
  extending the layer to satisfy it.
- **The payload names the reference.** `InvalidTransition` carries `rule='ISSUE_ARCHIVED'` and
  `field='ref'`: the parameter that carried the reference is called `ref` on all three surfaces
  (`hifa issue <verb> REF`, `POST /api/v1/issues/{ref}/…`, `data-identifier` on the board), so one
  field name reaches the client whichever edge it uses.
- **SPEC-050** §2.6.1 lists the single condition, §2.6 and §2.5.4 stop offering exit 5 for a cycle, and
  R5 stops listing `cycle close` among the idempotent verbs.
- **SPEC-060** publishes the map in §6.1; its example and its `move`-to-Done scenario answer
  `validation_error` 422.
- **SPEC-070** §5.4 step 4 answers 422 and keeps the same board reload and toast it already had.

**Why not the alternatives.** (a) *Give `CLOSE_WITH_OPEN_CHILDREN` an `invalid_transition` code*: it is
a domain rule that needs a count, not a transition of a state machine, and SPEC-020 reserves the class
for R20 on purpose. (b) *Make `complete_cycle` refuse a future cycle*: that is a product rule about
cycles which no requirement of v1 states; inventing it here would add a second producer to the layer to
satisfy a sentence of SPEC-050. (c) *Publish one field name per edge*: the same error would arrive with
different payloads depending on the transport, which is what the edge parity of P2 forbids.

## D-043 — Sending an issue to the trash is a per-issue write, not a subtree

**Conflict.** `001-mvp-v1.tasks.md` (T-06) describes sending an issue to the trash with
`trashed = 1` on the issue **and its descendants inside one transaction**. `SPEC-040` §5.5 declares
exactly one UPDATE of `trashed`, on the addressed issue, and `SPEC-050` §2.5.3 states that
`issue delete` has no cascade.

**Resolution: the component specs win, and the trash stays per-issue.** `SPEC-040` and `SPEC-050`
are normative for the write; the task line describes an intention that no requirement of v1 restates
and that the archive axis does not need: a parent and a child are independent rows, each with its own
`trashed` layer, and hiding a subtree is a matter of the read, not of the write.

- **SPEC-040** §5.5 keeps one UPDATE of `trashed` on the addressed issue.
- **SPEC-050** §2.5.3 keeps the "no cascade" sentence.
- **001-mvp-v1.tasks.md** T-06 is superseded on this point and follows the two component specs.

**Why not the alternative.** Cascading the trash would make one write touch rows the caller never
addressed, and restoring the parent would then have to guess which children to bring back — a subtree
semantics the archive axis deliberately does not have (D-015 keeps every layer reversible per row).

## D-044 — Inverting the direction inverts the values, not the treatment of absence

**Date:** 2026-11-13 · **Wins:** SPEC-040 R18 · **Touches:** SPEC-030 §2.5, §2.6, §2.6.1; SPEC-040
R18, §5.3; SPEC-050 §2.5.3.1, R8; SPEC-060 §2.3, R4

SPEC-040 R18 requires that a `NULL` goes to the end **in both directions**. SPEC-030 §2.6.1 built the
clause from a single `dir` variable that inverted the key **and** the `issue.id` tie-breaker, which
under `reverse` sends the rows without a value to the top: the `due` key was
`COALESCE(i.due_date, '9999-12-31')` and the `priority` key mapped `No priority` (0) to the sentinel
`5`, and both sentinels are values that a descending direction moves.

**Resolution: R18 wins, and the position of the absent rows is not a direction.** The total order has
two parts, and only the second one moves:

```
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> {dir}, i.id {dir}
     with dir = "DESC" if filters.reverse else "ASC"
```

- **The absence flag always ascends.** `(<sort_key> IS NULL)` is `0` for a row with a value and `1` for
  a row without one, so the valued rows come first and the absent rows last in `ASC` and in `DESC`
  alike. `dir` reaches the key and the tie-breaker only; it never reaches this term.
- **The key expressions express absence as `NULL`, not as a sentinel.** `priority` is
  `CASE i.priority WHEN 0 THEN NULL ELSE i.priority END` and `due` is `i.due_date`. A sentinel is a
  value and therefore moves with the direction; `NULL` cannot enter the key comparison at all, which
  is what keeps the absent rows outside the part of the order that reverses.
- **The tie-breaker moves with the key.** Inside the absent rows the order is still `i.id {dir}`, so
  the second part stays total and stable for rows that share the absence.
- **Two orders have an absence: `priority` and `due`.** `manual`, `created` and `updated` are
  `NOT NULL` columns (SPEC-010), their flag is `0` for every row and their clause degenerates to the
  plain keyset.
- **The keyset predicate is declared per case, not as a row comparison.** A comparison against a
  `NULL` anchor evaluates to `NULL` and matches no row, so SPEC-030 §2.6.1 states the predicate of the
  next page twice — the valued anchor with three disjoint branches, and the `NULL` anchor restricted to
  the rows that are also without a value and follow by `id`.

**Why.** A reader who reverses an order asks for the contrary sequence of **values**, not for the rows
without a value to jump to the top: "latest first" means "the newest due dates first", and an issue
with no due date has no place in that sequence. The listing also has to stay a total, stable order,
which is the property the keyset stands on: if the absent rows changed ends with the direction, the
boundary between the valued and the absent section would move under a predicate that seeks from an
anchor on the other side of it, and the page that crosses that boundary would repeat or skip a row.
An ascending flag makes one predicate per case exact instead of approximate.

## D-045 — The four relation types are one table, and each type has one stored order

**Date:** 2026-11-13 · **Wins:** SPEC-020 §2.1 · **Touches:** SPEC-020 §2.1, §2.2, §4.1 R7, REQ-F020-20;
SPEC-040 §2.1, §3.1, R11, REQ-F040-10; SPEC-050 §2.5.3, R18; SPEC-080 §3, §5.3, §6.1, R25

`issue_relation` holds one row per edge with four `type` values (SPEC-010 §3). Four facts about each type
decide every read and every write: which end is the source, whether the type is symmetric, which name is read
from each end, and whether the edge gates work. Written down in more than one spec they drift — one layer says
"source → target", another "issue → related" — and a reader cannot tell which of the two orders is the stored
one.

**Resolution: SPEC-020 §2.1 carries the table; no other spec restates it.**

| `type` | stored pair | `is_symmetric` | name from `issue_id` (`out`) | name from `related_issue_id` (`in`) | `is_hard` |
|---|---|---|---|---|---|
| `blocks` | `(blocker, blocked)` — the order the caller gave | `False` | `blocks` | `blocked_by` | `True` |
| `duplicate` | `(min ULID, max ULID)` | `True` | `duplicate` | `duplicate` | `False` |
| `related` | `(min ULID, max ULID)` | `True` | `related` | `related` | `False` |
| `similar` | `(min ULID, max ULID)` | `True` | `similar` | `similar` | `False` |

- **`blocks` keeps the direction it was given.** The source is the blocker and the order *is* the meaning of
  the row: swapping it inverts the fact, because `A` blocks `B` is not `B` blocks `A`.
- **The symmetric types are stored in a canonical order, and every writer applies it.** `RelationType.canonical_pair`
  sorts the two ULIDs by code point and returns `(min, max)`. Both write paths call it before the INSERT —
  `relate_issues` (SPEC-040 R11) and `import_json` (SPEC-080 §5.3) — so the pair always reaches the repository
  already normalised and `UNIQUE(issue_id, related_issue_id, type)` of SPEC-010 sees one triple per edge. The
  `UNIQUE` is the **backstop**, not the rule: it catches a writer that skipped the normalisation, and the same
  gap is closed at the other end by the inverse-order lookup `validate_relation` keeps for a caller that did.
  An `INSERT` that reaches SQLite unnormalised is a bug of the writer, not a case the schema is asked to
  classify.
- **The normalisation has one owner.** `canonical_pair` is the only place the order is decided; `relate_issues`
  calls it (SPEC-040 R11), `import_json` calls it (SPEC-080 §5.3) and `validate_relation` documents that its
  arguments arrive ordered, keeping the inverse-order lookup as the net for a caller that skipped that step.

**Why.** `UNIQUE(issue_id, related_issue_id, type)` indexes an ordered triple: `(A,B,related)` and
`(B,A,related)` are two different keys to SQLite although they are one fact. Without a canonical order the
table accepts the same edge twice, every read that looks at both ends returns it twice, the panel lists the
same issue on both sides, and `unrelate` deletes one row while the other keeps the edge alive — the relation
reappears on the next load. Sorting by ULID yields exactly one storable order per pair with no trigger, no
CHECK and no migration: both writers apply the same function, and the constraint the schema already declares
stands behind them as the backstop — it refuses the second spelling, it does not decide the first. The ULID is
the key because it is the only total order the two rows share — `identifier` belongs to a team and the pair may
cross teams.

**Why not the alternatives.** (a) *Store both directions and keep them in sync*: two rows to insert, delete
and repair, with no constraint able to express "these two rows are one edge" (D-046). (b) *Order the pair
inside the SQL* (`MIN`/`MAX` in the INSERT): the application layer would carry a second copy of the rule and
the read would still re-derive the order to answer `blocked_by`. (c) *A `UNIQUE` over
`MIN(issue_id, related_issue_id)` / `MAX(...)`*: there is no functional index in the schema of v1, so every
query would compare two spellings of the same pair.

SPEC-060 R10 and the relation panel of SPEC-070 §2.3.4 already read this rule the same way — one edge, the
inverse resolved on read — so neither is edited; SPEC-030 §2.4 `relations` already returns the issue's rows
in both directions, which is what the canonical order needs, so the schema and its `UNIQUE` stand as they
are. SPEC-080 §5.3 is the second writer: `import_json` normalises every `issue_relation` row with
`canonical_pair` before the `INSERT … executemany`, and a row that normalises onto an edge already stored — in
the file or in the destination — is the `RELATION_DUPLICATE` conflict SPEC-040 publishes for the same
duplicate, never a silent second row.

## D-046 — `blocked_by` is read, never stored

**Date:** 2026-11-13 · **Wins:** SPEC-020 §2.1 · **Touches:** SPEC-020 §2.1, §2.2 `IssueRelation`, §4.1 R7;
SPEC-040 §2.1 `RelateIssues`, §2.2, R11; SPEC-050 §2.5.3, R18

One `blocks` edge is visible from both ends: `A blocks B` reads as `B blocked by A` for the issue that is the
target. The table of SPEC-010 has four `type` values and none of them is `blocked_by`.

**Resolution: `blocked_by` is a name, not a row.**

- **It is derived on read.** `RelationType.name_as_seen("in")` answers `inverse_name` for the end that is
  `related_issue_id` — `blocked_by` for `blocks`, the type's own value for the symmetric types. A surface
  renders it by replacing `_` with a space and invents no second name.
- **`RelationType.parse` is the only input alias.** `'blocked_by'` → `(BLOCKS, swap=True)`: the pair the caller
  wrote is swapped before `canonical_pair` and the INSERT, so `relate A blocked_by B` stores `(B, A, blocks)`
  and means `B` blocks `A`. The four stored tokens → `(value, False)`; anything else is
  `Validation(rule='COMMAND_FIELD', field='type')`.
- **The inverse is not a field.** `IssueRelation` mirrors the row (`id`, `issue_id`, `related_issue_id`,
  `type`, `created_at`) and carries no `blocked_by`, no `inverse` and no `is_blocked`. The swap is input
  vocabulary and never reaches the stored `type`, which is one of the four values of the `CHECK`.
- **The verdict is derived too.** A `blocks` edge gates while the blocker is not finished: the answer is
  `is_hard` plus `WorkflowStateType.is_terminal` of the blocker's state, computed on read. `completed` and
  `canceled` both release the blocked issue, and no flag is written on the edge or on the issue, so a finished
  blocker cannot leave a task blocked by a stale column.

**Why.** Two rows for one fact need a writer that inserts both and a writer that deletes both, and nothing in
the schema can reject a pair that lost a half; each read would then answer according to the end it started
from. Swapping once, where the input vocabulary is parsed, keeps the decision in one place: `app/` hands the
repository the pair it already normalised, every read begins at a row whose source is the blocker, and
`blocked_by` costs one property call instead of a second query.

**Why not the alternatives.** (a) *A fifth `CHECK` value*: the vocabulary of the table would stop matching
SPEC-010, and the two rows of one edge would still need synchronising. (b) *A flag on `issue`*: an edge has
two ends and a column has one; the flag would be a second truth on the entity. (c) *Let each edge map the
name*: the CLI, the API and the panel would each decide when `blocks` reads as `blocked_by`, which is the
drift this decision removes (P4).

## D-047 — Hierarchy and relation refusals: one code each, and the parent layer does not cascade

**Date:** 2026-11-13 · **Wins:** SPEC-020 §2.5 · **Touches:** SPEC-020 §2.5, §4.1 R6/R7, §6; SPEC-040 §5.1,
§5.4, §6, R11; SPEC-050 §2.5.3

The hierarchy and the relations share the `issue` table and one set of validations, and both refuse more than
a cycle: a parent from another team, a tree that is too deep, an edge that crosses a workspace. A refusal with
no name is a refusal each layer spells for itself — and the depth bound was stated for a create while a
reparent of a chain could push a descendant past it.

**Resolution: seven codes, declared in SPEC-020 §2.5 and mapped once in SPEC-020 §6.**

| `rule` | Raised by | Condition | Class | CLI | HTTP |
|---|---|---|---|---|---|
| `PARENT_SELF` | `validate_parent` | `parent.id == child.id` | `Validation` | 2 | 422 |
| `PARENT_TEAM_MISMATCH` | `validate_parent` | parent and child in different teams | `Validation` | 2 | 422 |
| `PARENT_CYCLE` | `validate_parent` | `child_id` appears in the ancestor chain of `parent` | `Validation` | 2 | 422 |
| `PARENT_DEPTH_EXCEEDED` | `validate_parent` | `len(ancestors) + 1 + subtree_height > MAX_SUBISSUE_DEPTH` | `Validation` | 2 | 422 |
| `RELATION_WORKSPACE_MISMATCH` | `validate_relation_scope` | the two ends live in different workspaces | `Validation` | 2 | 422 |
| `RELATION_SELF` | `validate_relation` | `source_id == target_id` | `Validation` | 2 | 422 |
| `RELATION_DUPLICATE` | `validate_relation` (and the `UNIQUE`) | the triple exists, or its canonical inverse does | `Conflict` | 4 | 409 |

- **Depth: five levels, root = 1, counting the subtree that moves.** `MAX_SUBISSUE_DEPTH = 5` and the bound is
  evaluated as `len(ancestors) + 1 + subtree_height`: `ancestors` is the chain of the parent (so its length is
  the depth the parent already has) and `subtree_height` is the longest downward path below the issue being
  attached (0 for a leaf and 0 for an issue that does not exist yet). A create and a reparent are then bounded
  by the same expression; without the height, moving a three-level subtree under a level-3 issue would leave a
  descendant at level 6 while every individual write looked legal.
- **Team and workspace are different axes.** The hierarchy never crosses a team (`PARENT_TEAM_MISMATCH`); a
  relation never crosses a workspace (`RELATION_WORKSPACE_MISMATCH`) and *may* cross teams. The workspace of an
  edge is the one fact a relation shares with the hierarchy, and the far end's team travels in the read model
  so a surface can still label it with its own key (SPEC-030 §2.6, `IssueDetail.relations`).
- **The bucket follows D-014 and D-042.** Only `RELATION_DUPLICATE` is `Conflict` / exit 4: it is uniqueness,
  the case bucket 4 exists for, and the same bucket the `UNIQUE` produces when the domain lookup is skipped.
  The other six are `Validation` / exit 2 / 422 — D-042 puts every domain refusal there.
- **The reads stay in `app/`.** `ancestors` comes from `IssueRepo.ancestor_chain` and the height is counted
  from `IssueRepo.list_children` inside the same transaction (SPEC-030 §2.4); the domain decides with the
  numbers it receives and reads nothing (SPEC-020 §1).
- **The parent layer neither reparents nor cascades.** Archiving or trashing a parent writes one row — its
  `archived_at` or its `trashed` (SPEC-040 §5.5) — and changes no child: `parent_id` is untouched, no child is
  archived, trashed or hidden, and `restore_issue` clears only the columns of the addressed issue. Archiving a
  parent is still refused while it has open children (`CLOSE_WITH_OPEN_CHILDREN`, R6), so the layer never
  detaches a live subtree; the per-row reversibility is D-015 and the trash of one issue not being a subtree
  write is D-043. This decision cites both instead of restating them.

**Why.** Each refusal needs a name that the three edges map identically, or the CLI, the API and the dashboard
answer three different codes for one rule (A7, P2). The depth bound needs the height of the moved subtree
because the tree is bounded as a whole, not per write. And the parent's archive must stay a per-row write: a
cascade would touch rows the caller never addressed, and restoring the parent would have to guess which
children to bring back — the subtree semantics the archive axis deliberately does not have.

**Why not the alternatives.** (a) *One `PARENT_INVALID` for the four hierarchy cases*: the user gets the same
message for a typo in the reference and for a subtree that is too deep, and no test can tell them apart.
(b) *Bound the depth on the moved issue only*: it is the create rule, and it lets a reparent build a tree
deeper than the constant names. (c) *Make the depth and the scope `Conflict` because they feel structural*:
bucket 4 is uniqueness and version (D-014), and a cross-workspace edge is a bad reference, not a duplicate.
(d) *Reparent the children to the parent's parent when it is archived*: it rewrites rows the command did not
address and makes the archive a subtree operation (D-015, D-043).

**Verification.** Unit tests per `REQ-F020-10` and `REQ-F020-11`, the acceptance scenarios of
`specs/acceptance/001-mvp-v1/07-hierarchy-relations.feature`, and the `# raises:` lines of SPEC-040 §2.2 and
the use-case matrix of SPEC-040 §6, which list every code above.

## D-048 — The relation reads: the row travels, the direction is derived, `blocked` is its own read

**Date:** 2026-11-13 · **Wins:** SPEC-050 §2.5.3 · **Touches:** SPEC-050 §2.3, §2.4.1, §2.5.3, §7, R5;
SPEC-030 §2.5, §2.6, R5, §2.1 export table; SPEC-040 §2, §6

The domain has the relations since v0.4.0 — four types, one stored pair, the seven refusals — and no verb of
SPEC-050 reads them: a user can write an edge with `issue relate` and then has no way to see it, and no way to
ask which issues are stuck. Three questions had no answer in the contract, and each edge would have answered
them differently: what a read of one issue's edges emits, whether the reading direction travels in that
envelope, and whether "blocked" is a filter of the listing or a query of its own.

**Resolution: two read verbs, declared in SPEC-050 §2.5.3, over reads that already exist or are added once.**

| Verb | Reads | `--json` | Exit |
|---|---|---|---|
| `hifa issue relations REF` | `issue_detail(db, REF).relations` (SPEC-030 §2.6) | `{items: Relation[]}`, the five keys of SPEC-050 §2.4.2 | 0,3 |
| `hifa issue blocked [--team KEY]` | `list_blocked(db, team=…)` (SPEC-040 §2.2) over `blocked_view` (SPEC-030 §2.6, SQL-10) | `{items: Issue[]}`, the items `issue list --json` emits | 0,2,3 |

- **`relations` emits the stored row, five keys, from either end.** `id, issue_id, related_issue_id, type,
  created_at` and nothing else: no `direction`, no `blocked_by`, no far end's `identifier`, no `resolved`.
  This is D-046 applied to a read: the object is the row, and the directed reading is a function of the row
  and the issue that asks.
- **The direction is recovered, not transported.** For a `blocks` row, `out` iff `issue_id` is the asking
  issue's ULID and `in` iff it is `related_issue_id`; the symmetric types have no direction, because their
  pair is stored in ULID order and that order is storage, not meaning (D-045). A consumer needs the asking
  issue's ULID — the `id` of `issue show REF --json`, or the ULID it typed — and the row's two ULIDs, which
  the envelope already carries. The names are `RelationType.name_as_seen` of SPEC-020 §2.1; the table prints
  them, the JSON does not need them.
- **`resolved` is not a field either.** Whether a blocker still blocks is `is_hard` plus the blocker's
  `state.type` on read (SPEC-020 §4.1 R7): a property of the two issues at read time, not of the edge. A key
  carrying it would freeze a verdict that the next `issue move` invalidates.
- **The hierarchy is not a relation.** A sub-issue is `issue.parent_id`, not a row of `issue_relation`; the
  hierarchy is read by `issue list --parent` and `issue show --with children` (§2.5.3, GAP-06). `relations`
  answers with the five names of the relation table and never a sixth.
- **`blocked` is its own read, not a listing filter.** The predicate — an incoming `blocks` edge whose source's
  state is neither `completed` nor `canceled` — is SPEC-020 §4.1 R7 and gets one SQL expression (SPEC-030
  SQL-10). It is not a `ListFilters` field: the filter object is the input of the listing and of the board
  (D-011, D-027), and a relation verdict inside it would be reachable from two verbs with two different flag
  sets, while `blocked` has neither a cursor nor a limit nor an order flag to fill. Only the two layers hide a
  row (`visible`), never the blocked issue's own state: an issue closed while a blocker is still open is
  exactly the row a reader has to see.
- **`relate` stays the only verb that writes an edge.** `link` and `unlink` are not declared and no alias is
  added: the tree of §2.5 is closed (R17) and an undeclared verb is exit 2 `invalid_option`. The reverse
  direction is `TYPE=blocked_by` (D-046) and the undo is `--undo`. For the same reason R5 stops listing
  `relate` among the idempotent no-ops: the duplicate is `Conflict(RELATION_DUPLICATE)` / exit 4 (D-047).
- **The refusals keep their one field name.** `COMMAND_FIELD` carries `field: type` — the token outside the
  five, the name the command, the parser and the `Relation` key share — and `RELATION_SELF` /
  `RELATION_WORKSPACE_MISMATCH` carry `field: related`, as SPEC-020 §2.5 already declares. No second spelling
  of the same field is introduced at an edge.

**Why.** The two reads are what turns a stored edge into a capability: without them the CLI can write relations
and cannot show them, so every consumer — the dashboard panel, an agent script — would build its own reading of
the same table and its own answer to "is this blocked", which is exactly the duplication P4 removes. Keeping the
envelope at five keys costs the consumer one comparison it can make from data it already holds, and it keeps one
fact in one place; adding `direction` would make the same edge describable in two ways depending on which end was
queried, the drift D-046 closed for the entity. `blocked` gets a read because the predicate is a join over two
tables plus a state type, not a column of `issue`: it belongs where the SQL lives, next to `board_view`, and not
in the filter matrix of a listing that must stay expressible as one `ListFilters`.

**Why not the alternatives.** (a) *A `direction` key in the item*: a sixth key about one edge, derivable from the
other five and from the caller's own reference — a second truth, and it would have to be `null` or absent for the
symmetric types, which breaks "emitted complete, no key omitted" (R2). (b) *A nested shape per direction*
(`{blocks: [...], blocked_by: [...]}`): a second envelope for `Relation`, and the same row would appear under two
keys for the symmetric types. (c) *`issue show --json` gaining the collection*: `--with relations` is GAP-06 and
the base `Issue` object is closed; a read with its own verb is cheaper than a key on the most-used object of the
CLI. (d) *`issue list --blocked` as sugar over `ListFilters`*: the flag would have to reach the board and every
`list_issues` caller, `blocked` would inherit a paging contract it does not want, and the two verbs would answer
the same question with different exit sets. (e) *Deriving `blocked` in the CLI from `relations` plus
`issue show`*: N+1 calls at the edge and a rule copied into `cli/`, which P4 forbids.

**Verification.** The `# raises:` lines and §6 matrix of SPEC-040, SQL-10 and R5 of SPEC-030, the notes and the
§7 scenarios of SPEC-050, and the `relations` / `blocked` / invalid-type scenarios of
`specs/acceptance/001-mvp-v1/07-hierarchy-relations.feature`.

## D-049 — The revision is one opaque number from `data_version` on a single long-lived read connection, polled

**Date:** 2026-11-13 · **Wins:** SPEC-070 §2.1.3.1 · **Touches:** SPEC-070 §2.1.3, §2.1.3.1, §2.1.4, §2.3.1,
§2.6.6, §2.6.8, §3, §4 (R12, R26), §5.6, §6, §7, §8, §10, §11

The dashboard renders a page and the page keeps showing that render. The CLI is a second writer to the same
SQLite file — D-030 already accepted that for the sidebar — so an issue created in a terminal, or five of them
moved by an agent, are invisible in an open tab until the user reloads it. The fix must be a **change signal**
and not a timer: re-rendering on a schedule fights the scroll position, the open panel and the half-typed quick
add, and pays a query for a page nobody changed.

Three sources were on the table and the choice among them is not stylistic, because the cheapest one is
worthless under the connection model the edge has today. Measured on this host, three connections over one
database:

| Reading | `data_version` |
|---|---|
| the same connection, before a foreign commit | 1 |
| the same connection, after that commit | 2 — it changed |
| a **new** connection after the same commit | 1 |
| another new connection | 1 — it never changes between requests |

And the edge opens a connection **per call**: `Database.connect`/`ro_connection` →
`open_db` (`src/hifa/adapters/sqlite/connection.py:102,105`), while `create_app` builds a single `Database`
object and every request asks it for a connection (`src/hifa/web/app.py:894`). So `data_version` is the right
answer only if the probe stops using that path.

**Resolution: the probe owns one long-lived read-only connection per process, and the number is its raw
`data_version`.**

- **One connection, created lazily, closed by `lifespan`.** The first `GET /_/revision` opens a read-only
  connection (`Database.ro_connection()`), the app keeps it on `app.state`, every later probe reuses it, and a
  `lifespan` shutdown handler closes it. One per process, not one per request, not a pool: the value is
  per-connection state, and a connection that is opened for the request that reads it has no history to compare
  against — it answers its baseline every time (the table above).
- **Read in autocommit, never inside a transaction.** A connection holding a transaction keeps its snapshot and
  `data_version` does not advance; it would also pin the WAL and keep checkpoints from running. The probe
  executes the `PRAGMA` and nothing else, so between probes it holds no read lock — which is why SPEC-001 EC-012
  ("`serve` opens one connection per request, no long transactions") is still true: the invariant is about
  transactions, and this connection never opens one.
- **The number emitted is `data_version`, with nothing added to it.** Not a sum, not an offset, not an epoch, not
  a timestamp. One number, and it is compared for **equality** only: it is opaque, so nothing orders it, subtracts
  it, or shows it to a user.
- **The in-process counter is discarded, with the case it would have covered.** It would cover "the web process
  wrote something", and that case is already covered: the process writes through its request connections, which
  are *other* connections than the probe's, so a quick add, an inline edit or a drop bumps the number exactly
  like the CLI does. A counter would be a second truth about the same fact, and one that every write route can
  forget to bump — a rule without an enforcement (P8).
- **`max(updated_at)` is discarded, with the case it cannot cover.** `workflow_state` has **no** timestamp column
  at all (SPEC-010 §3, `001_init.sql`): a column reorder writes `position` and nothing else (R25), so the last
  resort would not see the one change the board is most likely to be watched for. Two more reasons: `updated_at`
  is `TEXT` whose resolution merges two writes in the same second into one value, and the check costs a scan of
  three tables per poll per tab, where the `PRAGMA` costs nothing. It stays documented as the answer for a
  *different* connection model: if the probe had to use a connection per request, `data_version` would be
  unusable and this would be the only remaining source, with the column-reorder hole declared.
- **A restart costs exactly one spurious reload.** `data_version` starts from its baseline in each process, so a
  page rendered by the old process carries a number the new one will not produce; the poll sees a difference,
  re-renders once, and adopts the new number — the page is correct from then on. No epoch, token or generation
  counter is invented for it: an epoch would be a second number whose only job is to suppress a reload that is
  cheaper than the mechanism that avoids it.
- **A replaced file is not a commit.** The long-lived connection reports commits to the file it opened, so a
  database swapped underneath a running server (a `.bak` put back in place, a fresh `hifa init`) is invisible to
  the probe and `hifa serve` needs a restart. The layer already assumes a stable file for the life of the process
  (`user_version` and the viewer are resolved at startup), so this is a boundary stated rather than a behaviour
  introduced: per-request connections happened to follow the path, and the contract does not promise that.
- **The over-approximation is accepted and named.** The number changes on *any* commit by another connection — a
  write to another team, a migration, an `issue_history` row. The consequence is one redundant fragment render,
  never a missed change: the signal errs towards showing the truth twice, which is what a poll of one `PRAGMA`
  can afford.
- **A write of the page itself does not provoke a reload.** Every `200`/`201` of the layer answers with
  `HX-Revision` and the page adopts it, so the poll finds the numbers equal on the next tick instead of
  re-rendering what the user's own action just produced. The residual is declared: a foreign commit that lands
  between the page render and the user's own write is masked by the adopted number and shows up on the next
  commit or on a reload — the number is opaque, so the page cannot tell how many commits it skipped.
- **The transport is a poll, and it is honest for this deployment.** One local process, one user, one SQLite
  file. A push channel would need a long-lived connection per tab, fan-out and reconnection state inside the
  server, and a second protocol in a layer whose whole contract is "HTML over HTTP" — and it would still poll
  the file underneath, because SQLite does not notify anyone. `hx-trigger="every REVISION_POLL [visible]"` is one
  request per **visible** tab every 5 s, with no reconnect story, no state on the server and no daemon to keep
  alive; hidden tabs cost nothing. The interval is a single constant so that changing it is one edit.
- **The visibility guard cannot ride on the trigger.** htmx compiles a trigger's `[condition]` filter through
  `Function`, and this layer serves `script-src 'self'` (SPEC-070 §2.1.4) — no `'unsafe-eval'`. An eval that is
  refused is caught by htmx and reported as *do not fire*, so the filter would be not a weaker guard but a silent
  stop: the probe would never run and the dashboard would look untouched. The interval stays unfiltered in the
  attribute and the guard moves to the client, which cancels `htmx:beforeRequest` for the probe while the tab is
  hidden. The alternative — widening the CSP to allow eval — trades a page-wide capability for a one-line
  condition, and the layer already refuses that trade for Alpine, which is why the CSP build is the one vendored.
- **The cost is bounded and declared.** One read-only connection per process (a new resource in this layer,
  closed by `lifespan`), one `PRAGMA` per visible tab per interval, one fragment re-render per observed foreign
  revision, and one read of one issue when an open, clean panel re-renders. Nothing is written, nothing is
  locked, and `--read-only` is unaffected: the probe is a read, and a read-only dashboard watching a CLI writer is
  the case it exists for.

**Why one number and not listening to the database.** There is no notification to listen to across processes:
`sqlite3_update_hook` is per connection and fires for the connection that wrote — the wrong side — and a trigger
that wrote into a queue table would need a consumer, a poll of that queue, and a bus, which A8 forbids. One
number that any reader can compare is the smallest thing that answers "did anything I can see change", and it
needs no writer to cooperate.

**Why not the alternatives.** (a) *Render the whole board every 5 s and let the diff be invisible*: that is the
timer the issue rejects — queries nobody needs, flicker, and a swap that deletes an open panel or a half-typed
quick add. (b) *SSE or a websocket*: see above — more server state and a second protocol to carry one integer.
(c) *`data_version` from a connection per request*: measured, it answers the same value forever; it is not a
weaker source but an inert one. (d) *A revision column in the database, bumped by triggers*: a schema change, a
write on every mutation, and a table that exists only to be polled. (e) *A cache of the last board render keyed
by revision*: A8 forbids the cache and the render is already cheap (REQ-N070-02), so it would buy a
correctness risk for latency nobody is missing. (f) *Refreshing the panel only when its own issue changed*:
the number is global and carries no identity, so that condition cannot be evaluated; the panel re-renders on its
own state instead (clean, unfocused) and pays one read of one issue, which is what a row click already pays.

**Verification.** The `/_/revision` scenarios of SPEC-070 §7 (guard, stability across two reads, change after a
write from another connection, the page marker and the header, read-only), the four scenarios §7 marks manual
(no swap without a change, swap with one, the open panel, the drag in flight — no Playwright on this host), and
§2.6.8 of SPEC-070, which is where the client contract lives.

## D-050 — The refresh never touches what the reader is looking at, and only a collection answer adopts

The change signal brought three questions that the first contract answered by extending the client,
and every extension turned into a defect that only a browser could see. The answers, together:

- **The panel is blocking, not refreshing.** Its re-render was allowed while it was open and clean, on
  the theory that a panel is the user's own interaction rather than something a swap destroys. It is
  the wrong trade twice over: the revision is one global number and says nothing about *which* issue
  changed, so "re-render only if its issue changed" cannot be evaluated; and replacing the markup of a
  panel someone is reading costs more than waiting for them to close it. A panel is refreshed by the
  click that opens it, which is already the freshest answer the product has.
- **Only an answer that replaced the collection adopts its revision.** Adopting from every fragment
  looks harmless and is not: the panel, a row, the palette and the counters all answer with the number
  the *server* observed, foreign writes included, and none of them updated the board. The page then
  believes it is current while showing an older board, and the poll goes quiet — the failure is
  invisible, with no toast and no mark, and only the next foreign write corrects it.
- **The reload carries the live filters.** `page["refresh_url"]` comes from the page's query, which is
  not what the user is looking at once they type in the filter bar. A reload without those values
  answers a different question and puts an unfiltered board under a filtered input.
- **The wait is bounded.** One request that never answers must not silence the page for the rest of the
  session: a tick that finds the previous request still in flight gives up on it and asks again.
- **The collection fragment carries rows, not the page's form.** The quick-add form was shipped as an
  out-of-band swap with the rows. It replaced the form on every automatic reload, which is a reset
  nobody asked for, and the controls ended up foster-parented inside the table. The form is reset by
  htmx after its own submit, which is the only time it needs to be.

The common rule, and the reason these live together: **the refresh is a background task and may never
be the reason something stops working.** Every one of these defects had the same shape — a page that
looked correct, a suite that was green, and a feature that stopped answering.

## D-051 — `issue move` answers the issue, not the rebalance: there is no `rebalanced` key

**Date:** 2026-11-13 · **Wins:** SPEC-050 §2.5.3 · **Touches:** SPEC-001 §4.1 (REQ-F019), §4.5, §Notes;
`specs/001-mvp-v1.tasks.md` §T-08; `specs/acceptance/001-mvp-v1/06-board-ranking.feature`

Two contracts answered the same command with two different documents. REQ-F019 of the vertical says that
a move whose resulting gap is smaller than the threshold "reports `rebalanced: true` in `--json`", §4.5
repeats it as a rule of the strategy, and two scenarios of `06-board-ranking.feature` read that field
(`false` in the average case, `true` in the rebalanced one) — so a third document depended on the first.
SPEC-050 §2.5.3 says the opposite in one sentence: `--json` prints the canonical `Issue` of §2.4.2 after
the write ("with the new `state`, `board_order` and the derived milestone") and "no key is added: there
is no `rebalanced` field, because the rebalance happens inside `move_issue` and is not a visible outcome
(SPEC-040 §5.2 R9)".

**Resolution: SPEC-050 wins. The vertical loses the clause, and the two scenarios stop reading a key the
contract does not have.**

- **Why 050.** AGENTS.md §3 gives the component spec the last word over `001-mvp-v1.md`, and the vertical
  says so itself in its header ("it does not define contracts", D-005). There is no reading under which a
  scope document wins a normative envelope.
- **Why not the other way round.** The `--json` answer of every write verb is one canonical object
  (SPEC-050 §2.4.2, REQ-F032). A `rebalanced` key would make `issue move` the single verb whose object
  carries a field no other object has, it would have to be declared in the key list of §2.4.2 — the list
  `tests/e2e/cli/conftest.py::CANONICAL_KEYS` asserts as a tuple — and the API and the web would owe the
  same field or an explanation of why they do not (P2). Granting it is not a one-line change in one edge.
- **Why the caller does not need it.** The rebalance is a repair, not a request: the issue ends exactly
  where the user asked, and R9 of SPEC-040 runs the repair inside the same transaction without error,
  timeout or partial state. What a caller can actually observe — and the only thing that can differ — is
  the numbering of the column afterwards: when the repair ran, every `board_order` of the column is a
  multiple of `1024.0`; when it did not, the neighbours keep their values. Both are already readable,
  because `board_order` is a field of `Issue` and the column can be queried. SPEC-070 R10 goes further
  for the other edge: the client may not even know the 1024 multiple.
- **What the vertical says now.** REQ-F019 keeps the arithmetic and the trigger and drops the reporting
  clause: the rebalance is internal and adds no key. While it was open, the same requirement also
  published a flag no spec declares (`--to-state`) and named a gap of `1e-4`; both are corrected here and
  in D-052 — the verb is `hifa issue move REF --state S` (SPEC-050 §2.5.3). §4.5 keeps "a move that needs
  rebalancing does it **before** computing the new order" and loses "and reports `rebalanced: true`".
- **What the task file says now.** The T-08 bullet loses "reports `rebalanced` in `--json`" and points at
  SPEC-050 §2.5.3 for the shape of the answer. The two bullets that are genuinely finished are ticked in
  the same pass (the domain algebra and the `move` verb); the board read and the 200-move test stay open,
  with what is missing written next to them.
- **What the acceptance file says now.** The two scenarios keep their titles and their intent and stop
  asserting the key. The average case asserts the order after the move (`ENG-1`, `ENG-3`, `ENG-2`) and
  that the two neighbours kept their positions; the exhausted case asserts the new numbering of the
  column (multiples of `1024.0`), the order after the move and the absence of a repeated `board_order`
  inside the column. Nothing is deleted: the property the field was proxying is asserted directly.
  The `When` of both scenarios also named `--after` **and** `--before` at once, which SPEC-050 §2.5.3
  refuses with exit 2 `invalid_option`; each one now names the single anchor that expresses its intent
  (`--after ENG-1`, i.e. immediately before the next issue of the column).

**Verification.** `tests/e2e/cli/test_issue_move.py::test_move_changes_the_state_and_records_it` asserts
`"rebalanced" not in payload` **and** `tuple(payload) == CANONICAL_KEYS`, which is SPEC-050 §2.4.2 as a
tuple: the shipped CLI already matches 050, so this decision changes no code — it removes the last
documents that promised the field. The re-pointed scenarios of `06-board-ranking.feature` are the spec
side of the same check, and the 200-move scenario of D-052 asserts the numbering a rebalance produces
without reading how it was produced. A caller that needs the repair to be visible would have to add the
key to SPEC-050 §2.4.2 (a `version` bump), to the API and web objects and to both e2e suites; that is a
different decision, and it is not this one.

## D-052 — The numbers of the board column: `MIN_GAP = 1e-6`, order `(board_order, id)`, no sign constraint

**Date:** 2026-11-13 · **Wins:** SPEC-020 §2.4 (the threshold) + SPEC-030 §2.6 SQL-6 (the order)
**Touches:** SPEC-020 §2.4, §4.1 (R15), §9, §Notes; SPEC-001 §4.1 (REQ-F018, REQ-F019), §4.5, §Notes;
`specs/001-mvp-v1.tasks.md` §T-08; SPEC-040 §5.2, §Notes;
`specs/acceptance/001-mvp-v1/06-board-ranking.feature`

Three documents described the same column and none of the three numbers agreed. The vertical (§4.5)
declared `RANK_GAP = 1024.0` and `RANK_EPSILON = 1e-4` and claimed the values are "always finite and
positive"; T-08 said "rebalances if the gap < `1e-4`"; SPEC-020 §2.4 declares `INITIAL_GAP = 1024.0` and
`MIN_GAP = 1e-6` and SPEC-040 R9 uses `1e-6` in three places. On the order, REQ-F018 declared
`board_order ASC` with `number ASC` as tie-breaker and §4.5 renumbered with
`ROW_NUMBER() OVER (ORDER BY col, number)`, while SPEC-030 §2.6 SQL-6 — the query that produces the
column — orders `s.position, i.board_order, i.id` and does not even select `number`.

**(a) The threshold is `1e-6`, because it is the largest value the float can still honour.**
SPEC-020 §2.4 wins: it is the component that owns `ranking`, its §2 is normative, and its value is the
one the shipped `ranking.py` implements and the 20 unit tests assert.
- A `float64` at the magnitudes a column uses (the first position is `1024.0`) has an ulp of **2.3e-13**.
  A `1e-6` slot is still **4.4e6** representable steps wide, so `(prev + next) / 2` cannot land on a
  neighbour while the slot is at least `2 * MIN_GAP`; a `1e-4` slot would be 4.4e8 steps, i.e. seven
  decades of usable room thrown away.
- A rebalance is not free: it rewrites every row of the column and one `issue_history` row per row, inside
  the user's move (SPEC-040 R9). At `1e-4` a column would be rewritten while the float could still split
  the slot exactly — the user pays N writes for a gap that is not exhausted. Under `MIN_GAP`, one slot
  absorbs **29** successive insertions before the repair runs and the 30th is refused (measured on this
  host: `1024 → 1.907e-6` after 29 splits, below the `2 * MIN_GAP` floor).
- The two values are not interchangeable in the acceptance file either: a gap of `5e-5` — the number the
  feature file used to carry — is **not** exhausted. `between(1024.0, 1024.00005)` returns `1024.000025`
  and no rebalance happens, which is exactly what the shipped code does. A scenario that expects a
  rebalance at `5e-5` is asserting a threshold that does not exist; it now uses `5e-7`.
- The contract, stated once: `MIN_GAP = 1e-6`; `between` refuses a slot narrower than `2 * MIN_GAP`
  (`RANKING_EXHAUSTED`); a column with two neighbours closer than `MIN_GAP` is degraded
  (`needs_rebalance`) and the use case renumbers it with step `INITIAL_GAP` before computing, or after a
  refusal; a second failure is `RANKING_DEGRADED` (SPEC-040 R9). `1e-4` does not exist in the specs.
- The single `1e-4` left outside the vertical lived in SPEC-040 §5.2, in the row that describes the web
  payload ("`rebalance=true` if the client's gap was `< 1e-4`"). It is removed because SPEC-070 R9/R10
  forbid the client from sending `rebalance` at all and declare the payload `{state_id, before, after}`;
  the row is wrong about the payload, not only about the number. `MoveIssue.rebalance` stays on the
  command as the internal forcing switch of R9, with no edge setting it in v1.

**(b) The order is `board_order`, then `id`; the rebalance preserves it.**
SPEC-030 §2.6 SQL-6 wins: it owns the query, and the same `ORDER BY s.position, i.board_order, i.id` is
what `reads.py::_BOARD_SQL` sends. `number` is not in that statement.
- Consequence, stated in SPEC-020 §4.1 R15 so that every reader of a column quotes one rule: a column is
  read ordered by `board_order` ascending and, between two rows with the same value, by `id` ascending.
  The tie-break only decides for rows whose value was never made distinct — an import that leaves
  `board_order` at its default `0` (SPEC-080 §1) — so it guarantees the read is total; it is not a second
  ordering rule, and it never fires in a column the use case has written, because R9 never writes a
  duplicated position.
- Why the correction does not hurt: `number` and `id` agree for every issue this tool created. `number`
  is `max + 1` per team at creation and the ULID generator is monotonic in the process, so within a team
  the two orders coincide; they can disagree only for rows restored from another machine's JSON export,
  which keeps the original ULIDs (SPEC-080). The read uses `id`, so `id` is what the specs declare.
- `rebalance` preserves the relative order, and this is the property the feature file asserts instead of
  the `rebalanced` key: the sequence arrives in read order (`IssueRepo.positions` orders `column, id`),
  the i-th element becomes `i * INITIAL_GAP`, and a tie inside the input is broken by the order of the
  sequence itself, never by the values (`test_rebalance_breaks_ties_by_sequence_order`). The
  `ROW_NUMBER() OVER (ORDER BY col, number)` of the vertical describes the same outcome with a tie-break
  the read does not use, and it is removed: the renumbering is not one SQL statement but a write per row
  of the column (SPEC-040 §5.2), and that is a detail of 030/040, not of the vertical.

**(c) A position has no constraint on its sign.** §4.5 claimed the values are "always finite and
positive"; the arithmetic contradicts it — `between(None, 1024.0)` is `0.0` and one more insert at the
beginning of that column is `-1024.0`. Nothing reads a position as a magnitude, no column has a `CHECK`
on it (SPEC-010 §3, `board_order REAL NOT NULL DEFAULT 0`), and the order is the only thing that matters,
so the sentence is corrected instead of the code. A negative `board_order` is a valid one.

**Verification.** `tests/unit/domain/test_ranking.py` (20 tests: the exhausted gap, the slot of exactly
`2 * MIN_GAP`, the sequence-order tie-break, the no-mutation and determinism cases);
`tests/e2e/cli/test_issue_move.py` for the verb; the re-pointed scenario "a gap smaller than the
threshold triggers the column rebalance" (`1024.0` and `1024.0000005`) and the new scenario "200 random
moves keep every column in the implied order and its positions unique", both in
`specs/acceptance/001-mvp-v1/06-board-ranking.feature`; and `tests/e2e/web/test_dashboard.py`, which
renders every issue in the column of its state. The board read is not covered by a test that asserts the
order for two rows sharing a `board_order` — there is none today, and it is the case the `id` tie-break
exists for: `import --format csv` leaves every `board_order` at `0` (SPEC-080 §1), so a fresh import is
the cheapest fixture for it. No code changed under this decision; if an implementer finds that the `id`
tie-break and the `number` tie-break disagree on a real database, the answer is the `id` column of
SPEC-030 SQL-6 and the code is already right.

## D-053 — `archive` and `restore` are two routes, and a layer write requires `confirm=1`

**Date:** 2026-11-13 · **Wins:** SPEC-070 §2.1.2 (rev 19) · **Touches:** SPEC-070 §2.1.2, §2.1.3, §3,
§4 (R27), §5.7, §6, §2.6.9, §11; `specs/001-mvp-v1.tasks.md` §T-20;
`specs/acceptance/001-mvp-v1/16-dashboard.feature`

The dashboard's write paths are one issue in the tracker (HIFA-3) and four contracts in the corpus: the
five capabilities the CLI already has, comments, labels and the palette. The smallest of the four — the
parity debt — was still two capabilities short of a contract. `scripts/check_edge_parity.py` reports
`issue.delete` and `issue.restore` as CLI-only, and §2.1.2 of SPEC-070 (rev 18) had **no route** for
either: the only way to reach the trash layer from the dashboard was nothing, and the only way to reach
restore was a body field, `undo?`, on `POST …/archive`.

**Resolution: `archive` and `restore` are two routes, and both `archive` and `delete` require
`confirm=1` on the body. `undo` does not exist.** SPEC-070 §2.1.2 is the spec that changes; it was
already the contract this work had to revise, so no spec loses a clause to another — the clause that
goes away is its own `undo?` field: it made one route mean two requests, one of them confirmation-gated
and the other not, and it left `restore` with no route of its own for the parity map to point at.

- **Why a capability needs its own route.** The parity checker is a map of capabilities — one row per
  capability — and it validates in both directions: a route no row declares is a violation, and a row's
  `web` pointer must be a route that exists. A `restore` that is reachable only by posting an `undo` flag
  to the archive route has no route to point at. The row would either share the pointer with `issue.archive`
  (two capabilities, one route: the map stops being one row per capability) or point at a route whose
  method and path say *archive*. Neither survives the next reader.
- **Why not `undo`, even though the CLI has it.** `hifa issue archive --undo` is a convenience alias,
  and SPEC-050 §2.5.3 says so: it "runs `restore_issue` on the same reference", i.e. it **is** restore —
  `restore_issue(...) if undo else archive_issue(...)`, and `restore_issue` clears both layers
  (`archived_at` first, then `trashed`, §5.5 of SPEC-040). The CLI nonetheless publishes **two verbs**,
  `issue archive` and `issue restore`, and the parity map gives each its own row with its own `debt=`:
  folding them into one route with a flag would leave the map with two capabilities pointing at one
  path, which is the shape this bullet before it already rejects. And the two requests are not the same
  request twice: `archive` requires the confirmation and `restore` never asks (D-053's own rule, and
  SPEC-050 §2.5.3 — undoing is not destructive), while what they answer differs too — an archive sends
  the reader to the list (the issue left it), a restore sends them back to the detail (the issue is still
  there, §2.1.2). A single route carrying an `undo` flag would put two different confirmations behind one
  path and make the caller's intent a body field the route has to interpret.
- **Why the confirmation, and why this shape.** P6 is normative: nothing destructive runs without an
  explicit `--yes`. The CLI implements it as a prompt, `--yes` to skip it and `--no-input` to refuse
  (`confirmation_required`, exit `2`, SPEC-050 §2.5.3). The dashboard had no equivalent, which made the
  web edge the one place where a single mis-aimed request takes an issue out of every listing — and a
  POST is exactly what a stale tab, a replayed form, a crawler following a link or an agent without the
  contract sends. The shape chosen is a **required body field `confirm=1`**, refused with `422`
  `confirmation_required`, and a checkbox with `name="confirm" value="1" required` in the block that
  offers the button (§2.6.9). Three alternatives are rejected: a modal `<dialog>`, because `open` is an
  attribute only the server or a script can set — a dialog is permanently open or permanently shut and
  the modal method of `<dialog>` needs a script, where the corpus needs none and the panel is already a
  sheet on a phone (D-055); typing the
  identifier, because that is friction the CLI does not impose (`--yes` is a flag, not a word) and it
  would make the web edge a different act from the CLI's; and a `GET …/confirm` page, because it is one
  route per action and a page a crawler can fetch, i.e. a confirmation that confirms nothing.
- **Why `confirm=1` is not decoration.** The `required` attribute is the browser's guard and the field
  is the server's: the route refuses a body without it before it touches the database, so the write
  cannot happen by navigation, by a link, by a replayed form from another session's tab or by a client
  that did not read this spec. It is the same act as `--yes`: an answer to "are you sure", given by the
  person who pressed the button.
- **Why `restore` never asks.** SPEC-050 §2.5.3: undoing is not destructive, and `hifa issue restore`
  never prompts. The web edge mirrors the verb, not a generic "write" rule; asking for confirmation to
  undo an undo is how a confirmation becomes noise.

**Verification.** `scripts/check_edge_parity.py` is the executable form of the whole issue: run it after
T-20.1 and `issue.archive`, `issue.delete` and `issue.restore` print no `DEBT` line, because the map
rows of `CAPABILITIES` lose `debt="HIFA-3"` and gain the `(METHOD, path)` pointers of this revision —
a row with both edges and a stale `debt=` is a violation, so the map cannot be left half-closed. The
contract itself is asserted by the scenarios "archive without the confirmation is refused", "archive
with the confirmation takes the issue out of the list and keeps its state", "delete without the
confirmation is refused", "an archived issue can be deleted", "restore clears both layers and writes one
history row each" and "the panel of an archived issue offers Restore and no other write" in
`specs/acceptance/001-mvp-v1/16-dashboard.feature`, written as
`tests/e2e/web/test_dashboard_layers.py`. `tests/e2e/cli/test_issue_archive_restore.py` already pins the CLI
side of the same two layers, which is what makes this a parity change rather than a second contract.

## D-054 — After a write the answer is the element, or nothing at all and the collection is re-read

**Date:** 2026-11-13 · **Wins:** SPEC-070 §2.1.3 (rev 19) · **Touches:** SPEC-070 §2.1.3, §2.1.2, §2.6.8,
§2.6.9, §5.7, §4 (R27), §11; `specs/acceptance/001-mvp-v1/16-dashboard.feature`;
`specs/001-mvp-v1.tasks.md` §T-20

Four questions of shape that the corpus did not answer, and they only look like one: does a write answer
the row, the panel or the collection; is the row re-rendered or the collection reloaded; what does the
panel answer after a layer write; and what is in the body of a move. They are answered together because
each one is the same question — *which node does the answer own* — and answering them in four places is
how a layer ends up with four idioms.

**(a) A field edit answers the element; a layer write answers nothing.**
The distinction is whether the element survives the write. An edit changes the content of a row, a card
or the panel, and the element is still in the collection afterwards, so the answer is that element and
htmx swaps it (`200`, `outerHTML` for a row/card, `innerHTML` for the panel).
An archive, a delete or a restore changes **membership**: the row the panel was opened from is no longer
in the collection, and there is no element to return that would be true. Returning the panel instead
would leave the archived card on the board, because a panel answer does not adopt the revision (D-050)
and the poller would then believe the page is current. So the answer is `204` with **no body**,
`HX-Trigger: issue-archived|issue-deleted|issue-restored` and `HX-Revision`, and the client re-reads the
collection from `page["refresh_url"]` — the fragment the filter bar and the poller already use (§2.6.8),
the only answer that carries server-rendered rows and the only one that adopts the revision. One path,
already shipped and tested (`tests/e2e/web/test_revision.py`), and no new swap target.
Rejected: `200` with the whole collection (a write route that has to know the filters of a page it did
not render, duplicating `/_/issues`); `200` with an empty body plus `HX-Reswap: none` (the same thing,
lying about the content type); and letting the client remove the row (the DOM becomes a second source of
truth, which R7 forbids and which loses the row for good if the request was the one that failed).

**(b) The panel needs a third `view`, because the panel is where the description is edited.**
§2.6.3 says the description is not inline: it is edited in a panel with a `<textarea>` and a Save
button, and §2.6.6 says "edit a field: yes (panel form + Save)". A panel editor whose Save posts to the
page route follows a `303` into an htmx swap and pastes a document into `#panel`. So
`PATCH /_/issues/{identifier}` accepts `view=panel`: same route, same use case, same read model (R13),
target `#panel`, `innerHTML`, body the `<aside>` of `partials/issue_panel.html`. `view=list|board` keep
the answer they had.

**(c) `state` and `state_id` are two names because they are two things, and a reviewer will try to
unify them.** `state` on the edit form and on the PATCH body is a **reference** — a name or a ULID,
resolved inside the transaction exactly like `hifa issue edit -s`, and setting it appends the issue to
the target column (`UpdateIssue`, SPEC-040 §2.2). `state_id` on the move form and on the drop payload is
**the column the issue was put in**, and it comes from `data-state-id` of the node under the pointer
(§2.6.4). Unifying them would mean one of two worse shapes: the move form resolving a *name* after the
drop already happened, or the edit form accepting a column id where the CLI accepts a state name — the
same key carrying two contracts on the wire. Neither name is a position, which is the property R9
actually protects.

**(d) Neither form carries a position, and the wire permits the neighbours the rendered form does not
send.** The move route accepts `before?`/`after?` (§2.1.2), so a form *could* name the two rows it is to
land between and reach the same place the drop does without a script — but §2.6.4's rendered form sends
`state_id` and nothing else, so the form a reader actually submits lands `position="last"`. The wire
permits; the rendered control does not name.
What neither request ever carries is a **position** — no `board_order`, `sort_order`, `rebalance` or
float, whether the neighbours are present or absent, and with none of them the placement is
`position="last"`. The float is computed by `domain/ranking.between` inside `move_issue`/`reorder_issue`
(R9/R10). Nothing in this decision relaxes that: it is restated in R27 so that it binds the edit and the
layer writes too, which are the routes a reader would assume are "just a form".

**Verification.** Four tests of `tests/e2e/web/test_dashboard_writes.py` — a file **T-20.1 writes** (the tree
has `test_board_reorder`, `test_dashboard`, `test_revision` and `test_teams_dashboard` under
`tests/e2e/web/` today), so the names below are the contract that phase implements, not tests that exist:
`::test_the_drop_answers_204_with_no_body` (status,
empty body, `HX-Trigger: issue-moved`), `::test_the_panel_edit_answers_the_panel` (`view=panel` returns
`<aside id="panel">`, not a document), `::test_archive_answers_204_and_the_row_leaves_the_list` (204,
header, then a GET of `page["refresh_url"]` no longer contains the identifier),
`::test_restore_answers_204_and_the_row_returns_to_the_list` (204, no body, then a GET of
`page["refresh_url"]` contains the identifier again), and the scenario set of
`specs/acceptance/001-mvp-v1/16-dashboard.feature` named in D-053 plus "the inline edit answers the row
it changed" and "the drop answers 204 with no body and lands between its neighbours". The client half —
panel closes, collection re-read, focus where §2.6.9 says — is verified **by hand in a browser**, because
this host has no Playwright (the debt §2.6.8 already records); the HTTP half is pytest. If an implementer
finds a write that cannot be answered with `204`, the answer is not a body: it is a second route whose
`hx-target` is the collection, declared in §2.1.3 before it is written.

## D-055 — The confirmation is a `<details>` block the server renders: no modal, no route, no wire field

**Date:** 2026-11-14 · **Wins:** SPEC-070 §2.6.9 · **Touches:** SPEC-070 §2.6.9, §2.1.2, §5.7, §6, §11;
`specs/acceptance/001-mvp-v1/16-dashboard.feature`

D-053 decides *that* `archive` and `delete` require `confirm=1` and is silent about *how the reader says
yes*. §2.6.9 had to render that answer five times the same way — panel footer, detail page header, on a
phone at 320 px, with JavaScript and without it — and every mechanism that carries state off the document
loses one of those five: a modal needs a script to appear, a `GET …/confirm` page needs a route and
navigates the reader out of the panel, a wire field describing "open" puts presentation on the wire. So
the confirmation is a **`<details>`/`<summary>` block rendered by the server**, with the form inside it.
It is the only mechanism that works with no JavaScript (R4/P5), adds no route and no wire field, and
whose three states — closed, open, refused — are each a server-rendered document.

- **What the block is.** `archive` and `delete` each render one `<details class="confirm">` in the action
  group: the `<summary class="btn">` **is** the action, the form inside it posts to the route of §2.1.2,
  and the checkbox is `name="confirm" value="1" required`. There is no third surface and no second copy of
  the payload: the fragment forms carry `hx-post` to the `/_/` route with `hx-swap="none"`, the page forms
  carry `method="post"` to the page route and no `hx-*`, and both are the same block — the two renderings
  differ only in the form's attributes, and each answers what its own route answers: `303` from the page
  copy, `204` with no swap from the panel's (§2.1.3, §2.6.9, D-054(a)).
- **Why the `required` checkbox is not the mechanism.** `required` is the browser's guard and it is what a
  browser without JavaScript enforces: the form cannot be submitted with the box unchecked, so the no-JS
  path reaches the server only with `confirm=1`. The server refuses with `422 confirmation_required`
  anyway, for the client that never saw the HTML (D-053). The block is the interface; the field is the
  contract.
- **The three states, all server-rendered.** Closed is the `<summary>` alone. Open is the summary plus the
  form, with the content already in the document — nothing is fetched to open it. **Refused** (`422`
  `confirmation_required`) re-renders the same `<details>` **with the `open` attribute**, so the block that
  failed is still open in front of the reader, the checkbox carries `.is-invalid` and its message, and
  focus goes to that checkbox (§2.6.9 focus table). An open attribute the server wrote on the refusal is
  the only way "open" ever appears in the markup, and it is a consequence of a request — not a state the
  client asked for.
- **Closing is the summary, so there is no `Cancel` link.** The `<summary>` is the control that closes the
  block and it is still focused after the block shuts; a link would navigate away from the panel the
  reader is standing in. `Esc` closes the block through the panel's key handler (§2.6.2) and leaves the
  panel open; without JavaScript `Esc` belongs to the browser and the summary is the way back — a difference
  this decision accepts, because the no-JS path has no panel key handler to route it to.
- **Rejected: `<dialog>`.** A `<dialog open>` **does** render without a script — that is not why it loses.
  It loses because `open` can only be produced by the server or by a script: a dialog is permanently open
  or permanently shut, and the modal method of `<dialog>` needs JavaScript, so the reader pressing the
  button cannot open it without either a script or a request that already knew the answer. A modal
  overlay is rejected on its own grounds: the panel is already a sheet on a phone, and a box inside it
  puts a scrim over content the
  reader can no longer reach (§2.5.6).
- **Rejected: a `GET …/confirm` page.** It is one route per action that a crawler, a prefetcher or a stale
  tab can fetch, and it navigates the reader out of the panel — a confirmation page that confirms nothing.

**Verification.** The mechanism is executable, not argued. `specs/acceptance/001-mvp-v1/16-dashboard.feature`
pins both halves of the field rule: *"archive without the confirmation is refused"* and *"delete without
the confirmation is refused"* post the form with no `confirm` and assert `422 confirmation_required`, no
history row and `archived_at` unchanged (the delete one: `trashed 0`); *"archive with the confirmation takes
the issue out of the list and keeps its state"* posts `confirm=1` and asserts the `303`, `Location:
/ENG/list`, the `archived_at`, the state kept and exactly one history row. The markup is asserted on the
refused render, where it is produced: both refusal scenarios contain `<details class="confirm" open>`, the
`name="confirm"` field and `is-invalid` on the checkbox — the `open` attribute and the marked field are
pinned by a scenario, not only by this paragraph. That the same block reaches the **panel footer** is the
shared-template assertion of T-20.1 (`tests/e2e/web/test_dashboard_writes.py`, the file that phase writes).
These are assertions on the response body, i.e. a client that parses
HTML and posts a form can execute every one of them, and **a browser with no JavaScript executes the whole
flow**: it opens the block with `Enter` on the summary, ticks the box because the browser will not submit
otherwise, and the server either closes the panel or hands back the open, marked block. The client-only
half — `Esc` closing the block before the panel, focus landing on the checkbox after a refusal — is
verified by hand in a browser, for the reason §2.6.8 records (no Playwright on this host).

## D-056 — The inline edit posts a form, not JSON: htmx's own encoding, and the hidden form inside the cell

**Date:** 2026-11-14 · **Wins:** SPEC-070 §2.6.3 · **Touches:** SPEC-070 §2.6.3, §5.3, §11;
`src/hifa/web/static/js/app.js` (the deleted override), `src/hifa/web/templates/partials/issue_row.html`

§2.6.3 prescribed a wire format no route of the layer can accept, and the prescription — not the code —
is what produced a real bug. It said three things: the `PATCH` body is JSON
(`body JSON: {"title": "…"}`); a `htmx:configRequest` shim in `app.js` in place of `json-enc`; and the
hidden `<form method="post">` outside the `<td>`, reached by `hx-include="closest form"`. T-20.1
implemented the section as written and the first request that reached a browser failed. The three
clauses fail for three different reasons, and each one is checkable without reading the implementation
as an intention:

- **No fragment route parses JSON, and that is by design.** Every `/_/` write of the corpus is parsed by
  `_parse_form` in `src/hifa/web/app.py`, which decodes the body as UTF-8 and hands it to `parse_qs` —
  urlencoded. The single JSON body under `/_/` is `GET /_/revision`, declared as an exception in
  §2.1.3.1 and resolved by D-049, and it is a **read**: nothing in the family *accepts* JSON, so a JSON
  `PATCH` is not a format the layer chose against, it is a format it never had. The declaration is not
  even reachable as a route signature — the handler takes `body: bytes = Body(default=b"")`, so a
  `Content-Type: application/json` body is validated as JSON by the framework and the request dies in
  `RequestValidationError` ⇒ `422 invalid request`, an error page about the envelope rather than about
  the title. Making JSON work would mean a second body parser for exactly one route, in a layer whose
  whole write surface — the five fragments of §2.1.3 and the seven page forms of §2.1.2 — already speaks
  urlencoded, and whose no-JS path (§2.6.6) can only ever speak it.
- **The shim cannot work, because the body is already encoded when it runs.** The event it hooks is a
  notification: htmx fires it *after* serializing the form and does not honour `preventDefault()` on it.
  A listener that sets `Content-Type: application/json` therefore does not change what is on the wire —
  it only relabels a `title=Fix+SSO+login` body as JSON. The server sees a header it parses as JSON and
  a body that is not JSON — neither format — and answers `422 invalid request`, the same refusal,
  reached through the shim instead of through the declaration. That is exactly the failure the first
  browser run produced, and it is why the override was deleted instead of repaired: there is no version
  of the shim that can work, since the one thing it would have to change is the thing the event no
  longer controls. The client's own note in `app.js` now says so where the next reader will look for the
  missing code.
- **A `<form>` child of `<tr>` is foster-parented out of the table.** The HTML parser does not allow a
  form between `<tbody>` and `<tr>`, so it lifts it above the table and leaves it where a table may not
  have content — the exact hazard the docstring of `tests/e2e/web/test_revision.py` names for the quick
  add, and the reason that test asserts what the fragment ships rather than that it ships no form. The
  declared markup therefore could not be the markup a browser builds: the form ended up detached from the
  row it belongs to, and `hx-include="closest form"` had no ancestor form left to find. The working shape
  is the same form **inside the `<td>`** that holds the field, `hidden`, carrying an `id` of its own, with
  the control naming it twice — `form="edit-{identifier}"` for ownership (and the no-JS submit) and
  `hx-include="#edit-{identifier}"` for what htmx serializes. Inside the cell it is not foster-parented,
  it travels with the row it is rendered into, and it is replaced together with it (§2.6.3).

- **Rejected: vendoring the JSON extension and keeping the JSON body.** It is the one alternative that
  would make the declaration executable, and it loses on two counts the corpus treats as binding. It adds
  a vendored asset to the §2.4 budget — seven files with a `≤ 75 KB` total and per-file ceilings
  enforced by `test_asset_budget_is_under_75_kb_gzip`, where every new file is a new licence row, a new
  `sha256` in `static/vendor/README.md` and a new number in a total with ~9 KB of room left — to buy a
  wire format the server does not want. And it would give **one** route of the layer a second
  serialization while its seven page forms, its four other fragments and its no-JS fallback stay
  urlencoded: two formats for one write, distinguished per element, with the difference invisible at the
  route. The form the browser already encodes is the contract; the extension is a cost with no reader on
  the other side.

- **What the contract is now.** §2.6.3's exchange block states `Content-Type:
  application/x-www-form-urlencoded` and a `title=…` body; its markup block shows the hidden form inside
  the `<td>` and the control naming it by `id`; its rule states that `app.js` keeps **no**
  request-rewriting hook for `PATCH`es and why. `Enter` is declared as the implicit submission of the form
  owner — the same `hx-patch` the form carries — rather than as a `keyup[key=='Enter']` trigger, which
  the shipped markup does not have and which would have sent the request twice. §5.3 step 1 stops
  drawing the payload as an object. No route, field, enum or `Content-Type` of a **response** changes,
  so `version` stays 4 and only `spec_revision` moves (19 → 20).

**Verification.** The contract is executable in two places, and the browser one is the reason this
decision exists. `/var/tmp/t03/probe_browser.py` is a Playwright run against a real `hifa serve` and a
real Chromium: it reads the row's ownership off the parsed DOM and requires `formThere`, `hidden`,
`method=post`, `action=/ENG/issue/ENG-1`, `hx-patch` on the form, `ownerIsForm`, `ownFields ==
["title"]`, `insideTr` **and** `formInCell` — i.e. the parser did not move the form; then it types a
title, presses `Tab`, and asserts the one exchange it captured has
`content_type=application/x-www-form-urlencoded`, `body=title=Renamed%20in%20a%20real%20browser` and
`status 200`, and that the swapped row comes back carrying the new title; then it does the same with
`Enter`, which reaches the same `PATCH` through the form owner and answers `200`; then it clears the
field, leaves it, and asserts the exchange is `422` while the returned control carries
`aria-invalid="true"`, `.is-invalid` and the **stored** title — the field marked and the server's value
in it (R7), not the empty one the reader typed; finally it fails the run on any CSP violation or page
error in the console. The pytest half is `tests/e2e/web/test_dashboard_writes.py` (the edit across
`?view=list|board|panel`, the `200` with the row, the `422` with the field marked and the stored value);
until that file lands with the rest of the phase, the property already asserted in the tree is the one
`tests/e2e/web/test_revision.py` carries — every form the row fragment ships writes its row through
`hx-patch="/_/issues/…"`, keeps `method="post"` and `action="/ENG/issue/…"` as the no-JS fallback, and is
`hidden`, which is the shape §2.6.3 now declares. A `Content-Type: application/json` on that route is
not covered by a test because it is not a behaviour to preserve: it answers `422 invalid request`, and if
a future reader wants it they have to reopen this decision rather than find an unasserted path.

## D-057 — A write from another origin is refused before the route runs, and a request with no origin information is the declared non-browser client

**Date:** 2026-11-16 · **Wins:** SPEC-070 §2.1.7 · **Touches:** SPEC-070 §2.1.7 (new), §11; SPEC-000
REQ-S005 (rev 10); SPEC-001 REQ-S005 + §Notes (rev 10); `16-dashboard.feature`,
`17-dashboard-hardening.feature`; `001-mvp-v1.tasks.md` §T-20; `INDEX.md`

**Prior state.** The corpus declared two ways for a write to be told no, and neither concerned the caller:
R18's read-only mode is a property of the **process**, R5's htmx guard a statement about which **client** is
asking (the family of `Accept`), and `confirm=1` (§2.1.2) a statement about the **body**. SPEC-000 REQ-S005
named the gap and accepted it: the htmx header was the only guard on dashboard writes. True, never enough.

**The hole, measured at `ff01ec5`.** Five state-changing routes — the three `/_/*` writes
(`POST /_/states/{state_id}/position`, `PATCH /_/issues/{identifier}`, `POST /_/issues`) and the two page forms
`POST /{team}/issue` and `POST /{team}/issue/{identifier}` — and none reads `Origin`, `Sec-Fetch-Site` or a
token; `_can_write` answers "is this instance read-only", never "may this caller write". The two page forms
take `application/x-www-form-urlencoded` with **no header at all**, so a page the reader visits while the
dashboard is open on a cloudflared quick tunnel can submit `POST https://<tunnel>/ENG/issue/ENG-13` with
`title=…` and the server accepts it: no preflight, no CORS refusal, nothing the browser blocks. The other three
require `HX-Request`, which a `<form>` cannot set — a compatibility guard REQ-S005 mistook for a credential.
T-20.1 adds four page forms (`move`, `archive`, `delete`, `restore`) and four fragments, so the headerless
surface goes from two routes to six and starts carrying the destructive writes: five write routes today
become thirteen.

**Resolution: one guard, an HTTP middleware installed by `create_app`, keyed on the method, answering before
the route is matched.** It adds no route, verb, field or successful status, so `version` stays 4 and
`spec_revision` moves 20 → 21: a precondition on who may reach an existing route.

- **Not a per-route dependency.** The layer already has that shape (`HTMX = Depends(require_htmx)`), and its
  failure mode is a route that forgets the line — the bug being fixed. A middleware decides on the method alone,
  so a route added later is covered the moment it is registered and a method no route serves yet (`PUT`,
  `DELETE`) is covered too. What a test checks is the enumeration of `app.routes`, which turns "no write route
  can be added without passing through it" into a fact (P8).
- **What it compares.** `Sec-Fetch-Site` first: `same-origin` and `none` allowed, `cross-site` and `same-site`
  refused — one host and one port serve this edge, so no page of it can produce `same-site`. Absent, `Origin` is
  compared by **authority** (`Host`, port as addressed, case-insensitive, scheme dropped, so the tunnel's
  `https` browser over a plain `http` socket still writes); another authority is refused, and so is
  `Origin: null`, which names none.
- **Both headers absent is allowed, and that is the contract.** There is no cookie, no session and no token to
  ride or to demand (REQ-S005): a client that sets its own headers can set any, so refusing a headerless request
  buys nothing and breaks every non-browser client the corpus runs on (the `httpx` client of the tests, an agent
  driving fragments, `curl` in the no-JS path). The subject is the **browser the reader is using**, which sends
  those headers on its own; it is not a lock on the port, and §2.1.7 says so.
- **`403`, one sentence, returned and never raised.** `text/plain`, `cross-site write refused`, `no-store`,
  inside `_headers` like every other answer of this edge. `400` would call a well-formed cross-site form
  malformed, and `422`/`409` are statements about the body and the row. No exception type and no handler are
  added, and none could be: `@app.exception_handler` lives inside Starlette's `ExceptionMiddleware`, below the
  user middleware stack, so it would answer `500`. The edge's only exception-and-handler pair stays
  `FragmentRequiresHtmx`; the read-only `403` is an inline `return` in each write route, not an exception either.
  The two 403s differ by message, as two rows of §6.
- **The order is structural.** A middleware runs before routing, before any dependency and before the handler,
  so the guard precedes the `400 fragment requires htmx` and the read-only `403`. Two consequences, each pinned
  by a scenario: a cross-site write to an identifier that does not exist is `403` and not `404` (the refusal must
  not depend on the target resolving), and a cross-site write on a read-only instance is the guard's `403` while
  a same-origin write there keeps `server in read-only mode`. The guard reads no table, so it answers with the
  file locked.

**Rejected.** A per-route dependency (`Depends(require_same_origin)`): the same opt-in shape as `HTMX`, its
failure mode is the bug. A token in the URL or in a header set at startup: a credential in a history, a log and a
`Referer`, and it would change what the CLI is (A4) to defend a socket the CLI never opens.

**Scope and assumptions.** The threat model is a browser the reader is using, on a page the reader did not write:
an attacker running an HTTP client of their own sends no headers and is allowed, and `Sec-Fetch-Site:
same-origin` cannot be verified. A **same-origin page can write**; if a page of this app is compromised the guard
is not the defence. No account, session, token or TLS exists; out of scope: those, the tunnel's configuration,
rate limiting, a server-side CSRF token, any change to the CLI (A4) or to the read-only mode, any new route,
verb or field. **The API edge is out**: SPEC-070 governs `web/**` only and `src/hifa/api/` holds `__init__.py`,
so its routes need their own answer. Reads are not covered either: every `GET` is served as before, this edge
emits no CORS header (REQ-S005), and writes were the hole because they are **sent**, not read.

**The losing spec is adjusted in this branch.** AGENTS §3 requires the bump and the §Notes pointer; the branch
does it: SPEC-000 REQ-S005 and SPEC-001 REQ-S005 (both rev 10) name the guard and state what it does not do,
`17-dashboard-hardening.feature`'s header and `tasks.md` T-20/T-20.1 carry the correction, and `INDEX.md` counts
the decisions and the scenarios. No clause still calls the header the only guard.

**Verification.** `tests/e2e/web/test_write_guard.py` (T-20.0) mirrors the eleven scenarios of the guard block in
`16-dashboard.feature`. Refused: `::test_a_cross_site_write_is_refused_and_writes_nothing`,
`::test_a_same_site_write_is_refused`, `::test_an_origin_of_another_authority_is_refused`,
`::test_an_opaque_origin_is_refused`, `::test_the_guard_answers_before_the_route_runs` (`403`, never `404`, for
an unknown identifier), `::test_a_cross_site_write_on_a_read_only_instance_is_the_guards_refusal`. Served:
`::test_a_same_origin_write_is_accepted` (`Sec-Fetch-Site: same-origin` **alone** ⇒ `200`, which pins it as the
first comparison), `::test_an_origin_of_the_served_authority_is_accepted`,
`::test_a_write_without_origin_information_is_accepted`, `::test_a_cross_site_read_is_served`,
`::test_read_only_keeps_its_own_403`. Plus `::test_every_state_changing_route_is_covered`, which enumerates
`app.routes` (P8) — the reason this lands before T-20.1 adds its four page routes and their four
fragments.

## D-058 — One shared `In Review` column, seeded between `In Progress` and `Done` at 4.5

**Date:** 2026-11-16 · **Wins:** SPEC-100 §2.5 · **Touches:** SPEC-100 §1, §2.5, §2.7, §3.1, §4.2, §5,
§7, §8, §9, Notes; SPEC-050 §2.5.1, §2.5.2, §2.5.6, §2.7, §7, Notes; SPEC-070 §7, Notes; SPEC-040
REQ-F040-14, §7, §9, Notes; SPEC-001 REQ-F001, §9, Notes; `001-mvp-v1.tasks.md` (T-04, T-21);
`01-init.feature`, `02-teams-workflow-states.feature`, `06-board-ranking.feature`; `INDEX.md`

**Prior state.** `DEFAULT_STATES` (SPEC-100 §2.5) seeds six columns and no review column, and three
clauses say so on purpose: SPEC-050 §2.5.1 closes its list with "There is no `In Review`", SPEC-100 §11
repeats it in the D-013 bullet, and D-013 itself fixed the seed at six. The column is not new to the
corpus, only absent from it: SPEC-050 §11 records (spec_revision 3, D-013) that the pre-D-013 bespoke
list **had** `In Review` (and omitted `Triage`), and SPEC-050 §2.7 still uses it as the example of a state
a user creates by hand, at `4.5`, type `started`, colour `#BB87FC`. What the six-state seed cannot
express is the phase this work spends most of its time in: HIFA-31 has been open since 2026-11-13 and
every day of it was a review.

**Resolution: one `In Review`, shared by spec review and code review, seeded between `In Progress` and
`Done`.** The issue asked whether there should be one state per review phase or one shared state; this is
the shared one, because

- **the unit under review does not change when the phase does.** Same issue, same branch, same change:
  going from "the spec is under review" to "the code is under review" is the same card read twice, not a
  second card, so two columns would hold the same work twice, in sequence, with one name each.
- **the corpus's own review is two stages of one change**, and both end at the same event — the merge — so
  the board's question ("where is this work") has one answer for both stages.
- **the stage is already recorded where a reader looks for it**: the PR says which review it waits on and
  `issue_history` says when the card moved. A second column would duplicate that with no room for the
  reason, which is the part a reader needs.

The alternative — `In Spec Review` and `In Code Review` — costs a template of eight columns for every new
team, two drags per change, and a rule for the round trip when the code review sends the work back to the
spec: the issue would move backwards into a column it had already left, writing two `issue_history` rows
and two `updated_at` bumps for one loop. Both columns would also carry the same `type` (`started`), so no
read of the corpus — the state-type filter, `is_open`, the WIP counts — would tell them apart, and the
distinction would live only in the two names.

### The seed

| position | name | type | colour | `is_default` |
|---|---|---|---|---|
| 1.0 | `Triage` | `triage` | `#6B6F76` | 0 |
| 2.0 | `Todo` | `unstarted` | `#E2E2E2` | 1 |
| 3.0 | `Backlog` | `backlog` | `#6B6F76` | 0 |
| 4.0 | `In Progress` | `started` | `#F2C94C` | 0 |
| **4.5** | **`In Review`** | **`started`** | **`#BB87FC`** | **0** |
| 5.0 | `Done` | `completed` | `#5E6AD2` | 0 |
| 6.0 | `Canceled` | `canceled` | `#95A2B3` | 0 |

- **`type = started`, and it is the type the template carries twice** — the pre-D-013 bespoke list had a
  state named `In Review` too, until D-013 removed it (SPEC-050 §11, spec_revision 3). `type` is the
  fixed semantic bucket that survives a rename (SPEC-020 §9) and it answers one question: is the work open.
  `v_issue.is_open` is `type NOT IN ('completed','canceled')` (SPEC-010 §3.2), so `started` keeps an issue
  under review on the board, inside `open_count` and inside every `--open` listing — a change in review is
  not finished work. `completed` would count it as closed before it is merged **and** would set
  `completed_at` on the way into review, because SPEC-040 §4.1 R7 derives the milestone from the
  destination type. A seventh type is not on the table: it would change the CHECK of SPEC-010, the
  `WorkflowStateType` enum of SPEC-020 and the two vocabularies built on it (`--state-type` of
  `issue list`, `--type` of `state create`), for a column one of the six types already covers. The price
  of reusing `started` is one invariant: SPEC-100 §3.1 can no longer say "one state per type", and it now
  says one row per type with `started` twice.
- **`position = 4.5`, the midpoint of the slot the column describes.** `domain.ranking.between(4.0, 5.0)`
  is `4.5`, which is exactly what `hifa state create 'In Review' --type started --color '#BB87FC'
  --before Done` computes on a team that
  still has six states — so a fresh team and an existing team that ran the documented command hold the
  **same seven numbers**, and no state that exists today changes its stored position (`Done` stays `5.0`,
  `Canceled` stays `6.0`). Every clause that reads "positions 1.0 to 6.0" stays true. The rejected
  alternative was renumbering to `1.0…7.0` so the seed is a plain ordinal: it would move `Done` to `6.0`
  and `Canceled` to `7.0` for every **new** team while every existing team keeps `5.0`/`6.0`, so two
  teams in one workspace would print different numbers for the same column, the seed's `In Review` (`5.0`)
  would differ from the value its own documented command produces on an old team (`4.5`), and the range
  clauses of SPEC-050 §2.5.6 and `01-init.feature` would have to be rewritten to say nothing more.
- **`colour = #BB87FC`.** It is the colour SPEC-050 §2.7 already prints for a hand-created `In Review`, it
  is inside the palette the dashboard already draws (it is the `Feature` label of SPEC-100 §2.5
  `DEFAULT_LABELS`), and it sits between the yellow of `In Progress` and the indigo of `Done` without
  repeating either. That a colour is shared with a label, or between two states, is established:
  `#6B6F76` is both `Triage` and `Backlog`, because a state colour is a dot on a column header, not a
  key.
- **`is_default` stays on `Todo`.** Untouched, so `create_issue` with no `--state` still lands where it
  did and the "exactly one default" invariant of SPEC-100 §3.1 is untouched.

### Existing teams get the column with one command

A seed reaches `hifa init` and `create_team` and nothing else, so the six-state teams already on the real
board keep six columns until their owner acts. The operator runs, once per team:

```
hifa state create 'In Review' --type started --color '#BB87FC' --before Done
```

(SPEC-050 §2.5.6.) `--before Done` resolves the anchor, computes `between(4.0, 5.0)` on the server and
sends `CreateState(position=4.5)` — the seed's own number, through the seed's own arithmetic, with no flag
that carries a float. Two outcomes, both correct: exit `0` with the new row, or exit `4`
`Conflict(UNIQUE_STATE_NAME)` when the team already has a state of that name — which is the case the
corpus's own CLI example produces, since creating `In Review` by hand is what a team does before this
decision exists. There the reconciliation is `hifa state update 'In Review' --before Done`, which moves the
column instead of creating it.

### No migration, and that is a decision

`workflow_state` rows are user data, so no migration writes them. Three reasons, and the third is
specific to this column:

1. **A column set is not a schema.** An owner who renamed, deleted or reordered a column has a board a
   `INSERT … SELECT` cannot respect: the migration would not know whether that team wants a review column
   at all, nor where its owner put `Done`.
2. **A migration could not tell "the column exists" from "a different column with that name".** A team
   that already created `In Review` by hand (SPEC-050 §2.7, `02-teams-workflow-states.feature`) collides
   with the seeded row, and both candidate behaviours — skip and overwrite — are wrong for one of the two
   teams.
3. **R25 already says it for this table.** An existing team is reordered with the route or with
   `hifa state update`, never with a migration (SPEC-070 R25, D-034). This decision does not open a second
   mechanism for the same column; it uses the one that exists, and `schema_migration` keeps its 3 rows,
   which `02-teams-workflow-states.feature` asserts.

### The CI check is dropped

The last item of HIFA-31 reads "ideally a `scripts/` check that fails CI when a merged PR has no issue in
`Done` referencing it". The board it would read is the local SQLite file (`*.db` is in `.gitignore`, and
`git ls-files` tracks no `.db`): CI checks out the repository, so the state of an issue is not an input
any job has. The two halves of that check are not equally reachable:

- **The reference half is reachable and proves nothing.** A workflow can read `pull_request.body` and
  require an identifier matching `^[A-Z][A-Z0-9]{1,9}-[0-9]+$`. That is a statement about a string in a
  form, and it goes green for a PR whose issue is in `In Review`, in `Backlog` or deleted.
- **The state half is not reachable.** No job can read the file; `hifa doctor` reads the database and
  nothing else and has no network (A4); a release-checklist item in SPEC-110 would be executed by the same
  pipeline with the same blind spot.

Writing the first half would leave the corpus claiming that a merged PR has a tracked issue in `Done`
while the only evidence is a regex over a body — a check that fails CI for a formatting mistake and
passes a PR nobody tracked. **So no script, no workflow, no `doctor` finding and no checklist item is
specified here.** What closes the loop is the pair that already exists and is delivered: the move verb,
`hifa issue move REF --state Done` (`src/hifa/cli/commands/issue.py`, T-06), and the agent rule kept
outside this repository, which requires the issue to be on the board and moved by the agent that merged.
The price, stated: nothing mechanical detects a merged PR whose issue was left in `In Review`. The
detector is the person reading the board — and the new column is what makes that state visible instead of
hiding it inside `In Progress`.

### Scope and the losing specs

No new verb, route, field, table or enum value: `DEFAULT_STATES` gains one row, and the counts that
depended on six move to seven. `version` is unchanged in every spec — D-034 set that precedent for this
same tuple, because the seed is data and no signature, command, endpoint or enum moves. The §2.5.4
wireframe of SPEC-070 draws four columns of one sample board and is not the template, so it stays.

The clauses that said "there is no `In Review`" are corrected rather than deleted (SPEC-050 §2.5.1,
SPEC-100 §11's D-013 bullet), the counts of SPEC-100 §1/§3.1/§4.2/§5/§7/§8 and SPEC-040 REQ-F040-14/§7,
SPEC-050 §2.5.1/§2.5.2/§2.7/§7 and SPEC-001 REQ-F001 move to seven, the two `state create` scenarios that
used `In Review` as the example of a **custom** state use a name that is not in the template, and the
canonical-order scenarios of `01-init.feature`, `02-teams-workflow-states.feature` and
`06-board-ranking.feature` list the seven names. `02-teams-workflow-states.feature` also gains the
existing-team path as a scenario, so the one-command route cannot rot into a claim.

**Verification.** The code side is T-21: `DEFAULT_STATES` in `src/hifa/app/teams.py` and the tests that
pin it (`tests/unit/app/test_create_team.py`, `tests/integration/app/test_state_reorder_db.py`, whose
position list is `[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]` today). This branch is the spec change only; the drift
is the reason for the `spec_revision` bump and closes when T-21 lands.

## D-059 — A trashed issue is served by its own detail page and its panel, and `issue_detail` gains the one declared way to read it

**Date:** 2026-11-17 · **Wins:** SPEC-070 §2.1.1, §4 (R29) · **Touches:** SPEC-070 §1, §2.1.1, §2.1.2,
§2.1.3, §2.1.4, §2.3.4, §2.6.4, §2.6.9, §4 (R27, R29), §5.7, §5.7.1, §6, §7, §11; SPEC-030 §2.5 (SQL-7
row 1), §2.6 (`issue_detail`, `IssueDetail.trashed`); SPEC-040 §2.2 (the `issue_detail` signature), §4.1
(R21), §11; `specs/001-mvp-v1.tasks.md` (T-20.1); `specs/INDEX.md`;
`specs/acceptance/001-mvp-v1/16-dashboard.feature`

**Prior state.** SPEC-070 §2.6.9 states the rule for the two layers in one sentence: "the panel of an
archived or trashed issue renders `Restore` and nothing else". For the archive layer that sentence is
true and its control has a page to live on — `archived_at` is carried by the detail read, so
`GET /{team}/issue/{identifier}` answers `200` for an archived issue and renders the one write it
accepts. For the trash layer the same sentence has nothing behind it: `issue_detail`'s statement 1
filters `i.trashed = 0` (SPEC-030 §2.6, SQL-7 row 1), `tests/integration/db/test_reads_detail.py`
pins that a trashed reference answers `None` (`test_a_trashed_issue_has_no_detail`), and the edge turns
that `None` into a `404` — so the panel and the detail of a trashed issue are both `404`, and the
`Restore` the corpus promises is markup no route can produce. The corpus is not silent about the trash
elsewhere: three refusals give the reader an instruction, `ENG-13 is in the trash; restore it first`
(the `422` of §6, produced by `ISSUE_TRASHED` in SPEC-040 §6), and the CLI has the verb that carries it
out, `hifa issue restore` (exit `0`, never prompting, SPEC-050 §2.5.3). What the dashboard lacked was
the page the instruction sends the reader to: with the identifier in hand — the identifier the refusal
itself prints — there was no URL that showed the issue and no control that undid the layer. The CLI could
already do it (`hifa issue restore ENG-13`), so this is a P2 parity hole and not a new capability.

**Resolution: a trashed issue is served by its detail page and by its panel, and by nothing else, and the
detail read gains one keyword that is the only way to return one.**

- **The two routes that serve it.** `GET /{team}/issue/{identifier}` and
  `GET /_/issues/{identifier}/panel` answer `200` for a trashed issue and render the issue with
  `Restore` as its only control — no edit form, no move `<select>`, no other layer write (§2.6.9). The
  `/{team}/issue/{ulid}` alias answers `303` to the same page, because R6 declares the identifier URL
  canonical and an alias that `404`s while its canonical URL answers `200` is two URLs disagreeing about
  one row.
- **Everything else keeps the trash out.** The board, the list, the search page, the palette, the
  counters, and the `row` and `card` fragments never return a trashed issue: these are collections and
  collection members, no collection contains a trashed row, and a `<tr>`/`<article>` for a trashed issue
  would be a row the collection does not have. The row and card fragments keep the read's default and
  answer `404` (§4 R29), which is also what keeps their answer unchanged by this decision.
- **Why the panel and not only the page.** They are one read model (§2.3.4, R13) and one route pair; a
  panel that `404`s where the page answers `200` would make the same reference mean two things depending
  on which fragment was asked, and it would leave the "open this identifier" path — `hx-get` on a row,
  `HX-Push-Url` — able to load a page whose panel cannot be rendered. The panel is not a collection: it
  is the sheet next to one, and it is the place the write it offers lives.

**The collision this decision had to resolve, and how.** SPEC-040 R21 (spec_revision 23, the same day)
says the opposite of the paragraph above, in its own words: "`issue_detail` still answers `None` for an
issue the trash layer hides, so `archived_at` is the only flag a render can find set, and `trashed` is
therefore **always `False`** on a detail this read returns". A test pins it. Two clauses of the corpus
cannot both stand, so one of them is revised, by name, here:

- **The detail read gains `include_trashed: bool = False`** (SPEC-040 §2.2), a keyword-only parameter with
  a default, and R21's sentence is rewritten to say what is true after it: the default read hides the
  trash exactly as before — every existing caller, every listing and the pinned test are untouched — and
  `include_trashed=True` is the single declared exception, which changes statement 1's predicate and
  nothing else. The statement count of SQL-7 does not move (**12** with `DETAIL_INCLUDE`, **8** with an
  empty `include`): the keyword buys a predicate, not a statement. `trashed` is `False` on every detail
  returned by the default and may be `True` only on one returned with the keyword.
- **Why the read and not a second read.** The alternative was a `trashed_detail` (or a `layers=` variant)
  next to `issue_detail`: sixteen statements — SQL-7 — duplicated, kept in step by hand, with two
  functions whose answers must agree on fifteen of them, to serve one page whose content is the issue plus
  one button. It would also re-open the exact drift the previous revision of this spec closed: R21 exists
  because a render that composes its model from two reads can see the issue change between them
  (spec_revision 23 of SPEC-040), and two detail reads that differ in one predicate are the same trap one
  layer down. The corpus already names this axis: `ListFilters.include_trashed` (SPEC-030 §2.6) is the
  listing's word for it, and `hifa issue list --trashed` is the CLI's (SPEC-050 §2.5.3). The keyword reuses
  the name rather than inventing a second vocabulary for one predicate.
- **What R21 keeps.** Its rule is that a render of an issue is served by one call on one connection and
  that a fact a render needs and the read does not carry is a defect of the read. This decision adds a
  fact the read could not previously carry (`trashed = 1` on the row it already loads) and hands it to the
  render through the same call. Nothing about the "one read, one connection" rule moves, and no render
  opens a second read to learn which layer it is looking at.

**The alternatives rejected.**

- **A `/trash` view.** The obvious third surface: a page under `/{team}/trash` listing the trash, with the
  same filters and counts as the list. It was rejected on cost and on fit. On cost, it is a page route, a
  read model, a template, a fragment, its own filter and pagination rules, a place in the sidebar, and a
  new rule telling every other count and listing to keep excluding rows the new page shows — and it still
  does not answer the question the refusal message asks ("I know the identifier, take me back"), which is
  the only question the trash is asked in practice. On fit, the CLI's answer to "enumerate the trash" is a
  flag on the existing listing (`hifa issue list --trashed`, SPEC-050 §2.5.3), not a second listing, and a
  page of its own would be the dashboard's second way of enumerating issues where the reference surface
  has one. It remains the honest answer to a different requirement — *enumerating* the trash — and that
  requirement is priced below, not built here.
- **Leaving the trash unreachable and documenting the CLI as the only way back.** Cheapest, and it was the
  state of the tree: the dashboard tells the reader `ENG-13 is in the trash; restore it first` and then
  hands them a terminal. It was rejected because the corpus already publishes the control (§2.6.9's
  sentence covers the trashed panel), because it makes the dashboard the one surface where a write can be
  undone only by leaving it, and because the asymmetry is invisible in the spec — the sentence reads as if
  the panel worked. A corpus that documents a control it does not serve is how the next reader concludes
  the trash is a rendering bug.

**The price, stated.**

- **The trash is reachable only by identifier.** No page of the dashboard enumerates it, counts it, or
  links to it: a reader who does not know `ENG-13` cannot find a trashed issue from the browser.
  `hifa issue list --trashed` can (SPEC-050 §2.5.3), so this is a **P2 gap this decision does not close**
  and does not hide — enumerating the trash is a listing with its own filters, its own empty state and its
  own rule against every count, and it needs its own ticket before it is contracted. The mitigation is the
  one that matters for the flow that exists: the refusals that mention the trash print the identifier
  (`ENG-13 is in the trash; restore it first`), so the reader who is told to restore has the address.
- **Two reads of one route pair now differ from the other two.** The detail and the panel pass the
  keyword; the row and the card do not. That asymmetry is deliberate (a collection member versus a
  standalone render) and it is the one thing the next implementer can get wrong, which is why R29 states
  it in §4 and one scenario asserts the `404` of the row fragment for the same identifier whose page
  answers `200`.
- **The trash does not become a place to work.** The page renders the issue and one button: a trashed
  issue cannot be edited, moved, commented or relabelled from it, because those writes refuse with
  `ISSUE_TRASHED` anyway (§6), and a page of read-only fields with an edit form that always fails would be
  a page that lies about what it can do.
- **The two edges now disagree about one row, in this direction.** The dashboard serves a trashed issue on
  its page and its panel while `hifa issue show ENG-13` still answers exit `3` `not_found` — the read's
  default, which this decision does not touch (SPEC-050 §2.5.3) — so P2 is restored for the page a refusal
  sends the reader to and **not** for the CLI's read, and D-059 is not the record of a trash that closed
  parity on both edges.

**Verification.** The contract is asserted by four scenarios of
`specs/acceptance/001-mvp-v1/16-dashboard.feature` — the detail page of a trashed issue answers `200` and
its only form is `Restore`, the panel of the same issue answers `200` with the same single control, the
row fragment of the same identifier stays `404`, and the archived detail keeps answering `200` after the
archive sent the reader to the list. The read's default is pinned by the test that already exists
(`tests/integration/db/test_reads_detail.py::test_a_trashed_issue_has_no_detail`, which this decision does
not touch) and the keyword's other side belongs to T-20.1. **This branch is the spec change only**: the
keyword, the two routes and the `404` of the row fragment are not in `src/`, and the drift is the reason
SPEC-040 goes to `spec_revision` 24 and SPEC-070 to 23.

## D-060 — A comment is a write to a parked issue, so it waits: the composer, the depth and the one anchor

**Date:** 2026-11-17 · **Wins:** SPEC-020 §2.5 · **Touches:** SPEC-020 §2 (the `rules` and `constants` rows of
the symbol table, §2.5's block), §4.2 (REQ-F020-21), §7 (its scenario), §8 (the symbol count), Notes;
SPEC-040 §2.2 (`comment_on_issue`'s `raises:` line), §4.1 (R20), §4.2 (REQ-F040-35/36), §6 (three tables,
including the two producer cells), §7 (four scenarios), Notes; SPEC-050 §2.4.1, §2.4.4, §2.5.3 (the row,
its exit set and its notes), §2.6.1 (the exit-5 condition), §5.1, §5.2, §11; SPEC-070 §2.1.2, §2.1.3,
§2.3.4, §2.6.2, §2.6.9, §2.6.10 (new), §4 (R27, R29), §6, §7, §11; SPEC-001 REQ-F022;
`specs/001-mvp-v1.tasks.md` (T-20.2);
`specs/acceptance/001-mvp-v1/08-comments-attachments.feature`,
`specs/acceptance/001-mvp-v1/16-dashboard.feature`; `specs/INDEX.md`

**Prior state: creating a comment is contracted on three edges and implemented on none of them above the
adapter.** The row exists — `comment` with `parent_id`, `resolved_at` and `edited_at` (SPEC-010 §3) —, the
rule exists (`validate_comment_body`, SPEC-020 §2.5), the entity exists (`Comment`), the port exists
(`CommentRepo.insert` / `CommentRepo.get`), the use case is declared (`comment_on_issue`, SPEC-040 §2.2,
with `CommentOnIssue` in §2.1 and its row in §3.1), the verb is published (`hifa issue comment REF [BODY]`,
SPEC-050 §2.5.3, and the `ID ISSUE AUTHOR CREATED BODY` table of §2.4.1), and the dashboard reserves two
page/fragment pairs (`POST /{team}/issue/{identifier}/comment`, `GET|POST /_/comments/{identifier}`,
SPEC-070 §2.1.2/§2.1.3). `src/hifa/app/issues.py` has no `comment_on_issue`, `src/hifa/cli/commands/issue.py`
has no `comment` leaf, and `src/hifa/web/app.py` registers no comment route. A surface that is declared and
not served is the one kind of hole the corpus cannot see: nothing fails, because nothing runs.

An audit of the three edges against those declarations found six blocking holes (a comment on a parked
issue has no declared answer on any edge; the depth bound REQ-F022 promises has no producer; the page has
nowhere to put a composer; the two fragment rows target one element with two different node types; the
`422` catalogue has no sentence for either refusal a comment can raise; `COMMENT_PARENT_MISMATCH` carries no
`field`, so the web's per-field map has no key to file it under) and five surface ones (§2.4.1 says one
column is truncated and the comment table needs a second; `--quiet` promises the affected *identifier* and
a comment has none; a NULL author has no rendering; the `CREATED` format is not pinned; §5.1-§5.2 are still
Spanish against D-024). **The decisions below are the answers; the corpus is what changes.**

### (a) A comment on a parked issue is refused, and the composer is not rendered for it

`comment_on_issue` gains the same two refusals every other write carries, and it gains them first — before
it reads the parent, writes the row or opens anything:

- `trashed = 1` ⇒ `Validation(rule='ISSUE_TRASHED', field='trashed')` ⇒ exit `2` / `422`, the message the
  catalogue already prints (`ENG-13 is in the trash; restore it first`).
- `archived_at IS NOT NULL` ⇒ `InvalidTransition(rule='ISSUE_ARCHIVED', field='ref')` ⇒ exit `5` / `409`,
  the one condition of D-014's bucket 5 (SPEC-040 §6, R20).

This is the answer D-059 already assumed in prose. Its price paragraph says "a trashed issue cannot be
edited, moved, commented or relabelled from it, because those writes refuse with `ISSUE_TRASHED` anyway
(§6)" — and that "anyway" was false of `comment_on_issue`, which refused nothing: a trashed issue's page
would have accepted a comment through a form the page did not render, and a client could have posted one.
R20 is the same shape one layer up: it lists the eight writes that refuse on `archived_at` and then says
`comment_on_issue` is "outside this rule in v1 … the product has no declared semantics for them under
archive yet". After this decision that sentence stops excluding it, and the sentence above it — "the two
operations that stay available are the ones that change the layer itself" — becomes true of the comment
too.

**The composer is not rendered where the write always fails.** §2.6.9's group renders `Restore` alone for
an issue in either layer, and the composer is not one of the group's controls (§2.6.9 states the group's
content exhaustively); it is gated on `vm.can_write and not vm.is_archived and not vm.is_trashed`, so the
page that says `Restore` is the page that offers `Restore` and nothing else. A control that is rendered
and always refused is the page lying about what it can do — the same reason D-059 removed the edit form
from the trashed page.

**The alternative rejected: allow commenting on an archived issue, because Linear does.** It costs a
second gate that says "this issue is parked, so you may do exactly one thing, unless it is that thing":
R20's list becomes eight writes plus one exception, the `Restore`-alone branch of §2.6.9 becomes
`Restore`-plus-a-composer, and the rule that a layer takes a row out of every listing "so a row that
accepts edits while nobody can find it is a row whose history is written where no one reads it" loses its
one-line form. Price accepted: **to comment you restore first, exactly as to edit.** The refusals print
the way back (`restore it first`), and `hifa issue restore ENG-13` is one command.

### (b) Depth is a rule with a home: `MAX_COMMENT_DEPTH = 3`, a root is depth 1

`MAX_COMMENT_DEPTH = 3` joins the constants of SPEC-020 §2.5 (`MAX_TITLE`, `MAX_BODY`, `MAX_DESCRIPTION`,
`MAX_SUBISSUE_DEPTH`) and `validate_comment_parent_depth(parent_chain_length: int) -> None` joins its
validators, with the naming of its neighbours: the caller counts, the validator decides.

- **A root comment is depth 1.** A reply to a depth-1 comment is depth 2, a reply to that is depth 3, and a
  reply to a depth-3 comment is refused — so the deepest reachable comment is depth 3 and the fourth link
  of a chain is `COMMENT_DEPTH_EXCEEDED`.
- **The caller counts by walking `parent_id` with the existing `CommentRepo.get`.** No adapter change, no
  new statement in any declared SQL of SPEC-030, and no column: the chain is read one row at a time,
  bounded by the constant, and the count enters the domain as an `int` — the split §1 already draws
  (`app/` reads and counts, `domain/` decides, SPEC-020 R6's `subtree_height` precedent).
- **The refusal is `Validation(rule='COMMENT_DEPTH_EXCEEDED', field='parent_id')`** ⇒ exit `2` / `422`,
  message `the thread is 3 deep; reply to a shallower comment` (the number formatted from the constant, so
  it cannot drift from it). Two edges print it as §6 says: CLI `hifa: <message> (field: parent_id)`, HTTP
  `422` with `field: "parent_id"` and the sentence as the composer's `form_error`.

**Why this is a rule and not a convention.** REQ-F022 promised "maximum depth 3" from the first revision of
the vertical, T-10 repeated it ("maximum depth 3"), and nothing in the tree produced, counted or refused
it: `validate_comment_body` looks at a body, `Comment.parent_id` accepts any ULID, and the DDL has no
`CHECK` that could see a chain. A bound that is not a rule is a promise nobody keeps — the exact failure
mode this repository's P8 exists for. The alternative — a `CHECK` or a trigger in SPEC-010 — was rejected
because the depth of a row is not a property of the row: it is a walk, and A3 gives walks to the adapter by
statement, not assertions to the schema by trigger.

### (c) The dashboard's composer is flat, and threading is CLI-only until its debt lands

The composer posts `body` **only**. It has no `parent` field, no "reply" control, and
`issue_vm["comments"]` stays the flat list §2.3.4 declares — no `parent_id`, no `depth`, no nested
`replies` — because a page that cannot ask for a thread cannot indent one. The list is rendered in
creation order (`created_at`, then `id`), which is the order the read returns.

`comment.reply` enters `CAPABILITIES` of `scripts/check_edge_parity.py` as a **single-edge row with
`debt="HIFA-59"`** — `cli="issue comment"`, `web=None` — next to a `comment.create` row that carries both
edges (the page form and the `/_/` fragment). The map exists to show exactly this: a capability the
reference surface has and the dashboard does not, printed as `DEBT` with the ticket that closes it, so the
next round cannot forget it and no route has to be invented to silence it.

**The price, stated.** An agent or a human can thread from the CLI (`--parent`) and cannot from the page;
and the page renders a thread started elsewhere as a flat list, in creation order — **a lie about shape,
not about content**. It is declared here and in §2.3.4 rather than discovered by the first reader who
wonders why the replies are not indented, and the ticket is what removes it: indentation needs a `depth`
key the read model does not carry (SPEC-030 §2.6's `Comment` row is the stored row) and a composer per
comment, which is a second form per row and a second target in §2.1.3.

### (d) One anchor, one swap target, two shapes, both valid HTML

The comment section of the detail page and of the panel is one structure with two node levels:

```html
<section id="comments">
  <ol id="comment-list" class="comment-list">
    <li class="comment" data-comment-id="…">…</li>
  </ol>
  <!-- the composer of §2.6.10, gated on can_write and neither layer -->
</section>
```

- `GET /_/comments/{identifier}` returns the **whole `<ol id="comment-list" class="comment-list">`** and the
  client swaps it in **`outerHTML`** at `#comment-list`; `hx-target="#comment-list"`, `hx-swap="outerHTML"`.
- `POST /_/comments/{identifier}` returns **one `<li>`** with status **`200`** and the client appends it
  `beforeend` into **`#comment-list`**; `hx-target="#comment-list"`, `hx-swap="beforeend"`. `200` and not
  `201`: this layer's answers are HTML the client puts somewhere, and the only `201` of the tree is the
  REST edge's `POST /api/v1/issues` (SPEC-060).
- The no-JS form's `303` lands on `…/issue/{identifier}#comments`, which is the section, so a browser
  scrolled to that fragment shows the list and the composer the write changed.

**The alternative rejected: both routes targeting `#comments` with `innerHTML`.** It nests an `<ol>` inside
an `<ol>` on the `POST` (the answer is one `<li>`) and appends an `<li>` into a `<section>` on the `GET` if
the anchors are swapped — the collision the old §2.1.3 rows had, where `GET` and `POST` named the same
element with different node types and only one of the two could be right.

**And the second alternative, rejected after the first was written:** the `GET` swapping the **`innerHTML`
of `#comments`** with the `<ol>`. It is valid HTML and it is still wrong, because the section also holds
the `<h2>` and the composer of §2.6.10: a refresh would delete the box the reader is typing in. The list is
the node that changes, so the list is the node the swap names — `outerHTML` on the `<ol>`, which leaves the
heading and the box in place. `#comments` keeps its job as the URL fragment a `303` lands on, and stops
being a swap target; a reader following the anchor is not the same event as a client replacing the list.

**What this decision does not do.** It adds no verb, no endpoint, no column, no table and no enum value: a
comment is still one `INSERT` into `comment` with `author_id` from the actor (SPEC-040 §3.1), the history
table still does not see it (the comment **is** the record, §4.1 R3), and the depth bound is computed from
rows that already exist. It does not publish `comment resolve` or `comment edit` either: resolving and
editing a comment stay in the open questions of SPEC-001 §10.1 point 8, and `update_comment` /
`resolve_comment` stay declared-not-implemented in SPEC-040 §2.2.

**Verification.** SPEC-020 gains the constant, the validator and one scenario (`REQ-F020-21`), SPEC-040's
matrix names three rules for `comment_on_issue` where it named two, SPEC-050's exit set for the verb
becomes `0,2,3,5`, and `specs/acceptance/001-mvp-v1/` asserts the two refusals on both edges: the depth-3
reply and the empty body in `08-comments-attachments.feature` (exit `2`, no row inserted), the `303` to
`#comments`, the `200` with one `<li>`, the `422` with `a comment needs a body` and the two absences of the
composer (archived, trashed) in `16-dashboard.feature`. **This branch is the spec change only**: the use
case, the verb, the two routes and the composer are not in `src/`, and the drift is the reason SPEC-020
goes to `version: 3` (a name enters the `rules` row of §2, which is the public contract), SPEC-040 to
`spec_revision` 25, SPEC-050 to 22, SPEC-070 to 25 and SPEC-001 to 12. The parity rows of (c) are T-20.2's
step, in the same PR as the routes, because `CAPABILITIES` may not name a verb the tree does not have.

## D-061 — The label surface: five verbs, one replacement and one refusal to retire what is in use

**Date:** 2026-11-20 · **Wins:** SPEC-040 §2, §4.1 (R10, R22), §5.6, §6 · **Touches:** SPEC-040
§2 (the table of §2's intro, which gains the four label command classes and `restore_label`),
§2.1/§2.2/§3.1/§4.1/§4.2/§5/§5.6/§6/§7/§9/Notes, SPEC-050
§2.3/§2.3.1/§2.4.1/§2.4.3/§2.4.4/§2.4.5/§2.5/§2.5.3/§2.5.3.1/§2.5.4/§2.7/§3/§6/§9/§11,
SPEC-020 §2.5/§2.6/§4.2/§6/§7/Notes (the exception class of `validate_label_scope` and the `field` of
`NotFound`), SPEC-060 §2.5.1/§2.5.3/§2.7/§5.3/§6.1/§6.3/§7/§11 (the set that triggers the label-in-use
`409`, the transport codes, the `field` of a 404, its two published examples and the scenario of §7 that
asserts that `field`), SPEC-001 REQ-F024,
REQ-F034, §7 and Notes,
`specs/001-mvp-v1.tasks.md` (its header, its acceptance-coverage line and its Notes — no checkbox),
`specs/acceptance/001-mvp-v1/09-labels.feature`, and the three `error.field` steps of a missing issue
in `03-issues-crud.feature`, `15-cli-contract.feature` and `16-dashboard.feature` (see (h), the one
place a change outside the label surface was forced), `specs/INDEX.md` (the label paragraph, the
corpus count and the decision count), and this file; `scripts/check_edge_parity.py` is the
implementer's step, not this revision's. **The list is the diff's, re-derived in round 3:** it also
carries SPEC-050 §2.7 (the `hifa issue show ENG-999` example, whose `field` moves with (h)) and §9 (the
provenance bullet that tells a reader where `label show|update` and `--undo` came from), and it does
not carry SPEC-050 §2.4.2, which no hunk of this branch edits — the `replaced` object left that section
in revision 24 and the entry said so while still naming it. **Round 4 adds the one entry the list still
owed:** SPEC-060 §7, whose `error.field` step is `060:1302` and whose hunk is the `ref` → `issue` change
the round-3 cascade made; D-062 already carried it, so the two entries now name the same sections for the
same reason (D-062).

**Prior state: labels worked on the way in and nowhere after that.** `--label` on `issue create` accepts
repeats or a comma-separated list, resolves each name per team, creates nothing, links `issue_label` and
the dashboard renders the chips. Everything after creation was declared in prose and hollow in the
contract. SPEC-040 §2.2 named five label functions and left `add_label` returning an `Issue` — with no
place for the `replaced` REQ-F024 has required since the first revision of the vertical, because
`issue_label` is written only by `create_issue`. SPEC-050 published `label add|list|archive` and no
`label update`, while the ticket asks to rename or recolor a label without editing SQLite by hand;
`label archive --undo` was published with no use case behind it (`retire_label` only sets
`retired_at`); `label archive`'s in-use refusal lived in a prose sentence while `retire_label`'s
`raises:` line carried only `LABEL_RETIRED`; `AddLabel.create_if_missing` and `color` had no flag to
arrive through; `label_detail` had no verb; `label list --team` named a flag whose meaning nobody
wrote; and `issue label` carried a `--team` that can only contradict the issue's own team. Ten holes,
one ticket, and the web half already split out as HIFA-61.

### (a) The replacement travels in a result object, and the answer is the one two-key envelope

`add_label` returns `AddLabelResult(issue, replaced)`. `replaced` is the label entity the write took off
the issue, or `None`. `issue label --json` and `issue unlabel --json` emit
`{"issue": <Issue>, "replaced": <{id,name,color}|null>}`; the table output stays one row with the
columns of `issue list` and `--quiet` prints the identifier.

**The alternative rejected: a `replaced` key on the canonical `Issue`.** §2.4.2's objects are emitted
complete, so a key added for one verb appears on every issue of every command, and it would say
"nothing was replaced" about a read that replaced nothing because it wrote nothing. The replacement is a
fact about the **call**, not a property of the issue — the same distinction D-051 drew for the board
rebalance, which is why `move` has no `rebalanced` key. Here the fact is demanded by a requirement, so
it travels beside the object instead of inside it.

**The alternative rejected: the CLI computing it.** It would read the labels before the write, call the
use case, and diff — a second copy of the rule at the edge (P4), and a race between the two reads.
**And the third: a string.** REQ-F024 wrote `replaced: <label>`; a `{id, name, color}` object is what a
consumer can act on (the chip to remove) and it is the shape `labels[]` already carries, so a client
reads one shape for both. The cost is that this is the only mutation of the tree answering two keys, and
SPEC-050 §2.4.3 says so in one sentence rather than leaving it to be discovered.

### (b) The replacement is two history rows, with the field name the adapter already uses

A `singleSelect` group replaces: every label the issue carries with the same `parent_id` is deleted,
then the incoming one is inserted. The history gets one row per label moved, sharing the single `now` of
the use case, **detach first**: `('label', '<replaced name>', NULL)` and then `('label', NULL, '<incoming
name>')`. `label` is the logical key SPEC-030 §2.5 already declares for this table, so no new name is
invented and a test can count rows by `field = 'label'`.

**Why a row at all.** A history that says "bug was added" while a label silently disappeared is a
history that cannot explain the issue it describes. **Why not one row with both values.** The pair is
two facts — one label left, another arrived — and R3's granularity is one row per change.

**The invariant is stated over the set, not over one row.** "At most one label of a `singleSelect`
group per issue, and it is the requested one" holds even when a hand-edited database carries two
siblings: every sibling **other than the incoming label** is removed (step 6 reads the group's rows with
`l.id != label.id`), the attach row is written only when the label was not already linked, and
`replaced` is the first removed sibling in `name` ASC order, because R16 forbids an order that depends
on how the rows came back. Through this port the case does not arise; stating it costs one clause and
removes the question.

### (c) The catalogue grows two verbs: `label update` and the undo of a retire

`hifa label update REF [--name] [--color] [-d/--description] [--clear-description]` over `update_label`,
and `hifa label show REF` over `label_detail` — the read had no consumer on the reference surface, and a
catalogue whose only reader is `label list` cannot answer "what is this label" before renaming it.
`--clear-description` together with `-d/--description` is exit 2 `invalid_option`, `field:
"description"`: the pair says "clear it" and "set it to this" about one column, and a flag that
contradicts another flag is a bad invocation, the same answer `--json` with `--quiet` and `--team` with
`--no-team` already get. It is a refusal, not a precedence — a silent winner would leave the loser
unexplained on a command whose whole job is one column.

`restore_label` is a **second use case**, not a flag: `RetireLabel` carries a ref and nothing else, one
use case opens one transaction (R1), and `trash_issue`/`restore_issue` are the family's precedent for
two commands over one column. `label archive --undo` is the undo SPEC-050 already published; D-031 is
the record of what happens when an edge publishes an `--undo` before the function exists.

**A repeated change is a conflict, not a no-op.** `retire_label` of a retired label is
`LABEL_RETIRED` (already declared), so `restore_label` of a live one is `LABEL_NOT_RETIRED`: the family
answers a repeated change with exit 4 and `issue archive`'s idempotent 0 is the exception that has its
own reason (an archive can be asked twice by a script that does not read). A silent 0 here would also
hide the likelier mistake — a wrong reference.

**What `update_label` may not touch.** `team_id`, `parent_id`, `is_group`, `group_type` and
`retired_at`. Moving a label between levels or re-parenting it changes what every chip means and needs a
product decision ("does the team keep the links?") that nothing in the corpus has taken. `label update`
renames, recolors and re-describes; a retired label may be renamed, because the row is the record and
the fix is worth having.

### (d) `LABEL_IN_USE` means "any `issue_label` row", and the trash counts

`retire_label` refuses a label with `Conflict(rule='LABEL_IN_USE')` when `LabelRepo.count_uses > 0` —
every layer, archived and trashed issues included.

**Why the stricter reading.** The alternative (only live issues) needs a count that filters the layer,
and the port has no such read: `count_uses` counts rows of `issue_label`. Adding a second read to
SPEC-030 for one refusal is a contract change in the adapter to serve a judgement the application layer
owns. And the stricter reading is the true one: a restored issue comes back with its chips, and a label
retired under an archived issue is a chip nothing can re-attach — `add_label` refuses a retired label
(SPEC-020 R8), so the only way to fix it is `label archive --undo`, which is a fine answer but not one a
refusal should hide.

**The price, stated.** Retiring a label whose only uses are archived issues is refused, and the reader
detaches it or restores what it is attached to. **And the losing spec, edited:** SPEC-060 §2.5.3's
parenthetical — "(`issue_label` with ≥1 row and the issue not archived)" — narrowed the condition to a
set the use case cannot see. SPEC-040 wins (D-012), and SPEC-060 `spec_revision` 9 now states the rule
this decision applies (any `issue_label` row, whatever layer) instead of the narrower one; the route's
`409` for `label in use` does not move, and neither does its status code.

### (e) `--create` is the cheap creation, and `issue label` loses a flag that had no meaning

`hifa issue label REF LABEL... [--create] [--color HEX]`: `--create` maps to
`AddLabel(create_if_missing=True)` and the label is created in the **issue's team** — not the
workspace — because a label born while tagging an issue belongs to the team that is tagging. Color
`#6B6F76` by default, the same default as `label add --color`; a group, a workspace label or a
description stays `label add`'s job, so REQ-F040-26's "one creation path" survives with one named
exception. `--color` without `--create` is exit 2 `invalid_option`: a color that would be written
nowhere is a typo, and `AddLabel`'s own rule already requires the color when it creates.

`--team` is **removed** from `hifa issue label`: the label has to belong to the issue's team or be a
workspace label, so a flag that could only contradict the issue is not a scope, it is a trap. `--team`
at leaf level stays the global option of §2.2, which this verb does not consult. No user can be
surprised by the removal: the verb is not in the Typer tree yet.

### (f) One resolution rule per kind of reference, and one refusal for the name two teams share

Resolution is decided by **what the reference is attached to**, and there are exactly three cases:
a *catalogue* reference (`label show|update|archive`, `label_detail`) has no issue to take a team from,
so it resolves a ULID directly and a name case-insensitively over the **whole workspace**; an
*issue-scoped* reference (`issue label`, `issue unlabel`) resolves the same ULID and then a name in the
**issue's team, then the workspace** — the issue's team is the tie-breaker, so two matches cannot be
ambiguous there; and `label add --parent` is a group of the new label's **own level**, because a team
label hanging from a workspace group (or the reverse) is a level no rule of this corpus defines.

More than one workspace match on a catalogue reference is `Validation(rule='LABEL_AMBIGUOUS',
field='ref')` ⇒ exit 2, with the team keys in the message and `hifa label list --json --all` as the way
to the ULID.

**That scope reads retired rows, and the listing does not.** The four catalogue references resolve with
`LabelRepo.list(..., include_retired=True)`: these verbs exist to act on the catalogue, and the two
things the family was extended for — clearing `retired_at` with `label archive --undo` and renaming a
retired row — are invisible under the port's default `include_retired=False`. With the default,
`label archive infra --undo` on a retired label resolved `None` and answered `NotFound`/exit 3 while the
verb and its scenario say exit 0, and the double retire and the rename were equally unreachable by name.
The two defaults therefore differ on purpose and both are stated: a **read that renders a catalogue**
hides retired rows unless `--all`, and a **write that acts on one reference** sees them, because hiding
a row from a listing is a display choice and hiding it from the verb that un-retires it is a dead
contract. Ambiguity is judged over the same set the resolution reads, so a retired `Bug` of one team and
a live `Bug` of another is `LABEL_AMBIGUOUS` rather than a silent pick of the live one; two rows with one
name in the **same** team cannot arise, because `uq_label_team`/`uq_label_ws` count retired rows
(SPEC-010 R6, D-010), so the code always means two teams and never one team twice.

**The alternative rejected: resolving team-first on the catalogue verbs too.** There is no issue to take
the team from, and `--team` on those three would be a second scope on a command that already has one for
`add` (creation) and `list` (a listing). A silent pick between two teams' "Bug" is the one outcome a
rename must never have. **The alternative rejected for the list filter:** `issue list --label` has no
issue either, so it uses the *command's* team (`--team`, else the resolved default) and then the
workspace — stated in SPEC-050 §2.5's conventions, because calling it issue-scoped was a rule nothing
could implement. **`label list --team` means the set an issue of that team can wear** (the team's plus
the workspace ones), because that is the set `issue label` accepts; with no `--team` it is every label of
the workspace.

### (g) Edge parity: seven rows of debt on two tickets — the implementer's step

The web half is HIFA-61 (T-20.3: `POST`/`DELETE /_/issues/{identifier}/labels`) and the label
administration page is HIFA-62. `CAPABILITIES` may only name a verb the tree has and a route the app
serves, so the rows land **in the same PR as the code**, not in this one:

- `issue.label` — `cli="issue label"`, `web=None`, `debt="HIFA-61"`
- `issue.unlabel` — `cli="issue unlabel"`, `web=None`, `debt="HIFA-61"`
- `label.list` — `cli="label list"`, `web=None`, `debt="HIFA-61"` (the panel's picker has no catalogue
  without it, which is what T-20.3 says)
- `label.create`, `label.show`, `label.update`, `label.archive` — `cli=…`, `web=None`, `debt="HIFA-62"`
  ("Dashboard: label administration — create, rename, retire and show"). These four are **user-facing**,
  so `reason=` is not available to them: `check_edge_parity.py` reserves that escape hatch for "an
  operational command with no dashboard meaning", and P2 says no edge owns a feature the other cannot
  reach. The first draft of this decision called them EXEMPT on the grounds that no `SPEC-070` page was
  contracted; that is a gap, not a by-design exclusion, and a gap with a ticket is debt.

`issue list --label` needs no row and no change: the filter is the `issue.list` capability, its team
scope is the command's (§2.5's conventions), and the AND semantics inside the family are SPEC-030 §2.6's
(unchanged).

### (h) A 404 publishes what does not exist, and the resource is the accessor — recorded in D-062

`NotFound` gains a derived `field` that returns its `resource` (SPEC-020 §2.6): a **reference** that
resolves nowhere is `NotFound(resource='label')` ⇒ `field: "label"`, and a label the issue exists without
wearing is `NotFound(resource='label_link')` ⇒ `field: "label_link"`. That settles the contradiction the
label surface carried — SPEC-060 §2.5.3 published `label_link` for the link while SPEC-050 §2.4.5
declared **both** of that verb's 404s `label` — and no raise site passes the field, so the two edges read
one value and cannot drift apart.

**D-062 is the record of the change and this subsection is its label half.** It states why a per-site
`field` argument was rejected, what the derived value costs the whole issue family, and every file and
section it forced; read them together.

**The price, and the two universals a previous draft of this paragraph got wrong.** (1) It called the
`error.field` steps of a missing issue "the only statements outside the label surface this revision
edits"; the revision also edits SPEC-001 REQ-F034, SPEC-050 §2.5.3 (the `relations`, `--parent`,
`--assignee` and label clauses that name a resource) and §2.5.3.1, SPEC-060 §5.3 (the mapping line), and SPEC-020 §6 and
§7 (the `NotFound` row and the two `field` scenarios) — D-062 carries the list the diff gives. (2) It said
"every other `ref` in the corpus is `InvalidTransition(rule='ISSUE_ARCHIVED', field='ref')`": the `ref`
that stays is that one **and** the six sites of `Validation(rule='LABEL_AMBIGUOUS', field='ref')` — the
`label_detail` comment and R22 of SPEC-040, its §6 row, its §7 scenario, and two sentences of SPEC-050
§2.4.5 and §2.5.3 — which this same decision defines and which do not move, because there `field` names
the argument the verb took and not a resource. Nothing else of a 404 changes: the code stays `not_found`,
the exit code stays 3 and the status stays 404.

### (i) What this decision does not do

No web route, no fragment, no `replaced` in the panel's chip (a `singleSelect` replacement leaves the
replaced chip in the DOM until the labels container is re-rendered — HIFA-61's contract, named here so
it is not discovered later). No re-parenting, no level change, no `label delete` (D-015). No change to
`create_issue`'s use of labels beyond the exception class of a retired one, to `issue list --label`, to
`export`/`import`, or to the REST surface: SPEC-060 keeps `POST /issues/{ref}/labels ⇒ 200 IssueOut` and
reads `.issue` from the result object, which is not a change of its contract.

**Verification.** SPEC-040 goes to `version: 5` / `spec_revision: 29` (a return type, a field of
`CreateLabel`, a new command and four codes are §2; the second round corrects the catalogue scope, the
matrix rows and one producer cell without touching §2's signatures; the third adds the §7 scenario of
the retired rename and its count), SPEC-050 to `version: 5` /
`spec_revision: 26` (five leaf commands, two exit sets, one envelope, and the `TEAM` column it now
names; the second round adds the retired visibility of a `REF`, the two 404 `field` values, the
`--clear-description` refusal and one filter row; the third renames an unknown `-a/--assignee` resource
to `member` and the `version` bump is the one the round-2 note owed, because two §2 statements had
moved), SPEC-020 to `version: 5` / `spec_revision: 12` (the
exception class `validate_label_scope` declares in §2.5, and the derived `field` of `NotFound` in §2.6;
the third says which reader reads the `details` that carries them),
SPEC-060 to `version: 4` / `spec_revision: 11` (the set that triggers the label-in-use `409`; the second
round publishes transport codes in §2.5.3 and the published `field` of a 404 in §5.3/§6; the third
corrects the §7 assertion, whose `version` 4 is owed for the two codes §2.5.3 moved), SPEC-001
to `spec_revision: 16` (one 404 example in REQ-F034, and the count) and `specs/001-mvp-v1.tasks.md` to
`spec_revision: 15` with **no checkbox ticked**. The executable half is `specs/acceptance/001-mvp-v1/09-labels.feature`: the three scenarios
that already existed plus seventeen that pin the rename, the recolor, the two conflicts of the retire
axis, the undo, the shared name, `--create`, `--color` without `--create`, the two shapes of a missing
unlabel and the partial application of an N-label command, plus the two the third round adds (the
`--clear-description` refusal and the rename of a retired label) — 22 scenarios, and the corpus of
`specs/acceptance/001-mvp-v1/` is **240** (228 `Scenario` + 12 `Scenario Outline`), the number every
place that states it now carries; the second round changes no scenario count and no scenario text of
that file except the one `error.field` step whose value §(h) settles, and the third changes no text of
it either — it adds two scenarios and the count that follows. **This branch is the spec change
only**: `src/hifa/app/labels.py`, `src/hifa/cli/commands/label.py`, `add_label`/`remove_label` and the
seven capability rows are not in the tree, and that drift is what the `spec_revision` bumps record.

## D-062 — A 404 publishes the resource it could not find

**Date:** 2026-11-20 · **Wins:** SPEC-020 §2.6 (the exception owns the noun of what is missing) ·
**Touches:** SPEC-020 §2.6, §4.2 (REQ-F020-14), §6, §7, Notes; SPEC-040 §2.2, §4.1 (R22), §5.6, §6;
SPEC-050 §2.4.5, §2.5.3, §2.5.3.1, §2.7, §6, §11; SPEC-060 §2.5.1, §2.5.3, §2.7, §5.3, §6.1, §6.3, §7,
§11; SPEC-001 REQ-F034 and Notes; `specs/acceptance/001-mvp-v1/03-issues-crud.feature`,
`15-cli-contract.feature`, `16-dashboard.feature`; `specs/INDEX.md`

**The rule.** `NotFound` derives `field` from its own `resource` (SPEC-020 §2.6): the noun of the thing
that does not exist *is* the field the edge publishes, no raise site passes it, and a 404 therefore says
`issue` for a missing issue, `label` for a reference that resolves nowhere, `label_link` for a label the
issue exists without wearing, `member` for an unknown `-a/--assignee`. One exception, one field, on both
edges.

**Why the edges could not keep choosing it.** The surfaces had already drifted on the same input:
SPEC-060 §2.5.3 published `field: "label_link"` for a label the issue does not wear while SPEC-050 §2.4.5
declared **both** of that verb's 404s `label` and promised "the same envelope as the API", and both were
published contracts a client could have coded against. A rule that lives in two spec tables and nowhere in
the codebase is a rule with two implementations, and the second one is the bug.

**The alternative rejected: an optional `field=` argument per raise site.** Four reasons, in order of
weight. (1) It is the same defect with better manners: every raise site deciding the string is one more
place to disagree, and the review found two of them already disagreeing in the published corpus. (2) Every raise
site would carry information it does not have: `NotFound(resource='label', key=ref)` already says what
is missing, and a `field` beside it can only repeat it or contradict it. (3) The default would be the
contract — an omitted `field` would fall back to something, and a reader would have to know which
something. (4) A derived value is testable in the domain (`NotFound(resource='label').field == 'label'`)
instead of in each edge, so a new edge cannot get it wrong.

**What it does not put on the wire.** The `resource`/`key` mapping `HifaError.details` returns is the
exception's own accessor surface (SPEC-020 §2.6, §6), not the payload of an HTTP error: SPEC-060 §6.3
declares `details` as `null` or a list of `{field, message}` and a 404 renders `"details": null`, because
the resource already travels in `field` and the key in `message`. Widening §6.3 to carry a dict for one
class of error is the alternative this decision did not take: it would change the shape of every error
envelope to save one key, and it would give a client two places to read the same noun. Round 4 closed the
last place the corpus still promised otherwise: SPEC-050 §2.4.5's 404 sample printed three keys while
promising the API's envelope, and it now prints `"details": null` — the CLI and the API publish the same
four keys, as §2.4.5 has promised since the first revision.

**The consequence, which is the whole issue family and not the label one.** A derived field cannot be
`ref` for an issue and `label` for a label: `ref` names the *parameter* the route took, and the rule says
the field names the *resource* that is missing. So `GET /api/v1/issues/ENG-999` publishes
`field: "issue"`, `hifa issue show ENG-999` prints `(field: issue)`, and the three acceptance steps of a
missing issue assert it. The statements it forced, from the diff of this branch:

- SPEC-020 §2.6 (the accessor and the sentence that says who reads `details`), §4.2 REQ-F020-14 (the two
  observable values), §6's `NotFound` row, §7's two `field` scenarios.
- SPEC-040 §2.2 and §5.6 (the raise sites, which pass `resource`/`key` and never `field`), §4.1 R22, and
  §6's `NOT_FOUND` row, whose `When` cell lists the resources the field now publishes.
- SPEC-050 §2.4.5 (both 404s of `issue unlabel`), §2.5.3 (the `--parent`, `--assignee` and label clauses
  that name a resource, and the `-a/--assignee` value that moved from `assignee` to `member`), §2.5.3.1
  (the rule that produced the second spelling), §2.7 (the `issue show ENG-999` example) and §6.
- SPEC-060 §2.5.1 (the two 404s of the labels subresource), §2.5.3 (the same pair for the issue's labels),
  §2.7 (the 404 example), §5.3 (the mapping line), §6.1 (`not_found`'s `field` cell), §6.3 (the envelope
  sample) and §7 (the scenario that asserts it).
- SPEC-001 REQ-F034, and the three `error.field` steps of a missing issue in
  `03-issues-crud.feature`, `15-cli-contract.feature` and `16-dashboard.feature`.
- The spelling of the noun, which round 4 finished. `state` is the noun the flag names and the one
  SPEC-050 §2.5.3 publishes for a `--state` that resolves nowhere (exit 3 `not_found`), while SPEC-040
  §5.2's declared raise for that same input named the table instead and the two `raises:` lines of §2
  said the same: a value a client reads was publishing an implementation name. The raise site, those two
  `raises:` lines and §6's `NOT_FOUND` row now spell `state`, and that row is the single statement of the
  set — an implementation name survives nowhere in SPEC-030 or SPEC-040 as a noun, only as the table it
  is (SPEC-010 §3).

**What does not move.** The code stays `not_found`, the exit code 3 and the status 404. The `ref` values
that are not resources stay exactly as they were: `InvalidTransition(rule='ISSUE_ARCHIVED', field='ref')`
in SPEC-040 §6, where the field names the parameter the write carried, and the six
`Validation(rule='LABEL_AMBIGUOUS', field='ref')` sites of the same decision (the `label_detail` comment
and R22, §6's row, §7's scenario, SPEC-050 §2.4.5 and §2.5.3) — there the ambiguity is about the argument
the verb took, and the argument is what a caller has to re-enter. No route, flag, column or enum value is
added.

**The `version` bumps it required.** SPEC-020 4 → 5 (a public attribute of the exception, §2.6 — taken
in revision 11, under D-061's entry, because the two decisions shipped in one change),
SPEC-050 4 → 5 (a published 404 `field` moves in §2.5.3, and §2.5.4 publishes a refusal it lacked),
SPEC-060 3 → 4 (two `error.code` values of §2.5.3 move). SPEC-040 stays at `version: 5`: this decision
changes no signature of its §2 — the raise sites already carried `resource` and `key`, and what moved is
which edge value a 404 publishes.

**Verification.** SPEC-020 `spec_revision` 11 → 12, SPEC-040 28 → 29 (the §7 scenario of the retired
rename and its count, which the same round owed), SPEC-050 25 → 26, SPEC-060 10 → 11, SPEC-001 15 → 16
and `specs/001-mvp-v1.tasks.md` 14 → 15 with **no checkbox ticked**. The sweep that closes it — every `ref`
a 404 assertion or envelope sample still names, filtered by the two rules that legitimately keep one —
returns nothing outside `LABEL_AMBIGUOUS` and `ISSUE_ARCHIVED`; the corpus of
`specs/acceptance/001-mvp-v1/` is **240** scenarios (228 `Scenario` + 12
`Scenario Outline`), the figure `SPEC-001 §7`, `specs/INDEX.md` and `specs/001-mvp-v1.tasks.md` now
carry.

**The round that closed the residue (round 4).** SPEC-020 `spec_revision` 12 → 13 (§6's sample and the
§7 scenario that read it), SPEC-040 29 → 30 (§2's two `raises:` lines, §5.2's raise site, §6's
`NOT_FOUND` row and the payload sentence under it), SPEC-050 26 → 27 (§2.4.5's 404 sample), SPEC-001
16 → 17 and `specs/001-mvp-v1.tasks.md` 15 → 16 (six pointers into a numbered subsection of SPEC-001 §7
that document does not have — its §7 runs straight to §8 — now pointing at `§7` itself), again with no
checkbox ticked and no scenario added or removed: the corpus is still **240 (228 + 12)** and this file
still holds **62** decisions. No `version` moves in that round: §6 of SPEC-020 stops claiming a payload
the edges never built, and a pointer, a sample's fourth key and the spelling of a noun are not a change
to any requirement, flag, route, exit code or payload shape.

## D-063 — A page with no query is a state, not a refusal

**Date:** 2026-11-20 · **Wins:** SPEC-070 §2.1.8 (the page owns its four states) and SPEC-090 §2.6.1 (the
use case owns `query_empty`) · **Touches:** SPEC-070 §2.1.1, §2.1.8, §2.1.9, §2.3.5, §4 (R21), §6, §7,
§11; SPEC-090 §1, §2.5, §2.6, §2.6.1, §6, §7, §11, §12; `specs/INDEX.md`

**The rule.** `GET /search` has exactly four states: **empty** (no `q`, or a `q` of spaces) ⇒ `200`,
the use case is not called; **results** ⇒ `200`; **no results** (`hits=[]` from a valid query) ⇒ `200`;
**error** (a query SPEC-090 §6 refuses) ⇒ `422`, the same template with the query kept. It answers `400`
for none of them. `query_empty` survives as the refusal of the two edges that are **asked to run a
query**: `hifa search` (exit 2) and `GET /api/v1/search` (422, SPEC-060 §2.5.4).

**Why the surfaces could not keep the `400`.** SPEC-070 §2.1.1 and §6 declared "an empty `q` in `/search`
⇒ `400`", and `base.html` renders `<a href="/search">` in the top bar of **every** page: the one URL the
navigation offers answered an error page, which is the dead end T-11 was opened to remove. The
contradiction was not between two specs but between a spec row and a link the same spec declares in
§2.1.1 — and a page whose only entry point is a `400` is a page nothing links to, whatever the table
says.

**Why `422` and not `400` for the query it cannot read.** The page never *asked* for a query: the reader
did, in a form the page serves. A missing `q` is not invalid input, it is the state the page is in before
the first search; a `q` SPEC-090 refuses is invalid input, and `422` with the input kept is what this
layer already does for every other rejection (§6). `400` stays where it already was: the `/_/` family
without `HX-Request` (R5) and the cross-site write guard (R28) — both of them a malformed *client*, not
a mistyped query.

**The alternatives rejected.** (1) *Keep the `400` and point the sidebar at `/search?q=`* — there is no
query to prefill, and a seeded one would run a search nobody asked for on every click. (2) *Let the page
call `search_issues` with an empty text and render the `query_empty` it raises* — the page would catch
the refusal to show the empty state, which is a `400` wearing the markup of a `200`; the state is
declared instead, and the use case is never called without something to search for. (3) *Make the empty
state a `404`* — the page exists and the workspace exists; only the query is absent.

**What it does not change.** The CLI's exit `2` and the API's `422` for `query_empty`, its code and its
message; the `q` grammar of SPEC-090 §2.3; the palette (`/_/palette`, R19), whose two phases already
answer an empty `q` with actions only; and the board/list `q`, which is the title filter of D-025.

**Verification.** SPEC-090 §2.6.1 lists the seven consumer rules; SPEC-070 §2.1.8's state table is the
contract and §7 has a scenario per state (the empty one asserts `200` and the absence of any hit); the
catalogue of §6 no longer carries `type something to search`, because it was a refusal that no longer
exists — the empty state's copy is a read-model key of §2.3.5.

## D-064 — The search use case is `search_issues`, and the hit is flat

**Date:** 2026-11-20 · **Wins:** SPEC-090 §2.2/§2.3 (the shape of the payload) and SPEC-040 §2.2 (the
entry point every edge calls) · **Touches:** SPEC-090 §1, §2.3, §2.5, §2.6, §2.6.1, §8, §12; SPEC-050
§2.3, §2.4.1, §2.4.1.1, §2.4.3, §2.5.1, §7, §11; SPEC-070 §2.1.4, §2.1.8, §4 (R21), §11;
`specs/INDEX.md`

**The rule.** (1) **One entry point:** `search_issues(db: Database, query: SearchQuery) -> SearchPage`,
the signature SPEC-040 §2.2 has carried, is what every edge calls; `SearchService` is the object
`app/search.py` builds over `Reads` and nobody outside that module constructs; `parse_query(raw)` is the
public parser and the class's `parse` method is gone. (2) **One hit shape:** `SearchHit` of SPEC-090 §2.2,
flat and eleven fields — `issue_id, identifier, title, state_name, state_type, priority, due_date,
assignee_display_name, updated_at, snippet, score`. D-002's `{issue, snippet, score}` is the one part of
that resolution that does not survive: it predates the dataclass and would have made every edge read six
values out of a nested 31-key `Issue`.

**Why the entry point had to be settled.** The corpus carried three answers to "how do I search": SPEC-040
§2.2 declares the function, SPEC-090 §2.3 declared the class, SPEC-050 §2.3 and SPEC-070 §2.1.4/R21 called
the class, and T-11 was opened on the observation that "there is no `search_issues` use case". A class
whose constructor takes `SearchReads` cannot be the application port: an edge that builds it has to import
`adapters/sqlite/reads.py` to obtain the port, which A3 forbids for `web/` and `cli/`, and D-012 settles the
question in advance — an edge calls a function of SPEC-040 §2.2 and declares none of its own. The
implementation is one function that constructs the service, calls it once and returns its page, so the
class keeps the logic and the port stays a function.

**Why the hit is flat.** The CLI table (SPEC-050 §2.4.1.1) and the page (`search_vm`, SPEC-070 §2.3.5)
print the same six fields — identifier, title, state, priority, updated, snippet — and a flat hit hands
them each one key; the nested shape hands them `hit.issue.identifier`, `hit.issue.title`,
`hit.issue.state.name`, `hit.issue.priority`, `hit.issue.updated_at` and `hit.snippet`, i.e. a 31-key
payload to render six values, with two keys of the same object read at two depths. The envelope stays the
five keys of SPEC-090 §2.2 — `hits, total, has_more, backend, took_ms` — and only the element changes.

**The alternatives rejected.** (1) *Promote `SearchService` to the port and declare it in SPEC-040 §2.2* —
a port whose constructor takes an adapter object forces every edge to know the adapter; it also leaves
`search_issues` declared and unimplemented in the spec that owns the port. (2) *Keep `{issue, snippet,
score}`* — see above; it also contradicts the flat hit every scenario of SPEC-090 §7 already asserts.
(3) *Two shapes, one per edge* — the same query would answer two payloads depending on who asked, which is
exactly what D-016 forbids and what the API already defers to SPEC-090 for.

**What it does not change.** The `hifa search` flags, the envelope's five keys, the ranking, the `like`
fallback, the `snippet`'s markers and the rule that no edge escapes them; the API's `SearchPage`
(SPEC-060 §2.5.4) and its `422`; the CLI's exit codes `0/2/6`; and the page's `count=False`, which is
D-063's page, not this shape's.

**Verification.** SPEC-090 §8 step 11 names `search_issues`/`parse_query`/`SearchService` for the contract
check; SPEC-050 §7 asserts the eleven keys of a hit; SPEC-070 §7 asserts the six rendered fields and the
single escaping of the snippet; `grep -rn "SearchService(" src/hifa/cli src/hifa/web` must return nothing.

## D-065 — `fts5_unavailable` has one producer, and it is the connection layer

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.1/§6 (the class and the check) · **Touches:** SPEC-030 §1,
§2.1, §2.2, §4.2, §5.1, §6, §7, §10, `Notes`; SPEC-050 §6; SPEC-100 R21, `Notes`; SPEC-090 §6, §11

**The rule.** `fts5_unavailable` is raised by **`require_fts5(conn)`** of SPEC-030 §2.1, and by nothing
else. It answers `SELECT 1 FROM pragma_compile_options WHERE compile_options = 'ENABLE_FTS5'`
(`FTS5_ENABLED_SQL`) and raises `Fts5Unavailable(code='fts5_unavailable')` when the connection's SQLite was
built without the module: exit `6`, HTTP `500`, the internal class of D-014. It runs in exactly two
declared places — in `migrate()`, before the backup and before the first `BEGIN`, when
`pending_migrations()` is non-empty (the v1 schema creates the `issue_fts` virtual table, so a build
without FTS5 cannot apply it); and as the **first statement of `reindex(conn)`** (SPEC-100 R21), the one
command whose whole job is to write the index. It does **not** run in `connect()`.

**Why the code needed a producer at all.** SPEC-050 §6 and SPEC-100 R21 published `fts5_unavailable` and
sent the reader to "the migrator (SPEC-030 §2.3)" for the check; §2.3 of that document declared neither the
class nor the statement, and nothing in the corpus tested it. A published code with no producer is worse
than an unpublished one: the implementer either invents the check in the edge (where A7 says the edge maps
codes and does not define them) or never writes it and the exit `6` is dead. The producer now lives where
the fact lives — the adapter owns the connection, so the adapter is the layer that can ask the connection
what its SQLite was compiled with.

**Why not in `connect()`.** A database whose index cannot be read is still searchable: SPEC-090 R14 sends
that query to the `like` path with exit `0`, because the rows are in `issue` and a reader who gets their
issues is better served than a reader who gets an error. A check in `connect()` would refuse the
connection and take those rows away from every command, including the ones that never touch the index. The
two callers that *must* have the module ask for it explicitly.

**Why `pragma_compile_options` and not `PRAGMA compile_options`.** It is one `SELECT` over a table-valued
function (SQLite ≥ 3.16; the corpus already requires ≥ 3.35 for `RETURNING`), it composes with a `WHERE`
so the answer is a row or no row rather than a string a caller has to parse, and it works on a read-only
connection — which matters because `connect(readonly=True)` is how every read reaches the file.

**The alternatives rejected.** (1) *Raise it from the migrator only* — `db reindex` runs on a database that
is already migrated, so it would never meet the check and would fail with `no such module: fts5` from
inside its transaction, which is the unnamed error this decision removes. (2) *Let `translate_sqlite_error`
map `no such module: fts5`* — that mapping is for errors the driver raises on a statement the caller wrote;
here the caller writes no statement that can fail, and SPEC-090 R14 already claims `no such module: fts5`
as a *fallback trigger* of the search, so the same string would mean two things in two layers. (3) *A
`doctor` check* — `doctor` reports and repairs nothing (R20), and a build without FTS5 makes `doctor`'s own
C8 unanswerable.

**What it does not change.** The search's behaviour on a degraded build — `backend="like"`, exit `0`
(SPEC-090 §6, R14); the exit set of `db reindex`, `0/3/6` (R21); and `hifa db migrate`'s own exit codes,
which already had `6` for `SQLITE_TOO_OLD`. `not_a_hifa_database` stays where it was born, the `hifa init`
refusal of R14c.

**Verification.** SPEC-030 §7's two scenarios — the check passes on a build that has the module and raises
`Fts5Unavailable` with `code="fts5_unavailable"` on one that does not (the second stubs `FTS5_ENABLED_SQL`
with no row, which is the only way to test it on a build that has FTS5); REQ-F030-23; and
`grep -rn "fts5_unavailable" src/hifa` returning exactly one raiser.

## Amendments to D-063 and D-064 (review round, 2026-11-20)

Appended, not rewritten: the two decisions of this ticket are five hours old and were reviewed against the
corpus rather than against their own prose, so what follows corrects them in place and leaves their text
standing.

**D-063, `400` and the write guard.** The rule above puts "the cross-site write guard (R28)" among the
places `400` stays. It does not: R28 and SPEC-070 §6 answer **`403`** (`text/plain`, `cross-site write
refused`, `Cache-Control: no-store`), and the guard is not a `400` of that layer at all. The sentence is
wrong on a status code and nothing else; the argument around it — a missing `q` is a state, a malformed one
is a `422` — is untouched, and so is the list of places `400` does stay (the `/_/` family without
`HX-Request`, R5). SPEC-070 §2.1.1 and §2.1.8 carried the same slip and are corrected in the same commit.

**D-064, the parser takes one argument.** The rule above says `parse_query(raw)` is the public parser and
that it turns the raw string **plus the CLI flags** into the `SearchQuery` — and SPEC-090 §2.3 said the
same in one sentence. Both are corrected: **`parse_query(raw: str) -> ParsedQuery`** parses text and
nothing else, where `ParsedQuery` carries the free text, the filters the text itself carried, the
terms-derived values (`terms`, `match`, `match_title`, `like_terms`) and `is_empty`; the **edge** composes
the `SearchQuery` from that plus its own flags, which override the embedded value of the same field, plus
the `limit`/`offset`/`sort`/`count` only it owns. The twelve flags of `hifa search` never enter the parser,
and neither does `default_team`, which the previous revision declared: it let the parser read
`[cli].default_team` and stamp it into `SearchFilters.team`, so `parse_query` was not a function of its
argument, `app/` read configuration (A1/A3), and a workspace-wide search was silently narrowed to one team
that no flag named. The team comes from the edge that resolved it, as `--team`'s value does.

**D-064, the use case takes the instant.** `search_issues(db, query, *, now)` — `now` aware and in UTC —
is the signature, and `SearchService(reads).search(query, *, now)` passes it down. The rule above writes
the signature without it, and that signature could not compute a relative `due:` window: SPEC-090 R13 used
a `Clock` this module may not import (A2), and the adapter is forbidden to call `datetime.now`. The instant
comes from the edge's clock; SPEC-090 R13 derives `today = now.astimezone(zone).date()` where `zone` is the
**query's** timezone (the team's when the query names one, **UTC** otherwise, because the schema has no
workspace timezone and the default team is configuration).

**D-064, `Touches:` was incomplete.** The list above names SPEC-090, SPEC-050, SPEC-070 and `specs/INDEX.md`
and omits **SPEC-060**, which publishes the same hit as `SearchHit(issue: IssueOut, snippet, score)` in
§2.2, asserts its three keys in §2.6 and §7, and therefore needed a `spec_revision` bump and a §Notes
pointer in that round and got neither. SPEC-060 is added here and its revision 12 does the work; the lesson
is the one SPEC-030's rev 12 already wrote down about D-059 — a decision whose `Touches:` list is short by
one spec produces a corpus with two shapes for one payload.

## D-066 — `not_a_hifa_database` names the file, and the edge that refuses it names the exit

**Date:** 2026-11-20 · **Wins:** SPEC-100 R21/§6 (the detection, its two rows and the sequence of the
rebuild) · **Touches:** SPEC-100 §2.6, §2.7, §4.3 (R21), §6, §7, §11; SPEC-050 §6, §11; SPEC-090 §11,
§12; `specs/INDEX.md`

**The rule.** `hifa db reindex` reads the file before it writes to it: one
`SELECT name FROM sqlite_master` and the two tables a hifa database cannot lack — `workspace`, the marker
`hifa init` already uses (R14c), and `issue`, the table the rebuild indexes. A file that **opens** and is
**valid SQLite** but carries no both ⇒ `not_a_hifa_database`, exit `3`, nothing written. A file that
**SQLite itself refuses** (`file is not a database`, `database disk image is malformed`) ⇒
`corrupt_database`, exit `6`. The exit set of the verb does not move: `0, 3, 6`.

**Why the code had to come back.** SPEC-100's revision 9 removed `not_a_hifa_database` from R21 and sent
"another tool's SQLite file" to `corrupt_database`, on the argument that the corpus maps every unreadable
file to that class. The input does not behave that way: a valid foreign file opens, passes
`require_fts5(conn)`, has its virtual table created by the repair path and fails only at the rebuild, with
`no such table: main.issue` — which SPEC-030 §6 maps to `DB_NOT_MIGRATED`, a code that says the schema is
older than the binary. The published answer for a foreign file was therefore a code about a migration that
will never exist, for a file no migration can ever touch: not a code without a producer, worse — a producer
that answers something else.

**Why two exits for one code.** `hifa init` publishes `not_a_hifa_database` at exit `2` and keeps it: what
it refuses is the `--db` **argument** — the user named a path expecting it to be free, and `--force` is the
override — which is what `2` means in SPEC-050 §6. `db reindex` refuses something else: the **database** it
was told to operate on is not a hifa database, and that is what `3` carries for every command of the corpus
(`database_not_found`, `not_found`). One name for one condition, with each edge's exit saying what it had to
refuse. The alternative — a second code such as `not_a_hifa_database_for_reindex` — would have been a second
spelling of one fact, which is the class of defect this corpus keeps removing. `NotAHifaDatabase` is the
class of the `db` group (`code = "not_a_hifa_database"`, no `field`); `init` goes on raising the
`Validation` of R14c with the same lowercase code, and both surfaces are rows of SPEC-050 §6.

**What it also fixed.** The same round made the rebuild's statement sequence executable. The three triggers
of SPEC-090 §3.1 are `AFTER INSERT/DELETE/UPDATE ON issue`, so they belong to `issue` and not to the index:
`DROP TABLE issue_fts` takes the virtual table and its shadow tables and leaves all three in `sqlite_master`,
and the verbatim `CREATE TRIGGER issue_fts_ai` that revision 9 prescribed raises `trigger issue_fts_ai
already exists` inside the transaction. R21 prints the sequence it means — `CREATE VIRTUAL TABLE IF NOT
EXISTS`, `DROP TRIGGER IF EXISTS` for each of the three names, the three `CREATE TRIGGER`s,
`VALUES('rebuild')` — so every starting state (both present, table gone, triggers gone, both gone) ends at
exit `0` with the index rebuilt and the triggers back.

**The alternatives rejected.** (1) *Keep `corrupt_database` and correct SPEC-030's mapping so the rebuild
error becomes `corrupt_database`* — the mapping is right about a migration state and wrong only about this
input; rewriting it would make every `no such table` a corruption. (2) *Detect with `PRAGMA user_version`
instead of `sqlite_master`* — a foreign file has `user_version 0`, which is also what an unmigrated hifa
database has, so the check could not tell them apart. (3) *Check the columns of `issue` as well* — the two
table names are the declared verdict; a column check would be a second statement, and a second rule to keep
in sync with SPEC-010, for a file that has already answered the question this command asks. (4) *Leave the
detection to the migration path* — nothing in `migrate()` runs on a database whose `user_version` is
current, and a foreign file has no `user_version` at all, so the file would reach the rebuild with its
schema still unexamined.

**Verification.** SPEC-100 §7: the recreate scenario drops the table **and** the triggers and asserts exit
`0`, the triggers back and `backend="fts"`; the refusal scenario asserts exit `3` `not_a_hifa_database` for
a valid SQLite file carrying a `notes` table and exit `6` `corrupt_database` for a file with no SQLite
header. SPEC-050 §6 publishes the `NotAHifaDatabase` row and SPEC-090 §11 publishes the same set. The
sequence was executed against SQLite 3.53.1 from a database whose `issue_fts` had been dropped with the
three triggers left in place.

## Amendments to D-063, D-064 and D-065 (second review round, 2026-11-20)

Appended, not rewritten, for the reason the block above gives: these decisions were reviewed against the
corpus, not against their own prose.

**D-063, the listing's `q`.** The rule says what it does not change: "the board/list `q`, which is the title
filter of D-025". True, and unenforced. SPEC-060 §2.5.1 and §2.6 described `GET /api/v1/issues?q=` as
"FTS5 search delegated to SPEC-090", with `order` defaulting to a `relevance` that is not even a member of
that document's `OrderField`, and D-063's `Touches:` did not name SPEC-060 — so the one row of the corpus
that contradicted the decision survived the round that wrote it. SPEC-060 rev 13 now says the filter: no
`MATCH`, no `backend`, no ranking, no band and no effect on `order`. The lesson is the one D-064's third
amendment already wrote about the same document: a decision whose `Touches:` list is short by one spec
produces a corpus with two meanings for one parameter.

**D-063, the palette's call.** SPEC-070 R21 fixed the palette's composition — "both compose the
`SearchQuery` … call `search_issues(db, query, now=…)`" — while §2.1.9 of the same document said the palette
"is a separate ticket — this revision contracts the page alone". A spec may not exclude a route and
constrain its call in the same breath without saying so: R21 now states that the rule binds **every edge
that composes a query**, that this revision ships one of them, and that the palette is one when it ships;
§2.1.9 carries the matching sentence. Nothing of the palette's markup, its two phases or its fragment is
contracted here — the exclusion stands, and the inheritance of the call rule is named.

**D-064, the parser's layer.** The rule makes `parse_query(raw) -> ParsedQuery` the public parser of
`app/search.py`. SPEC-040 §4's table of "edge names that are NOT part of the port" went on filing it as a
"CLI/JS helper (paths and query string), they do not touch the database" for nine revisions — an
application-layer symbol described as edge code, which is the direction A3 forbids the edges to move in.
SPEC-040 rev 32 removes the row, states the owner (SPEC-090 §2.3) and why the function gets no §2.2 row of
its own, and corrects the older bullet of its Notes that had repeated the classification.

**D-065, the second carrier of the code.** The rule ends "`not_a_hifa_database` stays where it was born, the
`hifa init` refusal of R14c". It now has a second surface: `db reindex` answers the same code at exit `3`
for a file that opens and is not a hifa database (D-066). `fts5_unavailable` is untouched — one producer,
`require_fts5(conn)`, the same two call sites — and the exit set of `db reindex` is still `0, 3, 6`.

## Amendments to D-063, D-064, D-065 and D-066 (third review round, 2026-11-20)

Appended, not rewritten, for the reason the two blocks above give: these decisions were reviewed against
the corpus and against their own executable text, and two of the corrections below are statements the
reviewer ran.

**D-063, `q` and the order.** The amendment above says the listing's `q` has "no effect on `order`" and
names SPEC-060 §2.5.1 and §2.6 as the places that now say so. One sentence of the same document survived
the round: **R9**, which read "the default order is `score DESC, id ASC` and an explicit `order` with `q`
⇒ 422". The search endpoint declares no `order` at all — its parameter is `sort`, the five values of
SPEC-090 §2.5, and `relevance` is what `score DESC, id ASC` means — so R9 refused a parameter this
document does not publish, in the one place a client reads the refusal. R9 now states the rule the other
three sections state: `q` does not change the order, and no `order`/`sort` value is refused because `q` is
present. SPEC-060 rev 14.

**D-064, the port carries the text half.** The rule makes `search_issues(db, query, *, now)` the entry
point and `SearchService` its implementation, and it left the port's text arguments where revision 6 had
them: `reads.search(match=…, match_title=…, like_terms=…)`, with `like_terms` described as "the positive
terms". §5.3 (rev 7) then printed a form whose entire predicate is a negation, and the adapter's third
state was declared as "`like_terms == ()` **and no negated term**" — a condition that, inside the
adapter, is spelled `like_terms == ()` and nothing else, because nothing in its arguments could carry a
negated term. The no-positive form could not be called, and "no text at all" could not be told from "text
that is all negation". §2.4's `text` argument is now a `TextPredicate` with both tuples (D-067), and
§2.4's table decides the path from its four fields alone. `SearchService.search`'s signature does not
move: what it passes down does. SPEC-090 rev 8, `version` 3 → 4.

**D-064, `NOT` over a nullable column.** The `like` path is the decision's own contract — the fallback of
R14a/R14b — and its printed `NOT (title LIKE :n0 OR description LIKE :n0)` dropped rows: with
`description IS NULL` (legal, SPEC-010 §3.4) and a title that does not carry the term, the predicate is
`NULL`, `NOT NULL` is `NULL`, and the `WHERE` discards the row. Measured on a four-row fixture — `i1`
title "Deploy roto", `i2` "Bug del deploy" with a NULL description, `i3` "Otra cosa" with a NULL
description, `i4` "Otra mas" with "tiene bug dentro" — the revision-7 form of `hifa search -bug` returned
**1** row (`i1`) instead of the **2** it must return; with `COALESCE(description, '')` inside the `NOT`
block it returns `i1` and `i3`, the `description IS NULL` row included. R14 now states the rule once — "a
predicate under `NOT` treats a NULL column as empty" — and §5.3 carries it in both forms. The `fts` path
needs no such rule: there `NOT` is evaluated over the index, which stores a NULL column as an empty one.

**D-065, where `require_fts5` runs in `reindex`.** The rule puts the check "as the **first statement of
`reindex(conn)`**", i.e. on the writable connection R21 opened before it had identified the file. With the
probe now first (D-066, below), the check moved with it: `require_fts5(probe)` runs on the **read-only**
probe, which is what the rule's own last paragraph already claims is possible ("it works on a read-only
connection"), and the writable connection is opened only after the verdict. One producer, two call sites,
unchanged: `migrate()` and `reindex`.

**D-066, the two carriers, and the order of the probe.** The rule says `db reindex` "reads the file before
it writes to it". It did not read it before it **opened** it: revision 10 opened the database read-write
(which creates a missing path — 4096 bytes, empty schema, SPEC-030 §2.1) and then asked `sqlite_master`,
so `database_not_found` was unreachable and the command created the file it promised not to touch. R21 now
prints three ordered steps — (a) the path exists? no ⇒ `3` `database_not_found`, nothing opened; (b) a
**read-only** open, the `sqlite_master` verdict and `require_fts5` ⇒ `3` or `6`, nothing written; (c) only
then the writable connection and the transaction — and (a) and (b) leave the file byte-identical
(measured: a foreign file keeps its sha and its 8192 bytes and leaves no `-wal`/`-shm` behind). The probe
opens by URI mode and **not** with `connect(readonly=True)`: that function of SPEC-030 §2.1 applies the
whole `PRAGMAS` tuple and its first entry is `journal_mode=WAL`, a write to the header that answers
`attempt to write a readonly database` on a file that is not already in WAL — exit `4` on exactly the
input the step exists to name. The decision's **other** carrier was dead for a different reason: `hifa
init` ran its "no `workspace` table" probe **after** the migrator, and `001_init.sql` creates `workspace`
on any `user_version`-0 file, so a foreign file was migrated (measured: `workspace` and `issue` present
after applying the migration to a file with a `notes` table) and the probe could never fire. It is now
§5.2 step 5, before the migrator, on the same read-only open: refused at `2` with no table created and no
migration run, byte-identical, `--force` the override §6 already named. Both exits of the one code are
now reachable, which is what the decision claims and could not previously show. §7 pins both: the missing
path (`database_not_found`, no file created) and the foreign file (`not_a_hifa_database`, bytes unchanged).
SPEC-100 rev 11, `version` 4 → 5.

**One count, corrected in passing.** SPEC-100 §11's revision-10 note said "the six statements" and then
enumerated eight (`CREATE VIRTUAL TABLE`, 3 × `DROP TRIGGER`, 3 × `CREATE TRIGGER`, `VALUES('rebuild')`);
it now says eight between `BEGIN IMMEDIATE` and `COMMIT`, ten counting those two.

## D-067 — The adapter receives the text half as one predicate, with both of its tuples

**Date:** 2026-11-20 · **Wins:** SPEC-090 §2.2/§2.3 (the shape the service builds) and SPEC-090 §2.4 (what
the port receives) · **Touches:** SPEC-090 §2.2, §2.3, §2.4, §2.5, §4.1 (R4, R14), §5.1, §5.3, §8, §12;
`specs/INDEX.md`

**The rule.** The text half of a search crosses the port as **one** frozen `TextPredicate` —
`match: str | None`, `match_title: str | None`, `like_terms: tuple[str, ...]`,
`not_terms: tuple[str, ...]` — built by **`build_text_predicate(text)`** in `app/search.py` and passed as
`reads.search(conn, text=predicate, …)`. It replaces the three loose keywords (`match`, `match_title`,
`like_terms`) of the previous signature. The negated terms are an argument of the port; the adapter never
parses the text and never receives it.

**Why the negated terms had to become an argument.** Revision 7 printed a `like` form whose whole
predicate is a negation, and the port carried no way to ask for it: `like_terms` is "the positive terms",
and the third text state was declared as "`like_terms == ()` **and no negated term**". Inside the
adapter, "no negated term" has no representation — so `hifa search -bug` and `hifa search --state Done`
were the same three arguments, and every implementation had to choose which of the two answers to break.
An argument is the only place a decision can be made without re-deriving the query; the alternative —
letting `reads.py` call `sanitize_terms` — puts the parser of `app/` inside the adapter and gives the two
layers two copies of one rule.

**Why not a field on `SearchQuery`.** `SearchQuery.text` already carries the negation (`-bug` is text),
and `SearchQuery` is composed by each edge from the `ParsedQuery` plus its own flags. A `not_terms` field
there would be a second copy of one fact, filled by the edge — which means the edge would have to read
the terms, the one thing §2.3 forbids it to do for anything but its two local decisions, and a copy that
can disagree with the text it was parsed from. The direction is the other one: `SearchService` derives
both tuples from `query.text` in step 4 of §5.1 and the predicate is the only object that carries them.

**Why one object and not four keywords.** The path is a function of four values, and it is now decidable
by reading one argument's fields (§2.4's table): `match is not None` ⇒ `fts`; `match is None` with
`like_terms` ⇒ the first `like` form; `match is None` with `like_terms == ()` and `not_terms` non-empty ⇒
the second `like` form; both tuples empty ⇒ `backend="none"` and no text predicate. Four independent
keywords invite a caller to pass three of them, which is how the third state became unreachable in the
first place.

**The alternatives rejected.** (1) *Add `not_terms` next to `like_terms`* — it works, and it leaves the
three states spread over two booleans and a nullable string in five places (§2.4, §5.1, R4, R14, §5.3)
instead of one table; the object is what makes the table writable once. (2) *A `TextPredicate` with only
`not_terms` and `like_terms`* (no FTS expressions) — the adapter would then build `MATCH`, which is
`app/`'s job (R4, REQ-N090-05: `app/search.py` holds the grammar, `reads.py` holds the SQL). (3) *A
`backend` field the service computes* — the service cannot know the retry: when the `MATCH` raises one of
R14's four triggers the adapter answers `backend="like"` while the predicate still carries a `match`, and
a field would be a promise the retry breaks.

**What it does not change.** `SearchQuery`, `SearchFilters`, `ParsedQuery` and `SearchPage` (no field
moves); the `fts` path's SQL (§5.2); R14's four retry triggers and its ranking bands; the CLI flags, the
API parameters and the page's four states; and `search_issues(db, query, *, now)`, whose signature every
edge already calls (D-064).

**Verification.** SPEC-090 §2.4's table is the contract and §5.1 steps 4-5 name the four values; §8 step 4
asks for the split test (`deploy -bug -infra` ⇒ `like_terms=("deploy",)`, `not_terms=("bug","infra")`);
§7's purely-negative scenario asserts `backend="like"` with the three surviving issues; and the two SQL
blocks of §5.3 were executed against the four-row fixture of the D-064 amendment above — `hifa search -bug`
returns **2** rows with the new block and returned **1** with the old one, the row whose `description` is
`NULL` being the one that moved.

## D-068 — The probe is a symbol of the connection layer, and the flag counts rows

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.1/§4.1/§4.2 (the read-only open and the placement of
`require_fts5`), SPEC-100 R14/R21/§5.2 (the probe and the meaning of `already_initialized`), and D-065's
sentence that put the check "as the **first statement of `reindex(conn)`**" · **Touches:** SPEC-030 §2.1,
§4.1 (R1), §4.2 (REQ-F030-23, REQ-F030-24), §6, §7, `Notes`; SPEC-100 §2.5, §2.6, §4.2 (R14), §4.3 (R21),
§5.2, §6, §7, §11; SPEC-050 §2.5.1, §6, §11

**The rule.** `hifa db reindex` and `hifa init` identify a file on **one** symbol of the adapter:
**`open_probe(path) -> Probe`** in `connection.py`, where `Probe` is `(conn, tables)` and `tables` is what
**`PROBE_SQL`** — `SELECT name FROM sqlite_master WHERE type IN ('table','view')` — returned. It opens by
URI `mode=ro` with `uri=True`, sets `row_factory` and `isolation_level`, **applies no pragma of `PRAGMAS`**,
runs `PROBE_SQL` exactly once, raises `NotFound(resource='database')` for a path that does not exist
(nothing created, because `mode=ro` never creates) and `HifaError(code='DB_CORRUPT')` for bytes SQLite
refuses (`file is not a database`, `database disk image is malformed` — raised by the **read**, since
`sqlite3.connect()` is lazy), and answers `tables == frozenset()` for an empty file. The caller closes
`conn`. **`require_fts5` runs on that probe**, immediately after `open_probe()` returns and before any
writable statement exists; `reindex(conn)` declares it as a precondition and no longer claims it as its own
first statement. **`already_initialized` is `True` iff a `workspace` row existed when `init` read the
database** — missing file, empty file and migrated-but-unseeded all answer `False`.

**Why the probe had to become a symbol.** SPEC-100 R21 prescribed
`sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` inside `cli/commands/db.py`. Two independent rules
forbade it: `@REQ-F030-05` (no `sqlite3.connect` outside `connection.py` and `migrate.py`) and the port
itself, which published no read-only open without pragmas. A step that can only be implemented by breaking
a requirement of the layer it runs in is not a specification of that layer; it is a request for a symbol
that does not exist yet. Declaring the symbol is also what makes the probe **testable as one thing**: two
edges (`reindex` step (b), `init` step 5) with two independently written `sqlite_master` comprehensions is
the class of defect this corpus keeps removing.

**Why the probe applies no pragma.** It is the only read-only open of the corpus and it must work on a file
the verb has not identified yet. `connect(readonly=True)` cannot: it applies the whole `PRAGMAS` tuple (R1),
and the first entry, `journal_mode=WAL`, is a write to the file header. Measured on SQLite 3.53.1 — the
four inputs, with the tuple and without it:

| Input | `connect(readonly=True)` (the tuple) | `open_probe` (no pragma) |
|---|---|---|
| foreign file in `delete` mode | `attempt to write a readonly database` ⇒ `Conflict(DB_READONLY)`, exit `4` | `tables` read, file byte-identical |
| 0-byte file | `attempt to write a readonly database` | `tables == frozenset()`, still 0 bytes |
| bytes SQLite refuses | `DatabaseError: file is not a database` ⇒ `corrupt_database` | `corrupt_database`, from `PROBE_SQL` |
| seeded hifa database (WAL) | tuple applies — and identifies nothing | `tables` read, `workspace` and `issue` present |

The last row is the one that settles the argument. The previous revision said the tuple failed "only on a
file that is not already in WAL", which reads as a licence: our files are WAL, so use `connect`. It is
true and useless — whether the open succeeds is a function of **the file's journal mode**, exactly the fact
the probe was opened to establish, so a probe built on it answers exit `4` on a foreign file and something
else on a hifa file. A condition on an unread file is not an error to report, it is another reason to read
no pragma. (One case does fail on a WAL file at rest: inside a directory the process cannot write, a reader
cannot create the `-shm` it needs and answers the same `attempt to write a readonly database`. That is a
permission failure, not a mode, and the adapter's own directory is `0700`/`0600` — R16.)

**Why `reindex(conn)` keeps its connection and does not become `reindex(path)`.** The alternative shape is
real — the verb would own both opens, and the order would be one function's body — and it was rejected for
two reasons. `migrate(path)` takes a path because a migration is a thing done **to** a file; a rebuild is a
thing done **in** a transaction, and `db vacuum` (SPEC-090 R19) owes the rebuild on a connection it already
holds, so a path-shaped `reindex` would either reopen the file it is inside or force `vacuum` to duplicate
the sequence. And `not_a_hifa_database` is a verdict about the **argument** the verb was given, taken before
any write-capable handle exists: keeping it in the command, next to the `stat` of step (a), is what lets
R21 say "nothing is opened for writing until the file has been identified" and mean it. What the signature
needed was not a different shape but an honest precondition, and that is what it now has.

**Why the flag is a row and not a table.** "`workspace` and `team` exist" was underdetermined, and both
readings were wrong. The table reading makes **every** `init` report `true` — step 6's migrator creates the
table on any `user_version`-0 file, so "the table exists" is true of every database that reaches step 7 —
which contradicts R14(a)'s "created and seeded" and makes the flag carry no information at all. The row
reading says what a person means by "already initialized": there is a workspace **in** this database. It
also repairs the argument for step 5's position: under the table reading, a foreign file migrated first
would answer `true` (a lie); under the row reading it would answer `false` and be **seeded** on top of
another tool's tables (a worse lie), and the exit `2` of R14(c) would be unreachable, because
`001_init.sql` leaves a `workspace` table behind. The probe is before the migrator because the refusal must
be reachable, not because the flag would be wrong.

**What it does not change.** SPEC-090's search contract in every part (the `like` blocks, the four-row
state table, `TextPredicate`, the counts); `reindex(conn)`'s signature and the eight statements between
`BEGIN IMMEDIATE` and `COMMIT`; the exit sets (`db reindex` `0/3/6`, `init` `0/2/4`, `doctor` `0/1/2/3`);
`fts5_unavailable`'s single producer (D-065 stands, only its placement moves); `not_a_hifa_database`'s two
carriers and their two exits (D-066); and the `.feature` corpus, which stays at 17 files and 240 scenarios.

**Verification.** SPEC-030 §7's `@REQ-F030-24` scenario (four inputs, `PRAGMA foreign_keys` answering `0`
on the probe's connection, the sha256 of each existing file unchanged); SPEC-100 §7's new scenario for the
flag's four inputs; and the harness that runs the symbols the two specs now name against a missing path, an
empty file, a foreign file and a seeded database — the probe, `reindex` end to end on those four states and
`init`'s `already_initialized` — which is what this revision was measured with. One measured detail the
promise of R21 does not depend on but a test will meet: on a database **in WAL** the probe leaves the
database's bytes untouched and SQLite still creates the reader's `-shm` and `-wal` sidecars next to it; on
the files where the promise is made — a foreign file, an empty file, a file SQLite refuses — no sidecar
appears and the sha256 is the same after the probe as before it.

## D-069 — One condition, one class, one code — and the exit set the probe was measured to have

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.1/§4.1/§4.2/§6/§7 (the probe's three verdicts, `Probe`'s two
sets, the reachability sentence of revision 14), SPEC-100 §2.6/§2.7/§4.3 (R21)/§5.2/§6/§7 (the same
verdicts and the exit set), SPEC-050 §2.5.5/§2.6/§5.3/§6 (the handler and the exit-4 row), SPEC-090 §11
and §8 step 9 (the published set), D-068's sentence that `DB_READONLY` "is unreachable from" the probe and
its claim that "the exit set of the verb does not move", D-066's "`0, 3, 6` and it does not move", and the
absence of a definition for `DatabaseNotFound` and `CorruptDatabase` in the whole corpus · **Touches:**
SPEC-020 §2.1, §2.6, §4.2 (REQ-F020-14), §6, §7, `Notes`; SPEC-030 §2.1, §4.1 (R1), §4.2 (REQ-F030-24),
§6, §7, `Notes`; SPEC-050 §2.5.5, §2.6, §5.3, §6, `Notes`; SPEC-090 §8, §11, `Notes`; SPEC-100 §2.6,
§2.7, §4.3 (R21), §5.2, §6, §7, `Notes`

**The rule.** A condition the corpus names gets exactly **one class**, that class carries exactly **one
code**, and every table that publishes the condition names the class that raises it. Both edges of a
verdict publish that code verbatim — `error.code is "database_not_found"`, `error.code is
"corrupt_database"` — with no translation step between the exception and the envelope, so a reader cannot
meet two spellings of one fact depending on which document they opened.

**Why the classes had to be declared, not merely cited.** SPEC-000 §6, SPEC-010 §6, SPEC-050 §6 and
SPEC-100 §6 all published `DatabaseNotFound` → `database_not_found` → exit `3` → HTTP `503`, and
SPEC-000 §6, SPEC-010 §6, SPEC-050 §6 and SPEC-100 §6 published `CorruptDatabase` → `corrupt_database` →
exit `6` → HTTP `500`. No document declared either class. The adapter, which is the layer that raises
them, used two other names: `NotFound(resource='database')` — the *other* `3`, code `not_found`, HTTP
`404`, and a constructor call SPEC-020 §2.4 cannot build without `key=` — and `HifaError(code=
'DB_CORRUPT')`, an internal string with no row anywhere. SPEC-030 §7 and SPEC-100 §7 were therefore
jointly unsatisfiable: no implementation could answer both, because one of the two names did not exist and
the other was not the code the tests asserted. §2.6 of SPEC-020 now declares both, next to `NotFound`, and
§6 of the same spec gives every edge its mapping — which is the section SPEC-020 §6 already called "the
table `cli/errors.py` and `api/errors.py` **must** be exactly".

**Why the two codes are the wire spelling while the other five are `SCREAMING_SNAKE_CASE`.** §2.6 said
every `code` is `SCREAMING_SNAKE_CASE` and that the edge translates it (`NOT_FOUND` → `not_found`). That
stays true of `NotFound`, `Conflict`, `Validation`, `InvalidTransition` and the base. The two new classes
carry `database_not_found` and `corrupt_database` because four tables already published those spellings
and because a translation step is exactly where one condition acquires a second name; `NotAHifaDatabase`
(SPEC-100 §6) has carried its wire code the same way since D-066. The alternative — a `SCREAMING_SNAKE`
code plus a table row in each edge — was rejected: it reproduces the defect this decision removes, and it
makes `error.code` a function of the edge rather than of the exception.

**Why `DatabaseNotFound` is not a `NotFound` subclass.** It was the tempting shape — same bucket `3`, and
"the database was not found" reads like the domain's `NotFound`. But every edge's handler is ordered by
class (`except NotFound` first), and both would then publish `not_found` and a `404` for a missing file
whose published status is `503` and whose published code is `database_not_found`. A subclass would also
make `except NotFound` a silent re-badging of the condition, which is the failure mode of §5.3 itself. The
class is a sibling of `NotFound` under `HifaError`, with `key` (the path) and no `resource`/`field`: what
does not exist is the file, and the remedy is `hifa init`, not a corrected reference.

**Why the handler needed an arm and not a wider net.** SPEC-050 §5.3 printed four `except` arms, then
`KeyboardInterrupt` and `Exception`. Every `HifaError` subclass outside those four — the two new ones,
`Fts5Unavailable`, `NotAHifaDatabase`, `MissingWebExtra` and the five refusals of §6 — fell to the last arm
and answered exit `6` `internal_error`, which is precisely the answer that made SPEC-100 §7's
`error.code is "corrupt_database"` unimplementable as printed. §5.3 now has an arm that publishes the code
the exception carries and takes the exit §6's row declares; the four arms above it stay (they are the
classes that carry a `field`) and publish a carried code too, which is the rule §6's `Validation` row
already stated and which `not_a_hifa_database` at `2` and `DB_READONLY` at `4` require.

**Why the exit set is `0, 3, 4, 6` and not `0, 3, 6`.** SPEC-030 §2.1 and SPEC-100 R21 both said the
probe "applies no pragma, so `DB_READONLY` is unreachable from it". The measurement says otherwise for one
input, and it is the input a user can actually have: a database **already in WAL** — which every hifa
database is — inside a directory the process cannot write. The reader cannot create its `-shm`, so
`PROBE_SQL`'s read answers `attempt to write a readonly database` (measured, SQLite 3.53.1), which
SPEC-030 §6 has always mapped to `Conflict(code='DB_READONLY')` at exit `4`. The previous answer was exit
`6` `internal_error` by falling through the handler: a wrong cause for a condition the corpus already had a
row for. D-068 saw the case and dismissed it as "a permission failure, not a mode, and the adapter's own
directory is `0700`/`0600`" — true of the directory `init` creates, false of every file a user passes with
`--db`, which is the surface this verb exists for. The set was published in four places and now says
`0, 3, 4, 6` in all of them, with the row that produces the `4`: §2.7, §6 and §7 of SPEC-100, §2.5.5 and §6
of SPEC-050, §11 of SPEC-090.

**Why `Probe` carries two sets instead of one.** `PROBE_SQL` returned tables **and views**, and two
different questions were asked of that one set: "does this file carry a `workspace` **table**?" (R14c,
R21) and "does this file carry anything at all?" (R14a). Measured, a foreign file carrying `CREATE VIEW
workspace` answered yes to the first and passed `init`'s step 5, after which the migrator died with `view
workspace already exists` — an exit no table publishes. `PROBE_SQL` now returns `type` and `name`, still
**one** statement, and `Probe` carries its two projections: `tables` (`type = 'table'`) and `objects`
(`type IN ('table','view')`). The marker verdicts of both edges read `tables`; only the empty-file test
reads `objects`. Two sets are not a second statement, and the statement count the round certified does not
move.

**Why the close is `finally` and not a last line.** R21's step (b) and `init`'s step 5 printed
`probe.conn.close()` as their final statement, so every refusal taken *after* the probe returned —
`not_a_hifa_database`, `fts5_unavailable` — skipped it, while the same paragraphs declared "the caller
closes `conn`". Both flows are now `try/finally`; `open_probe` itself closes the connection it opened
before raising a refusal from the read, so no handle outlives a failed probe either.

**What it does not change.** SPEC-090's search contract in every part (the `like` blocks, the four-row
state table, `TextPredicate`, the counts); the WAL matrix of D-068's table (four inputs, with the tuple
and without it) — the fifth input is added here as a measurement, not as a revision of that matrix;
`reindex(conn)`'s signature and the eight statements between `BEGIN IMMEDIATE` and `COMMIT`; `db vacuum`'s
probe obligation (still declared for a verb that does not ship in v1, still nobody's work in this round);
D-066's recorded call shape for `not_a_hifa_database` (same code, same two exits, same two carriers); and
the `.feature` corpus, which stays at 17 files and 240 scenarios. No checkbox is ticked by this decision
and no `src/`, `tests/` or `scripts/` file moves.

**Verification.** SPEC-030 §7's `@REQ-F030-24` scenario (missing path ⇒ `DatabaseNotFound` with
`code "database_not_found"`; unreadable bytes ⇒ `CorruptDatabase` with `code "corrupt_database"`; the
migrated database's `tables` and `objects`; a `CREATE VIEW workspace` file in `objects` and not in
`tables`) and its new exit-`4` scenario; SPEC-100 §7's exit-`4` scenario and the view case added to the
foreign-file scenario; SPEC-050 §5.3's handler and §6's exit-4 row; and the probe run the round was
measured with — `open_probe` against a missing path, a file of garbage, a WAL database in a `0500`
directory and a file carrying `CREATE VIEW workspace`, with the connection count before and after each
call — which is what the two classes and the two sets are asserted against.

## D-070 — A name can be ambiguous inside one level, and no write may create one

**Date:** 2026-11-20 · **Wins:** SPEC-040 §4.1 R22 ("`LABEL_AMBIGUOUS` always means two teams and never
one team twice", "No ambiguity is possible, because the issue's team is the tie-breaker" and "This is the
only place `LABEL_AMBIGUOUS` can be raised"), R22's lead sentence that names **four** writers of the
`label` table, §4.2 REQ-F040-38 ("can never fire on `add_label`/`remove_label`"), §2.2's `label_detail`
bullet ("never ambiguous, because the issue's team is the tie-breaker"), the `raises:` lines of
`add_label`, `remove_label`, `create_label` and `create_issue` and `create_label`'s `NotFound(team|label)`,
§6's `LABEL_AMBIGUOUS` and `create_label` rows, its `UNIQUE_LABEL_NAME` row ("repeated
`(workspace_id, team_id, name)` on create or on rename") and the note under its matrix ("appears in two
cells and nowhere else … no issue-scoped reference and no list can raise it"), §5.6's "one row ⇒
`('label', <name>, NULL)` and no clock read", and §5.1 step 10's `get_by_name` resolution; SPEC-050
§2.5's label-`NAME` convention (which named two of the three paths that resolve one), §2.5.3's "the
issue's team is the tie-breaker, so no name is ever ambiguous here" and the exit lists of `issue label`
and `issue unlabel`, the `-l/--label` clause of the `issue create` row, §2.5.4's `UNIQUE_LABEL_NAME`
sentences and its `--parent` sentence, and §2.4.3's silence on the `replaced` of an invocation with
several names; SPEC-100 §4.2 R11, §3.1's `label` row and §5.2 step 8 (the exact `(workspace_id, NULL,
name)` lookup of the seed); and D-061(f)'s "two rows with one name in the **same** team cannot arise …
so the code always means two teams and never one team twice" together with D-061(h)'s count of "the six
sites" of `Validation(rule='LABEL_AMBIGUOUS', field='ref')` · **Touches:** SPEC-040 §2.2, §4.1 (R22),
§4.2 (REQ-F040-38), §5.1, §5.6, §6, §7, `Notes`; SPEC-050 §2.4.3, §2.5, §2.5.1, §2.5.3, §2.5.4, §7,
§11; SPEC-100 §3.1, §4.2 (R11bis), §5.2, §7, §11; `specs/INDEX.md`; this file

**The situation.** The label implementation is complete and its behaviour is right, and an adversarial
review of it found the corpus wrong or silent in six places. This decision is the corpus half: it does
not decide a behaviour the code does not have, except where §4 below says the delta is the directive —
and it is a `spec_revision` bump on three specs rather than an edit to the code, because `AGENTS.md §3`
says a spec is not edited so that the code fits it.

**1. A name that fits more than one row of one scope step is `LABEL_AMBIGUOUS`.** Every verb that
resolves a label **by name** reads its candidates in **one step** — the issue's team, the workspace, or
`create_label`'s own level for a `parent` — and a step that matches **more than one row** is
`Validation(rule='LABEL_AMBIGUOUS', field='ref', value=<the reference as typed>)`, never a silent pick.
The refusal names the **spellings** it found, `details={"labels": ["Bug", "bug"], "rule":
"LABEL_AMBIGUOUS", "field": "ref", "value": "bug"}`, in `name` ASC order; when the colliding rows sit in
**different levels** instead (two teams, or a team and the workspace, which only a catalogue reference
can see) the message and `details` keep the team keys (`details={"teams": [...]}`), and a set that holds
both shapes carries both keys. A **ULID** never reaches the rule: it names one row.

The set of verbs is `add_label`, `remove_label`, `create_issue` (the `-l/--label` path of
`issue create`), `create_label`'s `parent`, and the four catalogue references `update_label`,
`retire_label`, `restore_label` and `label_detail`. SPEC-040 §6 carries the cell in each of their rows and
says why `list_labels` (no label reference at all), `update_issue` (no label field) and every ULID-only
reference are outside it.

**Why the universal was false, and what it cost.** R22 said the code "always means two teams and never one
team twice", with the reason that `uq_label_team` and `uq_label_ws` "count retired rows too". They do — and
they compare **binary**, while every resolution of a name compares case-insensitively. `Bug` and `bug` are
two rows that both satisfy both indexes, so the premise of the universal was a property of the *index*
("one name in one team") applied to a *name* whose equality the corpus defines as casefolded. Measured on
a migrated database carrying that pair: `hifa issue label ENG-1 bug` attached **`Bug`**, the wrong row, and
answered as if nothing had happened; `hifa label show bug` answered `LABEL_AMBIGUOUS` while naming **one**
team, a message that described a condition the reader could not see. The schema cannot forbid what the
rule allowed, because fixing it there is the migration §2 of this decision rejects — so the refusal lives
where the guess was made.

**Why the refusal names the spellings and the levels separately.** They are two different questions.
Several spellings of one name inside one level is a **data defect** the user has to repair — one of the
two rows must be renamed — and the only way to repair it is to know which rows exist, which is what
`details.labels` is for. Several levels holding one name is a **choice** the caller can make: two teams'
`Bug` is exactly what a catalogue-scoped reference cannot decide and what its `--team`-less surface
already refused with the team keys. Collapsing the two into one message would have made the first
condition look like the second, and `hifa label list --json --all` — the documented way to a ULID — would
have been the only advice, for a case where a ULID is a workaround and not a fix.

**2. A second spelling is refused at the write, and the refusal is a rule of the use case.** The window
§1 describes is closed going forward: `create_label` refuses a `name` that a row of its **own level**
carries in another case, and `update_label` refuses a rename onto one, both as
`Conflict(rule='UNIQUE_LABEL_NAME', field='name')` — the code the exact repeat already answers with —
compared case-insensitively over the rows with the same `workspace_id` and the same `team_id`, **retired
rows included**, because the indexes count them and a spelling freed by a retire is not free. A rename
that changes only the row's **own** case (`Bug` → `BUG`) is allowed: the row is not a second spelling of
itself, and refusing it would make a cosmetic fix take two renames. A case variant at **another** level
is a second label and no conflict — a team `bug` beside a workspace `Bug` is the shape §1's resolution is
built on.

**Why not the index.** `name` carries no collation, so both partial unique indexes compare binary and
`Bug`/`bug` pass them; the rule as an index is `COLLATE NOCASE` inside `uq_label_team` and `uq_label_ws`,
and an applied migration is never rewritten (SPEC-010 R5.4; D-006: the checksum aborts it). That is a
schema migration plus a rebuild of every `label` table, for a refusal that one read of the level decides
in the transaction that already holds the row. The index keeps the exact repeat and stays the safety net;
the use case owns the spelling. The corpus pays for the choice in one place: `UNIQUE_LABEL_NAME` now has
two producers — the adapter's `DUPLICATE_LABEL` and the use case's own comparison — and SPEC-040 §6 says
so in the row rather than leaving a reader to infer that the code means the index fired.

**3. Every writer of the table obeys 2, and `init` is a writer of it.** `create_label`, `update_label`,
`add_label`'s `create_if_missing` (which cannot write a second spelling at all, because a name that
resolves at either level is used and nothing is created) and `hifa init`'s seed — plus `hifa import`,
whose own rule already resolves names casefolded (SPEC-080 R10). The seed was the one that did not:
`teams.bootstrap` looked its four names up **exactly**, so an `init` run after a legal rename (`Bug` →
`bug`) wrote `Bug` back beside `bug`, and the database the corpus promised could not exist was produced by
the command the corpus tells a user to run first. SPEC-100 R11bis is the rule: the step resolves
case-insensitively over `team_id IS NULL`, and a case variant is **skipped** — the row keeps its spelling,
the entry joins `skipped` under the seed's own string (`label:Bug`), and `created` stays empty on a second
run. **Why skipped and not refused:** a database whose labels are legal must not make `init` fail, the
exit set `0,2,4` of §2.7 has no row for it, and skipping is the same shape every other step of R11 has —
which is what keeps `init` idempotent.

**4. `issue create -l` resolves the way `issue label` does, and the corpus now says it in one place.**
SPEC-050 §2.5 already called a label name case-insensitive "resolved in the command's team and then in the
workspace", while the build resolved `-l/--label` with an exact lookup and `issue label` case-insensitively:
one name, two behaviours, and the two verbs disagreed about `-l BUG` on a team carrying `bug`. The three
paths that attach or detach a label by name — `issue create -l`, `issue label`, `issue unlabel` — now
share one rule (§2.5's conventions, stated once and referenced from the three rows): ULID first, else a
case-insensitive match in the **issue's team** and then in the workspace, and the refusal of §1 when the
level that answered holds two spellings. This is the one part of this decision the implementation PR
lands **after** this one: the spec is the directive, and the PR that follows makes `-l` obey it.

**5. The `replaced` of an invocation with several names is the last replacement performed.** `issue label`
is N `AddLabel` calls, one transaction each (R18/GAP-05), and each can replace a `singleSelect` sibling
while the envelope has room for one. `replaced` is the **last non-null** `AddLabelResult.replaced` of the
call — `issue label ENG-1 high low` on an issue wearing `Medium` of `Severity` performs two replacements
and answers `Low` — and `null` means no label of the call replaced anything, which with one name is
exactly "that label replaced nothing". The reason is the count: each label is its own transaction and the
printed `issue` is the row after the final write, so the envelope describes the state the caller is left
with and not the history of the call. The alternative — buffering the first replacement, or the union of
all of them under a list — was rejected: a list would break the one shape D-061 fixed for both label
verbs, and a first-wins rule would describe a state the issue is no longer in.

**6. §5.6's "no clock read" was unsatisfiable, and the sentence is restated.** `remove_label`'s detach
bullet said "one row ⇒ `('label', <name>, NULL)` and no clock read". A history row cannot be written
without a `now`: `HistoryRepo.record(…, *, now: datetime)` has no default (SPEC-030 §2.5, unchanged from
base), so a use case that records the detach *must* read the clock — and the implementation does. What the
sentence meant, and now says, is that the **DELETE stamps no timestamp**: `issue_label` is the pair and
nothing else (SPEC-010 §3), unlike `trash_issue`, which moves `updated_at` on the issue it parks. The
no-op guard's "no write, no clock" is a different sentence about a different path — it returns before the
clock — and it stands, because it is satisfiable.

**What it does not change.** No signature, no dataclass field, no command, no flag and no exit set of the
three edges: every number they publish is the one they published, and the only additions are refusals
inside existing buckets (`Validation` → exit 2 / `422`). The schema does not move: SPEC-010's two partial
unique indexes, their binary comparison and D-010 stand, and no migration is written. `LABEL_AMBIGUOUS`
keeps its class, its code, its exit and its HTTP status; `field='ref'` stays the field of every one of its
sites, `create_label`'s parent included, `parent` being the field the **group** refusals carry. The
catalogue resolution — whole workspace, `include_retired=True` — is unchanged, and so is `list_labels`'
display rule. `issue list --label` keeps the scope §2.5 gave it and is not decided here: it is a filter
over a set, not a reference, and no refusal is published for it (see the paragraph below). The
`.feature` corpus stays at 17 files and 240 scenarios, and no checkbox of any task moves.

**The one thing this decision leaves open, and says so.** `issue list --label` resolves a name in the
command's team and then in the workspace (SPEC-050 §2.5, D-061(f)) and, with two spellings present in that
team, has no published behaviour: it selects a set, and §1's refusal is written for a reference that
picks one row. No behaviour for it is stated in the corpus — §2.5.3.1 declares the scope and stops there
— and this delta does not invent one: a refusal on a filter would be a seventh change, and the six above
are the ones review measured. It is named here so that the next revision of the label surface finds it,
and it is not fixed silently in a docs-only commit.

**Verification.** SPEC-040 §7's four new scenarios (two spellings inside one team refused by `add_label`,
`remove_label` and `label_detail`; the case-variant refusal of `create_label` and of a rename, with the
row's own case allowed; a case variant at another level read as a second label; an ambiguous `parent`),
plus REQ-F040-38's count going to nine; SPEC-050 §7's four (the `-l` path resolving like `issue label`;
the three attach paths refusing; `label add` refusing a second spelling and `label update` changing its own
case; the two-name `replaced` reporting `Low`); SPEC-100 §7's one (a seeded `Bug` renamed to `bug`, then
`init --force` writing nothing). The measured inputs behind §1 are the migrated database with the
`Bug`/`bug` pair, on which `hifa issue label ENG-1 bug` attached `Bug` and `hifa label show bug` answered
`LABEL_AMBIGUOUS` naming one team; §4's two behaviours were measured on the same build (`-l` exact,
`issue label` insensitive); and §3's `init` wrote `Bug` beside `bug` after the rename.

## Amendments to D-070 (second review round, 2026-11-20)

Appended, not rewritten, for the reason the blocks above give: D-070 was reviewed against the label
implementation and three of its statements are ones the reviewer ran. The bullets below are normative;
where one contradicts the body above, this block wins.

**D-070 §5's answer is `High`, not `Low`, and the definition is one clause.** `replaced` names the
**sibling this call took off**, not the label it added. `issue label ENG-1 high low` on an issue wearing
`Medium` of `Severity` performs two replacements — `High` takes `Medium` off, then `Low` takes `High` off
— and the last non-null `AddLabelResult.replaced` is **`High`**: the label `low` displaced. The body
above says `Low`; that was wrong, and SPEC-050 §2.4.3 and its §7 scenario now answer `High` (measured:
`replaced = {"name": "High"}`, history `Medium → High → Low`). `Low` is what the issue carries, which is
what `issue` reports; it is not a replacement.

**D-070 §1's `details=` is the exception's accessor; the wire `details` is `null`.** The body above
writes `details={"labels": …}` and `details={"teams": …}` as if an envelope carried them, and §1's
sentence about what the `labels` key is for reads the same way. `details` has exactly two shapes on the
wire — `null` or a list of `{field, message}` (SPEC-050 §2.4.5, SPEC-060 §6.3) — and neither is a
mapping. The mapping is
the accessor SPEC-020 §2.6 declares (`exc.details`), and a `LABEL_AMBIGUOUS` envelope renders
`"details": null` while `message` carries the names: `label bug is ambiguous: 'Bug', 'bug'`, or
`label bug is ambiguous: it exists in ENG, OPS` when the rows span levels, or both clauses for a mixed
set. This is the split D-062 fixed for `field`, applied to the keys this refusal names; a message that
would be ambiguous without the mapping is a message to fix, not an envelope to widen. SPEC-040 R22 and
§6, SPEC-050 §2.5 and §2.5.4, and SPEC-060 §6.3 say it in those terms. The edge that forwards the
accessor as a dictionary today (`cli/errors.py::error_details_for`, and the API's equivalent) stops
forwarding `labels`/`teams` in the label-implementation PR's next commit; until then the delivered
`--json` envelope is the one this block calls wrong.

**`restore_label` is a writer of a live row, and §2 did not name it.** `restore_label` writes
`retired_at = NULL`, so restoring a retired `Bug` beside a live `bug` creates exactly the pair §1
refuses; §2's "every path that writes a `label` row" listed only the other writers. It now refuses
`Conflict(rule='UNIQUE_LABEL_NAME', field='name')` when the label's own level holds a **live** case
variant, with the cell in SPEC-040 §6 and the scenario in §7. The label-implementation PR
(`feat/labels-impl`, HIFA-14) lands this in its next commit, together with §4's `create_label.parent`
refusal — which is the one requirement of D-070 that is a directive and not a description: the build
this round measured still takes `next(...)` and exits 0 with `High` parented under `SEVERITY` when
`Severity` and `severity` both exist. SPEC-040 §7 marks those two scenarios as the target rather than
the measured build.

**`hifa import` is the sixth writer, and it did not behave.** §3 above says SPEC-080 R10 "already
behaves": R10 matched a name across `(workspace-wide) OR (target team)` and preferred the non-null
`team_id` — the silent pick §1 forbids, with `Bug`/`bug` at one level as the case it hides. R10 now
reads the target team and then the workspace one step at a time and rejects the row with its own
`ambiguous_label` verdict (§6.2) when a step matches twice; the team remains the tie-breaker **between
levels**. SPEC-080 rev 7. For the same reason SPEC-060 §2.2 C4's "the name is unique per team" is
corrected: identical spellings at one level are possible and refused, not unique and accepted; SPEC-060
rev 15.

**`LabelRepo.get_by_name` is an exact read, not a resolution.** §4's rule needs the level's **rows** to
count them; `get_by_name` compares `name = ?` and returns one row, so it can neither see two spellings
nor report them. SPEC-030 §2.4 now says it is for internal keys (a seed's or an import's own strings)
and that a user-supplied name goes through `list(..., include_retired=True)` + the `casefold` compare +
the refusal. No signature changes; SPEC-030 rev 16.

**`LABEL_NOT_FOUND` has two producers, not one.** §4's "assigns that code to this verb alone" and
SPEC-040 §6's "its only producer" contradicted §5.1 step 10, which gives the code to `create_issue`'s
`-l` path and was already in the matrix. `add_label` and `create_issue`'s `-l` path raise it; `unlabel`
answers `NotFound`. SPEC-040 rev 34.

**`Wins:` and `Touches:` were short by three specs.** Add SPEC-030 §2.4 (`get_by_name`), SPEC-060 §2.2
C4 and §6.3 (the label-name clause and the wire `details`) and SPEC-080 §4.2 R10, §6.2 and §7 (the
import verdict), plus the `INDEX.md` rows those revisions change. `specs/INDEX.md` still counts 70
decisions: this block resolves nothing new, it corrects a decision already numbered.

## Amendments to D-070 (third review round, 2026-11-20)

Appended, not rewritten, for the reason the blocks above give. These bullets are normative; where one
contradicts a block above it, this one wins, and where a block above declines a version bump this one
takes it. Nothing here reopens the delta's decisions and no behaviour moves: every bullet either
corrects a sentence so that it matches the code the earlier rounds measured, adds the row or the message
a code was missing, or records a bump those rounds declined.

**`replaced` is the last non-null replacement of the call, and a trailing no-op does not clear it.** The
second block's first headline — "`replaced` names the sibling the last write took off" — is right for
`issue label ENG-1 high low` and wrong for every call whose last name displaces nothing: for
`issue label ENG-1 high other` on `Medium`, the last write's own `replaced` is `null` while the call
performed a replacement. The rule is the sibling taken off by the last write **of the call that displaced
anything** — the last non-null `AddLabelResult.replaced` across the N calls of the invocation, each of
which is its own transaction (R18/GAP-05) — so the three instances are `high low` → `High`,
`high other` → `Medium`, and `null` only when no write of the call displaced anything. The body's §5
sentence ("the last non-null `AddLabelResult.replaced` of the call") and the code
(`_run_label_add`) already said this; the headline was the one thing that did not, and SPEC-050 §2.4.3,
§2.5 and §7 now say it in the one form, with a scenario pinning the trailing no-op.

**The `details` change is one function and two test files, and the API half of it is vacuous at this
revision.** The block above scoped the change to "stop forwarding `labels`/`teams`" and promised it for
"the API's equivalent". `Validation` and `Conflict` always merge `rule`, `field` and `value` into the
mapping (SPEC-020 §2.6), and `error_details_for` forwards the whole mapping, so the envelope stays a dict
after that commit for every refusal those two classes raise. The rule the delta publishes is a shape, not
a key list: `cli/errors.py::error_details_for` publishes `null` for a refusal whose accessor carries a
single detail and the list of `{field, message}` only when several are reported (SPEC-050 §2.4.5,
SPEC-040 §6). The two files that pin the old dict shape — `tests/e2e/cli/test_issue_create.py:149` and
`tests/e2e/cli/test_issue_relations.py:84,94,110` — are rewritten in the same commit. The API half is
**withdrawn as a promise**: `src/hifa/api/` holds `__init__.py` and no handler at this revision, so there
is no second conversion to change and no test to move; the contract binds the day the edge exists.

**`LabelRepo.get_by_name` has no caller, and the note that justified it named two that cannot exist.**
SPEC-030 §2.4 was rewritten to keep the method "for internal keys — an import's own values, a seed's own
string". `hifa import` has no implementation at all (`src/hifa/app/transfer.py` is absent, and
SPEC-080 §2.1.1 no longer consumes the method for the name it resolves), and the seed resolves its four
names case-insensitively through `case_variant_at_level` — measured on `feat/labels-impl` — so the method
has **no caller** in that tree. It is an exact one-row read (`name = ?`), it stays on the port for a caller
holding a **stored** key, and a user-supplied name must never reach it. SPEC-030 rev 17.

**`LABEL_NOT_FOUND`'s row was the one place the second block's claim was false.** It said §2.2, §6's note
and the matrix "all three now say the same thing"; the matrix and the note did, the row still read
"`add_label` only". The row now names both producers — `add_label` and `create_issue`'s `-l/--label` path
— and says that `unlabel` (`remove_label`) answers `NotFound` instead. SPEC-040 rev 35.

**The three bumps the earlier blocks declined are taken, and one more is taken for the same reason.**
SPEC-040 `version` 7 → **8**: §2.2's `restore_label` gains a refusal, §6's `UNIQUE_LABEL_NAME` row and
the `restore_label` matrix cell gain it and §7 a scenario, which is the ground rev 33 stood on for 6 → 7.
SPEC-050 `version` 9 → **10**:
`label archive --undo` can answer exit 4, an exit the command now produces, and the corpus holds no
precedent for a refusal inside an existing exit set being revision-only (rev 32 bumped for an exit-set
change; rev 33 bumped for prose with no exit added). SPEC-080 `version` 1 → **2**: `RowError.code` gains
`ambiguous_label`, a value a client branches on, which is SPEC-060's own ground for `version` 3 → 4.
SPEC-060 `spec_revision` 16 and SPEC-030 `spec_revision` 17 carry the two note corrections above.

**§4.1 of SPEC-080 gains the case the code table already had, and C4's state clause loses the premise its
label clause lost.** `ambiguous_label` had a §6.2 row, an R10 rule and a §7 scenario but no §4.1 row,
where its precedents `ambiguous_assignee` (B4) and `ambiguous_cycle` (B5) each carry theirs with the
message — B21 is that row, with the message contract `label BUG is ambiguous: 'Bug', 'bug'`. And C4's
**state** half justified accepting a name with "the name is unique per team" on a binary
`UNIQUE(team_id, name)`, the identical premise the label half was corrected for in the same row;
SPEC-060 rev 16 states the true reason (one level, read within the issue's team) instead.

**`Wins:` and `Touches:` grow by the clauses these bullets correct.** `Wins:` adds SPEC-030 §2.4's
caller claim (the seed's and the import's "own strings"), SPEC-060 §2.2 C4's **state** clause, and
SPEC-080 §2.1.1's `tx.labels` row (which consumed `get_by_name`) with R10's missing message.
`Touches:` adds SPEC-080 §2.1.1 and §4.1 and the `INDEX.md` rows of the five revisions above; the specs
themselves were already named by the second block. `specs/INDEX.md` still counts **70 decisions**: this
block resolves nothing new, it corrects a decision already numbered.

---

## D-071 — `issue show --with` is the detail read, and a payload with no producer is not a payload

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.6 (the owner of the set `include` gates: `comments`,
`attachments`, `history`, and "any other name" is `INCLUDE_INVALID`) and SQL-7 rows 8–12 (which
collections run always, which are gated, and in which order they come back); SPEC-040 §2.2's
`issue_detail(db, ref, *, include, include_trashed)` and its `DETAIL_INCLUDE` constant, §3.1 ("the only
use case that writes `attachment` is `add_attachment`, and only INSERT") and §6's `INCLUDE_INVALID` row;
SPEC-060 §2.2's `IssueDetail(IssueOut)` and `ParentRef`; SPEC-050 §2.5.3's `issue show` row and its
"`show --with relations` stays GAP-06" note · **Touches:** SPEC-050 §2.3, §2.3.1, §2.4.1, §2.4.2,
§2.5.3, §2.5.3.2 (new), §2.7, §7, §8, §11; SPEC-040 §2.2, §6, `Notes`;
`specs/acceptance/001-mvp-v1/07-hierarchy-relations.feature` (one prose comment); `specs/INDEX.md`;
this file

**The situation.** `hifa issue show HIFA-14` answers `No such command 'show'`: the verb is declared in
§2.5.3 and absent from the tree, and its `--with` payloads were parked in GAP-06 of §2.3.1 as "detail
reads beyond `issue_detail`". There are no such reads. `issue_detail` returns the children, the
relations, the comments, the history and the attachments of one issue, gates the three heavy ones with
`include`, and has been the panel's and the detail page's read since D-027/D-029. So the gap was not a
missing use case; it was an undeclared payload, and the corpus said twice that it would emit one "when
it lands" without ever saying what.

**1. The flag is a projection of one read, and the accepted names are the constant's.** `--with` maps
onto `issue_detail`'s `include`: `comments` and `history` enter it, `relations` and `children` do not
and must not — SQL-7 rows 8 and 9 run on every call, so those two are never gated, and a mapping that
passed them would raise `INCLUDE_INVALID` on a read the verb has to support. SPEC-040's `raises:` line
and §6 row said the five names did, which was false against that spec's own constant; both are corrected
(spec_revision 36) and SPEC-050 §2.5.3.2 declares the mapping value by value. No use case is added:
SPEC-040 §2.2, §3.1 and §6 do not move for the four values that land, which is the whole point of the
gap having been mis-filed.

**2. `attachments` is re-gapped as GAP-07, not shipped empty.** The read exists (SQL-7 row 12) and
nothing can write a row for it to read: `add_attachment` is declared in SPEC-040 §2.2 and implemented
nowhere, it is the only writer of `attachment`, and `issue attach` is unimplemented for the same reason.
The tempting shortcut — accept the value, always answer `[]` — declares a payload whose only possible
value is the empty one, and a caller cannot tell "this issue has no attachments" from "this version has
no attachments". The flag therefore refuses the value with exit **2** `invalid_option`, `field: "with"`,
in this edge's own envelope, and §2.3.1 carries GAP-07 in its one-row format for the revision that lands
`AddAttachment`. A refusal that names the gap is a contract; an always-empty list is a lie with a shape.

**3. The envelope is SPEC-060's `IssueDetail`, flat, with all five collection keys.** The collections are
appended after `updated_at` on the 33 keys of `Issue` — `comments`, `relations`, `attachments`, `history`,
`children` — never nested under an `issue` member and never omitted when unrequested. That is R2's own
sentence ("the `--with` variants append at the end, never reorder") made concrete, and it is what keeps
one read model's two edges one shape: a client that reads `GET /api/v1/issues/{ref}` and one that reads
`hifa issue show REF --json` parse the same object. The cost is stated rather than hidden: `[]` means
"none" **or** "not asked for", and no key distinguishes them, which is exactly the property SPEC-060's
`IssueDetail(IssueOut)` already has with its five defaulted lists. The alternative — a nested
`{issue: …, comments: …}` envelope that only ever prints the requested keys — would have made the CLI
and the API two shapes for one read, and would have re-introduced the branch "one shape, one source, no
branch" that §2.5.3 promised away.

**4. `--with` is a set, and the order is the object's, not the flags'.** Repeating a value is one
collection (de-duplicated before the read, the convention `issue label` applies to a repeated name), and
the sections render in the fixed order of §2.4.2 whatever order the flags were typed in, so two
invocations with the same set print the same bytes (REQ-N050-05). A value outside the five is exit 2
`invalid_option` with `field: "with"` — the bucket `issue relate` already uses for a `TYPE` outside its
five tokens — and it is refused by the command rather than by the parser, so `--json` prints this edge's
one error document instead of a usage line.

**5. The table grows sections, and the block it grows them from was off by one.** Each requested
collection with rows prints after the key-value block as a titled section with the columns of the verb
that owns it (`COMMENTS` drops `ISSUE`, because the block's header is that column repeated once;
`RELATIONS` is `issue relations`' four columns; `HISTORY` is `WHEN  ACTOR  FIELD  FROM  TO`; `CHILDREN`
is `IDENTIFIER  TITLE`, the three keys of the element minus the ULID). An empty collection prints no
section and one `0 <collection>` on `stderr`, the convention `issue list`'s `0 issues` and `issue
relations`' `0 relations` already use. Two drifts died here: §2.4.1 declared the key field of the block
as **9** columns while its own sample printed 10 — arithmetically impossible with `Priority` and
`Assignee` at 8 cells and the two-space separator — and the §2.7 sample printed 7 of the 11 declared
keys with no `…` to say so. `HISTORY`'s `FROM`/`TO` print the **stored** strings, so a `state_id` change
shows two ULIDs; rendering them as names is the timeline's rule (SPEC-070 §2.3.4) whose CLI half is
**HIFA-46**, and this decision neither fixes nor pre-empts it.

**What this does not do.** It does not add a verb, a flag or a use case to SPEC-040; it does not touch
the label surface (D-070) or the search surface (D-063/D-064/D-067); it does not change `issue
relations`, `issue list --parent` or `issue_detail`'s read (SPEC-030's SQL-7 and its totals do not
move); and it declares no attachment payload. `issue show` without `--with` keeps the eleven-key block
of §2.4.1 and the 33-key `Issue` of §2.4.2 exactly as declared — the verb was missing, not the
contract, so the only change to it is the `include` argument of the call it makes.

**What it supersedes in the record.** D-046's entry says twice that the hierarchy is read by `issue list
--parent` and `issue show --with children` "(§2.5.3, GAP-06)" and that "`--with relations` is GAP-06 and
the base `Issue` object is closed". Those sentences described the corpus of 2026-11-13 and are
**superseded here, not edited**: this file is append-only, and the record of what was true then is what
makes the drift visible. What is true now is §2.5.3.2: `--with children` and `--with relations` are
contracted, and the base `Issue` is still closed — the collections ride on `IssueDetail`, which is a
different object, emitted only when a `--with` asks for it.

---

## Amendment to D-071 (second review round, 2026-11-20)

Appended, not rewritten, for the reason the block above gives: D-071 was reviewed against the corpus and
not against its own prose, and three of its four rules survived review with a hole in the sentence that
stated them.

**D-071 §2, applied to the envelope it shipped in.** §2 refused a payload whose only possible value is
the empty one, and then §3 assembled `IssueDetail` with `attachments` in it and required that key to be
`[]` on **every** object, because `add_attachment` is declared in SPEC-040 §2.2 and implemented nowhere.
The rule was right and its own envelope contradicted it: a caller could not tell "this issue has no
attachments" from "this version has no attachments", which is precisely the property §2 called a lie
with a shape. The key is gone from v1's `IssueDetail` on both edges (SPEC-050 §2.4.2, SPEC-060 §2.4.2),
the object is **33 + 4 = 37 keys**, the `Attachment` element and `issue attach`'s payload cell are gone
with it, and the collection and its element come back — with the `include` name at both edges — in the
revision that lands `AddAttachment`. A decision that forbids an always-empty list must not publish one, and the second
review round is where that was found: the corpus is the place a rule is checked, and the rule's own
`Touches:` list was the place it was not.

**D-071 §4, the bucket that was cited backwards.** §4 justified `invalid_option` for a `--with` value
outside the five as "the same bucket `issue relate` uses for a `TYPE` outside its five tokens". It is
not: `relate_issues` refuses that token with `Validation(COMMAND_FIELD)` (SPEC-040 §6, §2.2), raised in
the use case's `__post_init__`, and `COMMAND_FIELD` is the corpus's code for a command field that does
not convert — the opposite of an edge-level `invalid_option`. The refusal stands, on its own ground: it
happens in the command before any use case is called, so no `Validation` of SPEC-040 applies, and
`invalid_option` is the code §6's row already carries for the tokens this edge refuses itself. A
precedent is a claim about another document; §4's was a claim about a code that document does not use.

**D-071 §1 and §2, the include set of the third edge.** §1 said `--with`'s mapping is "the
`DETAIL_INCLUDE` constant and nothing else", while §2 removed the only value of that constant the CLI
could not honour. The constant still names `attachments` — SPEC-030 §2.6 and SPEC-040 §2.2 are right to,
the read gates it (SQL-7 row 12) — and neither v1 edge passes the name, because neither envelope carries
a key for it: the CLI refuses it at exit 2, and SPEC-060 §2.5.1's route publishes `{comments, history}`
and answers 422 for the rest. What §1 should have said, and what both edges now say, is that the flag's
vocabulary and the read's constant are two sets, and the smaller one is the envelope's.

## Amendment to D-071 (third review round, 2026-11-20)

Appended, not rewritten, for the reason the block above gives. The second amendment corrected §1, §2 and §4
and left §3 standing: the rule it states is right, and the envelope it describes is not the one either edge
publishes.

**D-071 §3 is dead as written: the envelope has four collection keys, not five.** §3 said the collections
are `comments`, `relations`, `attachments`, `history`, `children` — "flat, with all five collection keys" —
and that no key distinguishes "none" from "not asked for", "which is exactly the property SPEC-060's
`IssueDetail(IssueOut)` already has with its **five defaulted lists**". Three clauses, and the third is the
one that survives: `attachments` is gone from both edges for the reason §2 gives (SPEC-050 §2.4.2,
SPEC-060 §2.4.2 — **33 + 4 = 37 keys**), because `add_attachment` is implemented nowhere and the key could
only ever be `[]`. "Five defaulted lists" is a count of the object §3 was assembling, and the object every
edge publishes today has **four**. What §3 was defending — a collection that was not asked for and one with
no rows are both `[]`, and no key tells them apart — is intact and unchanged, of four lists instead of five.

The fifth key returns with `AddAttachment`, beside the `include` name that gates SQL-7 row 12 and the
subresource whose rows were never writable; §3's `Touches:` list, not its `Wins:` list, is what the corpus
follows until then. A decision that refuses a payload with no producer must not describe its own envelope
as carrying one, and the sentence in §3 is **superseded here, not edited**: this file is append-only, and
the record of what was claimed is what makes the correction legible.

**What this does not change.** §1 (the flag is a projection of one read, and `relations`/`children` are not
`include` names), §2 (a list whose only possible value is `[]` is a lie with a shape), §4 (`--with` is a
set, the order is the object's, and the refusal is `invalid_option` on this edge's own ground) and §5 (the
sections of the table and the two drifts it killed) stand as written, with the second amendment of the same
date. Nothing else of D-071 moves.

## D-072 — An order, a visibility and a key are produced where the payload is assembled, and each one names its producer

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.6 (SQL-4's projection, the `LEFT JOIN project_milestone`
in it, SQL-7 rows 1, 8 and 9, and the 12/8 totals, which neither the join nor the two predicates move),
§2.6's
`IssueListItem`; SPEC-040 §2.2 (`issue_detail`: the order of `relations`, the visibility of `children`
and `relations`) · **Touches:** SPEC-030 §2.5, §2.6, `Notes`; SPEC-040 §2.2, `Notes`; SPEC-050 §2.4.1,
§2.4.2, §2.5.3, §2.5.3.2, §7, §8, §11; SPEC-060 §2.4.2, §2.5.1, §7, §8, §11; `specs/INDEX.md`; this file

**The situation.** Three published facts of one read had no producer. `relations` was declared in a total
order (`READS AS` rank, then the far end's `identifier`) that no statement produced: SQL-7 row 9 selects
from `issue_relation` on both ends and carries no `ORDER BY`, so `issue relations` printed the fetch
order and the §7 scenario passed because its fixture was inserted in the order the prose predicted. The
children and the far ends came back with no `trashed` predicate, so a verb that hides a trashed issue as
`REF` listed a trashed child and a trashed neighbour. And `milestone` — the 33rd key of `Issue` — was
publishable and unloadable: `issue.milestone_id` is writable, no statement read `project_milestone`, and
the key could only ever be `null`. The three are one defect a layer apart: each was a value a document
published and a layer that could not compute it.

**1. A published order names its producer, and the producer is the layer that holds the fact.** The
order of `relations` cannot come from SQL-7 row 9 as the read is declared: the statement returns rows
from both ends (`issue_id = ? OR related_issue_id = ?`) and **which name a row reads as** is a function
of the asking issue — `RelationType.name_as_seen`, SPEC-020 §2.1 — so the sort key needs the row *and*
its far end, which is the pair the use case already holds and the statement does not. `issue_detail`
therefore sorts the tuple in Python, on `(rank(name_as_seen(direction)), far_end.identifier)`, and
returns the ordered tuple; SPEC-030's statements do not grow an `ORDER BY` and the two totals do not
move. Every caller inherits the order because there is one call: `issue relations` reads through
`issue_detail`, `issue show --with relations` reads through the same call, and neither sorts again — one
order, one producer, no branch. The alternative that was rejected is the tempting one: an `ORDER BY` on
row 9 would have to sort by a column that does not exist in the row (`blocked_by` is a reading, not a
stored type) and would have moved the order into a layer that cannot see the direction.

**2. The trash is a visibility of the row, and a collection does not inherit the reader's.**
`include_trashed` is a keyword about the issue the read returns (D-059); it says nothing about the
neighbours a collection carries. The corpus nevertheless had two answers for one level of the hierarchy:
`issue list --parent` hid a trashed child through SQL-4's `i.trashed = 0`, and `issue show --with
children` listed it, because the children statement bound no predicate. SQL-7 rows 8 and 9 now carry
`AND i.trashed = 0` **unconditionally** — a trashed child is absent, and a trashed far end is absent even
on the one read that may see a trashed `ref` — so the two readers of one level list the same set. The
header's predicate is untouched, the keyword keeps its single meaning, and no count moves: a predicate is
not a statement. `comments` and `history` need no such rule and get none: neither table has a `trashed`
column, so there is no visibility to declare.

**3. A key with no loader is the defect a list with no writer is, one field wide.** D-071 §2 refused a
list whose only possible value is `[]`; a `{id, name}` that can only be `null` is the same refusal with
a different sign. `milestone` is now loaded where its siblings already are: SQL-4's projection — the one
SQL-7 row 1 reuses — selects `i.milestone_id` and `pm.name AS milestone_name` from `LEFT JOIN
project_milestone pm ON pm.id = i.milestone_id`, and `IssueListItem` carries both, appended with
defaults. No statement is added, so §2.5's **12**/**8** totals and R5 do not move, and the listing
carries the same two columns because it runs the same projection: one projection, both reads. What this
does **not** do is contract a milestone surface — no verb, no route, no `milestone` filter; `issue edit
--milestone` writes the column it always wrote, and this revision only lets a read say what it wrote.

**What this does not do.** It adds no use case, verb, flag, route, exit code or HTTP status; it does not
touch the label surface (D-070) or the search surface (D-063/D-064/D-067); it does not add an `ORDER BY`
to any statement of SPEC-030, so the "no `ORDER BY` assembled from user input" rule of §2.6.1 is not in
question; and it does not move the two statement totals of SQL-7. It does **demand code**, listed in the
three specs' notes and not written for this ticket: the sort in `src/hifa/app/boards.py` (here,
because the `(row, far end)` pair is here), the join, the two columns and the two `AND i.trashed = 0`
predicates in `src/hifa/adapters/sqlite/reads.py`, and `milestone` in the CLI's `Issue` payload
builders. Until that lands the spec is ahead of the tree, which is the state a spec delta is allowed to
be in and the state it must say out loud.

**What it supersedes in the record.** SPEC-050 §2.5.3's sentence "Rows are ordered by that name … and
then by `IDENTIFIER` ASC" was written before this decision and had no producer for three revisions: it is
not superseded but **backed**, and its citation of SQL-7 row 9 as the producer of that order is corrected
to the use case.
SPEC-050's rev 36 note, which counted "two drifts" in §2.4.1, is corrected there: a third stood one row
away in `label show`. Nothing else of D-046's or D-060's record is touched: the relation is still the row
as stored, the thread is still flat.

---

## Amendment to D-072 (2026-11-20)

Appended, not rewritten, for the same reason as the blocks above: two of D-072's three rules stated a fact
about a neighbour that the corpus cannot support, and the third named a write path that does not exist.

**D-072 §2 — "the two readers of one level list the same set" is false, and the parent pointer was the
third row that needed the predicate.** Two separate defects were folded into one sentence. First, the
equality: `issue list --parent` applies its default `--open` (`is_open = 1`, SPEC-050 §2.5.3) on top of the
trash predicate, and `issue_detail` has no open axis at all, so a **closed** child is listed by the listing
and absent from `children` — the two readers do not list the same set and never did. What they share is the
**trash visibility**, which is all SPEC-030 §2.6 now claims: a trashed child is absent from `children`.
Second, SQL-7 row 7 — the pointer that resolves `parent_id` — carried no `i.trashed = 0` while rows 8 and 9
did, so a trashed parent was *shown* by the object that hid the same row one key below. Row 7 now carries
the same predicate and answers `null`; the rule is stated **once**, in SPEC-030 §2.6, and SPEC-040 §2.2 and
SPEC-050 §2.4.2/§2.5.3.2 point at it instead of restating it.

**D-072 §3 — `milestone_id` is not writable, so the key is `null` on every object in v1.** §3 said
`issue.milestone_id` "is writable (SPEC-040 §5.1)" and that the key "could only ever have been `null`"
before the join gave it a loader. The join is real and stands — SQL-4's projection selects `i.milestone_id`
and `pm.name AS milestone_name`, `IssueListItem` carries both, no statement is added and the 12/8 totals do
not move. What does not exist is the write: `src/hifa/app/issues.py` raises `HifaError(NOT_IMPLEMENTED)` for
**any non-null** `project_id`/`milestone_id`/`cycle_id`, and clearing them to `NULL` is the only accepted
form. A loader is not a producer. In v1 `milestone` is therefore `null` on every object a read can produce,
the key becomes real the day the projects write path lands, and until then SPEC-050 §2.3.1 carries
**GAP-08** for that path in the table's one-row format.

**What this does not change.** §1 stands as written: the order of `relations` is produced by `issue_detail`
in Python on `(rank(name_as_seen(direction)), far_end.identifier)`, every caller inherits it, and no
statement of SPEC-030 grows an `ORDER BY`. Neither correction adds or removes a statement, so the 12/8
totals, R5 and the §7 counts of SPEC-030 hold. Nothing else of D-072 moves.

---

## D-073 — A normative query's cost is measured, not asserted, and the plan it needs is part of the contract

**Date:** 2026-11-20 · **Wins:** SPEC-090 §5.2 (the two `AS MATERIALIZED` keywords on the two CTE names,
and the note that replaces the false cost claim), R17/REQ-N090-02 (the plan names the materialization, not
only the absence of a scan), §7 (one scenario that pins both `MATERIALIZE` rows) · **Touches:** SPEC-090
§4.1, §4.2, §5.2, §7, §8, §12; `specs/INDEX.md`; this file

**The situation.** SPEC-090 §5.2 prints the **normative** reference query of the `fts` path, and implemented
verbatim it misses R16's own budget by an order of magnitude: on the 10 000-issue fixture that R16 names,
three terms, `limit=50`, `count=False`, the printed block measures **88.7 ms p50 / 94.5 ms p95** against a
budget of **< 50 ms p50 / < 120 ms p95**. The same query with `AS MATERIALIZED` on both CTEs measures
**8.8 / 9.1**, and the same form with a `team=ENG` filter added **9.5 / 10.0**. The materialized form is
the one that was measured to fit, and the document was left untouched: what §5.2 published is a query
nobody had run against the fixture it publishes next to. §5.2's second note made it worse rather than
better: it said the `title_hit` CTE "doubles the cost of the `MATCH`" and that "the measured cost is ~1.6×
that of a single `MATCH`, within the R16 budget" — a true statement about the materialized form and a false
one about the query in the block above it.

**The measurement, and what it eliminated.** Flat into the join, SQLite makes `issue_fts` the inner loop and
probes the virtual table **once per row of `issue`**, 10 000 times, twice — once per CTE:

```
revision 9 (flat):  SCAN i
                    SCAN issue_fts VIRTUAL TABLE INDEX 0:=M3 LEFT-JOIN   ← correlated
                    SCAN issue_fts VIRTUAL TABLE INDEX 0:=M3 LEFT-JOIN   ← the second CTE
materialized:       MATERIALIZE fts
                    SCAN issue_fts VIRTUAL TABLE INDEX 0:M3               ← once, uncorrelated
                    MATERIALIZE title_hit
                    SCAN issue_fts VIRTUAL TABLE INDEX 0:M3
```

Measured by elimination on the same fixture: `MATCH` alone **0.03 ms**, `+ bm25` **0.06 ms**, `+ snippet`
over the 25 matches **0.28 ms**, the matches joined to `issue` **0.35 ms**, the printed query **68-89 ms**
across runs (88.7 ms p50 in the run above). So the cost is the **plan** — not the row count, not the
snippet, not a second `MATCH` and not a second `count` query. A second `MATCH` run once over a
materialized CTE costs 0.03-0.06 ms; repeating it once per row of `issue` costs the budget.

**1. A normative query's cost is measured, not asserted, and the fixture that measures it is the one the
budget names.** R16 publishes "10 000 issues … < 50 ms p50". A sentence that publishes a cost next to that
budget is a claim about the output of running *that* query against *that* fixture, and the only way it is
true is that somebody ran it. The defect §5.2 carried for three revisions was not "the query is slow" — a
slow query that says it is slow is a budget problem, and R16 was there to catch it — it was that the
document *asserted a number* derived from a different form of the query and published it as the cost of the
one in the block. An implementer reading §5.2 has no way to tell which form the 1.6× belongs to, and the
honest reading of "within the R16 budget" printed under a query that is 88.7 ms is that the budget is not
real. The revision, therefore, prints both numbers, the fixture, the terms and the plan lines, and it
states that the keywords are what separate the two rows.

**2. The plan a normative query needs is part of its contract, and "no `SCAN issue`" is not a plan
assertion.** R17 forbade `SCAN issue` and R17 was satisfied by a query that misses R16 by 10×: **with a
team filter** the flat plan is `SEARCH i USING INDEX idx_issue_open` — `issue` resolved by an index, the
`SCAN issue` half green — while the virtual table is still probed once per surviving row and the query
still costs 68-89 ms. A plan check that only names the table it does not want the engine to scan cannot
distinguish "the search is bounded by the `MATCH`" from "the search is bounded by `issue` and the `MATCH`
is re-run for every row": the correlated half of the flat plan is the `=M3 LEFT-JOIN` probes, not the scan.
R17 now requires **`MATERIALIZE fts` and `MATERIALIZE title_hit`** in the plan in addition to the absence of
`SCAN issue` where the published query holds it — `team` and `team`+`state`, not `state` alone (round
three; item 4 below, HIFA-82) — and §7's scenario asserts exactly those two strings, captured from a real
plan on SQLite 3.53.1 over a 10 000-issue fixture. Neither string appears in the flat plan, so the scenario fails on the
form revision 9 published.

**3. The keyword is a contract, not an optimisation.** §5.2 is the query an implementation copies, so a
keyword the block omits is a keyword the implementation does not write; that is exactly what happened for
three revisions, and the note that was supposed to justify the second CTE's cost is what made the omission
look safe. `AS MATERIALIZED` on both CTEs is normative text with a requirement (R17), a scenario (§7) and a
checklist step (§8 step 10) behind it; an implementation that drops it ships a search an order of magnitude
over R16 with every other scenario of §7 green. It adds **no new floor** either: `AS MATERIALIZED` is SQLite
3.35.0, the same release as the `RETURNING` the adapter already refuses to run below (SPEC-030 §2.5's
SQL-2 note, whose `sqlite3.sqlite_version_info >= (3, 35, 0)` check lives in `connect()`), so a build that
can apply this schema can run this query.

**What it supersedes in the record.** One sentence, and only in its narrow sense: D-067's "what it does not
change" lists "the `fts` path's SQL (§5.2)" among the things its own delta left alone. D-067's decision —
the text half crosses the port as one `TextPredicate` carrying both tuples — stands untouched, and so does
the SQL's shape; what D-067 recorded as unchanged is now changed here, in the two CTE names only.
D-063 (a page with no query is a state), D-064 (`search_issues` is the entry point, the hit is flat),
D-065 (`fts5_unavailable` has one producer), D-066 (the `4` of the probe's exit set), D-068 (the probe is a
symbol of the connection layer) and D-069 (one condition, one class, one code) are not in question and are
not contradicted: none of them says anything about the plan of §5.2 or about its cost, and R16's budget,
R17's `SCAN issue` half, the envelope, the flags and the four `like` forms are unchanged.

**What this does not change.** §2 does not move — no signature, no dataclass, no field, no flag, no
envelope — so `version` stays **4** and only `spec_revision` moves (9 → 10); §5.2's query is otherwise
**byte for byte** what revision 9 published (no column, alias, predicate, binding or order). R16's numbers
are not re-baselined: the budget was right and the query was wrong, which is why the fix is in the query and
the budget keeps its 50/120 ms. §5.3, §6, §11 and the `.feature` corpus do not move: 17 files, **240**
scenarios (228 `Scenario` + 12 `Scenario Outline`), and `10-search.feature` carries no plan and no
performance scenario for this one to mirror. No code, no test and no task box is touched by this decision:
§5.2 is the text an implementation copies, so correcting the text is the whole of the fix. **The tree this
decision lands on already ships that form**: `src/hifa/adapters/sqlite/reads.py` builds §5.2 as `_FTS_CTE`
with `WITH fts AS MATERIALIZED (…)` and `title_hit AS MATERIALIZED (…)` and the rest of the query byte for
byte, and `src/hifa/app/search.py` is the service on top of it — so the correction makes the text say what
the code already does, and §7's plan scenario names two strings the tree can be checked against. Remove
either keyword and the plan loses `MATERIALIZE`, which is the failure the scenario exists to catch.

**Verification.** The two plans quoted above were captured with `EXPLAIN QUERY PLAN` over the same scratch
10 000-issue fixture on this host's SQLite (**3.53.1**), one run per form and once more with the team
filter — the flat form answers
`SCAN i` + `SCAN issue_fts VIRTUAL TABLE INDEX 0:=M3 LEFT-JOIN` ×2 without a team filter and
`SEARCH i USING INDEX idx_issue_open` + the same two probes with one, and the materialized form answers
`MATERIALIZE fts` / `SCAN issue_fts VIRTUAL TABLE INDEX 0:M3` / `MATERIALIZE title_hit` /
`SCAN issue_fts VIRTUAL TABLE INDEX 0:M3` in both cases. §7's new scenario asserts
`MATERIALIZE fts` and `MATERIALIZE title_hit`: absent from both flat plans, present in both materialized
ones. The millisecond figures above are the ones the defect was reported with, measured on R16's own
fixture; the plan text is what this host's run printed (the two agree on the shape, which is the claim
R17 and §7 pin), and the scratch run reproduced the direction without reproducing the absolute
milliseconds — its flat form ran 83-86× slower than its materialized one, on a fixture whose terms select
more rows than R16's 25 matches.

**What the review of this decision corrected.** Five sentences above do not survive this decision's own
numbers, and this paragraph replaces them; where any of them conflicts with this one, this one is the
record. (1) **"By an order of magnitude" overstated the miss by 5.6×.** The claim was ~10× against R16's
p50 half and the ratio is **1.774** (88.7 / 50), so it multiplied the miss by **5.6** — **10 / 1.774 =
5.6** — where the record has 1.77: **94.5 ms is inside the 120 ms p95 half** (0.79 of it), so that half
was never missed at all, and the ~10× is real only as flat against materialized, 88.7 / 8.8 = 10.1. The
consequence, stated exactly: **a query can miss R16's p50 half while passing its p95 half**, so the first
half is the one that catches this defect — and R17 asserts the `SCAN issue` half only where the published
query holds it, `team` and `team`+`state`, never for `state` alone (round three; item 4 below, HIFA-82).
"Misses R16's own budget by an order of magnitude" (the situation, above), "misses R16 by
10×" (point 2) and "ships a search an order of magnitude over R16" (point 3) are replaced by that
sentence. (2) **The coverage "What this does not change" claimed does not exist.** "Remove either keyword
and the plan loses `MATERIALIZE`, which is the failure the scenario exists to catch" is true of the plan
and was false about the suite: `grep -rn MATERIALIZE tests/ scripts/` answers nothing, the only plan test
(`tests/integration/db/test_search_reads.py::test_the_query_plan_of_the_ranked_query_does_not_scan_the_issue_table`)
inlines a copy of revision 9's CTE text and asserts that `SCAN (i|issue)` is absent and `SEARCH i USING
INDEX` present, nothing about `MATERIALIZE`, and it passes with the keywords in place — so **removing them
turns nothing red**, and a sentence that implied it did
turned debt into a false green. SPEC-090 §4.1, §8 step 10 and §12 now name the gap where they used to
claim coverage, as debt under `AGENTS.md` P8, with **HIFA-81** (the only query-plan test inlines a copy of
the CTE text instead of the adapter query) and **HIFA-80** (a test for §7's scenario) against it. (3)
**The second `MATCH` does not cost 0.03-0.06 ms.** That step of the elimination series is `MATCH` →
`MATCH + bm25`; the second `MATCH` over a materialized CTE was measured on its own — 6.70 vs 6.58 ms p50
(1.02×) on a 10 000-issue fixture whose terms select 115 rows — so it costs a fraction of a millisecond,
and the sentence above that annexed the `bm25` increment to it is withdrawn. A second pair that stood here,
5.09 vs 4.90 ms (1.04×), is withdrawn in round three rather than completed: the record states neither its
fixture, nor its term selectivity, nor its iteration count, and a ratio whose provenance is unstated is the
defect this block exists to correct. (4) **R17's premise and its example named different filters.**
`SCAN i` is the flat plan unfiltered or under a **state** filter; under a **team** filter the flat plan is
`SEARCH i USING INDEX idx_issue_open`, which **passes** the first half — as point 2 above says correctly
while the spec's R17 said both in one sentence. R17 now assigns each filter its own plan, and round three
went one step further, because that was still a statement about the **flat** form: measured with §5.2's own
text and both CTEs materialized, the normative query prints `SEARCH i USING INDEX idx_issue_open` under
`team`, `SEARCH i USING INDEX idx_issue_team_state` under `team`+`state`, and **`SCAN i` under `state`
alone** — so the requirement's first half was false for that filter, not merely unmet. R17 now prints the
four-row table, asserts the half for `team` and `team`+`state` and for no other filter, and files the
`state` case as **coverage**: no index on `issue` leads with `state_id` (SPEC-010 §3.2), so its remedy under
SPEC-030 §10 is an index on `issue(state_id)` in a migration — **HIFA-82**, the other half of the rule this
decision read from SPEC-030 — and not anything this delta adds. That `state` plan is also the statistics'
to decide: with `ANALYZE` the review's fixture lost the `SCAN` (resolving `idx_issue_team_state`), with
`ANALYZE` on a skewed 200-row fixture all four cases print `SCAN i`, and `grep -rn ANALYZE src/ scripts/
tests/` answers nothing, so R17 is asserted on a database that has never run it and no `ANALYZE` is added.
(5) **The provenance
of the millisecond rows is now stated.** The flat row cannot be produced by any harness in the
tree, because `_FTS_CTE` always materializes and re-typing revision 9's text is the only way to time it
again; the committed harness (`tests/perf/test_search_speed.py`) calls `search_issues`, with two terms and
always a `team=ENG` filter, so it can re-run the shape of the third row and nothing else. The three rows
are the figures the defect was reported with, and SPEC-090 §5.2 says which is which instead of printing
all three as "measured".

**What this does not contradict in SPEC-030.** SPEC-030 §10 ("Out of scope") ends with: *"Query plan
optimization beyond the indexes of SPEC-010: if `EXPLAIN QUERY PLAN` shows a `SCAN`, the solution is a new
index in a migration, not a change in the adapter"* (`030-sqlite-adapter.md:2009-2010`). This decision
**is** a change in the adapter's SQL, made for plan reasons — the unfiltered flat plan is `SCAN i` — and
the rule's remedy set cannot reach the defect: no index on `issue` removes a correlated `issue_fts` probe,
because the cost is the virtual table being the inner loop and not missing coverage; materializing a CTE is
not an index, and no migration can express it. The rule is therefore read correctly **within its scope**,
and its scope is the case it was written for: when the plan is bad **for a query that is the right query**,
the remedy is an index in a migration, not an adapter that rewrites the query's shape. Here the query's own
form was the defect, and the form is not the adapter's — four bullets above the rule, in the same
list, says the adapter "only exposes the `issue_fts` table as existing, and the query, the ranking and the
snippet are not its business" — so `_FTS_CTE` carries §5.2's normative text as SPEC-090 publishes it,
rather than an optimisation the adapter invented. **Verdict: the rule does not forbid this fix**, and it
does not need narrowing for this delta to stand; it does need to be read with its own neighbour bullet,
and a future revision of SPEC-030 that said "a `SCAN` the query's own text caused is fixed in the spec that
owns the text" would state it without leaving a reader to reconcile the two sentences. This decision
amends nothing of SPEC-030.

## D-074 — The history vocabulary is the writers', the backfill is a migration, and a timeline is a story

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.4 (the closed `field` vocabulary, 21 values, with its writer
and its shape), SPEC-010 §3.7 (migration `004`), SPEC-070 §2.5.7 (the one owner of the wording),
SPEC-050 §2.4.2 (a `HistoryEntry` of ten keys) and §2.5.3.3 (the verb) · **Touches:** SPEC-010 §2, §3,
§7, §8, Notes; SPEC-030 §2.4, §2.6, §3, §4.2, §7, §8, Notes; SPEC-040 §2.2, §5.1, §7, Notes; SPEC-050
§2.4.1, §2.4.2, §2.5.3, §2.5.3.2, §2.5.3.3, §6, §7, §8, §11; SPEC-060 §2.4.2, Notes; SPEC-070 §2.1.3,
§2.1.4, §2.2, §2.3.4, §2.5.7, §6, §7, §8, §11; `specs/001-mvp-v1.md`, `specs/001-mvp-v1.tasks.md`,
`specs/acceptance/001-mvp-v1/01-init.feature`; `specs/INDEX.md`; this file

**The situation.** HIFA-46 reports two defects of the activity timeline: a state change renders the
ULIDs of `workflow_state` (`...B80 → ...B7Z`) and the `created` row renders as an empty arrow pointing at
the issue's own identifier. The ticket also publishes a grammar table (§2) and a vocabulary (§11
consequence 2) that the writers do not produce, and **three of its numbers are not the database's**. This
entry records what was measured, which layer owns each half, and where the ticket and the code disagreed
and which one won.

**The measurement, and what it eliminated.** Read-only on `~/.local/share/hifa/hifa.db` on 2026-11-20
(the host has no `sqlite3` binary; the queries ran through Python's `sqlite3` module in `mode=ro`):

```
select field, count(*) from issue_history group by field
  created 92 · state_id 75 · board_order 34 · completed_at 18 · started_at 13 · description 11
  · relation 5 · trashed 4 · archived_at 2 · canceled_at 1                          -- 255 rows
select count(*) from issue_history where field='created' and new_value is not null   -- 92
select count(*) from issue_history h join issue i on i.id=h.issue_id
  where h.field='created' and h.new_value=i.identifier                               -- 92 (and 0 that do not)
select count(*) from issue_history b where b.field='board_order'
  and exists (select 1 from issue_history s where s.issue_id=b.issue_id
              and s.field='state_id' and s.created_at=b.created_at)                  -- 33 of 34
select distinct new_value from issue_history where field='relation'                  -- 'blocks HIFA-51', 'related HIFA-3', …
```

Three conclusions come out of it and all three are load-bearing. The **`created` rows are 92 and not the
45 the ticket publishes**, and every one of them carries exactly the issue's identifier — the ticket's
count is stale, not approximate. **`relation` is a noun whose value already reads `blocks HIFA-51`**, not a
discriminator of the form `relation:<type>`: the type and the far end's identifier travel inside the
value (`_relation_value` in `src/hifa/app/issues.py` builds it with `type.name_as_seen(direction)` plus
`other.identifier`), so the resolution the ticket thought a relation row needed does not exist. And
**`board_order` is almost never an event of its own**: 33 of its 34 rows share their `created_at` with a
`state_id` row, because `update_issue` and `move_issue` write both in one `record` call — the rank is the
move's consequence, and the one row that stands alone is a drag inside a column.

**1. The vocabulary is the writers', and the ticket's list is not.** The rule is the two-family rule
SPEC-050 §2.4.2 already stated (a change to a column of `issue` carries that column's name; a record event
that is not a column carries the noun of the write); the **set** is now enumerated — 21 values — in
SPEC-030 §2.4, with the use case behind each, the shape of its two sides and the sentence SPEC-070 §2.5.7
renders. The ticket's §2 table names sixteen fields and five of them cannot be stored by anything in this
tree:

| the ticket's name | what the writer actually stores | why |
|---|---|---|
| `label_id` | `label` | the row carries the label's **name**, and `label` is the noun of `add_label`/`remove_label` (SPEC-050 §2.4.2's rule) |
| `relation:<type>` | `relation` | the type is inside the value, `blocks HIFA-51`; a discriminator would put the same fact in two columns |
| `archived` | `archived_at` | the column is `archived_at` and the writer writes that name |
| `restored` | `archived_at` with a `NULL` new value, and `trashed` `"1" → "0"` | restoring is a direction, not a field: the same two columns carry it |
| `comment_added` | **nothing today** | no writer emits a history row for a comment; HIFA-6 will, and the revision that adds the writer adds the value |

and ten values the ticket never names are storable and therefore needed a sentence: `archived_at`,
`trashed`, `label`, `relation`, `estimate`, `branch_name`, `board_order`, `started_at`, `completed_at`,
`canceled_at`. The arithmetic is the record of the correction: the ticket's sixteen minus the five it
invents plus the ten it omits is **21**, and the enumerable set has no value that is not a column name or a
noun of a write. **The writers are the fact and the ticket's intent is the guide**: `archived`/`restored`
meant "the archive layer changed" and that intent is honoured by two sentences on `archived_at`; `label_id`
meant "a label came or went" and the intent is honoured by `label` carrying the name a chip can print.

**2. The `created` backfill is a migration, and the reason is hygiene rather than a rendering.** The row
says the issue was created; the identifier is the identity the timeline's own header carries, so echoing it
into `new_value` stored a value nothing could print. `create_issue` writes `("created", None, None)` from the
PR that lands this revision (SPEC-040 §5.1 step 16) **and `migrations/004_history_created_backfill.sql` nulls
the rows already written in that same PR** — the ordering `comment_added`/HIFA-6 is declared with, because a
writer and the data it leaves behind are one fact. **The migration is not what makes the screen right:**
SPEC-070 §2.5.7's grammar has no place for a value on `created`, so the sentence was `created the issue`
before the migration and after it (SPEC-050 §2.5.3.3's sample says so in its own words). What it buys is that
the stored row agrees with the writer's contract — `NULL` where the contract says `NULL` — so the next reader
(a diff, an export, a `data-*` attribute) does not have to know which version wrote a row. The idempotence is
twofold: the migrator applies a file only when its version exceeds `PRAGMA user_version` (SPEC-010 R5.1), so a
database at 4 never re-runs it; and the statement's `WHERE` matches no row after the first pass, so a manual
re-run is a no-op. **The window a migration cannot close is named:** a binary older than that PR still writes
the identifier on every issue it creates into a file `004` has already passed, and no second pass will clean
those rows; the consequence is invisible for the reason above — one shape on screen either way — and
SPEC-030 §2.4 says so rather than claiming `NULL` on every row of every database.
It **adds no schema object** — 17 tables, 3 views, the FTS table and its 3 triggers at version 4 are the
same set as at version 3 — so the DDL check of `scripts/ci_drift_check.py` cannot move because of
it, and what it costs is one full scan of `issue_history` on the upgrade (`idx_history_issue` leads with
`issue_id`; there is no index on `field`, and none is added for a one-time pass).

**3. Order is oldest-first, and D-D is binding.** The timeline is `<ol>` in `(created_at, id)` ascending —
`created` first, newest last — because a story is read forward and because `idx_history_issue(issue_id,
created_at)` already serves that order. The read model keeps its `DESC LIMIT 100` window (SQL-7 row 11) and
**the formatter sorts** (SPEC-040 §2.2's `history_rows`): the order of a payload is decided where the
payload is assembled and not in SQL, which is D-072 §1 applied to a second collection. **One payload, one
order:** the timeline, `hifa issue history` and its `--json` payload print it forward, and the CLI's
`HISTORY` section too — `issue history --json` publishes the formatter's page, so its elements are the
table's order and not the read's, and §2.4.3's list shape fixes `items` and not an issue's event order. The
only newest-first rendering left is the `history` key of `issue show --json`'s envelope, which is the read's
because that payload is (SPEC-050 §2.4.3) and because it is not a list verb of one issue. `--limit N` keeps
the **newest** N of the window and prints them forward — `git log -n`'s cut — and
the panel's cut is the **first** 8 (D-C), because the panel's reader starts at the top of the story. **The
window is 100 and it is a floor, not a total:** the read cannot say how many events an issue has, so
`Show all N` publishes the window's size and a full window prints `the last 100 events` — a hundred-row
issue and a ten-thousand-row issue answer the same 100 rows, and the section says which of the two it is
showing. A `COUNT(*)` was the alternative and it is not free (it is one more aggregate per page for a number
nothing on the screen uses) and not this delta's to add.

**4. Comments merge when HIFA-6 lands, and nothing of this delta merges them.** The ticket's D-A is accepted
and binds: when `comment_on_issue` writes a history row, that row is the timeline's and the `Comments`
section is retired rather than kept beside it. This delta **reserves** the value and does not declare a
sentence for it: `comment_added` is named in SPEC-050 §2.4.2 (the revision that adds the writer adds the
noun) and deliberately **absent** from SPEC-030 §2.4's table and SPEC-070 §2.5.7's grammar, because a
sentence for a field no writer stores is a rendering of nothing — the same refusal D-071 §2 made for a
payload with no producer. The consequence is a one-line change in three places when HIFA-6 lands, and no
place where a half-declared comment event could rot.

**5. One row, one shape, on both edges — and the sentence has one owner.** `HistoryEntry` is **ten keys**
on both edges (`id, issue_id, actor_id, field, old_value, new_value, created_at, old_label, new_label,
actor_label`, appended, never inserted): the CLI of SPEC-050 §2.4.2 and the REST class of SPEC-060 §2.4.2
publish the same ten, so an agent reads the story without joining `workflow_state`, `member` or `issue`, and
the raw pair stays for a client that wants the key (P7). `actor_label` is the key that makes the last claim
true: `actor_id` is a ULID, `HistoryRow`'s display name was app-internal, and until the wire carried it the
actor was the one field of the story a client still had to resolve. `IssueDetail` is still **33 + 4 = 37
keys**: an element of a collection is not a key of the object, and a delta that grew the envelope to publish
a label would have moved a number every count in this corpus quotes. The **wording** has one owner,
SPEC-070 §2.5.7, and the two other specs quote it — SPEC-030 beside each writer, SPEC-050 in a terminal —
which is the arrangement SPEC-050 §2.4.2 already used for the `field` rule. The row itself is declared once,
as SPEC-040 §2.2's `HistoryRow` (eleven typed fields, `dot_color` the one colour key), and the web's element
**is** that row plus `created_label` — with one type change, `created_at`, which the element carries as the
`…Z` string SPEC-040 §2.2's `wire_instant` renders, the same string the CLI and the REST elements publish.
The formatter answers `HistoryPage`, which carries `rows`,
`window_size`, `truncated` and `last_activity` — the four facts a row cannot. It is
`src/hifa/app/history.py::history_rows`, not a template (P4) and not `domain/` (a state's display name is not
a concept the domain layer is asked to learn), and not a web-only step (P2) — the CLI verb, the REST envelope
and the two web hosts project the same rows.

**6. A ULID never reaches the screen, and every way it could is a rule.** A target that is gone or unknown
renders `<kind> (deleted)`; a missing side renders the direction's own sentence (`cleared the priority`,
`unassigned`, `restored the issue`) and never a dangling arrow; an unknown field renders `changed <field>`;
an unknown actor renders `someone`. The formatter raises nothing — the four are answers, and SPEC-040 §2.2
declares `raises: nothing` for it. The web keeps the raw pair in `data-*` attributes, which are data and not
text, so a CSS `content:` cannot print a key.

**7. Three statements about the stored rows were false, and the fixes are in the layers that own them.** (a)
*Six kinds of stored value are opaque* left out two the grammar already renders: `priority "2"` is `High` and
`due_date "2026-11-20"` is `20 Nov 2026`, and neither had a producer, so `set priority to High` was a sentence
about a `2`. The count is **eight**, all with a producer: the six ULID kinds through the read's maps (SQL-7
rows 13/16/17) and those two through SPEC-040 §2.2's `PRIORITY_LABELS` and `DUE_DATE_FORMAT`, which are
constants — a closed 0-4 and a date format — and therefore cost no statement. (b) *A `board_order` row renders
nothing when it shares an instant with a `state_id` row* made the payload's cardinality a property of a
rendering, so the CLI, the JSON envelope, the REST array and the two web hosts could disagree, and "no row is
dropped" was false of the surface that dropped one. The read drops it now — SQL-7 row 11 excludes a
`board_order` row whose immediately preceding row of the same issue, by `id`, is a `state_id` row — which is
the writers' **batch** and not `created_at`'s precision (33 of the measured database's 34 `board_order` rows
are exactly `id − 1` from a `state_id` row), so no surface publishes a row the read dropped and no surface
drops one either. **Three surfaces publish the window the read returned and never cut it — the
`HISTORY` section of `issue show --with`, the REST array of SPEC-060 §2.4.2 and the page's list — while
two cut, in two different ways: the panel cuts without being asked (8 of the window, `Show all N`), and
`issue history` cuts to a `--limit` the caller controls — 50 by default and 100 at most, so no flag
publishes 50 of the read's 100-row window and `--limit 100` publishes the window (SPEC-050 §2.5.3.3).**
The panel's 8 is a layout decision and not a number about the read (SPEC-030 §2.4, SPEC-070 §2.5.7.2).
(c) The three milestone rows published one direction (`ISO → NULL`), and the writer does not have that
direction for all three. `apply_milestones` stamps `NULL → ISO` on entering a state and nulls
`completed_at`/`canceled_at` on returning to an open one, and it **never clears `started_at`**: so
`completed_at` and `canceled_at` publish both directions, and `started_at` publishes the one the writer has
(`started work`), with `cleared the start time` removed — the rule that keeps `comment_added` out of the
table, applied to a direction.

**What it wins over, and what it does not touch.** It **supersedes** SPEC-030's `spec_revision` 20 sentence
that "this table adds no name to them" — the table now enumerates the writers' set, because the enumeration
belongs to the layer that writes the `INSERT` while the *rule* stays SPEC-050 §2.4.2's. It **supersedes**
SPEC-050 §2.5.3.2's `HISTORY` section columns `WHEN ACTOR FIELD FROM TO`, which printed a ULID pair and
said its "CLI half is HIFA-46 and is not declared by this revision": the CLI half is declared in §2.5.3.3
and the section prints the verb's three cells. **It also supersedes D-071 §5's own sentence** — "`HISTORY`
is `WHEN  ACTOR  FIELD  FROM  TO`" and "`HISTORY`'s `FROM`/`TO` print the **stored** strings, so a `state_id`
change shows two ULIDs; rendering them as names is the timeline's rule … whose CLI half is **HIFA-46**, and
this decision neither fixes nor pre-empts it" — for the same reason and in the same words it supersedes the
section in SPEC-050: the columns are `WHEN  ACTOR  EVENT`, the cells are the sentence and its resolved
labels, and the stored pair survives only in `--json` (P7). D-071 §5's other four sentences stand — the
sections grow in a fixed order, `COMMENTS` drops `ISSUE`, an empty collection prints no section, and the
block's key field is 9 columns. No other decision publishes a `FIELD`/`FROM`/`TO` row for this collection.
It **supersedes** the ticket's §2 vocabulary list and its
`relation:<type>`, `label_id`, `archived`, `restored` rows, for the reasons measured in point 1, and the
ticket's `45` for the `92` of point 2 with the database and the date named. It **does not contradict**
D-024 (the delta is in English), D-059 and D-027 (the trash visibility and what the detail page reads are
untouched), D-060 (comments stay flat and keep their section until HIFA-6), D-062 (the `field`/`resource`
split of a 404 is not in question), D-071 (a payload with no producer is still refused: `comment_added` has
no sentence) or D-072 (its §1 is the rule this delta applies to a second collection; its §2–§3 are the trash
and the milestone keys and are untouched). The ticket's own D-A…D-E are not decisions of this file: they are
its §11 and they are honoured here as the ticket's binding input — D-C, D-D and D-E are the ones this delta
writes into three specs.

**What this does not change.** No table, column, index or view is added, removed or renamed: `004` is data
only and `issue_history`'s DDL is what SPEC-010 §3.1 already declared. No exit code moves — the verb's set
is **0, 2, 3**, the set of the read it projects — and no error class or `rule` is added; the one new sentence
(`--limit` outside 1-100) leaves by the bucket the same flag already uses on this edge, `validation_error`
with `field: "limit"`, which is what `issue list --limit` answers for the same mistake: one flag name with two
bounds (1-200 the listing's window, 1-100 the history read's) and **one** code, because two codes for one
mistake is a flag a caller has to read twice. No flag of an existing
command moves; `issue show --with history` costs the same read and the same statements, and SQL-7 gains
**one conditional statement** (row 17) whose gate is a *kind* — a page with one `parent_id` row and a page
with two hundred pay the same one — so REQ-F030-12's 12 is unchanged on a database whose history names no
parent. That last clause is **measured, not general**: the field list of `~/.local/share/hifa/hifa.db` on
2026-11-20 has ten values and `parent_id` is not one of them, but `update_issue` writes a `parent_id` row on
`issue edit --parent`, so any database that has used that flag pays the 13 — the 12 is a property of a file,
never of "the v1 writers". Nothing of `domain/` moves, no checkbox of
`001-mvp-v1.tasks.md` is ticked, and no `src/`, `tests/`, `scripts/` or migration file is written by this
delta: it is a specification, and the code lands in the implementation PR of HIFA-46, which also declares the
`issue.history` capability row — `cli="issue history"` beside this delta's fragment, **both edges present**,
so no `debt=`/`reason=` and nothing for `check_edge_parity.py`'s single-edge rule to refuse — together with
the route's `FRAGMENT_ALLOWLIST` entry, in
`scripts/check_edge_parity.py` in the same commit (a verb and a fragment the checker fails on until they are
declared). **Two sentences of the MVP layer still say the opposite** and this delta does not edit them:
`001-mvp-v1.md`'s seam 8 lists "reading history … `issue history|tree|blocking`" among the verbs that "today
have no command" and asks for "a contract change in SPEC-050 … with a `version` bump of 050" — which is this
delta, §2.5.3.3 and 13 → 14 — and `001-mvp-v1.tasks.md` T-06 says "(there is no `issue history` verb)". Both
are the implementation PR's to correct, beside the code; a requirements file that pre-announced a CLI edge
would be a fourth place claiming what the tree does. The **numbers** of the same two files are a different
matter and are corrected here: `REQ-F001` said `user_version = 3` and `01-init.feature` asserted it, which
contradicted the `MAX_VERSION` of §2 that this delta publishes, so `4` is written where `3` was — a value that
already had an owner, not a promise about a command.

**Counts, each measured in the worktree (2026-11-20).** SPEC-030 §7 **27 → 30** and SPEC-010 §7 **8 → 9**
(`grep -cE '^  (Scenario|Scenario Outline):' specs/030-sqlite-adapter.md` → 30,
`…/010-data-model.md` → 9); SPEC-050 §7 **62 → 69** and SPEC-070 §7 **72 → 80**
(`…/050-cli.md` → 69, `…/070-web-dashboard.md` → 80); SPEC-040 §7 **59** and SPEC-060 §7 **27** do not move;
`IssueDetail` stays **37** keys (33 + 4) and `HistoryEntry` goes 7 → **10**; the `.feature`
corpus stays **17 files / 240 scenarios (228 `Scenario` + 12 `Scenario Outline`)** — no scenario is added or
removed there, the four `user_version` assertions of `01-init.feature` and the two files of the MVP layer
carrying `3` are corrected to `4`, which is a value and not a count (`find . -name '*.feature' | wc -l` → 17;
`grep -cE '^  (Scenario|Scenario Outline):'` summed → 240);
this file goes **73 → 74** decisions. **Which tool checks what, because they are often confused:** 
`python3 scripts/ci_drift_check.py` applies the real migrations to a temporary database and compares the
resulting set of tables, views and indexes against the `CREATE` blocks of `specs/010-data-model.md` — a
data-only migration cannot move that set; `python3 scripts/spec_contract_check.py --all` runs the same DDL
comparison **and** the extra checks, among them `pyproject.toml`'s entrypoint (`hifa = "hifa.cli.main:app"`)
and the base dependencies. Both pass, and neither is **evidence about this delta**: they read a schema and an
entrypoint, not a sentence, a key list, a scenario count or a step order. A `version` moves in **six** specs —
SPEC-010 2 → 3 (`MAX_VERSION` and the file table),
SPEC-030 7 → 8 (`IssueDetail.history_parents`, SQL-7 row 17), SPEC-040 10 → 11 (`history_rows`),
SPEC-050 13 → 14 (a verb and three keys), SPEC-060 10 → 11 (a schema class), SPEC-070 9 → 10 (a fragment, a
view-model key and §2.5.7) — and every one of them is a §2 signature, key or table, not a note.

## D-075 — The rank rule is the batch, and the page keeps no control

**Date:** 2026-11-20 · **Wins:** SPEC-030 §2.4 (the `board_order` bullet) and SQL-7 row 11 (its `WHERE`) ·
**Touches:** SPEC-030 §2.4, §2.5, §7, Notes; SPEC-070 §2.5.7, §7; `specs/acceptance/001-mvp-v1/16-dashboard.feature`;
`specs/INDEX.md`; this file

**What D-074 got wrong, twice, and what the code proved.** D-074 stated that `update_issue` and `move_issue`
write `state_id` and `board_order` in one `record` call, "so the two rows are consecutive `id`s", and made the
read exclude a rank row "when the immediately preceding history row of the same issue, by `id`, is a `state_id`
row". The first half is false: `_move_once` writes the rank row **only when the position differs from the
stored one**, so a move into an empty column — or any move whose computed slot equals the store — writes the
`state_id` row alone. The consequence was a dropped event: a genuine drag written after such a move is the
very next row by `id`, so the rule ate it, on both edges and in the `last_activity` line, with no error and no
line anywhere that would report the loss.

The measured database agreed with the rule — 33 of its 34 rank rows are `id − 1` from a `state_id` row — and
that agreement was the trap: every one of those rows was of the batched kind, so **a correlation measured over
the common case is not the writer's invariant**. A rule that reads a position in an `id` sequence has to read
the fact that put the two rows in those positions. The rule now asks for **both**: the predecessor is a
`state_id` row **and carries the same `created_at`** — one `record` call stamps one instant, and the `id` order
says the rows left together. Measured on the same file, the corrected rule filters the same 33 rows and no
others; on a database driven through an empty column it keeps the drag.

**The second correction: the control belongs to the panel.** D-074's §7 scenario asked the page to contain
`Show all 100` while the same section gives the page the whole window, so the page printed a `button` that
expanded what was already on screen. The scenario was the defect, not the code that honoured it: the page's
cut is empty by construction and carries no control, and the panel keeps both the cut and the button.

**Residual, named rather than hidden.** `created_at` is stored at second precision, so a drag written in the
same second as a preceding state-only move still satisfies both conditions and is filtered. That is a window
of one second instead of a class of events, and closing it needs a batch identifier in the write — a schema
change this decision does not take.

## D-076 — `show` reads four reference forms, and only the number asks for a team

**Date:** 2026-11-21 · **Wins:** SPEC-001 REQ-F012 (the behaviour: which references resolve) and SPEC-050
§2.5.3.2 (the grammar, the codes and the message that spell it) · **Touches:** SPEC-050 §2.3.1, §2.5,
§2.5.3, §2.5.3.2, §4, §7, §11; `specs/INDEX.md`; `scripts/spec_claims_debt.json`; this file

**What the two documents said, and where they parted.** SPEC-001 REQ-F012: "`hifa issue show` accepts
three reference forms: `identifier` (`ENG-12`), full ULID (26 chars) and integer number with `--team ENG`.
It also resolves historical identifiers (old key in `team_previous_key` + `number`) and prints an
`identifier renamed` warning on `stderr`. A non-existent reference exits with exit code `3` and
`error.code=\"not_found\"`." SPEC-050's row of §2.5.3 closed the verb to `--with`, and §2.5.3.2 opened
with "`REF` — an identifier or a ULID — is resolved by `issue_detail(db, REF, include=…)`": one form
short of the requirement, and silent about the fourth and about the warning.

**Neither document loses a rule, and that is the resolution rather than a split.** SPEC-001 wins the
**behaviour**: the three forms and the historical one are what the vertical requires, and R17 never said
otherwise — R17 closes the **tree** (which leaves exist, with exactly the flags their row lists), not the
vocabulary of a `REF` the row already declares. A reference form is an input's grammar; `--team` is not a
new option either, it is the §2.2 duplicate the verbs that already consult the team list (`create`,
`list`, `assign`, `blocked`), and `issue show` now lists it for the same reason they do. SPEC-050 wins
the **spelling**: the order the forms are read in, which one needs a team, the code and `field` of every
refusal, and the channel and wording of the warning. SPEC-001 is unchanged: 001 §10.1 item 8 assigns the
CLI surface to SPEC-050, and §7's count of the acceptance corpus does not move because the new scenarios
land in SPEC-050 §7 and not in `specs/acceptance/001-mvp-v1/`.

**The three things SPEC-050 had to decide, and did.** (1) A bare number in a workspace with 0 or ≥2
teams and no `--team` is `missing_context` — exit 2, `field: "team"`, the message §2.2.2 and §6 already
carry — and **not** `not_found`: a number names no issue until a team is chosen, so what is missing is an
input, not a row, and REQ-F012's exit 3 keeps its meaning for a team's issue that does not exist. With
exactly one non-archived team the fifth source of §2.2.2 answers and the number resolves with no flag.
(2) The order of the four forms makes the answer total: a current identifier shadows a previous key of
the same spelling, which `team_previous_key`'s key `(team_id, key)` allows (SPEC-010 §3.6). Refusing
there would refuse a case the corpus's ambiguity rule does not cover — that rule (D-070) is for two rows
answering **one** form; these are two forms answering once each, and the more specific one wins. (3) The
warning is a line and not a payload: `identifier renamed: ENG-3 is now PLAT-3` on `stderr`, printed once,
in table, `--json` and `--quiet` alike, with the exit code at **0** and `stdout` keeping its one document
(R11); no key of §2.4.2 carries it, and the object stays at 33 and 37 keys.

**Residual, named rather than hidden.** The two new forms have no producer. SPEC-040 §2.2 declares
`issue_detail` for "`ENG-123` or a ULID", and no read returns an issue by `(team_id, number)` nor a team
by a current or a previous key, so the edge cannot resolve them without a use case of its own (D-012,
REQ-S050-01). That is GAP-09 of SPEC-050 §2.3.1, the §7 scenarios that drive the new forms carry
`@deferred(HIFA-83)`, and closing the row is HIFA-83's implementation half. `team_previous_key` has an
adapter writer (`TeamRepo.add_previous_key`) and **no caller**: the rotation that would write a row is
SPEC-001 REQ-F006, which SPEC-001 §10.1 item 8 lists among the verticals with no counterpart in SPEC-050 —
so until `team set-key` is contracted, the historical form resolves rows a future command left behind.

## D-077 — D-076's header is corrected: SPEC-040 was the losing spec, and the rotation settles form 3

**Date:** 2026-11-21 · **Wins:** SPEC-050 §2.5.3.2 (the grammar of `REF`, its order, its codes and the
wording of its warning) · **Touches:** SPEC-040 §2.2 and §Notes (`spec_revision` 39 → 40, `version` 11 →
12); SPEC-050 §2.3.1, §2.5, §2.5.3, §2.5.3.2, §4, §7, §11; `README.md`; `specs/INDEX.md`; this file

**Why a new entry rather than an edit.** D-076's header named no losing spec and listed neither SPEC-040
nor `README.md`, and one of its sentences is false after the adjustment below. This file is append-only,
so D-076 stands as written and this entry is the correction; the headers of D-076 are to be read through
this one.

**Where D-076's header was wrong.** (1) **The conflict was not with SPEC-001.** REQ-F012 is what SPEC-050
obeys; 001 §10.1 item 8 assigns the CLI surface to 050 and nothing in 001 contradicts the delta. The two
documents that disagreed were **SPEC-040 §2.2**, which closed `issue_detail` to "`ENG-123` or a ULID", and
**SPEC-050 §2.5.3.2**, which contracts four forms. SPEC-050 wins; **SPEC-040 is the losing spec** and is
adjusted at `spec_revision` 40, §2.2 delegating the grammar to §2.5.3.2 by reference and its §Notes
pointing at D-076 and at this entry. That is `AGENTS.md` §3's mechanism, and it is the whole of the
resolution: the GAP row below is a statement about the tree, not about the conflict. (2) **Touches.** §4
is not touched by this change and was listed; SPEC-040 and `README.md` were omitted, and both move —
`README.md` publishes `issue show` with `--with` only, while §2.5.3 now lists `--team`, and `AGENTS.md` §6
item 8 requires README, `AGENTS.md` and `INDEX.md` in the same pull request. (3) **The count was wrong.**
D-076 said the old text was "one form short"; `REF` — an identifier or a ULID — declared **two** of the
four, so it was two short. The claims gate cannot see this class of error: its provenance rule reads counts
of database rows, and a count of reference forms is prose.

**What D-076 left open, decided here.**

(1) **Case-insensitivity belongs to GAP-09, not to a second gap.** §2.5.3.2 compares both identifier forms
case-insensitively, and the read the verb calls binds `i.identifier = :ref` exactly: probed on this tree,
`hifa issue show hifa-1` exits 3 against a workspace that holds `HIFA-1`. The implementer who widens
`issue_detail` for the previous identifier and the number normalises the identifier in the same widening —
one resolver for `show` and `edit` rather than a CLI that resolves `hifa-1` for one verb and not for the
other. GAP-09's row and paragraph name the normalisation as part of the widening, so the gap is one row
for one resolver and not a form count.

(2) **The rotation makes form 3 total.** `team_previous_key` is `PRIMARY KEY (team_id, key)` with a
non-unique index on `key`: a team may leave `ENG` for PLAT, a team created later as `ENG` may leave it for
OPS, and then two rows carry `key = 'ENG'` while each of those teams numbers an issue 3 — `ENG-3` would
answer twice with no rule in force. The form is answered by the row with the greatest `changed_at`, an
exact tie broken by the greater `team_id`, because `changed_at` exists on the row for exactly this and is
otherwise a dead column. A refusal there would leave a legal pair of rotations unresolvable; the corpus's
ambiguity rule is for one form answering twice, not for two rotations that the schema allows.

(3) **The scenarios are absent, not deferred.** A `@deferred(HIFA-83)` tag in the commit that contracts
the behaviour names the ticket that writes the tests, and no code path ever reports such a tag stale: the
accounting prefers a test when one exists, so the tag can neither fail nor pass with the work. The eight
§7 scenarios were withdrawn with it, `HIFA-83` was unregistered from `scripts/spec_claims_debt.json`, and
they arrive in the implementation's commit each pinned by a test whose name is its title or by
`@test(<path>::<name>)`. The gate now fails a `@deferred` that a test has caught up with, which is what
makes a deferral a debt rather than a permanent claim.

(4) **`--team` is a leaf option, and three verbs carry it.** D-076 called it "the §2.2 duplicate the verbs
that already consult the team list (`create`, `list`, `assign`, `blocked`)": the root callback refuses
`hifa --team ENG` with exit 2 and `No such option`, `assign` declares no `--team`, and the members of this
family that do are `create`, `list` and `blocked`. What is true, and what §2.5.3.2 and its Notes now say,
is that `--team` is read at the leaf by the verb that needs it, with no global value to win over.

---

## D-078 — The case is folded in the parameter, the form is resolved before the read, and the edge owns its context

**Date:** 2026-11-21 · **Wins:** SPEC-030 §2.6 SQL-7 and §2 · **Touches:** SPEC-030 §2, §2.6 and §Notes;
SPEC-040 §2.2 and §Notes; SPEC-050 §2.5.3.2, §7 and §Notes; `src/hifa/adapters/sqlite/reads.py`,
`src/hifa/app/boards.py`, `src/hifa/cli/commands/issue.py`, `tests/integration/db/test_boards_detail.py`,
`tests/e2e/cli/test_issue_show.py`, `README.md`

D-076 and D-077 contracted the four reference forms and were implemented; this entry corrects the four
sentences they left false and one performance defect they introduced. Nothing here reverses D-076: the
forms, their order, the rotation tie, the warning and the single-read rule stand, and this is the record of
what was wrong around them.

(1) **`COLLATE NOCASE` was the wrong side of the comparison, and it cost every detail read its index.**
SPEC-030 revision 23 put the collation on `i.identifier`. `EXPLAIN QUERY PLAN` answers `SCAN i` for that
statement: a collation on the column makes `sqlite_autoindex_issue_2` unusable, both branches of the `OR`
collapse, and the one statement `show`, the board card and the issue panel all run reads every row of
`issue` — O(issues) on every detail read, against a spec whose §10 says a `SCAN` is answered by a new index
in a migration and never by a change in the adapter. No index was missing: `upper(:ref)` is a constant to
the planner, the plan is `MULTI-INDEX OR` with both `SEARCH` lines back, and the comparison is the whole of
the same rule, because `issue.identifier` is written normalised (`normalize_key` uppercases the team's key)
and folding the parameter is all the case-insensitivity there is. No migration, no new index, no Unicode
claim. `team_by_previous_key` carried the same defect on `pk.key` — a scan of `team_previous_key` — and got
the same one-line fix, the rotation storing its key normalised too.

(2) **SPEC-050's shape was the better one, so the code moved and not the spec.** §2.5.3.2 said the two
lookups run **before** the read and that the read is called **once**; the tree read first and resolved
second (`boards.py` read the reference, then read again by id), which is two reads of one issue and three
header statements for a form-3 `show`. `REF` is now resolved to an issue id first — ULID, current
identifier, previous identifier, number, in the order the table declares, where the code had been trying
the number first — and the detail read is asked for that id once. `reads` gains `issue_id_by_ref` for the
first two forms (one statement, both branches on their own index) and the app layer keeps the order. The
read's own statements and the two totals of SPEC-030 §2.5 do not move: the lookups were never its rows.

(3) **The edge resolves the context, and the specs now say so.** §2.5.3.2 said the edge "writes no SQL,
opens no second read and holds no vocabulary of its own" while `cli/commands/issue.py` opened a read to
resolve the team the number form needs and re-implemented the digit test `app/boards.py` already held.
§2.2.2 puts context resolution at the edge, so that sentence was wrong about the layer that owns it: the
edge resolves `--team`/`HIFA_TEAM`/`[cli].default_team`/the one team **once**, before the read, and it asks
`app.boards.is_number_ref` instead of keeping a second copy of the grammar. The *form* resolution stays in
`app/`, and the edge still writes no SQL.

(4) **SPEC-040 delegates and does not restate.** §2.2 said the grammar was SPEC-050 §2.5.3.2's "and is not
restated here" five words before restating the case rule, the rotation rule and the number rule — and the
restatement was incomplete, because the rotation tie picks the **team** and the team's `number` then picks
the issue. The restatement is deleted: §2.2 keeps that `issue_detail` answers all four forms and reports the
rename, and names that section for everything else. That is D-076's own lesson, applied to the layer that
wrote it.

(5) **Two counts became enumerations.** The revision 41 note said the one-team workspace is answered by
§2.2.2's **fifth** source (it is the fourth; the fifth is the refusal) and that three verbs carry `--team`
(it is ten: `issue create`, `issue list`, `issue blocked`, `issue show`, `label add`, `label list`,
`search`, `state list`, `state create`, `state update`). Both sentences are corrected in place, and the
count is replaced by the list, because a count of a set the same document enumerates is a claim that rots
on the next verb.

(6) **A test that pins a refusal does not pin a form.** Four of the eleven new e2e tests passed with forms
3 and 4 deleted from the tree: a digit token can never be an identifier, so "not found" is what both paths
say, and a number the team does not wear was indistinguishable from an unknown token. Each now runs the
reference that separates the form from the fallthrough — a number another team wears is that team's
missing issue, an identifier resolves where an unplaced number is `missing_context`, and the shadowed form
is shown to answer before the one that shadows it; the §7 scenarios carry the same steps, so their `@test`
pointers claim what the tests do. The case test cannot bite against forms 3–4 and is not asked to: its
subject is form 2's comparison, and it fails when the fold is removed.

## D-079 — A GFM table is rendered, and reaches the page through the allowlist that already owns markup

**Date:** 2026-11-22 · **Wins:** SPEC-070 §2.3.6 (the construction of the renderer) and R15 (the tag
allowlist), which SPEC-000 REQ-S003 names as the sanitizer contract · **Touches:** SPEC-070 §2.3.6,
R15, §7 and §11 (`spec_revision` 31 → 32, `version` 10 → 11); SPEC-001 §7, §4 (REQ-S004) and its
frontmatter (`spec_revision` 17 → 18); `specs/001-mvp-v1.tasks.md` (T-10); `specs/INDEX.md`;
`specs/acceptance/001-mvp-v1/17-dashboard-hardening.feature`; `src/hifa/web/markdown.py` and
`src/hifa/web/static/css/app.css`, the implementation this entry contracts

The defect is one URL: `GET /HERMES/issue/HERMES-1` serves a GFM table as literal pipe text inside a
single `<p>`. Two layers refuse it, each on purpose. The `commonmark` preset of `markdown-it-py` ships
the `table` rule **disabled** — `MD.get_all_rules()["block"]` lists `table` and the render is one
paragraph — and `ALLOWED_TAGS` has no table element, so the walker would drop the structure even with
the rule on. The stylesheet then has no base rule for `.description table` and one `@media` rule that
sets `display: block` on it, which destroys the layout the table has.

(1) **What wins, and what it costs elsewhere.** SPEC-070 §2.3.6 and R15 win: they state the renderer
and the allowlist, and the two places that quote them move in the same revision. The only other
document with a figure to move is SPEC-001, whose §7 publishes the corpus count: the one new scenario
makes it **241 (229 `Scenario` + 12 `Scenario Outline`)**, and the same figure is stated in
`specs/001-mvp-v1.tasks.md` and `specs/INDEX.md`. SPEC-000 does not change: REQ-S003 names the
allowlist as the contract and does not enumerate it, which is why this is a SPEC-070 revision and not
a 000 one.

(2) **The rule is enabled, the preset is not replaced.** `MD.enable("table")` on the instance that
already exists. The rule ships inside the installed parser, so no plugin, no dependency and no
`pyproject.toml` line comes with it. `gfm-like` was the alternative and is refused with its price: it
also enables strikethrough, linkify and task lists, which would put `del`, `s`, an autolinked URL and
a checkbox `input` in the allowlist — elements and an attribute path for a ticket about tables. A
preset is a bundle; the ticket bought one rule.

(3) **The allowlist grows by structure, not by permission.** `table`, `thead`, `tbody`, `tr`, `th` and
`td` are containers and cells: they carry no attribute of their own and execute nothing. The escape
layer is untouched — `html=False` still turns the user's markup into text before the walker sees it,
the scheme rule on `a[href]` is unchanged, and the wrapper of (5) is written by this module — so
REQ-S003's "no `javascript:`, no `on*`" is not weakened: a `<td onmouseover=…>` in a description
arrives as escaped text inside a cell, exactly as it did with the rule off.

(4) **`style`, as an exact match of three values.** markdown-it emits `style="text-align:left"`,
`style="text-align:center"` or `style="text-align:right"` on the `th` and the `td` of an aligned
column, and nowhere else. A CSS declaration parser was the other way and is refused because it would
decide a larger language than the renderer can produce: it would have to accept or reject
`background:url(…)`, `position:fixed`, `font-size:0` and every property written after this entry, and
each acceptance is a surface the ticket did not ask for while each misparse fails in a direction
nobody tested. Three literal strings have one behaviour — a value outside the set drops the attribute
and keeps the cell's text — and the check cannot be grown by a value it misreads.

(5) **The wrapper is the sanitizer's own, so `div` stays out.** A table wide enough for a phone pushes
the page, and the existing answer — `display: block` on the table — is what breaks its header's
alignment with its cells. The walker therefore wraps every `table` it emits in
`<div class="table-scroll">` and the `@media` overflow rule moves to that element. `div` does **not**
join `ALLOWED_TAGS`: with `html=False` markdown-it cannot emit one, a `<div>` in a description is
escaped to text, and the only `div` in the rendered description is the one this module wrote. Adding
`div` for a wrapper we author would widen the allowlist for a node no input can reach.

(6) **What the corpus pins.** `17-dashboard-hardening.feature` and SPEC-070 §7 gain one scenario with
the same title, `a markdown table in a description is served as a table`, deferred to **HIFA-113**
until its test lands: the HTML carries `<table>`, a `<th>` with the header text, a `<td>` with the
cell text, and `text-align:center` on the `th`/`td` of a column declared `:-:`. §2.3.6's mandatory
tests carry the same four assertions plus the dropped `style` and the escaped cell, because a rule the
allowlist enforces and the corpus does not read is a rule nobody reads twice.

(7) **A value is tested by the check its own attribute selects** (amendment, spec review of the
`spec_revision` 32 delta). The shipped walker gates every kept attribute on one condition —
`(tag, name) in ALLOWED_ATTRS and value is not None and _safe_href(value)` — so adding
`("th", "style")`/`("td", "style")` to the set without touching the condition leaves the scheme test
in front of a value that is not a URL: `urlparse("text-align:center").scheme` is `text-align`, the
attribute of every aligned column is dropped and the alignment of (4) is gone with no test failing.
The predicate is therefore the attribute's own — `href` against `ALLOWED_SCHEMES`, `style` against
`ALLOWED_STYLES` — and SPEC-070 §2.3.6 states it beside the three values.

## D-080 — The field is the control: one field per request, `view=field`, and a description that saves on Cmd/Ctrl+Enter

**Date:** 2026-11-22 · **Wins:** SPEC-070 §2.6.3 (inline editing), §2.1.3 (the values of `view`) and §2.3.4
(`issue_vm`) · **Touches:** SPEC-070 §2.1.2, §2.2, §2.3.3, §2.6.2, §2.6.6, §2.6.9, §4 (R27, R29), §5.3, §6,
§7, §8, §10, §11 and its frontmatter (`spec_revision` 32 → 33, `version` 11 → 12); SPEC-001 §7 and §Notes
(`spec_revision` 18 → 19); `specs/001-mvp-v1.tasks.md` (header, T-16 and Notes, `spec_revision` 17 → 18);
`specs/INDEX.md`; `specs/acceptance/001-mvp-v1/16-dashboard.feature`; `scripts/spec_claims_debt.json`;
`src/hifa/web/templates/**`, `src/hifa/web/static/css/app.css`, `src/hifa/web/readmodels.py`,
`src/hifa/web/app.py` and `tests/e2e/web/**`, the implementation this entry contracts and does not contain

**The defect is the pooled form, and it is measured, not argued.** Every editable field of an issue was a
control to go and find: `issue_detail.html` rendered `<form class="issue-edit">` with an
`<h2 class="form-title">Edit</h2>` and all seven controls plus one `Save`, and
`partials/issue_panel.html` rendered the same form with an `<h3>`. A reader who wanted to fix a word in the
title edited all seven values and submitted them together. On the served dashboard (`127.0.0.1:8823`,
commit `bb7ccf5`) `curl -s http://127.0.0.1:8823/HIFA/issue/HIFA-98 | grep -c hx-patch` answered **0** while
`curl -s http://127.0.0.1:8823/HIFA/list | grep -c hx-patch` answered **100** for 50 rows: the machinery
existed (a `PATCH` route, one row that used it) and the two surfaces where a field is actually read did not
have it. The panel was worse than the page: it carried the `hx-patch` on the **form**, so one changed field
posted all seven keys and the answer re-rendered the whole `<aside>` under the cursor.

**What wins, and why it is a contract change rather than a template edit.** Four sentences of SPEC-070 said
the opposite of what is now contracted: §2.6.3 listed the inline fields and then stated that `description`
is **not** inline, §2.1.3 justified `?view=panel` by that description editor, §2.6.6's "Edit a field" row
described the row's control posting a hidden page form with "`description` in the panel with a Save", and
§2.3.4 declared no per-field flag at all while `src/hifa/web/readmodels.py` produced `title_invalid` and
`description_invalid` for a row that has one control. The decision is
therefore SPEC-070's, at `spec_revision` 33, with `version` 11 → 12: a new value of `view` and a new query
parameter are the public contract of a route the layer already served.

(1) **One field per request, seven fields, both surfaces.** `title`, `description`, `priority`, `state`,
`assignee`, `due_date` and `estimate` each get a wrapper `<div class="field-inline"
id="field-{name}-{identifier}" data-field="{name}">` holding a `<details>` whose `<summary
class="field-value">` is the value and whose content is that field's own `<form method="post"
action="/{team}/issue/{identifier}">` with the control and a `Save`. The control state is **always in the
document**; nothing is built on click and nothing is fetched to render it. A body carries one field, which is
what makes the write the one field the reader changed instead of the seven they did not.

(2) **The reveal is the browser's, and no client holds a state.** `<details>`/`<summary>` is the mechanism
§2.6.9 already uses for the confirmations and the only one in this layer that needs no script at all. The
state a wrapper renders is the server's answer (`vm.fields[<name>].open|invalid|message`, §2.3.4), so a
`422` comes back with the field it refused still open and marked, and a `GET` comes back with seven closed
wrappers. The alternative — an Alpine component per field, or the `x-data`/`x-show` pair that the row's
docstring mentions and no template carries — is refused below.

(3) **`PATCH /_/issues/{identifier}?view=field&field={name}` answers one wrapper.** `view=list`,
`view=board` and `view=panel` keep their bodies and their swaps, unchanged. `view=field` returns the
`<div class="field-inline">` of the named field: the target is `closest .field-inline`, the swap is
`outerHTML`, and `200`/`422`/`409` each answer that node — the value, the control kept open with
`.is-invalid` and the message beside it, and the value with the message and **no** control when the issue is
archived. A `field` that names none of the seven is a resolution that failed: `404`, `text/plain`, `no such
field`, like the unknown `state_id` of D-059. The field is named in the URL and not derived from the body
because a refusal's body is `title=` and nothing in it says which wrapper to answer.

(4) **`description` is inline, with Cmd/Ctrl+Enter and an in-field `Save`, and never on blur.** This is the
deliberate reversal of the old sentence and the reason it is a decision: `Enter` in a `<textarea>` is a
newline, so the field saves on §2.6.2's existing rule — `Cmd/Ctrl+Enter` submits the form the focused control
belongs to — **or** on its own `Save`, and a reader who clicks away from a half-written note has not asked
for it to be committed. The value state stays the rendered markdown and the control state the stored source
(`description_raw`).

(5) **The pooled form and its heading leave the tree, and the read model gains the flags the render needs.**
`<form class="issue-edit">`, both `form-title` headings, `partials/issue_form_fields.html`, `.form-title` and
`.issue-edit` in `static/css/app.css` (their only two consumers were those two forms; `grep -rn
"form-title\|issue-edit" src/` before deleting) and the undeclared `edit_fragment`'s only client all go. The
`dl.meta` of both surfaces stops echoing a field the body controls: the detail page keeps `Project`, `Cycle`,
`Parent`, `Labels`, `Created` and `Updated`, and the panel's list is removed because `State`, `Priority`,
`Assignee` and `Due` were all it held. §2.3.4 gains `fields` — one entry per control, always all seven, with
`open`, `invalid` and `message` — and `description_raw`, `edit_action` and `edit_fragment`; §2.3.3 gains the
control values (`assignee_id`, `due_value`, `estimate_value`) and the two booleans a template gates a class
on (`title_invalid`, `description_invalid`). Every one of them is a flag and not a comparison, because
§2.3.7 forbids a template comparing a name to a string and the sidebar's `is_*` flags are the precedent.

(6) **`assignee` stays a text input holding the member id, and the spec says which read makes that the only
option.** `build_issue_vm` in `src/hifa/web/readmodels.py` builds `"assignees": []`, and
`IssueDetail.members` is the `member.id → display_name` map of the **comment authors and history actors** of
one issue (SPEC-030 §2.6, SQL-7 row 13) — not the team's members. The roster read exists in `app/`
(`active_members(repos)`) and nothing under `src/hifa/web/` calls it. A `<select>` over the empty list the
view model hands a template would be a control nobody can use; a member picker is another ticket.

(7) **The fragment `GET /_/issues/{identifier}/field/{field}` is withdrawn from §2.1.3.** It declared "the
edit control for one field" on a click and was never served: no route under `src/hifa/web/app.py`, no
template, no test, since the first commit of the corpus. It is withdrawn rather than implemented because the
reveal it implies — fetch the control, swap the node — is exactly the one a reader without JavaScript cannot
use, and the control is already in the document. T-16 of `specs/001-mvp-v1.tasks.md` loses the name in the
same revision, so the plan and the contract cannot disagree.

**Alternatives, and what each cost.**

- **An Alpine state machine per field** (`x-data="{open:false}"`, `x-show`, `x-init="$refs.input.select()"`).
  It is the shape the old §2.6.3 flow block described, and it is refused for the reason the layer already
  paid for once: Alpine is the CSP build precisely because `new Function` is banned (REQ-S003), every
  component is a client-side copy of a decision the server has to make anyway (which field is invalid, which
  one is open after a `422`), and with JS off a wrapper whose `x-show` never runs renders **no control at
  all** — the editor would stop working exactly where §2.6.6's "Edit a field" row promises it works.
- **A form per field that re-renders the whole page or the whole aside** (what the panel did). It answers the
  element the client did not edit: a title save replaced the `<aside>` under the cursor, so the reader lost
  their place to a write they had already made, and the focus had to be restored by a script for every field.
  The `view=field` answer is the fix, and it is why the panel's fields no longer post `?view=panel`.
- **Keeping `?view=panel` as the answer of every edit** and letting the client re-focus the node it needs.
  Refused because it throws away what §2.6.9's focus table promises — "the read-only value that replaced the
  control, on the re-rendered node with the same `data-field`" — and because the aside is a container with
  seven wrappers in it: a swap of the container is a swap of the six fields that did not change.
- **One pooled form and a `Save` per field inside it.** It reads like the small change and is not one: a
  `<form>` cannot hold a `<form>`, so seven `Save`s inside one form are seven submits of the same seven
  fields unless the server is told which button was pressed — a control name that exists only to disambiguate
  a form, which R27's "identifiers on the wire" rule does not have.
- **`view=detail`** (the ticket's first draft). Refused as a name: the value would answer the page, and what
  the reader needs answered is the field; a page-sized answer is what `view=panel` was already doing wrong.

**Consequences.**

- **The contract is new in two places and narrower in one.** §2.1.3 gains a fourth `view` and a `field`
  parameter, §2.3.4 gains `fields` and the two keys the wrappers print, and §2.1.3 loses the `field/{field}`
  fragment. `version` 11 → 12 is §2 changing.
- **The debt is the scenarios.** Ten scenarios land in `16-dashboard.feature` and the same ten in §7, each
  `@deferred(HIFA-114)` until its test exists: the pair is the corpus's own convention for a contract that
  arrives before its code (D-079's table scenario is the previous one), and the claims gate reads the
  deferral, not a key. `HIFA-114` is registered in `scripts/spec_claims_debt.json` as the ticket of the
  cluster with **no keys**: a key for a scenario a `@deferred` already accounts for is stale by construction —
  `scripts/spec_claims_check.py` fails it with "accepted key names no unaccounted scenario" — so the cluster
  records the owner and the reason, and the deferrals are the measurement.
- **The count moves in five places.** The corpus is **251 scenarios (239 `Scenario` + 12 `Scenario
  Outline`)** — the `grep -cE '^  (Scenario|Scenario Outline):'` of the seventeen files — and §7 of SPEC-070
  is **91**. `specs/001-mvp-v1.md` §7, `specs/001-mvp-v1.tasks.md` (header and coverage line) and
  `specs/INDEX.md` carry the same figure.
- **The implementation owes the tests, not the shape.** `tests/e2e/web/test_dashboard_writes.py` asserts
  `class="issue-edit"` twice — once to find the form and once to prove a read-only surface has none — and both
  assertions move to the field wrapper; the deferred scenarios become tests whose names are their titles, and
  the tags come off with them.
- **Two scenarios keep a title the change narrows, and neither is renamed.**
  `a visible issue's page and panel carry the four controls of the action group` (§7 and
  `16-dashboard.feature`) keeps its name and its steps: its fourth assertion is `action="/ENG/issue/ENG-13"`,
  which the seven field forms still carry, and a scenario's title is its test's name — renaming it would drop
  the claim for a count the steps do not make. `the panel edit answers the panel` keeps its `?view=panel`
  request and its `<aside>` assertion, because that value did not change: what changed is that no successful
  write of the UI uses it any more, so it now pins the body a layer refusal re-renders (and a caller that
  wants the aside whole). R29 states the action group by enumeration for the same reason a count is not
  repeated there: a count of a set the same document enumerates rots on the next control.

**Amendment (2026-11-23, `spec_revision` 34) — the statements above that were not true, and the work they owe.**
This entry is append-only, so (3), (4) and the consequences stand as written; this amendment is the text that
governs, and it is what §2.1.3, §2.6.2, §2.6.3, §5.3 and §6 of SPEC-070 now say.

1. **The `404` of an unknown `field` is the partial, not `text/plain`.** (3) says `404`, `text/plain`, `no such
   field`. A `/_/` answer that has no element to keep has one body in this layer — `partials/fragment_error.html`
   — and it goes to `#toasts` under `HX-Retarget: #toasts` and `HX-Reswap: innerHTML`; the sentence it carries
   names the field (`field 'duedate' is not a field of ENG-13`), and that is how an unknown field is told from an
   unknown issue. One response, one media type: the difference between those two `404`s is in the message and not
   in the header. `text/plain` stays where it already was and where there is no element and no partial — the `400`
   of a missing `HX-Request`, an unknown `state_id` on the two drops, the cross-site `403`, and the read-only
   `403` of `/_/states/{id}/position`.
2. **The `409` is new work in the client, and it is this ticket's.** (3) says a `409` answers the field wrapper.
   htmx swaps no `4xx` unless a listener enables it; the listener in the tree enables `422` alone and the generic
   `htmx:responseError` handler turns a `409` into the toast `could not save: the server rejected the change`, so
   the wrapper of (3) would never reach the page. This ticket adds `409` — and the `HX-Retarget` case that carries
   `fragment_error.html` — to `htmx:beforeSwap`, which is why §2.6.3 states the flow as a requirement of the
   implementation and §2.1.3 shows the listener it needs; R11 moves with it.
3. **`?view=field` reads through the trash, so a trashed issue's field is a `422` and not a `404`.**
   `_issue_edit_fragment` passes `include_trashed` for `view == "panel"` alone (`app.py`), so the natural
   `view="field"` path raises `NotFound` and answers a `404` for a state §6 refuses with `422`. The keyword is
   passed for `view == "field"` too: the wrapper comes back in its control state, `open`, marked, carrying
   `ENG-13 is in the trash; restore it first` and **no** write control, which is what R29's `Restore`-alone rule
   leaves. R29 names this fourth read and keeps it a non-surface, exactly as it does the history fragment.
4. **Two mechanisms were named as existing and are not.** `changed` compares the control's value with the
   `lastValue` htmx records when it processes the node, not with `data-original`, which no script of the tree reads
   (`grep -rn "data-original" src/` answers one printed attribute in `partials/issue_row.html`), so both shapes
   drop it. And `Cmd/Ctrl+Enter` does not run today: the shell's `keydown` returns on `metaKey`/`ctrlKey` and
   nothing under `src/hifa/web/` calls `requestSubmit()`, so §2.6.2 declares the branch as this ticket's work —
   which is also why (4) above is a contract and not a description.
5. **The scenario title stays, and its comment stops counting.** `a visible issue's page and panel carry the four
   controls of the action group` keeps its name for the reason the consequences give; the group its steps assert
   is the move control, the archive block and the delete block — three — and the fourth assertion pins
   `action="/ENG/issue/ENG-13"`, the target of the seven field forms. The comment inside the scenario says that,
   because a comment is not a test name and can tell the truth.
6. **Two keys and three classes were retired by the rule this entry already applies.** `row_vm` loses
   `description_invalid` — a row has one control, so `title_invalid` is its only flag, and the detail page and the
   panel read `fields[<name>].invalid` — and `.form-actions`, `.field` and `.field-input` leave `app.css` with
   their last consumers, while `.field-label` stays because the new value state prints it (§2.5.8 carries the
   greps that decide each one). And `view=panel` names no HTTP client of the UI any more: `_layer_refusal` renders
   the aside server-side through the same `_issue_edit_fragment(view="panel")`, so the value is contract and the
   body is shared, not a request.

**Amendment — spec_revision 35, 2026-11-23 (the second review round).** The review of this entry found eleven
statements in SPEC-070 that this decision's delta had made false or unbuildable. The seven that touch this
decision's own text are below, and the body of the spec is the authority where they disagree with it.

7. **Item (4)'s "both shapes drop `data-original`" is withdrawn.** The wrapper never prints the attribute —
   there is nothing to drop — and `partials/issue_row.html` keeps the one it prints today
   (`issue_row.html:20`): the row of the list does not change (§2.6.3, requirement 9). `changed` still
   compares htmx's `lastValue`, which is the mechanism the item was about.
8. **Item (6)'s `.field-input` retirement is withdrawn.** `partials/comment_composer.html:17` uses the class,
   and `textarea.field-input` (`app.css:444`) is the comment box's 96 px height and mono face: removing the
   rule would degrade a surface this ticket does not touch. `.form-title`, `.issue-edit`, `.form-actions` and
   `.field` still go, each with the grep of §2.5.8.
9. **`.inline-input` is not redeclared.** It keeps the rule it already carries (`app.css:431`:
   `width: 100%; max-width: 360px`), which the list row's one control depends on; a full new rule would give
   the row a border and a background and take its 360 px cap away (§2.5.8).
10. **The wrapper is seven partials, not one partial told a name.** `field_title.html` through
    `field_estimate.html`, each with its field's name, key, value expression and `?view=field&field=…` written
    literally, included in order from `issue_detail.html` and `partials/issue_panel.html`. `field_control.html`
    with `{% with %}` bindings would have needed `{% if name == … %}` to choose one of three controls and one
    of seven value keys — the branch §2.3.7 forbids (§2.2, §2.3.7).
11. **`fields[]` gains `writable` (bool), and the wrapper has three states.** `writable: True` is the editable
     wrapper (closed, or open on a `422` that hands the control back); `writable: False` + `open` is the
     trashed issue's `422 ISSUE_TRASHED` — `<details open>`, the value marked and the message, **no** control;
     `writable: False` + closed is the archived issue's `409` — the value and the message, **no** `<details>`,
     no control. The trash is `422` and the archive is `409`, never the other way round (§2.3.4, §5.3, §6).
12. **The field `404` is `HX-Reswap: beforeend`.** `_fragment_error` appends `partials/fragment_error.html` to
    `#toasts` (`app.py:280`); `innerHTML` would replace the container's content, which is not the answer this
    layer gives (§2.1.3, §6).
13. **The scenario's title stays and its steps are the authority.** `a visible issue's page and panel carry the
    four controls of the action group` keeps its name because a scenario's title is its test's name; the
    comment in `16-dashboard.feature` no longer counts the edit form among the group's controls — the group is
    the move `<select>`, the archive block and the delete block, and the edit is the fields themselves
    (§2.6.9). No prose of SPEC-070 states a count for that group.
