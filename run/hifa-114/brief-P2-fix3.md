# Brief — P2 fix round 3, HIFA-114 (final)

Working copy: `/home/administrator/wt-hifa114`, branch `feat/hifa-114-v3`.

## The decision — made by the owner's delegate, not by you

**The panel keeps per-field controls only. Its whole-form editor is retired.**

Why it is coherent and not merely convenient: `§2.6.11:2227` already states the panel has no no-JS
mode, so the panel's whole form only ever served a JS reader, and the `?view=field` control replaces
it exactly. `D-080`'s invariant — one route per field, both surfaces, all seven — survives unchanged.

What follows from it, and you must write all four:

1. **`§2.6.3`** stops contracting a whole-form editor in the panel. The shared form partial
   (`partials/issue_form_fields.html`) stays, because the **detail page's** no-JS page form uses it;
   say that in one sentence where the form is described.
2. **`?view=panel` gets a disposition.** If no client reaches it after this change, say so explicitly
   and mark it superseded by `D-080` — a contracted route with no client, and no sentence saying it
   was retired, is the drift this whole exercise exists to prevent. If something still reaches it,
   name what.
3. **`D-080` names its loser by reference, not by approving it.** Its `Loses` line must name
   `specs/acceptance/001-mvp-v1/16-dashboard.feature:206-212` (`the panel edit answers the panel`) and
   state its disposition: retired by this decision, its replacement is the per-field acceptance
   scenario in `§7`, and the twin is updated in the implementation PR. The current text cites that
   scenario as *support*, which is the opposite of what it does.
4. **Supersession notes in the two records that still carry the old rule as live text**:
   `specs/001-mvp-v1.tasks.md:542-544` ("the `view=panel` widening, because the description editor's
   Save must answer the `<aside>`") and `specs/DECISIONS.md:2033-2039` (D-054 §(b)). Each reads
   "superseded by D-080", and D-080's `Loses` line names D-054.

## The five MINOR findings from `run/hifa-114/review-3.md`

- **`REQ-F070-24` and the states table** promise `.is-invalid` for every refusal, while the trashed
  `422` now answers the panel with no `.is-invalid`. Split it: a *value* refusal re-renders the field
  with `.is-invalid`; a trashed one answers the panel. Point the states row at both.
- **The no-JS scenario's `Given` sets `state` twice** ("in the state \"Todo\" with title \"Fix login\"
  and state \"Backlog\""), so its `Then` proves `state` only by accident. One state in the `Given`.
- **"its priority is \"none\""** is not decidable: `none` is an input token. Assert what the corpus
  already declares (`display` is `-`, per `§2.3.4:761`), or say once what a cleared priority renders.
- **The revision note's count is wrong**: "three MAJOR and six MINOR" — the review it answers carries
  **three MAJOR and seven MINOR, ten findings**. Correct the note to match the record.
- Nothing else.

## Rules

- You may edit `specs/070-web-dashboard.md`, `specs/DECISIONS.md` and `specs/001-mvp-v1.tasks.md`.
  **Not** the `.feature` twin (it is retired by the decision and updated in the implementation PR).
- Bump `spec_revision` in every spec you touch; `version` only if the public contract gains or loses a
  route.
- ASCII, English, one commit: `spec: HIFA-114 the panel keeps per-field controls only`.
- Report: the measurement of `§2.6.11` **with the command you ran and its boundary**, the disposition
  you gave `?view=panel`, the `D-080` `Loses` line as you wrote it, one line per finding, and anything
  you could not close.
