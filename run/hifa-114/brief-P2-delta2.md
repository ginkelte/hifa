# Brief — P2 delta review, HIFA-114 (round 3, final)

Working copy: `/home/administrator/wt-hifa114`. **READ-ONLY**: write only `run/hifa-114/review-3.md`.

## Role

Spec reviewer, third round. Round 1 failed with 13 findings; round 2 failed with 9 (11 of 13 closed).
A second fix round claims to close all 9 and adds a decision record. Verify, then stop looking for
style.

## Half 1 — closure

Read `run/hifa-114/review-2.md` (the 9 findings) and `git diff b2f4792..HEAD`. One line per finding:
`n CLOSED|OPEN - <the line that proves it>`.

## Half 2 — regressions, and one specific thing

The fix round touched `§2.6.11`, `§6`, `§2.6.6`, `§2.1.3`, `§2.6.3`, R8, R27, `§2.3.4`, `§7` and
`specs/DECISIONS.md`. Check for breakage in each, and specifically:

- **`D-080`.** Is it a real decision record: an invariant, the losing spec named, the reason, and a
  pointer from every place that changed? Does any file still carry the old rule as live text? Does the
  decision contradict a `.feature` twin that the fix round was not allowed to touch, and is that
  recorded rather than hidden?
- **The `422` row.** Does it now agree with the `404` row above it, with the generic `ISSUE_TRASHED`
  row, with R29's "three permitted reads", and with `§2.6.11`'s "no control renders"? Does it carry the
  `HX-Retarget` the inline-validation row demands?
- **`REQ-F070-25`.** Is the no-JS path now one path, and does its scenario post all seven and assert
  each?
- **The scenario mapping.** Ten requirements, ten scenarios, each naming its command and the working
  directory. Count them.
- **Two scenarios that passed when inverted.** Are they now inverted-failing? Say how you know.

## Then the verdict

`VERDICT: PASS` or `VERDICT: FAIL`, the closure lines, a short regression section, and the findings
block last:
`FINDING | <BLOCKER|MAJOR|MINOR> | <path:line> | <what is wrong> | <the change required>`

A gate fails on one BLOCKER or one MAJOR. **A MINOR does not fail the gate.** If the only findings you
have left are stylistic, stop: this is a spec that has been reviewed three times and the next
iteration is implementation, where the remaining ambiguity will be tested by code.

Report anything you cannot verify as a BLOCKER. Do not fix anything.
