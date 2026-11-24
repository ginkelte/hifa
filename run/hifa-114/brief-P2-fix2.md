# Brief — P2 fix round 2, HIFA-114

Working copy: `/home/administrator/wt-hifa114`, branch `feat/hifa-114-v3`.

## Role

Spec author, second fix round. The delta reviewer failed the section with 3 MAJOR and 6 MINOR. You
close them by editing the spec — including, for the first one, `specs/DECISIONS.md`.

Read `run/hifa-114/review-2.md` first: it is your work list, verbatim.

## The one thing that changes the shape of this round

The `description` finding is not a wording defect. `§2.6.3`, `§2.1.3` and `R27` contract
`?view=panel` for the description editor; `§2.6.11` contracts `?view=field` for all seven. Two
sections of the same corpus now answer one request two ways, and `AGENTS.md §3` says exactly what to do
about that:

> "A conflict between specs **is not resolved by writing code**: it is recorded in
> `specs/DECISIONS.md` (append-only, numbered `D-0NN`), the losing spec is adjusted with a
> `spec_revision` bump, and its §Notes points at the decision."

So: **write the decision.** Check the last `D-0NN` in `specs/DECISIONS.md` and take the next number.
The decision states the invariant — a field's in-place control is one route, `?view=field`, on both
surfaces, for all seven — names `§2.6.3`'s panel-description exception as the loser, and records why
(one field with two answers regenerates two editors). Then adjust `§2.6.3`, `§2.1.3` and `R27` to
point at the decision, each with the `spec_revision` bump `§3` requires, and drop the open question in
`§2.6.11` that re-opens the choice.

If you conclude the decision should go the other way — the panel keeps `?view=panel` and `description`
leaves the seven — that is also a legitimate outcome, but then say so in the decision, and adjust
`§2.6.11`, its requirement and its scenario to match. What is not legitimate is leaving two answers.

## The rest

- **MAJOR, `REQ-F070-25`.** "Without JS both surfaces edit all seven through the page form and a `303`"
  is impossible: the panel is a `/_/` fragment that answers `400 fragment requires htmx` (R5), it is
  opened by `hx-get` and `#panel` is empty on load, so a no-JS reader is never in the panel. State the
  no-JS path once — the page form, reached from the read control's `href` — and make the scenario post
  all seven names and assert each landed.
- **MAJOR, the new `422` row.** It answers a trashed `PATCH ?view=field` with "that field re-rendered
  from the read model", but the field read of a trashed issue is the `404` above it (§2.1.4 has no
  `include_trashed`, R29 makes those rows its only consumers); the generic row for the same request
  still answers "the page (or the panel) with the message"; "the field stays open" contradicts
  `§2.6.11:2214`; and it omits the `HX-Retarget` the inline-validation row makes mandatory. Amend the
  generic row to name the `?view=field` case, say which permitted read produces the body, fix the
  "stays open" claim and carry `HX-Retarget`.
- **MINOR ×6**: the 1:1 scenario mapping (`REQ-F070-26` has none, `REQ-F070-23` has two, no scenario
  names its command or cwd); the mirror attribute's declaration is prose, not a selector, and no test
  pins either carrier; `§2.6.6`'s capability row still says the panel's `description` has a Save;
  `REQ-F070-20`'s write `404` has no `§6` row; `R8` still prescribes `closest tr:replace` where there is
  no row; `issue_vm.fields`'s dict documents four of six keys and `is_empty` duplicates `display == "-"`;
  two scenarios stay green when their requirement is inverted (`REQ-F070-18` checks one of six untouched
  fields, `REQ-F070-21` leaves four clearings and `priority=none` unasserted).

## Rules

- ASCII, English. `§2.6.11` stays under 8,000 characters, measured from its heading to the next heading
  of level 2 or higher — **state the command you measured with and its output**; a number without its
  boundary is not a measurement (this bit the orchestrator yesterday).
- One commit: `spec: HIFA-114 close the delta review findings`. Do not push.
- You may now edit `specs/070-web-dashboard.md` and `specs/DECISIONS.md`. Nothing else.
- Do not touch `specs/INDEX.md`, `001-mvp-v1*` or the `.feature` twins: their stale counts are a
  finding for their owner.

## Report back

One line per finding, closed or not and why. The `D-0NN` you wrote and whose spec lost. The
`spec_revision` of every file you bumped. The measurement with its command.
