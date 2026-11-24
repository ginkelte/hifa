# P2 delta spec-review — HIFA-114 (round 4)

Under review: `specs/070-web-dashboard.md` at `3b0358f` (fix round `3b0358f`, `spec_revision` 35 -> 36,
`version` 12), delta `git diff 5f3b62c..HEAD`, plus `specs/DECISIONS.md` D-080 rev 2, D-054 §(b) and
`specs/001-mvp-v1.tasks.md` rev 18. Judged from the artifacts only.

VERDICT: PASS

Counts (measured, not taken): **10 requirements** (`REQ-F070-18` … `REQ-F070-27`, `:2246-2255`) and
**10 scenarios** in the HIFA-114 block of §7 (`:3325`, `:3332`, `:3338`, `:3345`, `:3352`, `:3359`,
`:3366`, `:3373`, `:3379`, `:3387`); **91 scenarios / 0 outlines / five manual** in §7 (four at `:3178`,
one at `:3386`) — §8 (`:3419`) and §11 (`:3481`) agree. Budget: §2.6.11 measures **7,991** characters
from its heading to `## 3.` (`wc -m` on that slice), cap 8,000: it complies with nine characters to
spare.

All six findings in `review-3.md` close (1 MAJOR, 5 MINOR). The MAJOR closes by *choosing*, not by
reconciling: the panel's whole-form editor is retired, `?view=panel` is a contracted value with no
client, and the `.feature` that pins the retired behaviour is named in D-080 as a loser with its
disposition. Three residuals are MINOR and do not fail this gate.

## Half 1 — closure

1 CLOSED (MAJOR) — one writer, and the conflict recorded. `§2.6.3:1593-1598` now says the opposite of
what the finding quoted: "The panel renders no whole-form editor: it is a host of the per-field control
of §2.6.11 — `?view=field` on each of the seven — and of nothing else, so it carries no `<form>` around
the seven, no `hx-patch` of its own and no `Save` (D-080)"; `partials/issue_form_fields.html` is left one
host, "the **detail page's** no-JS page form". §2.1.3's row (`:168`) loses "a whole-form submit from the
panel" from its trigger, so no trigger of the `PATCH` route is the form any more; `§2.1.3:194-204` gives
the value's disposition in the route's own section ("**has no client, and is superseded by D-080**",
naming the two shipped requests that still send it and the implementation PR that retires them with
both); `§2.6.11:2216-2219`, `R27:2339` and `§2.6.9:1942-1944` carry the same rule from their own side
(the panel holds three controls, "the panel's edit *is* the seven per-field controls it renders"); and
D-080 (`DECISIONS.md:4970-4981`) names its losers **by reference with disposition** —
`16-dashboard.feature:206-212` (`the panel edit answers the panel`) "**retired by this decision**: its
replacement is the per-field acceptance scenario of SPEC-070 §7" and `16-dashboard.feature:657`
(`a visible issue's page and panel carry the four controls of the action group`), "whose panel half goes
with it … and the twin follows in the same implementation PR". Both line ranges check: `:206` is that
scenario's `Scenario:` line and `:657` is the four-controls one. The `.feature` is not cited as support
any more; it is cited as superseded, which is what the finding asked for.

2 CLOSED — `REQ-F070-24:2252` now splits the classes: "A refused *value* re-renders that field with
§6's sentence and `.is-invalid`, the typed value dropped; a *trashed* issue answers the panel
(REQ-F070-23)", and the states row `:2232` says the same ("`.is-invalid` and §6's sentence for a refused
*value*; a *trashed* issue answers the panel instead (REQ-F070-23)"). Both agree with `§6:2591` ("no
`.is-invalid` field is created") and with the `HX-Retarget` the inline-validation row demands.

3 CLOSED — the no-JS scenario's `Given` sets `state` once: `§7:3374` "Given the issue \"ENG-13\" in the
state \"Backlog\" with title \"Fix login\"", and the `When` posts `state=Todo`, so a page form that drops
`state` contradicts the `Then` instead of passing.

4 CLOSED — the clearing scenario no longer asserts the input token: `§7:3348` "the description,
assignee, due date, estimate and priority of \"ENG-13\" are empty". A `priority=none` treated as a no-op
now contradicts the `Then` (the value would still be `low`).

5 CLOSED — both records read the retirement: `specs/001-mvp-v1.tasks.md:544-545` "(**superseded by
D-080: the panel's whole-form editor is retired, `?view=panel` has no client, and each of the seven is
written through `?view=field`**)", with the rev-18 note at `:662-669` ("No checkbox is ticked by this
revision"); `specs/DECISIONS.md:2034-2036` puts "**Superseded by D-080**" above D-054 §(b) and keeps the
paragraph as "the record of it"; and D-080's `Loses` line names D-054 §(b) and `tasks.md:542-544`
(`:4973-4975`).

6 CLOSED — `§11:3484-3485` now reads "the delta review's three MAJOR and seven MINOR close", which is
what `review-3.md` carries, and the rev-36 note above it counts its own round correctly ("the delta
review's five minors", all five described in that entry).

## Half 2 — regressions

- **`?view=panel`'s disposition is stated, in the section that owns the route.** `§2.1.3:194-204` says it
  has no client, names the retired markup, names the two requests that still send it, says what it
  answers until they go ("target `#panel`, swap `innerHTML`, body the `<aside>`"), and points at D-080;
  `§2.6.11:2218-2219` and `R27:2339` restate it in one clause each. `§2.1.2:102` still lists `|panel`
  among the values of the edit route's fragment cell, but that cell is a route-value list, not a client,
  and §2.1.3 is where the value is dispositioned: no contradiction.
- **D-080 is a real decision record, and its four parts survive the revision.** Invariant
  (`:4984-4987`), the losing texts quoted and named (`:4970-4980`, with the long form at `:4989-4996`),
  two reasons (`:4998-5010`), pointers everywhere the rule is now read (`§2.1.3:199`, `§2.6.3:1594-1596`,
  `§2.6.9:1943`, `§2.6.11:2219`, `:2268`, `R27:2339`, `§11:3468-3470`). `Touches` is accurate:
  `§2.6.9` and `§7` did move, `34 -> 36` matches the header, `version` 12 stands.
- **The `.feature` twin conflict is recorded, not hidden.** Acceptance `16-dashboard.feature:206-212` and
  `:657` still carry the retired rule as live text, and both are named in D-080's `Loses` with their
  disposition inside the same implementation PR. That is the shape the finding demanded.
- **`§7`'s new negative assertion is satisfiable.** `:3070` "the second response does not contain
  \"action=\\"/ENG/issue/ENG-13\\\"\"" holds against the markup §2.6.11 now contracts, because a host
  renders the **read** state on load (`§2.6.11:2200-2203`, an `<a class="field-value">`), and the hidden
  `field-form-{field}` that carries a `method`/`action` only exists in the **edit** state the field
  fragment answers. The panel therefore has no `action=` on load once its form is gone, and the assertion
  bites any implementation that puts the form back. `§2.6.9:1952` and `:1954` still say "the four
  controls"; see finding 1.
- **`§6` untouched and consistent.** No row of §6 moved in this delta; the `404` row (`:2590`), the
  trashed `422` (`:2591`), the generic `ISSUE_TRASHED` row (`:2597`) and R29's three consumers
  (`:2341`) still agree with each other and with `§2.6.11:2221-2225`.
- **`§2.6.6`, `§2.3.4`, `§2.1.3.1`, R5/R8/R9** — no breakage. `§2.6.6:1763` gives the per-field control
  on both surfaces; the no-JS column is the page form only, which matches §2.6.11's "the panel is none".
- **Scenario mapping** — ten and ten, one each; nine name their `pytest -k` command *and* "the repository
  root" in the last step, the tenth (`:3387`) is the hand check and says "by hand", with no command to
  house in a directory. §7's block comment (`:3322-3324`) states the same rule the table now states.
- **The two scenarios that passed when inverted** (`:3325-3330`, `:3345-3350`) still fail inverted: a
  save that also wrote `assignee` or `due_date` contradicts the enumerated six values; a second history
  row contradicts "exactly 1"; a clearing treated as a no-op contradicts "are empty"; `priority=none`
  answered `422` contradicts "priority … empty"; an empty `state` accepted contradicts "each answer 422
  and write no history row".
- **Budget** — 7,991 of 8,000 characters. It complies; there is room for nine more characters, so a
  further sentence in §2.6.11 is not free.

## Findings

FINDING | MINOR | specs/070-web-dashboard.md:1879-1881 | §2.6.9's opening still places the edit write in the panel's footer — "The five writes of §2.1.2 — edit, move, archive, delete, restore — are reachable from two places and only two: the **panel** (`partials/issue_panel.html`, its footer) and the **detail page**" — while its own gate bullet (`:1942-1944`) and §2.6.3:1593-1598 say the panel's edit is the seven per-field controls of §2.6.11 in the panel body and that its footer keeps no form. Same defect one paragraph down at `:1952-1954`, where the edit to "the detail's edit form" now asserts that §5.7's panel footer is where the detail page's edit form lives, which §2.6.9:1985 and §5.7.1 contradict (the footer's copies are the move `<select>` and the two confirmation fragment forms). Neither sentence contracts a second writer, and `§7:3070` fails any implementation that follows them, which is why this is not the round-3 MAJOR again. | Change "the panel (`partials/issue_panel.html`, its footer)" to name the footer for move/archive/delete/restore and the panel body for the seven per-field controls, and drop "the detail's edit form" from the `:1952` list of controls that live in the panel footer.

FINDING | MINOR | specs/070-web-dashboard.md:2339 | R29's closing sentence still says "a visible issue fills it with the four controls (§2.3.4, §2.6.9, D-059)", which §2.6.9:1942-1944 now contradicts for the panel (three there, because the panel's edit is not a group control) and which the §7 scenario `:3058` renames around. The trash asymmetry R29 actually decides is unaffected. | "a visible issue fills it with the four controls on the detail page and three in the panel (§2.6.9, D-080)".

FINDING | MINOR | specs/DECISIONS.md:2066-2070 | D-054's `Verification` list still contracts `::test_the_panel_edit_answers_the_panel` (`view=panel` returns `<aside id="panel">`, not a document) as one of "the four tests … the names below are the contract that phase implements", with no supersession note — the `Superseded by D-080` line at `:2034` covers §(b)'s paragraph, not this list. It is the same class as round 3's finding 5 in the same file, and it tells a regenerator to write the test for a value D-080 has just retired; `tasks.md:666-669` discloses the retirement for its own reference but not this one. | Add "(retired by D-080: the panel keeps no whole-form editor; the implementation PR removes this name and its `?view=panel` assertion)" to the list entry, or strike the entry and say where it went.
