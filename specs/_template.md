---
id: SPEC-XXX
title: <Component name>
component: <domain | persistence | application | entry | support>
status: draft
version: 1
spec_revision: 1
depends_on: [SPEC-000]
implements:
  - src/hifa/<path>/**
  - tests/<level>/<path>/**
acceptance: specs/acceptance/XXX-<component>.feature
owner: Ginkelte
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# SPEC-XXX — <Component name>

## 1. Responsibility and boundaries

**Does:** <2-4 lines, concrete>.

**Does NOT do (explicit):**
- <what a reader could assume it does and it does not>
- <idem>

## 2. Public contract

> This section is normative. An implementer must be able to write the component from here.
> Exact signatures, no `...` and no "etc.".

```python
# src/hifa/<modulo>.py
def funcion(arg: Tipo, *, kw: Tipo = default) -> Retorno: ...
```

| Symbol | Type | Stable | Notes |
|---|---|---|---|
| `funcion` | function | yes | v1 contract |
| `Clase` | class | yes | |

If the component exposes HTTP:

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/issues` | query params | `IssueListResponse` | 400 |

If it exposes a CLI:

| Command | Flags | Output (`--json`) | Exit codes |
|---|---|---|---|
| `hifa issue list` | `-p/--project`, `--mine` | array of issues | 0, 2, 3 |

## 3. Data it touches

| Table | Operation | Invariant |
|---|---|---|
| `issue` | INSERT/SELECT/UPDATE | `UNIQUE(team_id, number)` |

## 4. Business rules

- **R1:** <verifiable rule>.
- **R2:** <verifiable rule>.

## 5. Flow

```
1. ...
2. ...
```

## 6. Errors

| Domain error | When | CLI | HTTP |
|---|---|---|---|
| `NotFound` | nonexistent id/key | exit 3, stderr | 404 |
| `Conflict` | violates uniqueness | exit 4 | 409 |
| `Validation` | invalid input | exit 2 | 422 |
| `InvalidTransition` | illegal state change | exit 5 | 409 |

## 7. Acceptance Criteria

```gherkin
@SPEC-XXX
Feature: <area>
  Scenario: <name>
    Given <state>
    When <action>
    Then <observable result>
```

## 8. Regeneration checklist

1. Read `SPEC-000` + this spec in full.
2. Create `src/hifa/<path>` with the contract from §2 **without changing signatures**.
3. Cover R1..Rn with tests in `tests/<level>/<path>`.
4. `pytest tests -k <component>` green.
5. `python scripts/spec_contract_check.py --spec specs/XXX-<component>.md` with no drift.
6. No import forbidden by `import-linter`.

## 9. Provenance

- <source: measured benchmark / ADR / author decision with date>

## 10. Out of scope

- <what this component does NOT cover>
