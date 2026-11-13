# specs/ — component index

Source of truth for `hifa`. The code is derived: every spec must allow its **component to be
regenerated from scratch**. Convention and flow: [`README.md`](./README.md). Template: [`_template.md`](./_template.md).

**Process status:** Spec Review round 1 (104 gaps) → 5 Fix rounds → re-reviews →
**11 approved specs and 1 draft** (SPEC-110, the release pipeline, at `version: 3` / `spec_revision: 6`
after the engine change of D-039 and the governed outcome classifier of D-040). Accepted debt P-01…P-09 in
[`DECISIONS.md`](./DECISIONS.md) (**41 decisions: D-001 … D-041**; P-12 closed by D-041). Reports in [`reviews/`](./reviews/).

**State order and state commands (D-034, D-035):** the canonical seed is
`Triage(1.0) · Todo(2.0, is_default=1) · Backlog(3.0) · In Progress(4.0) · Done(5.0) ·
Canceled(6.0)` — `Todo` sits **before** `Backlog`, and SPEC-100 §2.5 is the only template.
`hifa state list|create|update` is the CLI surface for it (SPEC-050 §2.5.6), and the board
columns can be dragged, persisted through `POST /_/states/{id}/position`
(SPEC-070 §2.6.5, R25). Existing teams are reordered with a command or a drag, **never** with a
data migration. Archiving or deleting a state is deferred (D-035): no verb is published for it,
because what happens to the issues that live in the state is undefined.

**Deferred on purpose (D-031):** `hifa team archive` is **not** part of the CLI surface —
`archive_team` is deferred with `update_team`, so SPEC-050 declares no row for it and SPEC-040 marks
the function "declared, not implemented". The same decision settles the list envelope: collections
are `{"items": [...]}` and the cursor keys exist only where the command paginates (`issue list`).

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
| 001 | **Vertical v1 spec (MVP)** | [001-mvp-v1.md](./001-mvp-v1.md) + [tasks](./001-mvp-v1.tasks.md) | the whole MVP (scope + AC) | 000…100 | approved |
| 010 | Data model | [010-data-model.md](./010-data-model.md) | `adapters/sqlite/migrations/*.sql`, `migrate.py`, `connection.py` | 000 | approved |
| 020 | Domain core | [020-domain-core.md](./020-domain-core.md) | `domain/**`, `tests/unit/domain/**` | 000, 010 | approved |
| 030 | SQLite adapter | [030-sqlite-adapter.md](./030-sqlite-adapter.md) | `adapters/sqlite/**`, `ports.py`, `tests/integration/db/**` | 010, 020 | approved |
| 040 | Application services | [040-application-services.md](./040-application-services.md) | `app/**`, `tests/integration/app/**` | 020, 030 | approved |
| 050 | CLI | [050-cli.md](./050-cli.md) | `cli/**`, `tests/e2e/cli/**` | 040 | approved |
| 060 | REST API | [060-api-rest.md](./060-api-rest.md) | `api/**`, `tests/e2e/api/**` | 040 | approved |
| 070 | Web dashboard | [070-web-dashboard.md](./070-web-dashboard.md) | `web/**`, `tests/e2e/web/**` | 040, 060 | **partial** (stage 1 delivered: read-only board/list/detail + quick add + `/healthz`; contract closed by D-025…D-028) |
| 080 | Import / export | [080-transfer-csv-json.md](./080-transfer-csv-json.md) | `app/transfer.py`, `adapters/sqlite/transfer_sql.py` | 040 | approved |
| 090 | FTS5 search | [090-search-fts.md](./090-search-fts.md) | `app/search.py`, `adapters/sqlite/reads.py::search` | 010, 040 | approved |
| 100 | Config and bootstrap | [100-config-bootstrap.md](./100-config-bootstrap.md) | `config.py`, `app/teams.py::bootstrap`, `cli/commands/db.py` | 010, 040 | approved |
| 110 | Semantic release pipeline | [110-release.md](./110-release.md) | `.github/workflows/release.yml`, `.github/release/tag-message.sh`, `.github/release/assert-outcome.sh`, `.releaserc.json`, `package.json`, `package-lock.json`, `CHANGELOG.md`, `.github/signers/**`, `pyproject.toml` (the `version` literal and the version-producing keys, D-038/D-039), `src/hifa/__init__.py`, `tests/unit/test_version.py`, `tests/e2e/test_release_contract.py`, `tests/e2e/test_release_outcome.py`, `tests/fixtures/release/*.log` | 000, 050 | draft (`version: 3`, `spec_revision: 6`: npm `semantic-release` engine, literal version, `v0.1.0` bootstrap — D-039; per-step secrets, governed and tested outcome classifier — D-040) |

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

`specs/acceptance/001-mvp-v1/` — **17 `.feature` files, 1 `Feature` per file, 106 scenarios**,
all with exactly one `When` (verified with `gherkin-official 42.0.1`).
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