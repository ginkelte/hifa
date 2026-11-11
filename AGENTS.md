# AGENTS.md — rules for agents working on `hifa`

> This file is read **before touching anything**. If you are going to change structure, commands,
> stack, schema or conventions, **you update `AGENTS.md` and `README.md` in the same commit**.
> Stale documentation is a bug (see §8).

## 1. What `hifa` is

Local issue tracker: **CLI + SQLite + lightweight web dashboard**, built on a complete
issue-tracker model (teams with `key`/emoji, `issue.identifier` like `ENG-123`, priority 0-4, labels, assignee,
`due_date` without a time, sub-issues, cycles, projects, history). No account, no network,
no Node build step.

## 2. Mandatory reading order

1. `specs/README.md` — spec convention and regeneration flow.
2. `specs/INDEX.md` — component map, dependencies and status.
3. `specs/DECISIONS.md` — cross-spec resolutions (who wins on conflict). **Mandatory**: there are
   documented exceptions (e.g. the `hifa doctor` exit codes) that look like bugs and are not.
4. `specs/000-system-architecture.md` — layers, rules A1-A8, REQ-N, REQ-S.
5. The spec of the component you are going to touch (`specs/0NN-*.md`).
6. `specs/001-mvp-v1.md` + `specs/001-mvp-v1.tasks.md` — v1 scope and breakdown.

**Hard rule:** no component code is written without having read its spec. If the spec does not
exist or is `draft` with an incomplete contract, the spec is written and approved first.

## 3. Specs are the source of truth

- Code is **derived**. If a component has to be rewritten, it is regenerated from its spec.
- A spec is **never** edited so that the code "fits". The code is edited instead, or the spec
  change is made explicit with a `spec_revision` bump (and `version` if the public contract changes).
- The **public contract** (`§2` of every spec) is normative: signatures, endpoints, commands, enums.
- On a conflict between `001-mvp-v1.md` and a component spec, **the component spec wins**.
- A conflict between specs **is not resolved by writing code**: it is recorded in
  `specs/DECISIONS.md` (append-only, numbered `D-0NN`), the losing spec is adjusted with a
  `spec_revision` bump, and its §Notes points at the decision.

## 4. Architectural rules (operational summary of SPEC-000)

| Rule | What it means in practice |
|---|---|
| A1 | `cli/`, `api/`, `web/` → `app/` → `domain/` ← `adapters/`. Never imports "upward". |
| A2 | `domain/` does not import `sqlite3`, `fastapi`, `typer`, `jinja2`, `os`, `time`. Time enters through the `Clock` port. |
| A3 | Only `adapters/sqlite/` touches the database. |
| A4 | The CLI works without a server. `hifa serve` is the only thing that opens a socket. |
| A5 | FastAPI/uvicorn live in the `[web]` extra; the core does not import them. |
| A6 | Every CLI output goes through `cli/output.py` (table / `--json` / `--quiet`). |
| A7 | Domain errors are mapped in a single place per edge (exit codes / HTTP). |
| A8 | No ORM, no event bus, no DI container, no queues, no cache. Changing it requires an ADR + a version bump. |

## 5. Design principles (P1-P9)

| Id | Rule | How it is enforced |
|---|---|---|
| P1 | **CLI first** — Every capability is implemented, tested, documented and verified as a `hifa <verb>` (SPEC-050, with `--json`, exit codes and tests) BEFORE any dashboard route consumes it. The CLI is the reference surface; the dashboard is a consumer. | SPEC-050 §2.5 verb contract (name, `--json`, exit code) + tests under `tests/e2e/cli/`; §6 step 3 gates the edge order. |
| P2 | **Edge parity** — Anything the CLI can do or show, the dashboard can do or show, and no edge owns an exclusive feature. A capability that exists only in `web/` is a bug, not a feature. | `.importlinter` contract `web does not know the CLI` (D-032) plus the CLI-vs-web capability parity checker of T-18 (`scripts/check_edge_parity.py` + a CI step). |
| P3 | **Specs carry logic and UI/UX** — Every spec that touches an edge declares both the data contract and the interface behaviour: empty, loading and error states, keyboard path, breakpoints and mobile layout. A spec without its UI/UX section is not complete. | The mandatory-sections list of `specs/README.md` includes the UI/UX declaration (D-032) and spec review rejects a spec without it. |
| P4 | **One source of logic** — Rules live in `domain/`, use cases in `app/`; edges only transport, compose and format. If an edge needs a rule, the rule moves UP into the core — it is never copied. | `lint-imports` contracts `domain is pure (SPEC-000 A1/A2)` and `app does not know the edges (SPEC-000 A1/A5)`. |
| P5 | **Web and mobile by default** — One responsive HTML: nothing hover-only or drag-only (a keyboard/tap path always exists), touch targets >= 44px, critical flows work without JavaScript, no horizontal scrolling at 320px. | Declared by this principle; SPEC-070 §2.5 (visual design) must be revised to carry the 44 px target and the 320 px floor — tracked as T-19 (see `specs/001-mvp-v1.tasks.md`). |
| P6 | **Reversible by default** — Prefer archive, soft-delete and `--undo`; nothing destructive runs without an explicit `--yes`. There is no hard delete. | D-015 (no physical deletion) today; the `--yes` guard arrives with `issue delete` / `issue archive` / `issue restore` (T-06) and is asserted then. |
| P7 | **Machine-readable always** — Every command has a stable `--json` envelope and every view has a canonical URL (deep link). Agents drive the CLI and URLs, never the DOM. | The single formatter of `cli/output.py` (A6) + canonical URLs in SPEC-070 §2.1 (a ULID or lowercase identifier answers `303` to the `identifier` URL). |
| P8 | **Contracts, not conventions** — Every architectural or design rule is executable (`lint-imports` contract or a test) or it is not a rule. A new edge ships its contract in the same PR. | `.importlinter` carries one contract per existing edge boundary (4 today: domain, app, web, api); a rule without a contract or a test is debt and must be registered as a task. |
| P9 | **Headless core** — `domain/` + `app/` must work with no CLI, no API and no web (the `[web]` extra uninstalled). Edges are disposable: deleting one leaves the core green. | `tests/unit/domain/test_purity.py` + the `[web]` extra split (A5); `hifa doctor` runs with the extra uninstalled. |

P1-P9 are normative and bind every change, exactly like A1-A8. A PR that violates a principle is rejected.

## 6. Workflow

1. Branch: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`. **Never commit directly to `main`** — and
   as of 2026-11-11 that is no longer a convention but a repository ruleset (`main is PR-only`,
   D-036): a direct push is rejected by GitHub with *"Changes must be made through a pull request"*,
   `main` cannot be force-pushed or deleted, only squash merges are allowed, and the `test` and
   `firmas` checks must be green before the merge button works. See the "Contributing" section of
   the README for the recovery path if a push is rejected.
2. Spec first if the change is logic (new behaviour or contract change).
3. CLI before dashboard (P1): a capability without its CLI verb is not ready for the web edge.
4. New rule => new contract (P8): express it in `.importlinter` or in a test in the same PR.
5. Tests together with the code; no tests, no merge.
6. Before committing: `ruff check`, `ruff format --check`, `pytest`, `lint-imports`.
7. PR with: what changes, which spec it touches, how it was verified (real commands and their output).
8. If the change alters stack, structure, commands or schema → **update `AGENTS.md`, `README.md`
   and `specs/INDEX.md` in the same PR**.

## 7. Verification (canonical commands)

```bash
uv sync --extra web            # environment
ruff check . && ruff format --check .
pytest                         # unit + integration
pytest -m "not slow"           # fast
lint-imports                   # SPEC-000 A1/A2 contracts
python scripts/spec_contract_check.py --all   # spec <-> code drift
hifa doctor                    # database integrity
```

## 8. Documentation is always current

**`AGENTS.md` and `README.md` do not go stale: they are part of the deliverable.**

They are updated in the same commit when any of these changes:

- [ ] a CLI command (name, flag, `--json` output, exit code)
- [ ] an HTTP endpoint
- [ ] the database schema (table, column, index, view, migration)
- [ ] a dependency, Python version or install extra
- [ ] the folder structure or the name of a module
- [ ] an architectural rule or a convention
- [ ] the workflow (branches, checks, PR)

If an agent detects `AGENTS.md`/`README.md` out of date and does not fix it, the PR is rejected.
The automated check arrives with `scripts/spec_contract_check.py` (T-01/T-17): it compares commands
declared in the docs against the real Typer commands, and declared tables against `sqlite_master`.

## 9. Code and text conventions

- Python >= 3.11, `src/` layout, typing on every public function (`from __future__ import annotations`).
- Formatting: `ruff format` (line-length 100), imports sorted by `ruff`.
- SQL: always parameterized (`?`). Interpolating strings into SQL is forbidden. Table and column
  names in English, `snake_case`, not plural (singular: `issue`, not `issues`).
- ULID ids in `TEXT`; ISO-8601 UTC timestamps; calendar dates `YYYY-MM-DD`.
- Tests: `tests/unit`, `tests/integration`, `tests/e2e`. Names `test_<behaviour>`.
- **Repository artifacts are English-only (D-024):** code, docstrings, comments, error messages,
  CLI output, specs, docs, README, `AGENTS.md`, commit messages and PR titles/descriptions.
  Identifiers, tables, fields, endpoints and commands are English too. Only the conversation with
  Vichoko is Spanish. Historical review reports under `specs/reviews/` keep their original language
  as dated evidence. Existing specs and docs are translated as they are revised; new files and any
  modified text are English from now on (D-024, migration tracked as accepted debt in D-033).
- Commits: `type(scope): message` (`feat`, `fix`, `docs`, `test`, `refactor`, `chore`).

## 10. Prohibitions

- Do not invent fields, tables or constants that are not in `specs/010-data-model.md`.
- Do not add dependencies without justifying them in the PR (the core stays on `typer` + `pydantic`).
- No external assets/CDN: everything vendored in `src/hifa/web/static/vendor/`.
- No `print()` in `app/` or `domain/`; no business logic in Jinja templates.
- No `async def` in endpoints that touch SQLite (a sync driver blocks the event loop).
- **No moralizing framing**: no "fair use", "ethical use" sections or normative warnings in specs,
  code, docs or commit messages.
- Do not delete untracked files without a previous backup (Ley XV).
