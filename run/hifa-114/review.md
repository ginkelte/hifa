# P2 spec-review — HIFA-114

Under review: `specs/070-web-dashboard.md` at `f006f1d` (`git diff HEAD~1 HEAD`), against
`run/hifa-114/intent.md` and the constraints of `run/hifa-114/brief-P1.md`.

VERDICT: FAIL

Counts: **9 requirements** (`REQ-F070-18` … `REQ-F070-26`), **9 commands** in the same table, and
**0** of those nine scenarios in `§7`'s acceptance criteria (the section's table is their only home).
Every requirement has a command and every command has a requirement; nothing is unpaired.

What holds: the seven field names match the edit form of `§2.1.2`; each of the seven `hifa issue
edit` flags is quoted correctly (`-t`, `-d`, `-p`, `-s`, `-a`, `--due`, `-e`, `SPEC-050 §2.5.3`);
clearing agrees with the CLI's `--clear-description`/`--clear-assignee`/`--clear-estimate`/
`--clear-due`, `priority=none`, and the two that never clear; `spec_revision` 32→33 with `version`
11→12 is the bump `AGENTS.md §3` requires for a new public route; the `issue.edit` row of
`scripts/check_edge_parity.py` (`:177-181`) does need no change and the fragment allowlist line does
land with the route; A1/A2/A3 are recorded with an owner and a cost of being overridden; non-goals
and open questions exist. What fails is meaning: one of the seven fields has two incompatible routes,
one status contradicts `§6` and R29, one requirement deletes a capability that is in the contract
today, one host gains an editor the intent excludes, and one scenario cannot be decided by the
command it names.

## Findings

FINDING | MAJOR | specs/070-web-dashboard.md:2166-2167 | The partial is declared in **four** hosts "in two states" — the row, the card, the detail page and the panel — so board cards gain in-place field editing, which `run/hifa-114/intent.md`:25 puts in non-goals, which the section's own opening (:2140) and A3/REQ-F070-19 (two surfaces) do not claim, and which T-114.4's paths (:2220 — `issue_detail.html`, `partials/issue_panel.html`) do not deliver: no requirement, scenario or task owns the card. | Name the hosts that are contracted (detail page, panel, and the row `§2.6.3` already serves) and drop the card, or add the card's requirement, its scenario, its template path and its answer where `closest [data-field]` is the only target it has.

FINDING | MAJOR | specs/070-web-dashboard.md:2179-2180 | `description` is one of the seven (A1 :2147, field table :2159) and the field contract sends every field's edit state to `?view=field` answering one `span[data-field]` (:2171, :2178, REQ-F070-23 :2208), but :2179-2180 keeps `description` on `?view=panel`, whose answer is the whole `<aside id="panel">` (§2.1.3 :194-198, R27 :2298); on the detail page `#panel` is `base.html`'s empty slide-over (§2.2 :518), so a description save there swaps a panel into it and leaves the reader's control standing. | Say which route `description` uses on each surface and what its answer replaces, or take it out of the in-place set in A1, the field table and REQ-F070-19/21 and give the panel's textarea its own requirement.

FINDING | MAJOR | specs/070-web-dashboard.md:2181-2182 | "`view=field` reads the base `IssueDetail`, with no `include_trashed`, so a trashed identifier is `404`" gives a **write** the read's answer, and `§6`:2553 and R29 (:2300) state that `edit` on a trashed issue refuses `ISSUE_TRASHED` with `422`; the R29 citation is inverted — its `404` belongs to the read surfaces, its `422` to every write but `restore`. | Either answer a trashed `PATCH …?view=field` with `422 ISSUE_TRASHED` (and say so at REQ-F070-23), or add the `§6` row and the `D-0NN` that moves `edit` on a trashed issue from `422` to `404`; one request cannot have two contracted statuses.

FINDING | MAJOR | specs/070-web-dashboard.md:2205 | REQ-F070-20 makes `labels` "offered and accepted nowhere", contradicting `§2.1.3`'s `POST /_/issues/{identifier}/labels` and `DELETE …/labels/{label_id}` (:175-176), `§2.6.3`'s `labels` inline control (:1579-1580), R27, and the section's own non-goals at :2223 ("labels, whose chips the row of §2.6.3 keeps"); a literal implementation deletes a capability that is in the contract today. | Scope the requirement to the in-place surface — no `data-field` value outside the seven and no `?view=field` write names any of them — and leave the label routes and chips alone.

FINDING | MAJOR | specs/070-web-dashboard.md:2211 | REQ-F070-26's `pytest -k keyboard_and_320` cannot decide "reachable and saveable by keyboard" or "no sideways scroll": the test extra is `httpx` only (`pyproject.toml`:22-23) and `tests/e2e/web/test_revision.py`:9 records "this host has no Playwright", so a test of that name asserts markup or a CSS declaration and stays green when the keyboard path is removed (K-08); the section's own precedent (:2135-2136) says the 44 px target and the 320 px check are verified by hand in a browser. | Split the requirement: the decidable part (`min-height: 44px` inside `@media (max-width: 900px)` and the one-column block) as a static test of `app.css` beside `tests/unit/web/test_mobile_layout.py`, and the keyboard/scroll part as a hand-checked step naming the browser and both viewports, as `§2.6.9` does.

FINDING | MAJOR | specs/070-web-dashboard.md:2168-2169 | The read state is a `<span … hx-get …>` with no `href`, which R4 (:2275) and `§2.6.6`'s rule (:1745-1747, enforced by a test that walks the rendered HTML) forbid; and the edit state puts `hx-patch` on "the control of §2.6.3" while dropping the hidden form, `form="edit-{identifier}"`, `hx-include` and `hx-sync="closest tr:replace"` that `§2.6.3` (:1561-1578) makes mandatory — yet claims `Enter` saves (:2193), which needs a form owner, and one `PATCH` in flight (R8, :2279), whose target `closest tr` does not exist on the detail page or in the panel. | State the no-JS equivalent of the read control (an `href` beside the `hx-get`, or an explicit amendment of R4/`§2.6.6` for this element) and the control's form ownership and in-flight target on a surface with no row.

FINDING | MINOR | specs/070-web-dashboard.md:2174-2176 | The field fragment's `404` names no body and no media type, and `§6` (:2537-2566) gained no row for the four answers REQ-F070-22 counts, so "its four statuses" (:2207) has to be inferred from prose. | Add the `§6` row (status, body, effect) and name the four statuses in the requirement.

FINDING | MINOR | specs/070-web-dashboard.md:2172 | `issue_vm.fields` is declared here rather than in `§2.3.4`'s normative `issue_vm` dict (:724), against R3 ("every key declared in §2.3") and the regeneration checklist, so a reader who rebuilds from `§2.3` never learns the key exists. | Move the seven dicts into `§2.3.4` and leave a pointer here.

FINDING | MINOR | specs/070-web-dashboard.md:2181 | `§2.1.4` (:259-302) is the table that states the application call of every route; the field fragment's call (`issue_detail(db, ref)`, base read, no `include_trashed`) is stated only in prose in this section. | Add the `/_/issues/{identifier}/field/{field}` row to `§2.1.4`.

FINDING | MINOR | specs/070-web-dashboard.md:2154 | "`title` and `state` never clear" leaves an empty `state` undecided — `422`, no change, or a refused value with `§6`'s sentence — while REQ-F070-21 (:2206) tests only a blank `title`. | State the answer for an empty `state` (and an empty `priority`), so the clearing rule is decidable for all seven.

FINDING | MINOR | specs/070-web-dashboard.md:2198-2211 | The nine commands are `pytest -k` selectors with no given/when/then and no stated assertion, `§7`'s Gherkin (:2661) gained no scenario for any of REQ-F070-18…26, and "from the repository root" is stated once for the whole table. | Give each requirement a one-line scenario in `§7`'s shape with its own working directory, and keep the `-k` name as the test's name.

FINDING | MINOR | specs/070-web-dashboard.md:2192-2193 | A second mechanism hides a JS-only element — `data-page-form` with `.js [data-page-form] { display: none }` — where `§2.6.6` (:1751-1753) declares the one mechanism, `[data-js-only]` with `.no-js [data-js-only]`; a CSS declaration is also contract that belongs to `§2.5`/`§2.4`, not to a `§2.6` rule (K-03). | Use `[data-js-only]`, the mechanism `§2.6.6` already rules, and cite `§2.6.6` instead of quoting the declaration.

FINDING | MINOR | specs/070-web-dashboard.md:2188 | "none when layered (R18, R29)" is not decidable: it does not say which element is absent, on which surface, and does not separate `--read-only` from trashed. | Rewrite the row as the visible fact per layer — in `--read-only` and on a trashed issue no `data-field` control renders and the value stays text.
