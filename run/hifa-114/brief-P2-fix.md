# Brief — P2 fix round, HIFA-114

Working copy: `/home/administrator/wt-hifa114`, branch `feat/hifa-114-v3`.

## Role

You are the spec author again, in a fix round. The reviewer failed your section with 13 findings.
You close them by editing the spec, not by arguing with the reviewer and not by writing code.

## Read, in this order

1. `run/hifa-114/review.md` — **the 13 findings verbatim. This is your work list.**
2. `run/hifa-114/brief-P1.md` — the constraints you were given, including decisions A1/A2/A3.
3. `specs/070-web-dashboard.md` — `§2.6.11` and every section a finding points at (`§2.1.1-2.1.4`,
   `§2.2`, `§2.3.4`, `§2.6.3`, `§2.6.6`, `§5`, `§6`, `§7`, R4/R8/R18/R27/R29/R30).
4. `AGENTS.md` §3 and §5, `specs/README.md`.

## Rulings the orchestrator already made — these are decisions, not options

Use them; they resolve the findings where the answer is a scope question rather than a wording one.

- **R-a (finding on the four hosts).** Drop the board card. The intent puts cards in non-goals
  (`run/hifa-114/intent.md`, `Non-goals`), and no requirement, scenario or task owns the card. The
  contracted hosts are the detail page, the panel, and the row that `§2.6.3` already serves.
- **R-b (finding on `description`).** All seven fields use `?view=field`, and the answer is that
  field's read state — including `description`. Delete the `?view=panel` exception on the detail page:
  `#panel` there is `base.html`'s empty slide-over, so the exception contracts a panel swap into an
  empty element. If the panel needs a different answer for `description`, that is its own requirement
  with its own scenario, not an exception inside this one.
- **R-c (finding on a trashed write).** A write on a trashed issue keeps `§6`'s answer: `422`
  `ISSUE_TRASHED`, per R29. `404` belongs to the read surfaces. Do not move a write's status to `404`.
- **R-d (finding on labels).** Scope the requirement to the in-place surface: no `data-field` value
  outside the seven, and no `?view=field` write names one of them. The existing label routes and the
  row's chips stay exactly as they are — the finding is right that a literal reading deletes a live
  capability.
- **R-e (finding on the keyboard/320px test).** Split it. The decidable part — `min-height: 44px`
  inside the responsive block, the one-column layout — becomes a static test of `app.css` beside
  `tests/unit/web/test_mobile_layout.py`. The keyboard path and the sideways-scroll check become a
  **hand-checked step** naming the browser and both viewports, the way `§2.6.9` already does, because
  this host has no Playwright. A `pytest -k` that cannot decide the behaviour must not claim to.
- **R-f (finder on the `hx-get` span with no `href`).** R4 and `§2.6.6` are enforced by a test that
  walks the rendered HTML, so either the read control carries its `href` beside the `hx-get`, or you
  amend R4/`§2.6.6` explicitly for this element. Do not leave a silent breach of a tested rule.

## Rules for the edit

- Every finding must end in something a stranger can check. If you cannot close one, say which and
  why in your report — that is legitimate; a fake close is not.
- Do not weaken a finding into prose. If the fix is a contract change, change the contract (bump
  `spec_revision`, and `version` only if the public contract changes again).
- Keep `§2.6.11` under 8,000 characters measured with `wc -m`, and keep the whole added diff under it.
- ASCII, English. One commit: `spec: HIFA-114 close the review findings`.
- Do not touch code, tests, CI, `AGENTS.md`, `specs/INDEX.md` (that file's stale version is a finding
  for its own owner, not yours).

## Report back

One line per finding: closed / not closed and why. Then the measurement, the new `spec_revision`, and
any finding where you concluded the reviewer was wrong — with the line that proves it.
