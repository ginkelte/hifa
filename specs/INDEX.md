# specs/ — component index

Source of truth for `hifa`. The code is derived: every spec must allow its **component to be
regenerated from scratch**. Convention and flow: [`README.md`](./README.md). Template: [`_template.md`](./_template.md).

**Process status:** Spec Review round 1 (104 gaps) → 5 Fix rounds → re-reviews →
**2 approved specs, 10 at `review` and 1 draft**, which is the `status:` of each frontmatter and what
the Status column of the Map below repeats (SPEC-110, the release pipeline, is the draft, at `version: 3`
/ `spec_revision: 9` after the engine change of D-039, the governed outcome classifier of D-040 and the
three revisions that followed it).
Accepted debt P-01…P-09 in [`DECISIONS.md`](./DECISIONS.md) (**72 decisions: D-001 … D-072**; P-12 closed
by D-041, and D-071's third amendment block and D-072's correct decisions already numbered — they resolve
nothing new, so the count stands). Reports in [`reviews/`](./reviews/).

**State order and state commands (D-034, D-035, D-058):** the canonical seed is
`Triage(1.0) · Todo(2.0, is_default=1) · Backlog(3.0) · In Progress(4.0) · In Review(4.5) ·
Done(5.0) · Canceled(6.0)` — `Todo` sits **before** `Backlog`, and the single `In Review` of type
`started` serves spec review and code review alike, so `started` is the one type the seed carries
twice (SPEC-100 §3.1 invariant 3 counts rows per type). SPEC-100 §2.5 is the only template.
`hifa state list|create|update` is the CLI surface for it (SPEC-050 §2.5.6), and the board
columns can be dragged, persisted through `POST /_/states/{id}/position`
(SPEC-070 §2.6.5, R25). Existing teams are reordered with a command or a drag, **never** with a
data migration: a team seeded before the review column existed gains it with
`hifa state create 'In Review' --type started --color '#BB87FC' --before Done`, which lands at the
`4.5` a new team's seed uses and moves no state that already exists (D-058). Archiving or deleting a
state is deferred (D-035): no verb is published for it,
because what happens to the issues that live in the state is undefined.

**Deferred on purpose (D-031):** `hifa team archive` is **not** part of the CLI surface —
`archive_team` is deferred with `update_team`, so SPEC-050 declares no row for it and SPEC-040 marks
the function "declared, not implemented". The same decision settles the list envelope: collections
are `{"items": [...]}` and the cursor keys exist only where the command paginates (`issue list`).

**The label surface is contracted and unimplemented (D-061, HIFA-14):**
`hifa label add|list|show|update|archive [--undo]` and `hifa issue label|unlabel [--create] [--color]`
over the label use cases of SPEC-040 §2 (`create_label`, `update_label`, `retire_label`,
`restore_label`, `list_labels`, `label_detail`, and `add_label`/`remove_label` on an issue), with the
`singleSelect` replacement REQ-F024 has required since its first revision and the `{issue, replaced}`
envelope it asks for. The dashboard half — `POST`/`DELETE /_/issues/{identifier}/labels` — is
**HIFA-61**, and the seven capability rows of `scripts/check_edge_parity.py` land with the code, in the
same PR (SPEC-050 §11) — three as `debt="HIFA-61"` (`issue.label`, `issue.unlabel`, `label.list`) and
four as `debt="HIFA-62"` (`label.create`, `label.show`, `label.update`, `label.archive`, the dashboard's
label administration page; D-061(g)).
**D-070 closes the four holes that review found in that surface's corpus:** a label name can be ambiguous
**inside one level** (`Bug` and `bug` satisfy the two partial unique indexes, which compare binary, while
every resolution compares case-insensitively), so `LABEL_AMBIGUOUS` is now raised by every reference that
resolves a label by name — `add_label`, `remove_label`, `create_issue`, `create_label`'s parent and the
four catalogue verbs — with the spellings and level keys in `message` and in the exception's `exc.details`
accessor (the wire `details` is `null`, D-062); a **second spelling** of a name is refused
at its level by `create_label`, by a rename and by a restore that would bring one back
(`UNIQUE_LABEL_NAME`, retired rows included) while a row may
change its own case; `init`'s seed resolves its four names case-insensitively and never writes the second
spelling (SPEC-100 R11bis); and `issue create -l` resolves a name exactly as `issue label` does
(SPEC-050 §2.5). Round two of the review corrected the two-name `replaced` (it is a **sibling the call
took off** — `High`, not the `Low` the call added), made `hifa import` refuse a twice-spelled level
with its own `ambiguous_label` row verdict (SPEC-080 R10), and replaced SPEC-060 C4's false "unique per
team" for labels. Round three finished both halves of that work: `replaced` is the **last non-null**
replacement of the call (`High` for `high low` on `Medium`, `Medium` for `high other`, where the trailing
`other` write displaces nothing, and `null` only when no write displaced anything), `LABEL_NOT_FOUND`'s
row names its two producers, SPEC-030 §2.4 stops pretending a seed and an import call `get_by_name` (they
do not exist to call it), SPEC-080 §2.1.1 stops prescribing it, `ambiguous_label` gets its §4.1 case with
its message, C4's **state** clause loses the same uniqueness premise its label clause lost, and the three
version bumps the earlier rounds declined are taken. The specs go to `version: 8` / `spec_revision: 35`
(SPEC-040), `version: 10` / `spec_revision: 35` (SPEC-050), `version: 8` / `spec_revision: 16`
(SPEC-060), `version: 2` / `spec_revision: 8` (SPEC-080), `spec_revision: 17` (SPEC-030) and
`spec_revision: 14` (SPEC-100, whose §2 does not move). Four of those numbers have moved again since:
SPEC-030 to `version: 7` / `spec_revision: 20`, SPEC-040 to `version: 10` / `spec_revision: 38`,
SPEC-050 to `version: 13` / `spec_revision: 39` and SPEC-060 to `version: 10` / `spec_revision: 18`,
by D-071, its second amendment, D-072 and the fourth round of this delta; SPEC-080 and SPEC-100 stay
where that list put them. The rows of the Map below carry the current numbers.

**The search surface is contracted and the page is no longer a stub (HIFA-7, D-063, D-064, D-067):**
`hifa search` (SPEC-090 §2.5, SPEC-050 §2.5.1) and `GET /search` (SPEC-070 §2.1.8) are the two edges of
the use case behind **`search_issues(db, query, *, now)`**, the entry point SPEC-040 §2.2 declares (D-064)
— over the FTS5 index of SPEC-090 §3. Each edge is a **consumer** that parses its own text with
`parse_query(raw)` and composes the `SearchQuery` from its own flags and the instant its clock read; the
parser takes no flag and no default team, and `app/` holds no clock (SPEC-090 §2.3, R13). The text half
crosses the port as one `TextPredicate` — the two FTS5 expressions and **both** term tuples, positive and
negated — so a query that only excludes (`-bug`) and a query with no text at all are two different
arguments and the adapter can tell them apart (SPEC-090 §2.4, D-067). The page has exactly four states: empty (`200`, no `q`), results,
no results (`200`, `hits=[]`) and error (`422`, a query SPEC-090 §6 refuses); it never answers `400`, which
is what the sidebar link to `/search` used to hit (D-063). `hifa db reindex` (SPEC-100 R21) is the only
corrective action of the index and the only verb of the `db` group that ships: `db migrate`, `db vacuum`,
`db path` and `hifa doctor` stay declared and unimplemented, one debt ticket each. Its rebuild is the
idempotent sequence R21 prints (`CREATE VIRTUAL TABLE IF NOT EXISTS`, `DROP TRIGGER IF EXISTS`, `CREATE
TRIGGER`), because a dropped virtual table leaves the three triggers of SPEC-090 §3.1 behind, and its
`sqlite_master` verdict runs on a **read-only** probe before the file is opened for writing — a path that
does not exist answers `3` `database_not_found` without being created — so a file that opens but is not a
hifa database is `3` `not_a_hifa_database` and only an image SQLite refuses is `6` `corrupt_database`
(D-066). The command
palette (`GET /_/palette`, T-20.4) is **not** part of this change and keeps the contract R19 gave it; what
it inherits is R21's rule for every edge that composes a query, and only for the day it ships. The listing's
`q` (`GET /api/v1/issues?q=`) is the **title filter of D-025** on every edge and is not the search: no
`MATCH`, no `backend`, no ranking, no effect on `order` (SPEC-060 §2.5.1, rev 13).

**`hifa issue show` is contracted, and its `--with` payloads are the detail read (HIFA-49, D-071, D-072).**
The verb was declared in SPEC-050 §2.5.3 and absent from the tree (`No such command 'show'`), with its
payloads parked in GAP-06 of §2.3.1 as "detail reads beyond `issue_detail`" — and there are none:
`issue_detail(db, ref, *, include=…)` has returned the children, the relations, the comments and the
history since long before. §2.5.3.2 now maps each `--with` value onto that read (`comments` and `history`
enter its `include`; `relations` and `children` are read on every call and are **not** `include` names,
which is what SPEC-040 §2.2's `DETAIL_INCLUDE` always said) and §2.4.2 declares the object:
**`IssueDetail`**, **37 keys** — the 33 of `Issue` with `comments`, `relations`, `history` and `children`
appended after `updated_at`, flat and never nested — SPEC-060 §2.4.2's `IssueDetail(IssueOut)` key for
key, so the CLI and the API print one shape for one read. **`attachments` is not one of the four**: the
read exists (SQL-7 row 12) and nothing can write a row for it (`add_attachment` is declared in SPEC-040
§2.2, implemented nowhere, and is the only writer of `attachment`), so the key would be `[]` on every
object and could not be told from "none" — `--with attachments` is refused with exit 2, `GET /issues/{ref}`
answers 422 `field: "include"` for it, and `issue attach` waits with it (GAP-07). The rule that selects the
accepted names is **the names of `DETAIL_INCLUDE` whose collection the envelope carries** — three accepted
by the read, two carried by `IssueDetail` — and not "the names that reach a key", which would have accepted
`children` and `relations`, keys of the object that are nobody's `include` name. The order of
`relations` and the visibility of `children`/`relations`/`parent` are produced by the use case and by the
read (D-072): SQL-7 row 9 returns an unordered set, and rows 7, 8 and 9 hide what the trash layer holds —
the trash predicate is all the detail shares with `issue list --parent`, whose default also binds
`is_open = 1`. `milestone` is `null` on every object in v1: the read loads it and no write stores an id
(`src/hifa/app/issues.py` raises `NOT_IMPLEMENTED`, GAP-08). The two sentences
that had `show --with` "staying GAP-06 until it lands" are past tense, and the note in SPEC-040 that
claimed GAP-01…GAP-06 were closed names the gap that was still open.

**Design principles P1-P9 (AGENTS.md §5) are normative since D-032; the web edge contract
joined `.importlinter` in the same change.**

**SPEC-070 stage 1 (read-only dashboard) is delivered** in `feat/dashboard-readonly`:
`create_app` (Jinja2 + `StaticFiles` + strict CSP), page routes (`/`, board, list, detail),
htmx fragments with the `HX-Request` guard, pure read models, templates, own CSS/JS and
`hifa serve` with a lazy FastAPI import. Its contract is closed by
**D-025…D-028** (`q` as a title filter, the read-model fields declared in SPEC-030 §2.6,
`list_board(filters=…)` restored, `web/` modules and `/healthz` declared in SPEC-000).
Inline editing, drag & drop, comments, labels and the
command palette stay open: they depend on `update_issue`, `move_issue`, `archive_issue` and
`comment_on_issue` (SPEC-040), which do not exist yet.

**Implementation:** T-01 (scaffold) and T-02 (DDL + migrations) delivered in `feat/bootstrap`
and verified in the Controller Gate: 52 tests, `ruff` and `lint-imports` green, 17 business
tables + 5 FTS shadow tables, 3 views, 12 indexes, 3 triggers, idempotent migrations and
**14/14 constraints** verified (they reject `priority=9`, `2026-13-45`, `2026-02-30`,
duplicate label of the workspace, state from another team, duplicate number, empty title,
self-relation; they accept NULL, `2026-03-01`, homonymous label in another team).

## Map

| ID | Component | Spec | Governs (`implements`) | Depends on | Status |
|---|---|---|---|---|---|
| 000 | System architecture | [000-system-architecture.md](./000-system-architecture.md) | `pyproject.toml` (**packaging keys except the version-producing ones**, D-038/D-039), `.importlinter` (`src/hifa/__init__.py` is not claimed here: D-038 gives it to SPEC-110) | — | approved |
| 001 | **Vertical v1 spec (MVP)** | [001-mvp-v1.md](./001-mvp-v1.md) + [tasks](./001-mvp-v1.tasks.md) | the whole MVP (scope + AC) | 000…100 | review |
| 010 | Data model | [010-data-model.md](./010-data-model.md) | `adapters/sqlite/migrations/*.sql`, `migrate.py`, `connection.py` | 000 | approved |
| 020 | Domain core | [020-domain-core.md](./020-domain-core.md) | `domain/**`, `tests/unit/domain/**` | 000, 010 | review |
| 030 | SQLite adapter | [030-sqlite-adapter.md](./030-sqlite-adapter.md) | `adapters/sqlite/**`, `ports.py`, `tests/integration/db/**` | 010, 020 | review (`version: 7`, `spec_revision: 20`: `LabelRepo.get_by_name` is documented as an **exact** read with **no caller** in the tree (D-070); the item projection SQL-4 shares with SQL-7 row 1 carries `LEFT JOIN project_milestone pm` with `i.milestone_id`/`pm.name`, the loader of a `milestone` key no v1 write can fill (GAP-08); and SQL-7 rows **7, 8 and 9** hide what the trash layer holds — one sentence, with 040/050 pointing at it — while the claim that the detail and `issue list --parent` list the same set is withdrawn, D-072 amended; and `HistoryRepo.record`'s `field` is stated in the two families SPEC-050 §2.4.2 owns — a column's name, or the noun of the write for `created`/`relation`/`label` — with `created` no longer missing from the list, round four) |
| 040 | Application services | [040-application-services.md](./040-application-services.md) | `app/**`, `tests/integration/app/**` | 020, 030 | review (`version: 10`, `spec_revision: 38`: `include` accepts the `DETAIL_INCLUDE` names and nothing else — `relations` and `children` are read on every call, not gated — and the note that claimed GAP-01…GAP-06 of SPEC-050 §2.3.1 were closed names the gap that was still open (D-071); §2.2 declares what only the use case can produce: `relations` sorted on `(READS AS rank, far-end identifier)` because SQL-7 row 9 returns an unordered set, and `children`/`relations`/`parent` with no row the trash layer holds — SPEC-030 §2.6's sentence, pointed at, with the "same set as `issue list --parent`" claim withdrawn, D-072 amended) |
| 050 | CLI | [050-cli.md](./050-cli.md) | `cli/**`, `tests/e2e/cli/**` | 040 | review (`version: 13`, `spec_revision: 39`: `issue show --with` is contracted — §2.5.3.2 maps the four values onto `issue_detail`, §2.4.2 declares the flat `IssueDetail` with its **four** appended collections (**37** keys) and the `HistoryEntry` object whose `field` is the writer's vocabulary (`created`, `relation`, `label`), GAP-06 closes and `attachments` is re-gapped as GAP-07 with exit 2 and no key, the milestone write path is GAP-08 (`milestone` is `null` in v1) and the refused `--with` values have their two contract messages — D-071, D-072 amended; and round four makes the §2.7 sample print §6's own sentence for `--with attachments`, drops the claim that GAP-07 is the only row of its kind, and marks R15/REQ-S050-04 as GAP-07's, with no §7 scenario to run them) |
| 060 | REST API | [060-api-rest.md](./060-api-rest.md) | `api/**`, `tests/e2e/api/**` | 040 | review (`version: 10`, `spec_revision: 18`: C4's two uniqueness claims are gone and §6.3 keeps `details` to `null`/list, naming the ambiguity in `message` (D-070); the detail route publishes the names of `DETAIL_INCLUDE` whose collection the envelope carries — `{comments, history}` — because v1's `IssueDetail` is **33 + 4 = 37** keys and `attachments` has no key until GAP-07, which is not "the names that reach a key" (that rule accepted `children`); `AttachmentOut` is one declaration with no producer and the two subresource rows are gone, D-071's second and third amendments) |
| 070 | Web dashboard | [070-web-dashboard.md](./070-web-dashboard.md) | `web/**`, `tests/e2e/web/**` | 040, 060 | **review** — partial delivery. Stage 1 delivered: read-only board/list/detail + quick add + `/healthz`; read contract closed by D-025…D-028, write paths contracted by D-053…D-056, the write guard of §2.1.7 by D-057, the two routes that read through the trash — detail and panel with `Restore` alone, §2.1.1/R29 — by D-059, the `/search` page with its four states — §2.1.8, D-063 — and `spec_revision: 29` removes the attachment surface: `issue_vm` carries no `attachments` key, the detail passes the two `include` names the envelope carries, R16/REQ-S070-05 and §7's attachment scenario are withdrawn (GAP-07) |
| 080 | Import / export | [080-transfer-csv-json.md](./080-transfer-csv-json.md) | `app/transfer.py`, `adapters/sqlite/transfer_sql.py` | 040 | review (`version: 2`, `spec_revision: 8`: R10 resolves a label name one level at a time and rejects a twice-spelled level with the `ambiguous_label` row verdict instead of preferring the team's row, §2.1.1 consumes `list` + the `casefold` compare instead of `get_by_name`, and B21 carries the case with its message, D-070) |
| 090 | FTS5 search | [090-search-fts.md](./090-search-fts.md) | `app/search.py`, `adapters/sqlite/reads.py::search` | 010, 040 | review (`version: 4`, `spec_revision: 12`: promoted by HIFA-7 — the one entry point `search_issues(db, query, *, now)` in §2.3, `parse_query` as the text parser the edges compose over, the page's consumption in §2.6 (D-063/D-064), the two `like` forms (§5.3) with the `TextPredicate` that carries the negated terms across the port and the NULL-safe `NOT` of both of them (D-067, R14), and §5.2's two CTEs `MATERIALIZED`, with the plan measured against R16's budget and R17 naming it — the miss is 1.77× R16's p50 half, not an order of magnitude; R17's `SCAN issue` half holds for `team` and `team`+`state` and not for `state` alone, whose residual is coverage and a new index in a migration (HIFA-82), and the plan test that would enforce the half is debt (D-073; HIFA-80, HIFA-81)) |
| 100 | Config and bootstrap | [100-config-bootstrap.md](./100-config-bootstrap.md) | `config.py`, `app/teams.py::bootstrap`, `cli/commands/db.py` | 010, 040 | review (`spec_revision: 14`: the seed resolves its four label names case-insensitively and writes no second spelling, R11bis, D-070) |
| 110 | Semantic release pipeline | [110-release.md](./110-release.md) | `.github/workflows/release.yml`, `.github/release/tag-message.sh`, `.github/release/assert-outcome.sh`, `.releaserc.json`, `package.json`, `package-lock.json`, `CHANGELOG.md`, `.github/signers/**`, `pyproject.toml` (the `version` literal and the version-producing keys, D-038/D-039), `src/hifa/__init__.py`, `tests/unit/test_version.py`, `tests/e2e/test_release_contract.py`, `tests/e2e/test_release_outcome.py`, `tests/fixtures/release/*.log` | 000, 050 | draft (`version: 3`, `spec_revision: 9`: npm `semantic-release` engine, literal version, `v0.1.0` bootstrap — D-039; per-step secrets, governed and tested outcome classifier — D-040) |

## Dependency graph

```
000 ──▶ 010 ──▶ 020 ──▶ 030 ──▶ 040 ──┬──▶ 050 (CLI)
                             │        ├──▶ 060 (API) ──▶ 070 (web)
                             │        ├──▶ 080 (transfer)
                             │        ├──▶ 090 (search)
                             └────────┴──▶ 100 (config/bootstrap)
```

`110` (release) sits outside the runtime chain: it depends on `000` (packaging) and `050`
(`hifa --version`), governs no module of `src/` other than the `__version__` symbol, and is the only
spec that governs CI (D-039). It is also the only component that writes to `main` without a pull
request: one `chore(release): X [skip ci]` commit per release, pushed through the ruleset's admin
bypass (D-039), while `pull_request`, `required_status_checks`, `non_fast_forward` and `deletion` stay
enforced for every other actor (D-036).

## Acceptance criteria

`specs/acceptance/001-mvp-v1/` — **17 `.feature` files, 1 `Feature` per file, 240 scenarios**
(228 `Scenario` + 12 `Scenario Outline`, each outline counted once),
all with exactly one `When` (verified by parsing every file with `gherkin-official`).
The per-component Gherkin lives **inline** in each spec (§7); the individual `.feature` files are
debt P-01 in `DECISIONS.md`.

## States

| State | Meaning |
|---|---|
| `draft` | written, not reviewed; the contract may change |
| `review` | under Spec Review (SDD step 2) |
| `approved` | frozen contract: the code must comply with it; changes ⇒ bump `version` |
| `implemented` | code delivered and verified against the ACs |
| `deprecated` | replaced (must state by which one) |

## How to regenerate a component (summary)

1. `git rm -r <implements paths>`
2. Give the implementer **only** `SPEC-000` + `SPEC-010` + the component's spec.
3. Implement respecting the signatures of §2 (public contract) — with no unspecified "improvements".
4. `pytest tests -k <component>` + `python scripts/spec_contract_check.py --spec <spec>`.
5. If the contract changed on purpose: bump `version` + `spec_revision` and add a note in §Notes.

## Rules

- A spec without a valid `implements:` is not considered approved (drift cannot be audited).
- The public contract (`§2`) is normative; the rest is guidance.
- Every decision with a real trade-off is recorded in `§Provenance` with date and source.
- **Conflicts between specs are resolved in [`DECISIONS.md`](./DECISIONS.md)**, never "in the code".
- **A fix is not finished if it was not propagated to `tasks.md` and to the Gherkin.** It is the
  most repeated gap across the two review rounds (R1: 19 open gaps, R2: 13 blocking, same pattern).