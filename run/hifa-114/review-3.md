# P2 delta spec-review — HIFA-114 (round 3, final)

Under review: `specs/070-web-dashboard.md` at HEAD (`5f3b62c`), delta `git diff b2f4792..HEAD`
(fix round `5f3b62c`, `spec_revision` 34 → 35, `version` 12), plus `specs/DECISIONS.md` D-080.
Judged from the artifacts only.

VERDICT: FAIL

Counts (measured, not taken): **10 requirements** (`REQ-F070-18` … `REQ-F070-27`, `:2237-2246`),
**10 scenarios** in the HIFA-114 block of §7 (`:3316` … `:3378`), **91 scenarios / 0 outlines / five
manual** (four at `:3169` + one at `:3377`) — §8 (`:3410`) and §11 (`:3469`) agree. Budget: §2.6.11
measures **7,949** characters from its heading to `## 3.` (`wc -m` on that slice; it was 7,976 at
`b2f4792`), cap 8,000. Nine of the ten findings in `review-2.md` close. The tenth — the one that
round 2 filed first — is still open, and the fix round wrote the two writers it named into one
sentence rather than choosing between them.

Note on the count: `review-2.md` carries **ten** findings (three MAJOR, seven MINOR), not nine; the
brief says nine and §11 (`:3455`) says "three MAJOR and six MINOR". Ten are touched in this delta and
none is skipped, so the work is complete on that axis — only the two counts are wrong (finding 6).

## Half 1 — closure

1 **OPEN** — the panel's `description` still has two writers. The open question is gone and §2.6.3,
§2.1.3, R27 and §2.6.6 now all name `?view=field` for the field control; but §2.6.3 (`:1587-1591`)
keeps the panel's whole-form editor in the same breath — "The panel's edit form is the same shape and
keeps them too — `method="post"` and `action="/{team}/issue/{identifier}"`, plus its own `hx-patch`,
`?view=field` on a field control (§2.6.11) and `?view=panel` on the form itself (D-080)" — §2.1.3
(`:168`) gives that submit its trigger ("a whole-form submit from the panel (§2.1.3)"), and that form
carries the same seven names, `description` included (`partials/issue_form_fields.html:14`). So the
panel renders a `description` textarea in its form posting `?view=panel` **and** a `description`
control posting `?view=field` (`:2189-2201`) — two editors and two answers for one field, which is
finding 1 of round 2 verbatim. It cannot be read as the panel's no-JS fallback either: `:2227` says
"the panel is none [no no-JS path], since `/_/` answers `400` without `HX-Request` (R5)".

2 CLOSED — one path, stated once: `:2225-2229` "Without JS the **page form** is the only path: the read
control's `href` opens `/{team}/issue/{identifier}`, whose form of §2.1.2 posts any subset of the seven
and answers `303` (A2, REQ-F070-25) - the panel is none, since `/_/` answers `400` without `HX-Request`
(R5)"; `REQ-F070-25` (`:2244`) carries the same single path; the scenario at `:3364-3368` posts all
seven names and asserts all seven. (Its `Given` is defective — finding 3.)

3 CLOSED — the row is now buildable and carries every piece finding 3 asked for: `§6:2582` answers
`422` with "the **panel** of §2.1.4's read with `include_trashed=True` (one of R29's three consumers,
so no fourth reads the trash) re-rendered with the sentence …, `HX-Retarget: #panel` and
`HX-Reswap: innerHTML`", and its effect column says "a trashed issue renders no control … and no
`.is-invalid` field is created"; `§6:2588` defers the `?view=field` body to it ("the `?view=field` body
is the row above, so one request has one answer"); `§2.1.3:214` carries `HX-Retarget` for this body;
`§6:2581` is the only row for both methods of the unknown field. (Two other sentences still promise
`.is-invalid` for that refusal — finding 2.)

4 CLOSED — ten and ten, one each: `REQ-F070-18…27` (`:2237-2246`) ↔ §7 `:3316`, `:3323`, `:3329`,
`:3336`, `:3343`, `:3350`, `:3357`, `:3364`, `:3370`, `:3378`, carrying `one_field_one_write`,
`both_surfaces_same_set`, `field_set_is_closed`, `clearing_is_a_value`, `field_fragment`,
`answer_is_the_field`, `refusal_keeps_the_field`, `no_js_still_edits`, `test_field_layout.py`, and the
hand check. `REQ-F070-23`'s two scenarios are one (`:3350`) and `REQ-F070-26` has its own (`:3370`).
Nine of the ten name their command and "the repository root" in the last step; the tenth is the manual
check (`:3382`), which has no command to place in a directory, and the table says "by hand".

5 CLOSED — the mirror has a selector and a test: `§2.6.6:1773-1777` "Two selectors, one mechanism, two
carriers: `.no-js [data-js-only] { display: none }` … and its mirror `.js [data-no-js-only] { display:
none }` … one static test reads the stylesheet and pins both selectors (REQ-F070-26,
`tests/unit/web/test_field_layout.py`)", `REQ-F070-26` (`:2245`) carries both, `§7:3370-3375` asserts
both.

6 CLOSED — `§2.6.6:1756` now states the two hosts and the seven fields ("the read control's `href`
opens the page … optimistic in place: one control per field, `?view=field`, on the detail page and in
the panel") and has dropped "`description` in the panel with a Save".

7 CLOSED — `§6:2581` "`GET /_/issues/{identifier}/field/{field}`, **or `PATCH
/_/issues/{identifier}?view=field`**, with a `{field}` outside the seven … `text/plain`: `unknown
field` for the first, **on both methods of the pair**" — status, body, media type and effect are in §6
for the write, not only in the requirement table.

8 CLOSED — `R8` (`:2311`) states the invariant and lets the attribute follow the host: "One `PATCH` in
flight per control … `this:replace` on the field control, which has no row (§2.6.11), and
`closest tr:replace` on a row's (§2.6.3) — the attribute follows the host, and no host leaves the guard
inert"; `§2.6.3:1598` keeps the row's attribute, `§2.6.11:2199` the field's. No literal `closest tr`
remains where there is no row.

9 CLOSED — `§2.3.4:759-765` documents both keys and answers the objection ("`label` the field's own
name for the control's `<label>`" … "`is_empty` is the one key a template may branch on … never
`display == \"-\"`, because a comparison in Jinja is logic (R2)"), and the example at `:766-773` carries
all six keys — `name`, `label`, `display`, `href`, `edit_url`, `is_empty`.

10 CLOSED — both scenarios now bite when inverted. `§7:3316-3322` asserts the six untouched values
(`description "draft", priority "low", state "Todo", assignee "administrator", due date
"2026-12-01"`, estimate 3), "the `updated_at` … moved once" and "exactly 1 row … with field `title`";
`§7:3336-3341` exercises `description`, `assignee`, `due_date`, `estimate`, `priority=none`, a
`title`-only save and the three `422`s with no history row. How I know they fail inverted: a save that
also wrote `due_date` or `assignee` now contradicts the `Then`; a save that wrote two history rows
contradicts "exactly 1"; a clearing treated as a no-op contradicts "are empty"; `priority=none`
answered `422` contradicts "its priority is `none`"; an empty `state` accepted contradicts "each
answer 422 and write no history row".

## Half 2 — regressions

- **§2.6.11** — the no-JS paragraph is now one path and the trash paragraph names a body, a read, a
  retarget and a swap; the seven-field and route paragraphs did not move. Two residual sentences
  (finding 2): the states table `saving, refused` (`:2223`) and `REQ-F070-24` (`:2243`) still say every
  refusal re-renders *that field* with `.is-invalid`, which the trashed `422` no longer does. The
  phrase at `:2215` ("the one read that may speak for a trashed issue") is loose against R29's three
  consumers, but it points at the panel row of §2.1.4 and does not contract anything.
- **§6** — the three rows now agree with each other, with R29 (`:2332`, the three §2.1.4 consumers),
  with `§2.6.11:2222` ("no control renders … no `.is-invalid` field is created" is the same fact), and
  with the `HX-Retarget` the inline-validation row (`:2580`) demands. Nothing else in §6 moved, and
  the `404` row still carries the trashed read's `404`.
- **§2.6.3 / §2.1.3** — this is where the regression is. `:1589-1590` and `:168` write the panel's
  whole-form `?view=panel` submit beside the field's `?view=field` control instead of choosing
  between them (finding 1), and D-080 (`:4995`) asserts "the panel keeps no second save control whose
  `Save` button would be a second write path for one value" while §2.6.3 keeps exactly that form and
  §2.1.3 gives it a trigger.
- **§2.6.6** — internally consistent, and its "With JS" cell ("one control per field, `?view=field`,
  on the detail page and in the panel") is the sentence that contradicts the panel form kept by
  §2.6.3 — the same defect, read from the other side.
- **R8 / R27** — no breakage. R8 leaves no literal `closest tr` outside a row; R27 (`:2330`) names "the
  row, the card, the field or the panel after an edit", `?view=field` for one field on either surface,
  `?view=panel` "for a body that asks for the panel". The phrase is the hinge of finding 1: the shipped
  `.feature`'s body is one field, not a body that asks for the panel.
- **§2.3.4** — six keys, each documented, example in step; the `href` (page) / `edit_url` (fragment)
  split is right and R3 still holds.
- **§7 / §8 / §11** — 91 scenarios, 0 outlines, five manual, one scenario per requirement, Gherkin
  step keywords only, one `When` per scenario. §2.6.11 is 7,949 characters (cap 8,000). §11's new entry
  is accurate about the changes and wrong about the count (finding 6).
- **D-080 as a decision record** — it has the four parts the brief asks for: the invariant
  (`:4971-4974`), the losing texts quoted and named (`:4965-4968`, and the long form at `:4976-4983`),
  two reasons (`:4985-4997`), and pointers where the rule is now read (`§2.1.3:199`, `§2.6.3:1590` and
  `:1604`, `§2.6.11:2203`, `:2209`, `:2259`, `R27:2330`, `§11:3456`). §6's two rows and R8 carry no
  D-080 pointer, but both reach §2.6.11 in one hop and neither states a different rule. What it gets
  wrong is the twin: see finding 1.
- **Old rule as live text** — two files still state it without a supersession note:
  `specs/001-mvp-v1.tasks.md:542-544` ("the `view=panel` widening, because the description editor's
  Save must answer the `<aside>`") and `specs/DECISIONS.md:2033-2039` (D-054 §(b), "The panel needs a
  third `view`, because the panel is where the description is edited … So `PATCH` … accepts
  `view=panel`"), which is the live record D-080's "Loses" does not name (finding 5). The two
  `?view=panel` mentions left in §11 (`:3748`, `:3819`) are revision history and correctly dated.

## Findings

FINDING | MAJOR | specs/070-web-dashboard.md:1587-1591 | The panel has two contracted writers for each of the seven fields, which is finding 1 of round 2 unresolved: §2.6.3 keeps "The panel's edit form is the same shape and keeps them too … plus its own `hx-patch`, `?view=field` on a field control (§2.6.11) and `?view=panel` on the form itself (D-080)", §2.1.3:168 gives that submit its trigger ("a whole-form submit from the panel"), and the form carries the same seven names including `description` (`partials/issue_form_fields.html:14`), so the panel renders a `description` textarea posting `?view=panel` beside the `?view=field` control of §2.6.11:2189-2201 — one field, two editors, two answers, on one surface (with JS: the panel has no other mode, `:2227`). D-080 hides the conflict instead of recording it: it claims `16-dashboard.feature`'s `the panel edit answers the panel` pins a "body that asks for the panel rather than one field" (`DECISIONS.md:5001-5003`), while that scenario (`16-dashboard.feature:206-212`) PATCHes `?view=panel` with `{"description":"**final** draft"}` and asserts the `<aside>` — a one-field body answering the panel, which is the losing rule, and the request D-080 never gives a status to. So the corpus cannot be satisfied: keep the form and REQ-F070-18/19/23 have two answers; hide it and the shipped client's editor disappears with no sentence authorizing it; repoint the body and the shipped acceptance scenario fails. | Say in one place whether the panel's form still renders its seven inputs — if it does, name which gesture uses which route and what a one-field `?view=panel` body answers; if it is only the markup of the no-JS fallback, say so and mark it `data-no-js-only` like the row's hidden form — and record the `.feature` conflict in D-080 by name (`16-dashboard.feature:206-212`, its disposition in a follow-up ticket) instead of citing it as support.

FINDING | MINOR | specs/070-web-dashboard.md:2243 | `REQ-F070-24` still promises `.is-invalid` for every refusal — "A refusal re-renders that field with §6's sentence and `.is-invalid`" — and the states table at `:2223` says the same ("then the server's value, `.is-invalid`, §6's sentence"), while the trashed `422` now answers the panel and states "no `.is-invalid` field is created" (`§6:2582`); the same request class therefore has two described effects in the section that owns it. | Qualify both: a *value* refusal re-renders the field with `.is-invalid` (`REQ-F070-24`), a trashed one answers the panel (`REQ-F070-23`), and point the states row at the two.

FINDING | MINOR | specs/070-web-dashboard.md:3365 | The no-JS scenario's `Given` sets one field twice — "the issue \"ENG-13\" in the state \"Todo\" with title \"Fix login\" and state \"Backlog\"" — so the `Then`'s "each of those seven values is what \"ENG-13\" holds" proves `state` only if the fixture takes `Backlog`; if it takes the leading `Todo`, a page form that drops `state` passes, which is the assertion finding 2 of round 2 asked for. | One state in the `Given`: "in the state \"Backlog\" with title \"Fix login\"".

FINDING | MINOR | specs/070-web-dashboard.md:3339 | "its priority is \"none\"" is not a decidable assertion: `none` is `REQ-F070-21`'s *input* token (`{"priority":"none"}` clears), and nothing in the corpus says what a cleared priority reads as — `§2.3.4:761` makes `display` `"-"` when empty and `§2.5.7:1338` prints "cleared the priority" in history — so a test can write this step two ways and one of them fails. | Assert the state the corpus already declares ("its priority is empty", or "`display` is `-`"), or say once what a cleared priority renders.

FINDING | MINOR | specs/001-mvp-v1.tasks.md:542-544 | The old rule is still live text in two records: this note says "the `view=panel` widening, because the description editor's Save must answer the `<aside>`", and `specs/DECISIONS.md:2033-2039` (D-054 §(b)) says "The panel needs a third `view`, because the panel is where the description is edited … So `PATCH` … accepts `view=panel`" — the rationale D-080 retires, with no supersession note and no place in D-080's "Loses" list. | Add the pointer each record needs — D-080's `Loses` line names D-054, and both sentences read "superseded by D-080" — or quote the retired reason as retired.

FINDING | MINOR | specs/070-web-dashboard.md:3455-3470 | The new note's count is wrong: "the delta review's three MAJOR and six MINOR close", where `review-2.md` carries three MAJOR and **seven** MINOR (ten findings, `review-2.md:52-70`), and this delta touches all ten. The brief for this round repeats the same figure, so the miscount is in the record, not only in the note. | Say "three MAJOR and seven MINOR" (or "ten findings") so the revision note and the review it answers agree.
