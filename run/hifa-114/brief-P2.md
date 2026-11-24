# Brief — P2 spec-review, HIFA-114

Working copy: `/home/administrator/wt-hifa114`. **READ-ONLY**: do not create, modify or delete any file except `run/hifa-114/review.md`.

## Role (SDD v3, `roles/spec-reviewer.md`)

You decide whether a spec is sufficient to implement against. You read only. You emit one verdict and a findings block. You did not write the spec and you do not fix it.

## Procedure

1. Read the intent, then the spec section. Every requirement must trace to a sentence of intent.
2. For each requirement ask the only question that matters: can I write a failing test for this without asking anyone? If not, it is not testable and it fails.
3. Check each scenario names a command and a working directory, and that the command could run.
4. Check the non-goals contradict nothing in the requirements.
5. Apply the knowledge nodes below as the criteria for steps 2 and 3. Apply them, do not paraphrase them.
6. Emit the verdict.

## Output contract

`VERDICT: PASS` or `VERDICT: FAIL`, then the findings block as the last thing in the reply and in the file:
`FINDING | <BLOCKER|MAJOR|MINOR> | <path:line> | <what is wrong> | <the change required>`
One claim per line. A gate fails on one BLOCKER or one MAJOR and passes with any number of MINOR. Never fail on style alone.

## Injected knowledge

[K-03-spec-contains-no-implementation]
No function bodies, no config files, no schema drafts. If the spec shows how, a reviewer spends
attention on style instead of on meaning, and an implementer copies the sketch instead of solving the
problem. Quoted literals are not implementation: they are contract.

[K-01-falsifiable-or-not-a-requirement]
If you cannot write a test that fails when the behaviour is inverted, you have written a goal.
Goals belong in the intent page. A requirement states something a stranger could check without
asking you what you meant.

[K-04-mechanize-the-checkable]
If a rule can be decided by a command, a reviewer must not be the one deciding it. Move it to the
lint and let the reviewer spend its budget on the part no command can answer: whether the meaning is
right.

[K-08-a-test-that-survives-inversion-tests-nothing]
Take the rule, invert it, re-run. If the suite stays green, the test asserts a shape or a mock, not
the behaviour.

[K-09-mutation-before-coverage]
Coverage says the line ran. Mutation says a change to the line was noticed.

## What is under review

- `specs/070-web-dashboard.md` — `§2.6.11 Editing one field in place (HIFA-114)`, the `§2.2` inventory rows it touches, the `§2.6.11` row in the `§2.1` contract table, and the `§11` revision note. Diff: `git diff HEAD~1 HEAD`.
- `run/hifa-114/intent.md` — the page it derives from.
- `run/hifa-114/brief-P1.md` — the constraints the author was given, including decisions A1/A2/A3.

## Repository rules the section must not violate (read them yourself)

`AGENTS.md` §3 (a spec is never edited so the code fits; a contract change bumps `spec_revision`, and `version` if the public contract changes), §5 **P1 CLI first** and **P2 edge parity**; `specs/README.md` (mandatory sections for a spec that touches an edge); `specs/050-cli.md` §2.5 (`hifa issue edit`); `specs/DECISIONS.md` (a cross-spec conflict is resolved there, not in code).

## Specific risks this gate exists for — check each, do not skip

- **A requirement any implementation satisfies** ("the field is editable", "the UI is responsive").
- **A scenario that still passes when the behaviour is inverted** — e.g. an assertion that passes whether or not the write happened, or a selector that matches nothing and therefore asserts nothing.
- **A scenario whose command cannot run** in this repository as written, or that names a working directory the section only states once for a table.
- **A requirement with no scenario, or a scenario with no requirement.** Count them and state both numbers.
- **Implementation smuggled in as contract**: a `hx-*` attribute recipe, a template fragment's internals, a handler's shape, a JSON body beyond what is contractual.
- **A field set that contradicts itself**: the seven fields, the CLI flags that carry them, the `--clear-*` flags, and what an empty value means must agree everywhere the section states them.
- **P1/P2 violations**: any requirement that gives `web/` a capability the CLI does not have, or that leaves the two surfaces with different field sets.
- **The no-JS fallback**: the section claims the page form survives; check that no requirement makes the in-place path the only path.

Write the verdict to `run/hifa-114/review.md` and report it back. Report anything you cannot verify as a BLOCKER and emit no verdict about it.
