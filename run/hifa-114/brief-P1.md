# Brief — P1 spec-author, HIFA-114

Working copy: `/home/administrator/wt-hifa114`, branch `feat/hifa-114-v3`. Work only there.

## Role (SDD v3, `roles/spec-author.md`)

You turn one page of intent into a specification a reviewer can accept or reject on evidence, then
deliver it as one commit. You write no product code.

## Procedure

1. Read `run/hifa-114/intent.md` first, without writing. List the behaviours it implies.
2. One behaviour per requirement, `REQ-` ids, in the vocabulary of the spec you are editing.
3. Quote every literal the delivery must contain, in backticks. An unquoted literal cannot be reviewed.
4. Write the task breakdown: ordered units, each with id, goal, exact paths, `done-when`.
5. One acceptance scenario per requirement, each naming a command and a working directory.
6. Write non-goals and open questions, each question with an owner.
7. Measure with `wc -m`; cut scope, never a requirement.
8. Commit once: `spec: <title>`.

## Output contract

The spec section added to `specs/070-web-dashboard.md`, plus a report naming: the measurement of the
added section, requirement count, scenario count, and what you deliberately left uncovered.

## Scope

You edit `specs/070-web-dashboard.md` only. You never touch product code, the CLI contract, another
spec, or `AGENTS.md`. A file outside your scope that must change is a finding, not an edit.

## Truths to confirm

- Every requirement has at least one scenario naming a runnable command.
- Every quoted literal is byte-identical to what the delivery must contain.
- The text contains no implementation body, no function body, no config file.
- The file is under budget with headroom, not at it.

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

## The request

"HIFA-114: quiero poder editar cada campo de una issue in place — en la página de detalle y en el
panel — sin tener que ir a un formulario aparte."

## Three assumptions you must treat as decisions, and record as such

The requester has not answered these yet. The orchestrator assumed them so the run could proceed; the
journal records their ownership. Write them into the spec as explicit decisions with their owner, and
say in one line what changes if the requester overrides them.

- **A1 Field set.** "Cada campo" means the seven fields the web edit form posts today
  (`title`, `description`, `priority`, `state`, `assignee`, `due_date`, `estimate`). Reference fields
  the CLI can edit but the form does not show (`project`, `milestone`, `cycle`, `parent`) and labels
  are **non-goals** of this delivery.
- **A2 No-JS path stays.** "Sin formulario aparte" means no *additional* form. The existing HTML form
  remains the fallback when JavaScript is off; removing it would waive `REQ-N006`/`P5` and needs a
  `D-0NN` decision, which this delivery does not make.
- **A3 Panel parity.** The panel edits the same field set as the detail page, because `P2` forbids one
  edge surface carrying a capability the other lacks.

## Repository rules you must obey (read them, do not paraphrase from here)

- `AGENTS.md` §2 (reading order), §3 (specs are source of truth; a spec is never edited so the code
  fits; a contract change bumps `spec_revision`, and `version` if the public contract changes), §4
  (A1-A8), §5 (**P1 CLI first**, **P2 edge parity**, **P3 a spec carries UI/UX**, **P5 web and mobile
  by default**: nothing hover-only or drag-only, touch targets >= 44px, critical flows work without
  JavaScript, no horizontal scroll at 320px).
- `specs/README.md` — the mandatory section list for any spec that touches an edge. A spec without its
  UI/UX declaration is rejected.
- `specs/070-web-dashboard.md` — the component you are extending. Read it entirely before writing: its
  `§2` public contract is normative and your section must not contradict it.
- `specs/050-cli.md` §2.5 — the `hifa issue edit` verb contract. **P1** means the CLI capability exists
  before the dashboard consumes it: name in the spec which verb already carries each field, and where
  it does not.
- `specs/DECISIONS.md` — if your section resolves a conflict between specs, it is recorded there as a
  new `D-0NN`; you may propose one, but you do not invent numbers without checking the last one.

## Hard constraints

- ASCII, English. Added section <= 8,000 characters (`wc -m` on the added text).
- One commit. Do not push. Do not modify code, tests, CI or `AGENTS.md`.
- If the intent page and the repository contradict each other, say so in your report and stop that
  thread — do not silently pick a side.

## Report back

Measurement of the added section, requirement and scenario counts, the spec_revision you bumped to,
which `D-0NN` you propose (if any), what you left uncovered, and every place where `P1` or `P2` forced
a requirement you would not have written otherwise.
