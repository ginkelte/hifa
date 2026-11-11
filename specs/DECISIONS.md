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
