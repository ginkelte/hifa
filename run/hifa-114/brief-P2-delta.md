# Brief — P2 delta review, HIFA-114 (round 2)

Working copy: `/home/administrator/wt-hifa114`. **READ-ONLY**: write only `run/hifa-114/review-2.md`.

## Role

Spec reviewer, second round. A previous reviewer (not you) failed this section with 13 findings. A fix
round claims to have closed all 13. Your job is a **delta review** in two halves.

## Half 1 — closure, one line per finding

Read `run/hifa-114/review.md` (the 13 findings, verbatim) and `git diff 5fa6802..HEAD`
(the fix round). For each finding, verify in the current `specs/070-web-dashboard.md` that it is closed,
and say `n CLOSED` or `n OPEN` with the line that proves it.

## Half 2 — regressions

The fix round touched `§2.6.11`, `§6`, `§2.3.4`, `§2.1.4`, `§2.6.6` and `§7`. Look for what it broke:
- does every new `§6` row state a status, a body and an effect, and does none contradict `§6`'s existing
  rows or R29?
- do the ten new `§7` scenarios parse as Gherkin, name a command and a working directory, and map one
  to one to REQ-F070-18…27?
- does `§2.3.4`'s `issue_vm` dict now declare the `fields` key without contradicting R3?
- does the `href`-bearing read control survive R4 and `§2.6.6`, and does the `data-no-js-only` mirror the
  fix introduced contradict the `[data-js-only]` mechanism or its test?
- does `spec_revision` (34) match what changed, and is `version` correctly left at 12?
- any requirement whose scenario passes when the behaviour is inverted, or any requirement with no
  scenario.

## Settled by the orchestrator — do not re-open

- **Budget.** `§2.6.11` is 7,976 characters measured from its heading to the next heading of level 2 or
  higher; the cap is 8,000. It complies. The *total diff* exceeds 8,000 because the reviewer's own
  findings required rows outside the section; that sprawl is accepted and is not a finding.
- **Finding 12's mechanism.** The fix round rejected the reviewer's suggestion with an argument: the
  page form is the *no-JS* path, so marking it `[data-js-only]` would hide it exactly when it is the
  only way to edit. It declared a mirror attribute `data-no-js-only` in `§2.6.6` instead. Judge whether
  that argument holds and whether the mirror is stated once, in one home — not whether it matches the
  original suggestion.

## Output

`run/hifa-114/review-2.md`: `VERDICT: PASS` or `VERDICT: FAIL`, then the 13 closure lines, a short
regression section, and finally the findings block:
`FINDING | <BLOCKER|MAJOR|MINOR> | <path:line> | <what is wrong> | <the change required>`
A gate fails on one BLOCKER or one MAJOR. Report anything you cannot verify as a BLOCKER. Do not fix
anything.
