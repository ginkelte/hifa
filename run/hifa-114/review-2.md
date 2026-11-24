# P2 delta spec-review — HIFA-114 (round 2)

Under review: `specs/070-web-dashboard.md` at `b2f4792`, delta `git diff 5fa6802..HEAD` (fix round `72ac7a9`),
against the 13 findings of `run/hifa-114/review.md`. Judged from the artifacts only.

VERDICT: FAIL

Counts: **10 requirements** (`REQ-F070-18` … `REQ-F070-27`), **10 new §7 scenarios** (81 → 91, measured),
**5 manual** (4 + 1) — both figures in §8 hold. Budget (`§2.6.11` = 7,976 ≤ 8,000) taken as settled.
Eleven of the thirteen findings are closed; 2 and 11 are not. Two of the fix round's own instruments — the
new `§6` `422` row and `REQ-F070-25` — carry defects the previous round would have called MAJOR.

## Half 1 — closure

1 CLOSED — two hosts, no card: `:2182-2184` "One partial, two hosts … on the detail page and in the panel … No card host (a non-goal; §2.6.4/§2.6.9 keep it control-free), no row host (§2.6.3 owns it)", plus the non-goal at `:2246`.

2 **OPEN** — `description` still has two routes. `:2200-2202` says one route for all seven, "`description` included, on both surfaces: one route per field, no exception"; but `:1593-1596` (§2.6.3) still rules that "`description` is **not** inline: it is edited in the panel with a `<textarea>` and a Save button", `:194` (§2.1.3) still says "`PATCH …?view=panel` exists because the panel carries the one editable field that is not inline — the description textarea", and `:2249` still asks the requester "Does the panel's `description` keep §2.6.3's `?view=panel` editor (§2.1.3) or take `?view=field`?" — the section both decides it and leaves it open, so a regenerated panel renders two textarea+`Save` controls for one field.

3 CLOSED — the write no longer borrows the read's answer: `:2205-2208` "The fragment reads … a trashed identifier is `404` … The write refuses it as §6 does for `edit` - `422` `ISSUE_TRASHED`", `REQ-F070-23` at `:2231` says `422`, `§6:2571` gains the row. (The new row's body is a separate defect — finding 3 below.)

4 CLOSED — scoped to the in-place surface: `:2228` "No `data-field` outside the seven, no `?view=field` write naming `project`, `milestone`, `cycle`, `parent` or `labels` (each `404`); label routes and chips untouched", and the §7 scenario at `:3315-3319` asserts the label `POST` still answers `200`.

5 CLOSED — split as required: `:2234` pins `app.css` (`min-height: 44px`, one column, inside `@media (max-width: 900px)`) to `pytest tests/unit/web/test_field_layout.py`, `:2235`/`§7:3358` put the keyboard and the 320 px check in a hand-check, and `:2222` says no selector decides it.

6 CLOSED — `:2185-2193`: an `<a class="field-value" data-field="…" href="/{team}/issue/{identifier}">` beside the `hx-get` (R4, §2.6.6's tested rule), the hidden form `id="field-form-{field}"` named by `form=` and `hx-include=` for a surface with no row, and `hx-sync="this:replace"`.

7 CLOSED — `§6:2570` gives the `404` its status, `text/plain` body and effect; `REQ-F070-22` at `:2230` names `200`, `404`, `400`, `403`.

8 CLOSED — `§2.3.4:758-770` declares `fields` inside the normative `issue_vm` dict and `:2194` points there; R3 is satisfied (the key is in §2.3 and the templates get it).

9 CLOSED — `§2.1.4:273` `/_/issues/{id}/field/{field}` → `app.boards.issue_detail(db, ref)`, empty `include`, no `include_trashed`, `404` before the read for an unknown `{field}`, and the `PATCH` pointed at the `update_issue` row (`:279`).

10 CLOSED — `:2169-2170` "an empty `state` or `priority` is a refusal, not a clear - `422` `invalid value`, the domain's message", kept by `REQ-F070-21` at `:2229` and asserted at `§7:3321-3325`.

11 **OPEN** — the scenarios exist (`§7:3303-3361`, ten of them, Gherkin-clean) but they do not map one to one: `REQ-F070-23` owns two (`§7:3333`, `§7:3339`) and `REQ-F070-26` owns none, and no scenario names its command or working directory — `:2221` puts both in the table ("its working directory in the row"), where `:2221`'s own "one verification each" is false for the tenth row.

12 CLOSED — the argument holds and the mirror has one home. `§2.6.6:1765-1769` states `data-no-js-only` once, "through the same class pair in the other direction. One mechanism, two carriers", and `§2.6.11:2217-2218` only cites it. Marking the page form `[data-js-only]` would indeed hide it exactly when it is the only way to edit. Residual: the mirror's selector is never written (finding 5 below).

13 CLOSED — `:2214` states the visible fact per layer: "in `--read-only`, and on a trashed or archived issue, no control renders and the value is text, with no `href`, `hx-get` or `data-field` (R18, R29)".

## Half 2 — regressions

- **§6** — both new rows carry a situation, a status, a body and an effect, and the `404` row is right against R29 (`no third surface reads the trash`). The `422` row is not: it contradicts the existing `ISSUE_TRASHED` row for the same request, and it needs a read the contract forbids (finding 3). Nothing else in §6 moved.
- **§7** — all ten scenarios parse as Gherkin (step keywords only, one `When` per scenario, `Scenario:` at the block's indent), the five manual ones are labelled consistently with §8, and the count `91`/`five` matches the block. What fails is traceability and one inverted-pass scenario (findings 2, 4, 10).
- **§2.3.4** — `fields` sits in the dict next to `assignees`, R3 is satisfied, and the example's `href`/`edit_url` split is the right one (page vs fragment). Two of its six keys are undocumented (finding 9).
- **§2.6.6 / the `href` control** — the read control survives R4 and §2.6.6's walker (an `<a>` with both `href` and `hx-get`), and the mirror does not contradict `[data-js-only]`: it is the same `<body class>` pair, read the other way, and the page form is genuinely the no-JS path. Only the selector's absence and the stale capability row are left (findings 5, 6).
- **`spec_revision` / `version`** — both right. Revision 33 is unshipped on this branch (`5fa6802` carries `version: 12`), so the fix belongs in one published revision pair: `spec_revision` 33 → 34 for the delta, `version` left at 12 for the change as a whole. The Notes' wording ("no route, field or status of §2 is new here") is loose — `§2.3.4`'s `fields` key and the trashed write's status are new *to those sections* — but the outcome is correct.
- **Requirements with no scenario / inverted-pass scenarios** — `REQ-F070-26` has no scenario (finding 4); `REQ-F070-25` is green with five of its seven fields unimplemented and with no panel path at all (finding 2); `REQ-F070-18` and `REQ-F070-21` are decidable only in part (finding 10).

## Findings

FINDING | MAJOR | specs/070-web-dashboard.md:2248-2249 | `description` still has two contracted routes: this section says `?view=field` for all seven "on both surfaces: one route per field, no exception" (`:2200-2202`), while §2.6.3 (`:1593-1596`) keeps "`description` is **not** inline: it is edited in the panel with a `<textarea>` and a Save button", §2.1.3 (`:194`) keeps `PATCH …?view=panel` as "the one editable field that is not inline", R27 (`:2320`) keeps "`?view=panel` answers the panel, for the description editor of §2.6.3", and the open question here re-opens the choice — so a panel regenerated from this section renders two editors and two answers for one field, which is the defect finding 2 named. | Delete the open question and pick the route for the panel's `description` in one place: either amend §2.6.3/§2.1.3/R27 so the panel's description is the `?view=field` control (and §2.6.6's row with them), or take `description` out of the seven for the panel and say at `:2200` and `REQ-F070-19/23` what the panel answers instead.

FINDING | MAJOR | specs/070-web-dashboard.md:2233 | `REQ-F070-25` states a behaviour the contract makes impossible: "Without JS both surfaces edit all seven through the page form and a `303`" — the panel is `GET /_/issues/{identifier}/panel`, a `/_/` fragment that answers `400 fragment requires htmx` without the header (R5, `:2298`), is opened only by `hx-get` + `hx-push-url` (`:166`), and `#panel` is `base.html`'s empty slide-over on a page load (§2.2), so a reader without JS is never in the panel; and the scenario at `:3351-3356` posts two fields to one surface, so it stays green while five of the seven fields or the whole panel path are missing. | State the no-JS path once — the page form, reached from the read control's `href` — and make the scenario post all seven field names to the page form and assert each one landed, or delete "both surfaces" from the requirement.

FINDING | MAJOR | specs/070-web-dashboard.md:2571 | The new `422` row cannot be built and contradicts two rows it sits beside: it answers a trashed `PATCH …?view=field` with "that field re-rendered from the read model", but the field read for a trashed issue is the `404` of the row above (`:2570`, §2.1.4 `:273`, `no include_trashed`), and R29 (`:2322`) makes the three `§2.1.4` rows that pass `include_trashed` "its only consumers", so the write that re-renders the value it refuses would be a fourth; the generic row for the same request (`:2577`, `edit … ISSUE_TRASHED`) still answers "the page (or the panel) with the message", i.e. one request with two bodies; "the field stays open" contradicts `§2.6.11:2214` ("on a trashed … issue, no control renders and the value is text, with no `href`, `hx-get` or `data-field`") and `:2207` ("the field back with `.is-invalid`"); and the body omits the `HX-Retarget` that the "Validation in a fragment / inline" row (`:2569`) makes mandatory for a fragment `422`. | Amend `:2577` to name the `?view=field` answer as its own case, say which permitted read produces the body (the page's or the panel's own call, one of R29's three) or answer with the panel re-rendered like every other trashed write, replace "the field stays open" with what a trashed surface actually shows, and carry `HX-Retarget` as the other fragment `422`s do.

FINDING | MINOR | specs/070-web-dashboard.md:2221-2235 | The ten scenarios do not map one to one to `REQ-F070-18…27`: `REQ-F070-26` (the `app.css` contract) has no scenario while `REQ-F070-23` has two (`§7:3333`, `§7:3339`), and no scenario names the command or the working directory its requirement gives (`:2221` says "its working directory in the row", where the tenth row has none), so `-k one_field_one_write` and "one save carries one field" are joined only by a comment's inference. | Give `REQ-F070-26` its scenario, give the two `REQ-F070-23` scenarios one id each (or fold them), and write the `-k` name and the working directory into each scenario's first or last step so the pair is decidable without the table.

FINDING | MINOR | specs/070-web-dashboard.md:1765-1769 | The mirror attribute's declaration is stated nowhere: the primary carrier's rule is quoted verbatim (`.no-js [data-js-only] { display: none }`) while the mirror is prose — "through the same class pair in the other direction" — so `app.css` gains a rule the spec never writes, and no test pins either carrier (P8: a rule without a contract or a test is debt). | Write the selector (`.js [data-no-js-only] { display: none }`) or point at the §2.5/§2.4 block that owns `app.css` declarations, and give both carriers the one static test that reads the stylesheet.

FINDING | MINOR | specs/070-web-dashboard.md:1748 | §2.6.6's capability table still describes the no-JS edit as "the row's own control posts the hidden page form of §2.6.3; `description` in the panel with a Save", which the same section's new paragraph and §2.6.11 (`:2182-2187`, two hosts, `description` through `?view=field`) no longer say. | Restate that row as the two contracted hosts and the seven fields, and drop "`description` in the panel with a Save" if finding 1 keeps `description` on `?view=field`.

FINDING | MINOR | specs/070-web-dashboard.md:2228 | `REQ-F070-20` contracts `404` for a `?view=field` write naming a field outside the seven, and §6 has no row for it: the new `404` row (`:2570`) covers only the `GET …/field/{field}`, so the write's status is inferable from a requirement table and nothing else — the defect finding 7 was fixed for the read. | Add the `?view=field` unknown-field row to §6 (status, `text/plain` body, effect) or fold it into the existing `404` row.

FINDING | MINOR | specs/070-web-dashboard.md:2301 | §2.6.11 substitutes `hx-sync="this:replace"` and cites R8 (`:2192`), but R8 still prescribes the mechanism verbatim — "One `PATCH` in flight per row (`hx-sync=\"closest tr:replace\"`)" — so the rule and the section it is cited for disagree on the attribute, and on the detail page and in the panel a literal `closest tr` matches nothing and the in-flight guard is inert. | Amend R8 to state the invariant ("one `PATCH` in flight per control, `this:replace` where there is no row") instead of one attribute, and keep §2.6.11 as the substitution.

FINDING | MINOR | specs/070-web-dashboard.md:758-770 | `issue_vm.fields`'s dict declares six keys but the comment documents four: `label` and `is_empty` have no stated reader, and `is_empty` duplicates information `display` already carries ("`-` when empty"), which under R2 leaves a key a template may branch on with no rule to branch by. | Document `label` and `is_empty` (who reads them, and why `display == "-"` is not enough) or drop `is_empty` from the dict.

FINDING | MINOR | specs/070-web-dashboard.md:3304-3308 | Two scenarios are green when their requirement is inverted: `REQ-F070-18`'s asserts one of the six fields that must keep their values (the estimate) and never asserts "`updated_at` moves once", so a save that wipes `due_date` or `assignee` passes; `REQ-F070-21`'s exercises `assignee`/`state`/`priority` only, so the `description`, `due_date`, `estimate` clearings, `priority=none` and "an absent field changes nothing" are unasserted. | Extend the two scenarios to every field and every claim the requirement names — the seven values before and after, one history row and one `updated_at` move, and each of the four clearings plus `priority=none`.

## Notes for the fix round

- Findings 1 and 3 are the same class the previous round failed on: a field or a request with two contracted answers. Fixing one of them by deleting a sentence in one section will move the contradiction, not close it — the sections that survive must be named.
- `§2.6.11` itself is internally consistent everywhere else, and the budget is not a problem; every MINOR above is a one- or two-line amendment.
