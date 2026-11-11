# specs/ — per-component versioned specifications

This folder is the **source of truth for the system**. The code is a derived artifact:
if a component is deleted, corrupted, or has to be rewritten in another language, it is
regenerated from here. That is why every spec is **self-sufficient**: it does not depend on
reading the code to be understood.

## Golden rule

> If an implementer (human or agent) with **only the repo contents + its spec** cannot
> rewrite the component from scratch and pass its acceptance criteria, the spec is incomplete.

## One file per component

| # | File | Component | Layer |
|---|---|---|---|
| 000 | `000-system-architecture.md` | System architecture, layers, dependency rules | — |
| 010 | `010-data-model.md` | SQLite schema, migrations, views, ranking | persistence |
| 020 | `020-domain-core.md` | Entities, enums, rules, ranking, identifier | core |
| 030 | `030-sqlite-adapter.md` | Connection, UoW, repositories, migrator | adapter |
| 040 | `040-application-services.md` | Use cases, commands, transactions | application |
| 050 | `050-cli.md` | Command surface, output, exit codes | entry |
| 060 | `060-api-rest.md` | Endpoints, schemas, OpenAPI, HTTP errors | entry |
| 070 | `070-web-dashboard.md` | Jinja views, htmx fragments, assets, command palette | entry |
| 080 | `080-transfer-csv-json.md` | CSV and JSON import/export (CSV interchange) | application |
| 090 | `090-search-fts.md` | FTS5 search, triggers, fallback | application |
| 100 | `100-config-bootstrap.md` | Config resolution, `hifa init`, `hifa doctor` | support |

## Mandatory frontmatter

```yaml
---
id: SPEC-020
title: Domain core
component: domain
status: draft            # draft | review | approved | implemented | deprecated
version: 1               # bumped when the public contract changes
spec_revision: 1         # bumped on any correction
depends_on: [SPEC-000, SPEC-010]
implements:              # globs this spec governs (for the drift check)
  - src/hifa/domain/**
  - tests/unit/domain/**
acceptance: specs/acceptance/020-domain-core.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
---
```

`implements:` is what makes **drift** detectable: if code under those globs changes without the
`spec_revision` changing, the CI gate flags it (see §Drift).

## Mandatory sections of every spec

1. **Responsibility and boundaries** — what it does and, explicitly, **what it does NOT do**.
2. **Public contract** — exact signatures, endpoints, commands, output formats. No vague prose.
3. **Data it touches** — tables/columns, with invariants.
4. **Business rules** — numbered (R1, R2…) and verifiable.
5. **Flow** — nominal sequence, step by step.
6. **Errors** — catalog and mapping per edge (exit code / HTTP status).
7. **Acceptance Criteria** — inline Gherkin (in addition to the `.feature` file when it exists).
8. **Regeneration checklist** — how to rebuild the component from scratch and how to verify it.
9. **Provenance** — where each decision comes from (measured benchmark, ADR, author decision).
10. **Out of scope** — what this component deliberately does not solve.
11. **Interface behaviour (P3)** — empty, loading and error states, keyboard path, breakpoints and
    mobile layout: mandatory for any spec that touches an edge (CLI, API, web) (D-032).

## Regeneration flow (the use case that justifies this folder)

```bash
# 1. Isolate the component
git rm -r src/hifa/domain tests/unit/domain

# 2. Give the implementer ONLY: specs/020-domain-core.md + specs/010-data-model.md
# 3. Implement until the spec's acceptance criteria pass
pytest tests -k domain

# 4. Verify that the public contract did not change (imports, signatures, enum values)
python scripts/spec_contract_check.py --spec specs/020-domain-core.md
```

## Drift

- Every spec declares `implements:` with the paths it governs and the contract `version`.
- `scripts/spec_contract_check.py` (to be written in T-0x) compares:
  - enums and values declared in the spec vs. the ones in the code,
  - public function signatures,
  - declared endpoints vs. routes registered in FastAPI,
  - declared commands vs. Typer commands.
- Drift detected = red CI. A contract change requires bumping `version` in the frontmatter.

## Ground rules for coexistence

- Specs are **not edited so that the code fits**: the code is edited. Changing a spec is
  an explicit decision, with a `spec_revision` bump and an entry in the repo changelog.
- A spec never mentions "to be defined" in a contract section: if something is missing, it goes in
  `Open Questions` and the contract does not use it.
- Language: **English** everywhere — prose, comments, commit messages and PR descriptions, as well
  as identifiers, tables, fields, endpoints and commands. This replaces the previous convention
  (prose in Spanish) as of 2026-11-11. The eleven specs that predate it are translated **as they are
  revised**; new text is English from now on (D-033, accepted debt of D-024).
