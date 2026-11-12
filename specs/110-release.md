---
id: SPEC-110
title: Semantic release pipeline
component: support
status: draft
version: 3
spec_revision: 9
depends_on: [SPEC-000, SPEC-050]
implements:
  - .github/workflows/release.yml
  - .github/release/tag-message.sh
  - .github/release/assert-outcome.sh
  - .releaserc.json
  - package.json
  - package-lock.json
  - CHANGELOG.md
  - pyproject.toml
  - src/hifa/__init__.py
  - .github/signers/allowed_signers
  - tests/unit/test_version.py
  - tests/e2e/test_release_contract.py
  - tests/e2e/test_release_outcome.py
  - tests/fixtures/release/*.log
acceptance: specs/acceptance/110-release.feature
owner: Ginkelte
created: 2026-11-11
updated: 2026-11-11
---

# SPEC-110 — Semantic release pipeline

## 1. Responsibility and boundaries
**Does:** defines how a hifa version comes into existence. The npm package **`semantic-release`** computes
the next version from the commit log of `main`, writes it as a literal into `pyproject.toml`, regenerates
`CHANGELOG.md`, commits both, creates a **signed annotated tag** `vX.Y.Z` and publishes a GitHub Release —
in one run triggered by a push to `main`. `hifa --version` reads the installed metadata, which carries
that literal.

**Does NOT do (explicit):**

- It does not let a human decide a version: the number is computed from `LAST_TAG..HEAD`, and
  `workflow_dispatch` carries only a `dry_run` flag (§2.4).
- It does not write more than **one** commit per release to `main`. That commit (`chore(release): X [skip
  ci]`) is pushed by the engine and enters `main` through the ruleset's **admin bypass** (D-039, §2.7.1),
  not through a PR. Every other change still needs a PR.
- It does not use `python-semantic-release`, `hatch-vcs`, `setuptools-scm` or a dynamic `[project]
  version`: that design is **reverted** by D-039 (§2.1).
- It does not publish to PyPI, build distribution artefacts, attach wheels, run on a schedule (§10), open
  SQLite, run on pull requests, or block a PR: it is not a required check (D-036).
- It does not define the commit convention: `AGENTS.md` §9 does, this spec maps it to bumps (§2.5.3) and
  requires §9 to carry the same table verbatim (§2.10, R15).
- It does not sign with GPG (SSH only, §2.7) and does not hand-render notes (§2.6).
- It does not classify a run from the workflow YAML: every outcome of §2.4.2 is decided by the governed
  script `.github/release/assert-outcome.sh`, which pytest executes against fixtures (D-040).
- It does not expose a secret to a step that does not use it: `env:` is declared per step (§2.4.3), never
  at workflow or job level, and the push credential is materialised inside the shell of the one step that
  pushes (§2.4.2).
- It does not create the first tag: `v0.1.0` is a one-time manual signed action (§5.2, R12), because a
  tag-free history makes the engine answer `1.0.0` (measured, §9).

## 2. Public contract

> This section is normative. An implementer must be able to write the workflow, `.releaserc.json`, the
> packaging keys and the `--version` path from here, without reading any other file.

### 2.1 The version: one literal, written by the pipeline

| Artefact | Before rev 4 (rev 3 design) | After (normative) |
|---|---|---|
| `pyproject.toml` `[project]` | no `version` key, `dynamic = ["version"]` | **`version = "X.Y.Z"`** (literal, exactly this spelling) |
| `pyproject.toml` `[build-system].requires` | `["hatchling", "hatch-vcs", "setuptools-scm==10.3.4"]` | **`["hatchling"]`** — one element, compared as a list (§7.2) |
| `pyproject.toml` `[tool.hatch.version]` | `source = "vcs"` | **absent** |
| `pyproject.toml` `[dependency-groups] dev` | `python-semantic-release==10.7.0` row | **that row removed** (the group may carry other tools) |
| `pyproject.toml` `[tool.semantic_release]` | present | **absent** |
| `src/hifa/__init__.py` | `importlib.metadata.version("hifa")` + fallback | **unchanged** — same block, same fallback |
| Git tag `vX.Y.Z` | the only source | the **record** of the release; the literal is what the install reports |

- **Exactly one product version literal exists: `pyproject.toml`, line `version = "X.Y.Z"`.** It is
  written **only** by `@semantic-release/exec`'s `prepareCmd` (§2.5.1), inside the release commit.
  Nobody edits it by hand: a hand-edited bump produces a version no tag records, and the next release
  overwrites it.
- **Formatting is part of the contract.** The line starts at column 0, with double quotes and exactly
  one space on each side of `=`. The `sed` matches that shape literally; any other spelling (single
  quotes, tabs, `version="…"`) makes the bump a no-op, which step 9 detects
  (`release_version_mismatch`, §6).
- **Three things leave `[build-system]`, not one.** `hatch-vcs` **and** `setuptools-scm==10.3.4` leave
  `requires` (two independent git-derived version backends), and `[tool.hatch.version]` with `source =
  "vcs"` is the switch that made either reachable, so it goes too; `build-backend = "hatchling.build"`
  stays. Removing only the name `hatch-vcs` leaves a second backend installed and passes every check
  that greps for one name — which is why §7.2 compares the **list**.
- `src/hifa/__init__.py` is exactly this block (unchanged from rev 3, and the reason no version literal
  lives in Python):

```python
"""hifa — local issue tracker (CLI + SQLite + dashboard)."""

from __future__ import annotations
from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("hifa")
except PackageNotFoundError:  # source tree that was never installed
    __version__ = "0.0.0+unknown"
__all__ = ["__version__"]
```

- `hifa.cli.main` keeps importing `from hifa import __version__` and printing
  `f"hifa {__version__}"` (SPEC-050 §2.2). Only the origin of the value changes, never the shape.
- **`package.json` is not a version carrier:** `"private": true` and `"version": "0.0.0"` as npm's
  placeholder; neither `npm` nor the pipeline bumps it, and a different `version` is a contract
  violation (§7.2). No generated version file exists either (no `_version.py`, no `VERSION`, no
  `export-subst`).

#### 2.1.1 The version test asserts the shape and the uniqueness, never the value

`tests/unit/test_version.py` is contract (§2.9), and what it may assert is bounded:

| It asserts | It never asserts |
|---|---|
| `[project].version` matches `^[0-9]+\.[0-9]+\.[0-9]+$`, or is exactly the documented fallback `0.0.0+unknown` (§2.2) | `version == "0.1.0"`, or any other concrete number |
| exactly one line matches `^version = "…"$` in `pyproject.toml`, and it is the value `tomllib` parses as `[project].version` | that the literal equals the current release, the last tag, or a number repeated from a fixture |
| `src/hifa/__init__.py` keeps the `importlib.metadata` read, the fallback exactly once, and no release literal | the distribution metadata of the machine that runs the suite |

**Why the value is forbidden and not merely discouraged.** The release rewrites that literal inside a
commit that enters `main` through the admin bypass with `[skip ci]` (§2.4, D-039), so **no CI run ever
sees the change**: a test that pins the value turns `main` red on the first successful release, and the
required `test` check then blocks every later pull request until an emergency fix lands. A test that
freezes the version breaks the design it exists to protect, once per release, with no run to explain it.

- The shape assertion survives every release; a value assertion is a snapshot of one commit, taken of a
  field that a machine owns (§2.1).
- The rule binds every test that touches a version, not only this file: §7.2 simulates the post-release
  state (`version = "0.2.0"`) and the suite must stay green. A check that fails that simulation is a
  defect, not a stricter check (R17).

### 2.2 `hifa --version` contract
One line on `stdout`, nothing on `stderr`, exit code `0`:

```
hifa <version>
```

| State of the installation | `<version>` | Example |
|---|---|---|
| installed from the tagged commit | `X.Y.Z` (the literal written by that release) | `hifa 0.2.0` |
| installed from a commit **after** the last release | the **last released** `X.Y.Z` (the literal does not move until the next release commit) | `hifa 0.2.0` |
| distribution metadata absent (source tree, never installed) | `0.0.0+unknown` | `hifa 0.0.0+unknown` |

- `<version>` matches `^[0-9]+\.[0-9]+\.[0-9]+$` or is exactly `0.0.0+unknown`: no `v` prefix, no
  spaces, no second line. It is fixed at install time — `importlib.metadata` reads the `dist-info`, and
  nothing is recomputed from `git` at run time (§10).
- **On the tagged commit it equals the tag without its `v`**, because the engine tags the commit that
  `prepare` produced (§2.3.1): `git describe --tags --exact-match` ⇒ `v0.2.0` ⇔ `hifa 0.2.0`.
- **The version can lag HEAD**: a checkout between releases reports the previous one. That is the
  accepted cost of a literal (D-039) — the tag, not the string, marks what shipped.
- `--version` opens no database, no config, no socket, imports neither `fastapi`, `uvicorn`, `jinja2` nor
  `sqlite3` (A4/A5, REQ-S110-02), and stays under **250 ms p50 warm** (SPEC-050 REQ-N050-02).

### 2.3 The tag, and the order in which the write happens

| Property | Value |
|---|---|
| Format | `v{major}.{minor}.{patch}` — `tagFormat: "v${version}"`, e.g. `v0.2.0` |
| Object type | **annotated** tag object, never a lightweight ref |
| Signature | SSH, made at creation time (`tag.gpgSign=true`, §2.4 step 6) |
| Principal | `hifa-release`, the line added to `.github/signers/allowed_signers` (§2.7) |
| Tagger identity | the engine's bot, `semantic-release-bot <semantic-release-bot@martynus.net>` — the engine sets `GIT_COMMITTER_*`, so the tagger is **not** configurable through `git config user.name` (measured, §9) |
| Message | the tag name git is creating (`v0.2.0`), written by `.github/release/tag-message.sh` from the message file git hands it (§2.3.3) |
| Target | the release commit created in the same run (§2.3.2) |
| Immutability | never moved, deleted or re-created by the pipeline, not even an unverified one |

**How a signed tag comes out of `git tag <name> <sha>`.** The engine calls `git tag <tagName> <ref>`
with **no `-m`** (`lib/git.js`, code-read). With `tag.gpgSign=true` alone that call aborts (*"Please
supply the message"*); with `tag.gpgSign=true` **and** `GIT_EDITOR` pointing at
`.github/release/tag-message.sh` it creates an **annotated, SSH-signed** tag whose message is the tag
name. That pair is the contract; `git cat-file -t` ⇒ `tag` and `git verify-tag` ⇒ `Good "git" signature
for hifa-release` are its observables (§7.2).

#### 2.3.1 The write order: the release commit is pushed **before** the tag exists
This is the one property an implementer must not get wrong, so it is stated as the engine's own sequence
(`semantic-release@21.1.2 index.js`, code-read, reproduced in §9):

```
verifyConditions → analyzeCommits → verifyRelease → generateNotes → prepare → git tag → git push --tags → publish
```

`@semantic-release/git@10.0.1 lib/prepare.js` ends with `add` → `commit` → **`push(repositoryUrl,
branch.name)**, i.e. `git push --tags <url> HEAD:main`: the release commit reaches `main` inside
`prepare`. The tag is created afterwards, on the commit `prepare` produced (the engine re-reads `HEAD`
after each prepare plugin — `lib/definitions/plugins.js`). Consequences, all normative:

- **The failure modes split in two.** A failure *inside* `prepare` (the `sed`, the changelog, the release
  commit or its signature) leaves nothing on the remote, because `add`/`commit` precede `push`. A failure
  *at* `git tag` (an unusable key at that moment, an empty message, a broken `GIT_EDITOR`, a tag that
  already exists) leaves `main` with a signed `chore(release): X.Y.Z [skip ci]` commit whose
  `pyproject.toml` says `X.Y.Z`, and **no `vX.Y.Z` tag** (measured, §9).
- **Therefore "signed or nothing" cannot rest on the tag step.** The pipeline adds a **signing pre-flight
  in `verifyConditions`**, which runs before `prepare` and therefore before any write:
  `@semantic-release/exec`'s `verifyConditionsCmd` (§2.5) prints `signing pre-flight: starting`, then signs a
  disposable `preflight-*` tag with the same `tag.gpgSign` + `GIT_EDITOR` pair, verifies it against
  `allowed_signers`, deletes it and never pushes it. It exercises the key **and** the message path, so a
  broken key, a broken helper or an empty message aborts with `main` untouched (measured: same broken editor,
  `main` not advanced). The opening line is part of the contract: it is what tells order 4 of §2.4.2 that
  the pre-flight *was reached* and failed, instead of never having run.
- **What remains is a residual, documented rather than denied:** a failure *between* the pre-flight and
  `git tag` (a concurrent actor creating `vX.Y.Z` in that window, a ref lock, an I/O error) still
  produces the partial state. Step 9 detects it and §5.5 repairs it — never a re-run.
- The pre-flight does not replace the creation condition: `tag.gpgSign` + `GIT_EDITOR` still sign the tag,
  and step 9 still verifies the tag object as the second net.

#### 2.3.2 Shape and immutability

- **No partial tags.** `tagFormat` has all three components; `v0.2` and `v0` are never produced.
  Anything not matching `^v[0-9]+\.[0-9]+\.[0-9]+$` is out of contract (§10).
- A tag is never moved, deleted, re-created or force-pushed by the pipeline (§6): the tag is public,
  and delete-and-retag is the history rewrite R6 forbids.
- A lightweight tag is out of contract: if `git cat-file -t "refs/tags/$TAG"` is not `tag`, the run
  fails with `release_tag_not_signed` even though the tag is already pushed.

#### 2.3.3 The message is the name git is creating, not the literal in the tree

`.github/release/tag-message.sh` is the `GIT_EDITOR` of the `git tag` call (§2.3), and its contract has
four rules:

| Rule | Value |
|---|---|
| Invocation | git runs `<editor> <file>`: the script requires **exactly one** argument (`$# -eq 1`). Zero or more than one exits non-zero — a helper that guesses its input is the failure this prevents |
| Source of the message | the **message file** git passes as `$1`. git seeds it with a comment block whose line after `# Write a message for tag:` is `#   <name>` (measured, §9); the script reads the name from there and writes that name back as the only non-comment line |
| Accepted names | `^v[0-9]+\.[0-9]+\.[0-9]+$` (a release tag, §2.3.2) or `^preflight-[A-Za-z0-9._-]+$` (the disposable tag of the pre-flight, §2.3.1). Anything else is refused |
| Fail closed | no argument, no message file, no `# Write a message for tag:` header, zero or more than one name, or a name outside the two patterns ⇒ exit non-zero and write nothing. An empty message is never signed |

- **It never reads `pyproject.toml`.** The tree literal is not the source of the message: the pre-flight
  tag cannot be derived from it, which is exactly what proves the helper reads the name git is creating
  instead of a value it could have got wrong.
- **Why this replaces the tree read.** A helper that messages the tag with the literal found in the
  working tree signs whatever the tree says, and the only comparison left would be a check that runs after
  `prepare` has already committed and pushed (§2.5). Deriving the name from git makes the mismatch
  impossible at creation time instead of detectable after the write.
- **The pre-flight is the test of the message path.** It signs a `preflight-*` tag through the same helper
  (`verifyConditionsCmd`, §2.5), before any write: a helper that cannot produce that name aborts the run
  with `main` untouched, and a `preflight-` message is a name no tree read could produce.

### 2.4 The workflow — `.github/workflows/release.yml`

| Property | Normative value |
|---|---|
| File | `.github/workflows/release.yml` (new; `ci.yml` and `signatures.yml` are not edited) |
| `name` | `Release` |
| `on.push.branches` | `[main]` — and nothing else |
| `on.workflow_dispatch.inputs.dry_run` | `type: boolean`, `default: true`, description "Compute and print the release plan; write nothing." |
| `on.pull_request` | **absent**. A `pull_request` trigger here is a contract violation (§7.1) |
| `on.schedule` | `cron: '0 */6 * * *'` — a self-healing fallback, see the rule below. A scheduled run is indistinguishable from a push run: it computes the same version, writes the same commit and tag, and classifies its own outcome |
| `permissions` (workflow / job) | `contents: read` / `contents: write` — **inert for the engine, see §2.7.2** |
| `concurrency` | `group: release`, `cancel-in-progress: false` (a release is never cancelled half-way) |
| `timeout-minutes` / `runs-on` | `20` / `ubuntu-latest` |
| `if` | **absent at job level.** The loop breaker is the `[skip ci]` token of the release commit message, which GitHub already honours for `push` (see below); a `contains()` condition on the head commit message is a second copy of that rule, and it also *skips* — grey, not red — a legitimate release whose squash-merge body quotes the phrase, i.e. it can hide a release instead of preventing a loop |
| Environment variables | **none at workflow or job level.** Each secret is declared in the `env:` block of the single step that uses it (§2.4.3); `$GITHUB_ENV` never carries one (§2.7.2) |

**The skip token is a trap in both directions.** GitHub honours `[skip ci]` (and its siblings) as a
case-insensitive substring of the **commit message** of the pushed commit, and it applies that rule
to *every* workflow triggered by the push — `ci.yml` and this one alike — silently, with no run, no
check and no error. The release commit therefore carries the token on purpose, so the release does
not trigger itself. The cost is that any **other** commit whose message quotes the token disables the
release for that push, and the most likely author of such a commit is a document explaining the
token.

- **Normative:** a commit message, a PR body and a squash-merge message **never** contain the literal
  token. Write it as "the skip-ci token" or describe it in words. The rule binds every contributor
  and every generated message.
- **Detection is impossible after the fact**, because the run that would have checked does not exist.
  That is why `on.schedule` exists: a skipped push is released within six hours, and a scheduled run
  with nothing to release classifies itself as `green: no relevant changes`.
- A missing run is not a red run: there is nothing to classify and nothing to alert on. The schedule
  is the only defence that does not depend on the message that caused the skip.

Steps, in this order. Each row is a contract: a step may be renamed, the effects may not be reordered.

| # | Step | What it must do | Observable |
|---|---|---|---|
| 1 | checkout | `actions/checkout@v4`, `fetch-depth: 0`, **`persist-credentials: false`** (public repository: no token needed to fetch, none may be written to `.git/config`) | `grep -c extraheader .git/config` ⇒ `0` |
| 2 | node | `actions/setup-node@v4`, **`node-version: '22.22.3'`** (the pair the lockfile was generated with, §2.8), `cache: npm`, `cache-dependency-path: package-lock.json` | `node --version` ⇒ `v22.22.3` |
| 3 | token gate | `env: HIFA_RELEASE_TOKEN` only; empty ⇒ print `release_token_missing`, write the dedupe mark `$RUNNER_TEMP/release_code_emitted`, `exit 1` | red before any write; the token value never in the log; the code printed **once** (§2.4.2) |
| 4 | install and baseline | **`npm ci --ignore-scripts`** — never `npm install`, never an inline `package.json`, never a lifecycle script (§2.4.3). It also **adopts the remote notes ref** (`git fetch --no-tags origin 'refs/notes/*:refs/notes/*'`, failure tolerated): the runner's clone carries no notes, so the engine's `git push … refs/notes/semantic-release` would not be a fast-forward and the remote would refuse it — *after* the tag was already published | `node_modules/.package-lock.json` present; `git diff --exit-code package-lock.json` clean; `PRE_RUN_HEAD`, `PRE_RUN_MAIN` and the tag list captured, each asserted non-empty before it is written; `refs/notes/semantic-release` exists locally when the remote has one |
| 5 | dry-run split | `inputs.dry_run` true ⇒ `SEMANTIC_RELEASE_ARGS=--dry-run`, otherwise empty; **always** writes `RELEASE_DRY_RUN=true`/`false` to `$GITHUB_ENV` — the key §2.4.2 requires and §2.4.1 authorises as non-secret | `::notice::` with the argument set and the flag |
| 6 | identity and signing | exactly the block of §2.4.1, with `env: HIFA_RELEASE_SIGNING_KEY` only; empty secret ⇒ `release_signing_key_missing`, flag, `exit 1` | `git var GIT_COMMITTER_IDENT`; `ssh-keygen -y -f "$RUNNER_TEMP/hifa_signing_key"` succeeded |
| 7 | credential scope check | proves that nothing persisted the credential: `$GITHUB_ENV` carries no `HIFA_RELEASE_TOKEN`, `HIFA_RELEASE_SIGNING_KEY`, `GIT_CONFIG_*` or `AUTHORIZATION`, and `.git/config` carries no `extraheader`. Declares no `env:` and writes nothing | both greps ⇒ `0` |
| 8 | release | exactly the block of §2.4.1: `env:` = `HIFA_RELEASE_TOKEN` + `GITHUB_TOKEN`, the base64 header exported **inside this shell** and never written to `$GITHUB_ENV`, the engine log captured, `RELEASE_EXIT_CODE` recorded. It prints **no** code | `signing pre-flight: starting` then `signing pre-flight ok`, **both before** `Prepared Git release`; then `next release version is X.Y.Z` and `Created tag vX.Y.Z`, or `no relevant changes` |
| 9 | post-conditions | **`if: always()`** and one command, `.github/release/assert-outcome.sh` (§2.4.2) — the only step that classifies a run — behind an existence test, because a failed checkout leaves no script to run | exactly one line: `green: …` with exit `0` (G1, G2, G3), or `release_<code>` with exit `1`; silent when a gate already printed the code; a script that is not on disk ⇒ `release_engine_aborted`, exit `1` |
| 10 | cleanup | `if: always()`: `rm -f` the key and its `.pub`, delete any local `preflight-*` ref, `ssh-agent -k` if used | runs on red and green |

**`[skip ci]`, precisely.** GitHub Actions documents `[skip ci]` (with `[ci skip]`, `[no ci]`, `[skip
actions]`) as skipping **`push` and `pull_request`** runs for that commit. The release commit therefore
triggers **neither** `release.yml` **nor** `ci.yml`, and that token is the **only** loop breaker: the job
declares no `if:` (§2.4), because a second copy of the rule —
`contains(github.event.head_commit.message, '[skip ci]')` — also matches a *squash-merge body* that quotes
the phrase, and GitHub then reports the run as **skipped** (grey, not red) instead of releasing or
failing. The second net is the engine's own idempotency, which has no such defect: a re-run at a released
commit analyses the same range and answers `no relevant changes` (R4, R13). Because the commit also lands
through the admin bypass, `required_status_checks` is bypassed with it: **the release commit is the only
commit on `main` that no CI run validates.** That is accepted because it touches two machine-generated
files and its signature is verified by the classifier of step 9.
*Documented behaviour of GitHub Actions, not measured in this repository (§9).*

#### 2.4.1 Steps 3, 4, 5, 6, 7 and 8 — copyable

```yaml
# step 3 — token gate: the only step besides step 8 that reads the token
- name: Token gate
  env:
    HIFA_RELEASE_TOKEN: ${{ secrets.HIFA_RELEASE_TOKEN }}
  run: |
    set +x
    if [ -z "${HIFA_RELEASE_TOKEN:-}" ]; then
      echo "release_token_missing"
      : > "$RUNNER_TEMP/release_code_emitted"   # the owner's mark, read by step 9 (§2.4.2)
      exit 1
    fi
    echo "::notice::HIFA_RELEASE_TOKEN is present (value never printed)"

# step 4 — install: exact, from the lockfile, with no lifecycle script (§2.4.3)
- name: Install the pinned release engine
  run: |
    npm ci --ignore-scripts
    test -f node_modules/.package-lock.json
    git diff --exit-code -- package-lock.json
    # Adopt the remote notes ref. The clone has none, so the engine's push of
    # refs/notes/semantic-release has no common ancestor with the remote's and is refused as a
    # non-fast-forward — after the tag is already published. Fetching first makes it a
    # fast-forward; a failure here is not a release failure, the classifier reports it instead.
    git fetch --no-tags origin 'refs/notes/*:refs/notes/*' || true
    head_sha=$(git rev-parse HEAD)
    main_sha=$(git ls-remote origin refs/heads/main | cut -f1)
    test -n "$head_sha" && test -n "$main_sha"   # fail closed: an empty baseline is not a baseline
    { echo "PRE_RUN_HEAD=$head_sha"; echo "PRE_RUN_MAIN=$main_sha"; } >> "$GITHUB_ENV"
    git ls-remote --tags origin | sort > "$RUNNER_TEMP/tags_before.txt"
    test -s "$RUNNER_TEMP/tags_before.txt"   # fail closed: "no tags" is a failed capture, not an empty baseline
    echo "PRE_RUN_TAGS=$RUNNER_TEMP/tags_before.txt" >> "$GITHUB_ENV"

# step 5 — dry-run split: the flag the classifier reads, and the engine's argument set
- name: Dry-run split
  run: |
    set -eu
    if [ "${{ inputs.dry_run }}" = "true" ]; then
      dry=true; args="--dry-run"
    else
      dry=false; args=""
    fi
    echo "RELEASE_DRY_RUN=$dry" >> "$GITHUB_ENV"
    echo "SEMANTIC_RELEASE_ARGS=$args" >> "$GITHUB_ENV"
    echo "::notice::dry run: $dry; engine arguments: ${args:-none}"

# step 6 — identity and signing: the key is visible to this step and to no other
- name: Configure identity and SSH signing
  env:
    HIFA_RELEASE_SIGNING_KEY: ${{ secrets.HIFA_RELEASE_SIGNING_KEY }}
  run: |
    set +x; umask 077
    if [ -z "${HIFA_RELEASE_SIGNING_KEY:-}" ]; then
      echo "release_signing_key_missing"
      : > "$RUNNER_TEMP/release_code_emitted"   # the owner's mark, read by step 9 (§2.4.2)
      exit 1
    fi
    printf '%s\n' "$HIFA_RELEASE_SIGNING_KEY" > "$RUNNER_TEMP/hifa_signing_key"
    chmod 600 "$RUNNER_TEMP/hifa_signing_key"
    ssh-keygen -y -f "$RUNNER_TEMP/hifa_signing_key" > "$RUNNER_TEMP/hifa_signing_key.pub"
    git config user.name  "hifa release"
    git config user.email "hifa-release@users.noreply.github.com"
    git config gpg.format ssh
    git config user.signingkey "$RUNNER_TEMP/hifa_signing_key"   # the PRIVATE key — see below
    git config gpg.ssh.allowedSignersFile .github/signers/allowed_signers
    git config commit.gpgSign true
    git config tag.gpgSign true
    echo "GIT_EDITOR=$GITHUB_WORKSPACE/.github/release/tag-message.sh" >> "$GITHUB_ENV"
    git var GIT_COMMITTER_IDENT

# step 7 — nothing has persisted the credential: assert it, write nothing
- name: Push credential scope check
  run: |
    set -eu
    if grep -qE 'HIFA_RELEASE_TOKEN|HIFA_RELEASE_SIGNING_KEY|GIT_CONFIG_|AUTHORIZATION' "$GITHUB_ENV"; then
      echo "the environment file carries a secret or the push credential (§2.4.3)"; exit 1
    fi
    if grep -q extraheader .git/config; then
      echo "the credential reached .git/config (§2.4.3)"; exit 1
    fi

# step 8 — the engine, with the credential materialised inside this shell only
- name: Run semantic-release
  env:
    HIFA_RELEASE_TOKEN: ${{ secrets.HIFA_RELEASE_TOKEN }}
    GITHUB_TOKEN: ${{ secrets.HIFA_RELEASE_TOKEN }}
  run: |
    set +x
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0=http.extraheader
    export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$HIFA_RELEASE_TOKEN" | base64 -w0)"
    echo "::add-mask::$GIT_CONFIG_VALUE_0"
    set +e
    ./node_modules/.bin/semantic-release ${SEMANTIC_RELEASE_ARGS} 2>&1 \
      | tee "$RUNNER_TEMP/semantic-release.log"
    status=${PIPESTATUS[0]}
    echo "RELEASE_LOG=$RUNNER_TEMP/semantic-release.log" >> "$GITHUB_ENV"
    echo "RELEASE_EXIT_CODE=$status" >> "$GITHUB_ENV"
    exit "$status"
```

**`user.signingkey` is the private key.** SSH signing needs the private half (or `key::<literal>`, or an
agent that holds it); the public half exists only as a line in `allowed_signers`. Binding
`user.signingkey` to a `.pub` file whose key is not in an agent makes `git tag` fail with *"Could not
read SSH key"* — i.e. exactly the partial state of §2.3.1.

**The credential never enters `$GITHUB_ENV`.** `export` in the shell of step 8 makes the header visible
to the engine and to its children only, for the life of that step. `$GITHUB_ENV` is a **plain file on
disk** that every later step sources, so a value written there is readable by any process of the job and
decodable by any child; `::add-mask::` protects the **log**, not the file. The mask stays as a second net
on the value that stays in memory. Two observables, both required: `grep -c extraheader .git/config` ⇒ `0`
and `grep -cE 'HIFA_RELEASE_TOKEN|HIFA_RELEASE_SIGNING_KEY|GIT_CONFIG_|AUTHORIZATION' "$GITHUB_ENV"` ⇒
`0` **after** the engine has run — the second one is asserted by the classifier (§2.4.2,
`release_credential_persisted`), because a check that runs before the write cannot prove anything about it.

**The only keys `$GITHUB_ENV` may carry are non-secret:** `SEMANTIC_RELEASE_ARGS`, `RELEASE_DRY_RUN`,
`RELEASE_LOG`, `RELEASE_EXIT_CODE`, `PRE_RUN_HEAD`, `PRE_RUN_MAIN`,
`PRE_RUN_TAGS`, `GIT_EDITOR` and, if an agent is used, `SSH_AUTH_SOCK`/`SSH_AGENT_PID`. Any other key is a
contract violation; §7.2 lists the redirects and refuses a key outside this set.

**An empty baseline is not a baseline.** `PRE_RUN_HEAD` and `PRE_RUN_MAIN` are asserted non-empty when
they are captured (step 4) and required non-empty again before the dry-run comparison (§2.4.2), and the tag
list is asserted **non-empty** (`test -s`) for the same reason: with `${PRE_RUN_HEAD:-$head_now}` a capture
that failed compares equal to itself and reports a green dry run that proved nothing, and an empty tag file
would make a comparison between two failures look like "no tag was added".

#### 2.4.2 Step 9 — the outcome assertion, and one code per run

Step 9 is `if: always()`, so it also runs when step 8 failed, and it is **one command**:
`.github/release/assert-outcome.sh` (§2.9), behind an existence test — `[ -x … ]` — because a step that
failed in step 1 leaves no script to invoke, and a bare call then dies with a shell error instead of
printing a code (§2.4.2, order 13). The classifier is a governed file, not a block of YAML: pytest
executes it against fixture logs and a throwaway git repository, with one fixture per evaluation order of the
table below and at least one fixture per order in which it must **not** fire (§7.2, D-040).

It reads the repository **and** the engine log step 8 captured: the signature and content checks need only
the repository, and the log is what separates failures that leave an identical state
(`release_tag_push_rejected` vs `release_partial_state`).

**The interface of the script — this is what pytest drives, and what an implementer must expose:**

| Input (environment) | Where it comes from | Required |
|---|---|---|
| `RELEASE_LOG` | step 8: the captured engine log | yes — a missing or empty log can never produce a green |
| `RELEASE_EXIT_CODE` | step 8: `${PIPESTATUS[0]}`. **When unset the script reads the sentinel `255`, never `0`** — that is the *input* sentinel, not the script's own exit status (§8 step 8) | yes |
| `RELEASE_DRY_RUN` | step 5: `true` or `false`, written to `$GITHUB_ENV` (§2.4 step 5) | yes |
| `PRE_RUN_HEAD`, `PRE_RUN_MAIN`, `PRE_RUN_TAGS` | step 4. `PRE_RUN_HEAD` must be **non-empty** to grant G1 **or G3** (both are statements about what this run did, and the delta needs a baseline); `PRE_RUN_MAIN` and `PRE_RUN_TAGS` additionally for G1 | for G1 and G3 |
| `RELEASE_CODE_MARKER` | path to the dedupe mark; default `$RUNNER_TEMP/release_code_emitted` | optional |
| `TAG` | the name on the log line `Created tag <tag>` — the **only** source. Absent (no such line) ⇒ the tag-dependent orders 5, 6 and 7 cannot be evaluated and the run lands on order 13. It is never "the last tag", never the subject of the head commit and never `tagFormat` plus the computed version | for G2 and for orders 5–7 |
| working directory | the checkout: `git`, `refs/tags/*`, `.git/config` and `$GITHUB_ENV` are read from it | yes |

**The script takes no arguments and reads no stdin.** Every input above is an environment variable plus the
working directory; `./.github/release/assert-outcome.sh` is invoked exactly as step 9 invokes it, so an
argument or anything on `stdin` is a usage it does not have. An argument is **refused**: no classification
line, a diagnostic on `stderr`, exit `1`. It is the one place the pipeline must fail closed, and
`assert-outcome.sh --help` once answered `green: released v0.2.0`, exit `0`.

**Output: exactly one** classification line on `stdout`, and **the three greens are literals, not prose**, so
a fixture asserts equality instead of a prefix:

| Outcome | The exact `stdout` line |
|---|---|
| G1 — nothing written | `green: nothing written` |
| G3 — no release (R4) | `green: no relevant changes` |
| G2 — release | `green: released <tag>` (e.g. `green: released v0.2.0`) |

Everything else prints `release_<code>`. The exit status carries the verdict (`0` only for the three greens,
`1` for every code). Diagnostics go to `stderr` and never carry a secret or the credential.

**One code per run, and who owns the line.** The invariant is *exactly one classification line per run*,
and it is enforced by ownership, not by luck:

| Catalogue id | Owner | Mechanism |
|---|---|---|
| 1 `release_token_missing` | step 3 | prints the code, writes the mark, exits `1`. The classifier **never evaluates catalogue ids 1 and 2**, so a missing secret cannot be printed twice |
| 2 `release_signing_key_missing` | step 6 | same |
| G1–G3 and catalogue ids 3–13 | step 9 (the classifier) | step 8 prints **no** code, only `RELEASE_EXIT_CODE` and the log |

- **The mark is the mechanism.** A gate that prints a code writes `$RUNNER_TEMP/release_code_emitted`; when
  the classifier finds it, it prints no classification line at all and exits `1` with a diagnostic. It
  suppresses a **line**, never a **failure**: the run stays red, it just does not say the same thing twice.
- **It is a file and not an environment variable** because it must survive a *failing* step, and a marker in
  `$RUNNER_TEMP` is read by the next step regardless of how the previous one ended.
- **A red run cannot be laundered green.** No evaluation order below turns a non-zero exit into a green:
  G1, G2 and G3 require `RELEASE_EXIT_CODE == 0` explicitly, and an absent exit code reads as the non-zero
  sentinel.

Evaluation orders, in the only order that is the precedence: the first match wins, the classifier prints it
and exits.

| Evaluation order | Catalogue id / outcome | Fires when (state **and** log) | Assertion it makes |
|---|---|---|---|
| 1 | 12 `release_credential_persisted` | `$GITHUB_ENV` or `.git/config` carries the token, the signing key, an `extraheader`, a base64 of either, **or a value that is a credential by value** — a `ghp_…`/`gho_…`/`ghu_…`/`ghs_…`/`ghr_…`/`github_pat_…` prefix, or a base64 blob that decodes to `x-access-token:` or to a `PRIVATE KEY` header | the credential existed only in the memory of step 8; otherwise the run is red and the token is rotated |
| 2 | G1 — green, nothing written | `RELEASE_DRY_RUN=true` **and** `RELEASE_EXIT_CODE == 0` **and** the log carries `next release version is` or `no relevant changes` **and** `PRE_RUN_HEAD`/`PRE_RUN_MAIN` are non-empty and unchanged **and** `git status --porcelain` is empty **and** the tag list is unchanged | the plan was computed **and** nothing was written — both, or it is not green |
| 3 | G3 — green, no release (R4) | `RELEASE_EXIT_CODE == 0` **and** the log carries `no relevant changes` **and** `PRE_RUN_HEAD` is non-empty and equal to the `HEAD` of the checkout **and** the log carries no `Created tag` line — i.e. this run created no tag and moved no head | the run's **delta** is empty. The head being a `chore(release): … [skip ci]` commit is *not* the question: a scheduled run starts on the commit the previous release wrote and answers `no relevant changes` because there is nothing after the tag, and a state-shaped test makes that run red forever |
| 4 | 3 `release_signing_key_invalid` | `RELEASE_EXIT_CODE != 0` **and** the log carries `signing pre-flight: starting` without the closing `signing pre-flight ok` | `main` untouched, nothing pushed, and no `preflight-*` ref **in `origin`**: a local ref left behind when the `set -eu` chain aborted before `git tag -d` is removed by step 10, so it is not asserted here |
| 5 | 4 `release_tag_not_signed` | a tag was created in this run and `git cat-file -t` is not `tag`, or `git verify-tag` fails | the tag is public and immutable: **not** deleted |
| 6 | 5 `release_commit_unsigned` | `git verify-commit "$TAG^{commit}"` fails against `allowed_signers` | the commit is on `main`; reported so it can be re-signed forward |
| 7 | 6 `release_version_mismatch` | (a) the log carries the pre-flight literal guard's message and no release commit exists; or (b) the literal at `$TAG` ≠ `${TAG#v}`, or `grep -c '^version = "'` ≠ `1` | (a) nothing was written; (b) tag + commit on the remote, and the next release fixes it |
| 8 | 7 `release_forbidden` | the log shows the API refused release creation (403) | the tag exists, no Release (§5.4) |
| 9 | 8 `release_notes_missing` | `CHANGELOG.md` at the tag does not mention the version — and the log carries no rejected-push signal, because a tag the remote refused is §5.5's state, not a missing note. (The other half this row used to claim — an empty Release body — has no observable in the repository or in the log, so it is not part of the row) | tag + commit exist; the body is recoverable from the commit message (R13) |
| 10 | 13 `release_notes_push_rejected`, then 9 `release_tag_push_rejected` | one row, two codes, because a refused push names two possible refs. **13** — the log rejected a push of `refs/notes/` and `git ls-remote --tags origin` lists the tag: the commit, the tag and the Release are public, only the notes ref is not. **9** — the log carries a rejected-push signal **and** `git ls-remote --tags origin` does **not** list the tag that the log says was created, so the tag push is the one that failed. A rejected-push message alone (`failed to push some refs`, `protected branch`, `cannot lock ref`) fires neither code when the tag is on the remote, and a log with no `Created tag` line cannot reach either. The message never echoes the credential | 13: tag on the remote, notes ref missing → one `git push origin refs/notes/semantic-release` repairs it, the notes are also in the commit body. 9: commit on `main`, no tag → §5.5 |
| 11 | 10 `release_partial_state` | `origin/main`'s head is `chore(release): X.Y.Z [skip ci]`, its literal is `X.Y.Z`, `git ls-remote --tags origin "refs/tags/vX.Y.Z"` is empty, and order 10 emitted nothing | commit on `main`, no tag → §5.5 |
| 12 | G2 — green, release | `RELEASE_EXIT_CODE == 0` **and** no earlier evaluation order matched **and** the tag named in the log exists locally and on `origin`, **and** the assertions of orders 5–9 all passed | tag on the remote, annotated and signed; commit signed; literal == tag; `CHANGELOG.md` mentions the version |
| 13 | 11 `release_engine_aborted` | none of the above: the engine exited without a release and without the `no relevant changes` token, **or** a green cannot be proven (no log, no baseline, no exit code) | depends on the phase; reported instead of passing as a no-op |

- **Why G2 sits at 12 and not at 2.** Its assertions *are* orders 5–9: an "everything is fine" row placed
  before them would fire on a state a later evaluation order rejects. As the residual green of a completed
  release it is reachable only when no failure matched.
- **Catalogue id and evaluation order are two different numbers, and the columns now say which is which.**
  The `Catalogue id` column — and the leading number of a code, `3 release_signing_key_invalid` — is the id
  this spec uses whenever it names a code (§6 lists the same thirteen by name); the
  `Evaluation order` column is the position in the
  precedence. They disagree on purpose: `release_credential_persisted` is id 12 and is evaluated first
  (a leaked credential invalidates every green below it), ids 3–10 sit at `order = id − 1`, id 11 is
  the residual at order 13, and id 13 shares order 10 with id 9 because both answer the same question
  — *which ref did the remote refuse?* **An invariant phrased against the first two positions suppresses
  order 1 (`release_credential_persisted`) and G1** — that is why no text here says "row N" without naming
  which column it means.
- **A green states a delta, a code states an observed fact.** G1, G2 and G3 answer *what did this run
  do*, so each one compares the checkout with the baseline step 4 captured before the engine ran; the
  codes answer *what is true now*, so each one is verified against the repository or against
  `git ls-remote` before it is printed. A substring of the log is a **signal**, never the fact: the same
  refusal text is produced by a refusal of any ref, and a release commit at `HEAD` is the normal state of
  every scheduled run six hours after a release.
- **G3 is not "HEAD is not a release commit".** That was the shape of this row before rev 9, and it makes
  the six-hour schedule red forever: the run starts *on* the commit the previous release wrote, answers
  `no relevant changes` because nothing follows the tag, and the state-shaped test then sees the release
  subject at `HEAD` and refuses the green. The delta — no tag created, head not moved — is the only
  observable that distinguishes "nothing to release" from "this run released something".
- **A refused push names a ref, and the ref decides the code.** The engine pushes two things that can be
  refused independently: the tag (with the release commit) and `refs/notes/semantic-release`. A refusal of
  the second is *not* a tag refusal — the commit and the tag are public at that point — so order 10 asks
  `git ls-remote --tags origin` which of the two states actually holds, and prints the code that matches.
  Reading `failed to push some refs` without that question reported a successful release as a failed tag
  push, with two false claims in the diagnostic.
- **Catalogue ids 1 and 2 do not appear in this table** because the classifier does not evaluate them: a
  gate owns each line.
- **G1 and G3 are not "the step did not fail".** Both require `RELEASE_EXIT_CODE == 0` **and** a positive
  signal in the log. A dry run whose engine died — an expired PAT, the pre-flight passing and the GitHub
  plugin failing with 403, nothing written — leaves the repository *identical*, so a state-only assertion
  reports "green: nothing written" for a run that computed nothing. The positive signal is that difference,
  and order 13 is where such a run lands.
- **`RELEASE_EXIT_CODE` has no benign default.** `${RELEASE_EXIT_CODE:-0}` makes "step 8 never ran"
  indistinguishable from "step 8 succeeded"; the sentinel is `255`, so absence reads as failure.
- **`dry_run: true` is the dispatch default**, so the dry-run branch is the *normal* dispatch path: an
  unconditional "tag exists" assertion would make every default dispatch fail.
- **`workflow_dispatch` is for re-running a failure, not for releasing an arbitrary commit.** The version is
  always computed from the last tag on `main`; dispatching on an older commit either reproduces a version
  whose tag exists (`git tag` fails → §5.5) or produces a version nobody reviewed (§10).

#### 2.4.3 Secret scope: which step sees what

| Step | `env:` it declares | `HIFA_RELEASE_TOKEN` | `HIFA_RELEASE_SIGNING_KEY` | `GITHUB_TOKEN` |
|---|---|---|---|---|
| 1 checkout | — | no | no | no |
| 2 node | — | no | no | no |
| 3 token gate | `HIFA_RELEASE_TOKEN` | yes — presence only, never printed | no | no |
| 4 install (`npm ci --ignore-scripts`) | — | no | no | no |
| 5 dry-run split | — | no | no | no |
| 6 identity and signing | `HIFA_RELEASE_SIGNING_KEY` | no | yes — to `$RUNNER_TEMP`, mode `0600`, removed in step 10 | no |
| 7 credential scope check | — | no | no | no |
| 8 release | `HIFA_RELEASE_TOKEN`, `GITHUB_TOKEN` | yes — in the base64 header, in memory only | no | yes |
| 9 outcome (classifier) | — | no | no | no |
| 10 cleanup | — | no | no | no |

- **Why per step and not workflow-wide.** A workflow-level `env:` puts both secrets in `os.environ` of
  **every** step, `npm ci` included: the lifecycle scripts of 616 packages would run with
  `HIFA_RELEASE_TOKEN` — an admin PAT whose blast radius is the whole repository because it bypasses the
  ruleset (§2.7.1) — and with the private signing key in the same environment. One compromised
  `postinstall` would then be a full takeover **signed by the release key**, i.e. a release a third party
  verifies as the project's own. Per-step `env:` leaves the install step with no secret at all, and the
  secrets in exactly the two steps that use them.
- **`npm ci --ignore-scripts` is mandatory**, and it is a second and independent control: no dependency can
  execute code at install time even if a later edit puts a secret back in scope. The lockfile carries
  **zero** `hasInstallScript` entries across 616 packages today (measured, §9), so the cost is zero and the
  vector stays closed permanently — including through a dependency bump that would introduce one.
- **Nothing secret reaches `$GITHUB_ENV`**, whose keys are the non-secret list of §2.4.1; the classifier
  asserts the absence *after* the engine ran (`release_credential_persisted`, order 1).

### 2.5 `.releaserc.json` — the engine contract

#### 2.5.1 The file

```json
{
  "branches": ["main"],
  "tagFormat": "v${version}",
  "plugins": [
    ["@semantic-release/commit-analyzer", {
      "preset": "angular",
      "releaseRules": [
        { "breaking": true, "release": "major" },
        { "revert": true, "release": "patch" },
        { "type": "feat", "release": "minor" },
        { "type": "fix", "release": "patch" },
        { "type": "style", "release": "patch" },  { "type": "refactor", "release": "patch" },
        { "type": "perf", "release": "patch" },   { "type": "build", "release": "patch" },
        { "type": "ci", "release": "patch" },     { "type": "chore", "release": "patch" },
        { "type": "revert", "release": "patch" },
        { "type": "docs", "release": false },
        { "type": "test", "release": false }
      ]
    }],
    ["@semantic-release/release-notes-generator", { "preset": "angular" }],
    ["@semantic-release/changelog", { "changelogFile": "CHANGELOG.md" }],
    ["@semantic-release/exec", {
      "verifyConditionsCmd": "set -eu; count=$(grep -cE '^version = \"[0-9]+\\.[0-9]+\\.[0-9]+\"$' pyproject.toml || true); if [ \"$count\" != 1 ]; then echo \"release_version_mismatch: expected exactly one version literal line, found $count\"; exit 1; fi; probe=\"preflight-$(git rev-parse --short HEAD)-$$\"; echo \"signing pre-flight: starting\"; git tag \"$probe\"; git verify-tag \"$probe\" >/dev/null; git tag -d \"$probe\" >/dev/null; echo \"signing pre-flight ok: $probe\"",
      "prepareCmd": "set -eu; expected=\"${nextRelease.version}\"; before=$(grep -cE '^version = \"[0-9]+\\.[0-9]+\\.[0-9]+\"$' pyproject.toml || true); if [ \"$before\" != 1 ]; then echo \"release_version_mismatch: expected exactly one version literal line before the substitution, found $before\"; exit 1; fi; sed 's/^version = \"[^\"]*\"/version = \"'\"$expected\"'\"/' pyproject.toml > pyproject.toml.tmp; after=$(grep -cxF \"version = \\\"$expected\\\"\" pyproject.toml.tmp || true); if [ \"$after\" != 1 ]; then rm -f pyproject.toml.tmp; echo \"release_version_mismatch: the substitution produced $after lines equal to $expected\"; exit 1; fi; mv pyproject.toml.tmp pyproject.toml"
    }],
    ["@semantic-release/git", {
      "assets": ["CHANGELOG.md", "pyproject.toml"],
      "message": "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}"
    }],
    ["@semantic-release/github", {
      "successComment": false,
      "failComment": false,
      "releasedLabels": false
    }]
  ]
}
```

| Key | Value | Why it is the contract |
|---|---|---|
| `branches` / `tagFormat` | `["main"]` / `"v${version}"` | a release is cut from `main` only (R5), with three-part tags (§2.3.2) |
| plugin order | analyzer → notes → changelog → exec → git → github | the lifecycle runs in array order: `verifyConditions` for **all** plugins first (the pre-flight, §2.3.1), then `prepare`, so the `sed` writes the literal before `@semantic-release/git` stages it, and the tag is created after both |
| `verifyConditionsCmd` | the **literal guard**, then a disposable `preflight-*` tag: sign, verify, delete | §2.3.1 and §2.5.1: the guard and the signature both fail **before** `prepare` writes anything. It must not push, and it must delete the ref in the same command. It announces itself with `signing pre-flight: starting` **before** it creates the tag — the token order 4 of §2.4.2 keys on, because the closing `signing pre-flight ok` alone cannot distinguish "never reached" from "started and failed" |
| `prepareCmd` | the **guarded `sed`** of §2.5.1 | the only writer of the literal (§2.1), and it fails when the substitution is not exactly one line |
| `preset` | `"angular"` on analyzer and notes | section titles and `BREAKING CHANGE` parsing come from it. `conventionalcommits` is **not** used: on this toolchain it aborted analysis with no message and exit `0` (§9) |
| `releaseRules` | §2.5.3, identical to `AGENTS.md` §9 | one table, compared mechanically (§2.10) |
| `assets` | `["CHANGELOG.md", "pyproject.toml"]` | exactly the two files a release writes; `package.json` is not an asset (§2.1) |
| `message` | `chore(release): ${nextRelease.version} [skip ci]` + notes as body | the `[skip ci]` token (§2.4); the body is the recovery source of the notes (§5.4, R13) |
| github options | the three options `false` | keeps the run from needing `issues`/`pull-requests` (REQ-S110-03, §2.7.2) |

**The literal guard, and the guarded `sed`.** The same two rules, unfolded — the JSON above is the
normative spelling, this is the same text without the escaping:

```bash
# verifyConditionsCmd, first statement: before any signature, before any write
count=$(grep -cE '^version = "[0-9]+\.[0-9]+\.[0-9]+"$' pyproject.toml || true)
[ "$count" = 1 ] || { echo "release_version_mismatch: expected exactly one version literal line, found $count"; exit 1; }

# prepareCmd
expected="${nextRelease.version}"
before=$(grep -cE '^version = "[0-9]+\.[0-9]+\.[0-9]+"$' pyproject.toml || true)
[ "$before" = 1 ] || { echo "release_version_mismatch: expected exactly one version literal line before the substitution, found $before"; exit 1; }
sed 's/^version = "[^"]*"/version = "'"$expected"'"/' pyproject.toml > pyproject.toml.tmp
after=$(grep -cxF "version = \"$expected\"" pyproject.toml.tmp || true)
[ "$after" = 1 ] || { rm -f pyproject.toml.tmp; echo "release_version_mismatch: the substitution produced $after lines equal to $expected"; exit 1; }
mv pyproject.toml.tmp pyproject.toml
```

- **Why the guard runs in `verifyConditions` and not at tag time.** `verifyConditions` runs for every
  plugin before `prepare` (§2.3.1), i.e. before the changelog is regenerated, before the release commit
  exists and before anything is pushed. A literal spelled differently — one character out, a tab, single
  quotes — is refused there and the run aborts with `main` untouched. The same check at tag time runs
  *after* `prepare` pushed the commit: changelog regenerated, commit on `main`, tag aborted,
  `release_partial_state` (§5.5) for a file a `grep` could have rejected up front.
- **Why `prepareCmd` checks again.** It is the second and independent net: the `sed` rewrites only a line
  starting at column 0 with `version = "`, and **a `sed` that matches nothing is a silent no-op**. `before`
  proves the input had exactly one candidate line, `after` proves the temporary file carries exactly one
  line with the new value, and a substitution count that is not exactly `1` fails the command. A failure
  inside `prepare` leaves nothing on the remote, because `add`/`commit` precede `push`.
- **Nothing is half-written.** Both checks read `pyproject.toml.tmp`, and the file is replaced by `mv` only
  when `after == 1`: an interrupted or refused step leaves either the old file or the new one.
- The four consumers of the number — tag, changelog header, Release body, literal — are compared once more
  against the tag by the classifier (order 7, `release_version_mismatch`), which is the assertion that
  survives into the published state.

#### 2.5.2 The engine's behaviour the job relies on

| Situation | Engine behaviour | Contract |
|---|---|---|
| no relevant commits since the last tag | `There are no relevant changes, so no new version is released.`, exit `0`, no tag, no commit | green, no effects (R4) |
| `docs:` / `test:` only | same, because `releaseRules` maps them to `false` (§2.5.3) | green, no effects |
| `feat:` after `v0.1.0` | `0.2.0` | §2.5.3, R2 |
| `fix:` after `v0.1.0` | `0.1.1` | §2.5.3 |
| `BREAKING CHANGE:` footer | `major` | §2.5.3 |
| no tag at all | `There is no previous release, the next release version is 1.0.0` | the bootstrap exists for this (§5.2, R12) |
| a tag `vX.Y.Z` already exists for the computed version | `prepare` writes, commits and **pushes**, then `git tag` fails with *"tag already exists"* — the abort point is **after** the write (§2.3.1) | never re-created (R6); state → §5.5 |
| tag exists, GitHub Release does not | `no relevant changes`, nothing created | **not self-healing**: manual recovery (§5.4, R13) |

#### 2.5.3 `releaseRules` — the one bump table
| Commit | Row(s) that match | Bump | From `v0.1.0` |
|---|---|---|---|
| any type **with** a `BREAKING CHANGE:` footer | `{"breaking": true, …}` | **major** | `v1.0.0` |
| `feat: …` | `{"type": "feat"}` | **minor** | `v0.2.0` |
| `fix` · `perf` · `style` · `refactor` · `build` · `ci` · `chore` | one `{"type": …}` row each | patch | `v0.1.1` |
| `revert: …` | `{"type": "revert"}` | patch | `v0.1.1` |
| `Revert "…"` (the body of `git revert`) | `{"revert": true}` | patch | `v0.1.1` |
| `docs: …` · `test: …` | `{"type": …}`, `release: false` | none | no release |
| any other type (`wip:`, `hifa:`, …), or a subject with no conventional prefix | none | none | no release |

**How the analyzer resolves a commit** (`commit-analyzer@10.0.4 lib/analyze-commit.js`, code-read):

- It keeps **every** matching rule and returns the **highest** release type among them
  (`compareReleaseTypes`, early exit once `major` is found). Array order is therefore *not* semantic:
  `{"breaking": true}` is first for readability, and a `fix` with a `BREAKING CHANGE:` footer resolves to
  `major` because major outranks patch, not because the breaking row comes first. A lower-type match
  never masks a higher one, and a `{"type": …}` row does **not** disable the preset's breaking detection —
  breaking comes from the parsed note, which `{"breaking": true}` matches on `commit.notes`.
- What *is* semantic is `release: false`: it makes the analyzer return `false` instead of `undefined`, and
  only `undefined` falls back to the engine's built-in default rules — that is how `docs`/`test` are
  silenced explicitly rather than by accident.
- **`{"type": "revert"}` only matches a literal `revert:` subject.** A `git revert` commit has the header
  `Revert "…"`, which the parser does not turn into a `type`; it sets `commit.revert`, and
  `{"revert": true}` is the row that matches it. Both rows exist so the table is total — without the
  second, such a commit would still release a patch, but through the engine's default rules.
- **A `feat:` and its `Revert "…"` in the same range release nothing.** `conventional-commits-filter`, a
  dependency of `commit-analyzer`, removes a commit whose revert is present in the same range **before**
  the rules run: the pair cancels, so the answer is `There are no relevant changes`, not the patch
  `{"revert": true}` would produce for the revert alone.
- **`feat!: …` without a footer releases nothing — and disappears from the changelog.** The angular
  preset's `headerPattern` (`/^(\w*)(?:\((.*)\))?: (.*)$/`, `conventional-changelog-angular@6.0.0`) has no
  `!` and there is no `breakingHeaderPattern`, so `feat!` yields no `type` and no `subject`; no rule
  matches and the writer discards a commit with no type and no notes. `AGENTS.md` §9 therefore requires
  the footer on every breaking change.
- **`allow_zero_version`, `major_on_zero`, `add_partial_tags`, `--print` and rev 3's truth table A–G do
  not exist here.** Zero-version semantics are the engine's arithmetic from the last tag plus the
  `v0.1.0` bootstrap (R12); partial tags are excluded by `tagFormat`; the preview is `--dry-run` (§7.2);
  the guard is §2.4.2.

#### 2.5.4 What this contract cannot guarantee

1. **No inspection of the tag ref before it is created.** The commit is pushed in `prepare` (§2.3.1);
   the pre-flight removes the *signing* class of failures before that, not every failure between the two
   phases. The residual is §5.5.
2. **A missing GitHub Release is not recreated by a re-run** (§5.4, R13).
3. **Tag immutability is convention plus assertion, not platform enforcement.** `non_fast_forward` and
   `deletion` cover `refs/heads/main`; the ruleset has **no** tag rule, so a human with write access can
   force-move a tag. A second ruleset with `refs/tags/v*` targets is debt in D-039.
4. **A `type!:` subject is invisible to the engine and absent from the changelog** (§2.5.3).
5. **Reproducibility is bounded by the registry:** `npm ci` is exact against `package-lock.json`, not
   against the registry's availability or a compromised tarball.
6. **`workflow_dispatch` cannot release an arbitrary commit safely** (R5, §10).

### 2.6 The changelog: `CHANGELOG.md` is machine-owned
| Property | Value |
|---|---|
| File | `CHANGELOG.md` at the repository root, **versioned in git** |
| Producer / owner | `@semantic-release/changelog` (writes) + `@semantic-release/git` (commits); **the machine** — no hand edits, no hand-written header, no manual conflict resolution |
| First activation | the file **must not exist** before the first release; the plugin creates it |
| Section titles | `### Features`, `### Bug Fixes`, `### Performance Improvements`, `### Reverts`, `### ⚠ BREAKING CHANGES` |
| Header (measured) | `# [X.Y.Z](https://github.com/ginkelte/hifa/compare/vPREV...vX.Y.Z) (YYYY-MM-DD)` |
| Bullets (measured) | `* subject without the type(scope) prefix ([shortsha](…/commit/<sha>))` |

- **Why total and not "mostly".** The reference setup shipped a hand-written `# Changelog` heading; the
  plugin prepends its generated `# [1.0.1](…)` heading *above* the existing content and the file ended up
  with two competing headers. A conflict is never hand-merged: the branch is rebased, or the hunk is
  discarded and the next release rewrites the header.
- **Created and staged in the same run:** the plugin writes only when `nextRelease.notes` is truthy, and
  `@semantic-release/git` lists additions with `git ls-files -m -o`, so an untracked `CHANGELOG.md` is
  committed (code-read, §9). **The compare link needs a URL remote:** it is built from `origin`, which in
  CI is `https://github.com/ginkelte/hifa` (a local-path remote yields a broken link, §9), and step 9
  asserts the header contains `https://github.com/ginkelte/hifa/compare/`.
- Rev 3's `.github/release/notes.py` is **deleted**; normative is existence, ownership, header shape and
  section set, not a byte-for-byte template.

### 2.7 Secrets, the bypass, and what actually authorises the write

| Secret | Content | Visible to (exactly) | If absent |
|---|---|---|---|
| `HIFA_RELEASE_TOKEN` | classic PAT of an **admin** user, scope `repo` **required** — the installed token also carries `gist` + `read:org` (§2.7.2, D-039 corrigendum) — expiry ≤ 90 days | step 3 (presence check) and step 8 (the push credential and the engine's `GITHUB_TOKEN`), each through its own step-level `env:` (§2.4.3) | `release_token_missing`, printed once by step 3, run fails before any write |
| `HIFA_RELEASE_SIGNING_KEY` | the **private** half, OpenSSH format, no passphrase | step 6 only: signs the release commit, the pre-flight tag and the tag | `release_signing_key_missing`, printed once by step 6, run fails before any write |

- The public counterpart is a line in `.github/signers/allowed_signers` with principal `hifa-release`
  **and `namespaces="git"`**, mirroring the `ginkelte` line, committed through a PR. One allowlist, two
  checks: the `firmas` job verifies **commits**, step 9 verifies **commit and tag**.
- Key handling (REQ-S110-01): never printed (`set +x`, no `echo`, no `cat`, no `env` dump); written only
  to `$RUNNER_TEMP` with mode `0600`; removed with `if: always()`; rotation means a new keypair, its
  public line **added** (the old one kept, so published tags keep verifying) and the secret replaced.
  Rotating a key never rewrites a tag.
- The token never reaches `.git/config` and never reaches `$GITHUB_ENV`: the checkout does not persist
  credentials, and the push authenticates through `GIT_CONFIG_*` variables exported **inside the shell of
  step 8**, so they exist in the memory of that process and of its children and nowhere else (§2.4.1).
  Both absences are asserted after the write — `.git/config` by step 7 and the log-time surfaces by the
  classifier (`release_credential_persisted`, §2.4.2) — because an assertion that runs before the write
  cannot say anything about it.

#### 2.7.1 The admin bypass: scope of the exception (D-039)

The ruleset `main is PR-only` (id `27053963`) carries `bypass_actors: [{ "actor_id": 5, "actor_type":
"RepositoryRole", "bypass_mode": "always" }]`, and GitHub reports `current_user_can_bypass: always` for
the administrator. `actor_id 5` is the repository **admin** role. The four rules stay active:
`pull_request`, `required_status_checks` (`test`, `firmas`), `non_fast_forward`, `deletion`.

| What the bypass may be used for | What it must never be used for |
|---|---|
| pushing the single `chore(release): X [skip ci]` commit that the engine creates | merging a feature branch, or pushing any commit that is not a release commit |
| the engine's own `git push --tags` inside the job of `release.yml` | editing `.github/workflows/**` by hand, or pushing a "quick fix" to `main` |
| re-running a failed release | force-pushing, deleting `main`, deleting or moving a tag |

- **Tag pushes do not use the bypass.** The ruleset has no `refs/tags/*` rule (§2.5.4 point 3), so
  `git push --tags` is an ordinary write by a user with `contents: write`; only the commit needs the
  exception.
- **The exception is role-based, not token-based.** Any admin can push to `main`, with or without
  `HIFA_RELEASE_TOKEN`; the token authenticates a job that exercises a power an admin already has. That
  is why its blast radius is the whole repository and why its lifecycle is part of the contract.
  Everything else stays enforced: a non-admin collaborator, a fork PR, a bot with a fine-grained token
  and no bypass, and a `git push --force` of `main` are all still rejected.
- **Why the release commit must bypass.** It is written *after* the merge by a job that has no PR; a
  version bump cannot be a PR without making every release two steps and a second signed commit (D-037
  candidate (c), rejected).
- **The GitHub App alternative is deferred, not rejected:** it would scope the exception to one identity
  with its own audit trail, at the cost of an App registration, a private key and a token exchange (D-039
  records it as the upgrade path).

#### 2.7.2 The `permissions:` block does not govern a PAT
The workflow sets `GITHUB_TOKEN` from `secrets.HIFA_RELEASE_TOKEN`, so every call that reads it — the
engine's push, its API calls, `gh` — authenticates with the **PAT**, not with the job's installation
token.

- **`permissions: contents: write` is inert.** It maps scopes onto the *job's* `GITHUB_TOKEN`; it cannot
  narrow a PAT. The effective authorisation is the PAT's scopes, and a classic PAT with `repo` is
  read/write on every repository resource of the account: contents, issues, pull requests, hooks,
  releases. The block is kept as a declaration of intent (and because it does govern any action using the
  implicit `github.token`, e.g. a checkout with `persist-credentials: true`, which step 1 forbids) — not
  because it constrains the engine.
- **Scopes: `repo` is required, and `gist` + `read:org` are the accepted ceiling of the token that is
  installed.** `repo` covers commit push, tag push and release creation; nothing here needs `issues`,
  `pull-requests` or `packages`; `workflow` is required only to push changes under
  `.github/workflows/**`, which no step does, so a PAT carrying it is a deliberate widening for other,
  manual uses and must be justified as such. **The installed token is not a `repo`-only classic PAT:** it
  was generated with the `gh` CLI device flow, whose OAuth app appends `gist` and `read:org` to every
  token it issues and cannot be asked to drop them, so GitHub reports `x-oauth-scopes: gist, read:org,
  repo`. That is a **documented deviation, not compliance**: `repo` stays the requirement here, in §2.7
  and in REQ-S110-03, and D-039 carries the corrigendum that records the measured set. What carries the
  weight is what is **absent** — no `admin:ssh_signing_key` (it would let the token replace the account's
  signing keys and forge a "signed" release) and no `workflow` (the one write §2.7 forbids). `gist` and
  `read:org` authorise nothing this pipeline does.
- **The levers that do work:** `persist-credentials: false` (no token on disk), the token in
  `GIT_CONFIG_*` (never in `.git/config`), `fetch-depth: 0` against a public remote, the three github
  options off (§2.5, so no `issues`/`pull-requests` call happens at all), and the 90-day expiry.

### 2.8 Dependencies, Node version and the lockfile
| Where | What | Why it is allowed |
|---|---|---|
| `package.json` (`devDependencies`, `private: true`) | `semantic-release 21.1.2`, `@semantic-release/changelog 6.0.3`, `@semantic-release/exec 6.0.3`, `@semantic-release/git 10.0.1`, `@semantic-release/github 9.2.6` — **exact versions, no ranges** | tooling only; installed with `npm ci`; never part of the wheel |
| `package-lock.json` | the exact tree, **versioned**, `lockfileVersion` 3 | `npm ci` refuses to install without it and fails when it disagrees with `package.json` |
| `[project.dependencies]` | **unchanged** (`typer`, `pydantic`, `markdown-it-py`, `python-ulid`) | REQ-N007 (SPEC-000 §4.4), REQ-N050-04 (SPEC-050 §4) |
| `[build-system].requires` / `[dependency-groups] dev` | `["hatchling"]` / no `python-semantic-release` row | §2.1, D-039 |

- `@semantic-release/commit-analyzer@10.0.4` and `@semantic-release/release-notes-generator@11.0.7` are
  **not** declared: they are dependencies of `semantic-release`, resolved from its tree and pinned by the
  lockfile.
- **`package-lock.json` is a generated artefact, not a document to hand-edit.** Policy: **who** — the
  implementation PR's author, with **Node 22.22.3 / npm 10.9.8**, the pair the workflow pins (§2.4 step
  2), because another npm major can produce a different `lockfileVersion` and tree; **when** — only in a
  PR that changes `package.json` (an engine or plugin bump), never "to fix CI"; **verified** — `npm ci` in
  the job (it fails when manifest and lockfile disagree), `git diff --exit-code package-lock.json` after
  it, and a review of the diff as "which packages changed and why"; **forbidden** — `npm install` in the
  job, an inline manifest, `^` ranges, a lockfile from `--no-package-lock`.
- The reference workflow generated a `package.json` in the runner with `^` ranges and no lockfile: two runs
  a month apart could analyse the same commits with two different parsers, in a job that can write to
  `main`. Nothing of the tooling is imported at runtime (REQ-S110-02); Node is not a runtime dependency.

### 2.9 Files this component governs

| File | Contract |
|---|---|
| `.github/workflows/release.yml` | §2.4, ten steps in order |
| `.releaserc.json` | §2.5, verbatim |
| `.github/release/tag-message.sh` | the `GIT_EDITOR` helper: requires exactly one argument, reads the tag name git is creating from the message file's own header, writes that name back and exits non-zero on anything ambiguous or outside the two accepted patterns (§2.3.3). It never reads `pyproject.toml` |
| `.github/release/assert-outcome.sh` | the outcome classifier of §2.4.2: reads `RELEASE_LOG`, `RELEASE_EXIT_CODE` (the **input** sentinel `255` when unset, never `0`), `RELEASE_DRY_RUN`, `PRE_RUN_*`, `TAG` (taken only from the log line `Created tag <tag>`, absent ⇒ order 13), the dedupe mark and the repository; **takes no arguments and reads no stdin**; prints **exactly one** classification line — `green: nothing written`, `green: no relevant changes`, `green: released <tag>`, or `release_<code>` — and exits `0` only for G1/G2/G3, `1` otherwise. Executable, `bash`, **no network writes**: `git ls-remote` is read only (for `refs/heads/main` and `refs/tags/*`, re-queried wherever an order must verify the fact it is about to print), there is no `fetch` and no `push`; no write to the repository; deterministic given its inputs |
| `tests/e2e/test_release_outcome.py` | executes both governed scripts: one fixture per evaluation order of §2.4.2 and per catalogue id, a negative fixture per order (the precedence), and a throwaway git repository in `$TMPDIR` for every assertion that reads refs or signatures |
| `package.json`, `package-lock.json` | §2.8; `private: true`, `version: "0.0.0"` |
| `CHANGELOG.md` | §2.6, machine-owned |
| `pyproject.toml` | only `[project].version` and these absences: `dynamic`, `hatch-vcs` and `setuptools-scm` in `[build-system].requires`, `[tool.hatch.version]`, the `python-semantic-release` row of `[dependency-groups].dev`, `[tool.semantic_release]` (§2.1). Everything else is SPEC-000's (D-038, D-039) |
| `src/hifa/__init__.py` | §2.1, the `__version__` symbol and its fallback |
| `.github/signers/allowed_signers` | the `hifa-release` line (§2.7) |
| `tests/unit/test_version.py`, `tests/e2e/test_release_contract.py`, `tests/e2e/test_release_outcome.py`, `tests/fixtures/release/*.log` | the tests of §8 step 11 |

### 2.10 Documentation this component governs, and how it is compared
| File | Section | Required content |
|---|---|---|
| `AGENTS.md` | §6 (workflow) | one step: a merged conventional PR on `main` is released by `release.yml`; the pipeline writes exactly one release commit, which enters `main` through the admin bypass (D-039); the version is the literal written by that commit and `hifa --version` reads the installed metadata |
| `AGENTS.md` | §9 (commit convention) | the type list and the bump mapping of §2.5.3, inside the machine-comparable block below |
| `README.md` | "Contributing" | how a release happens, how to read the version, how to verify a tag (`git verify-tag`), that `CHANGELOG.md` is generated, and both manual recoveries (§5.4, §5.5) |

**The comparable block.** `AGENTS.md` §9 carries exactly this text between the markers (same table, same
rows, same order), and so does this section:

<!-- release-types:begin -->
| type | release |
|---|---|
| any type with a `BREAKING CHANGE:` footer | major |
| feat | minor |
| fix, perf, style, refactor, build, ci, chore, revert | patch |
| docs, test | none |
<!-- release-types:end -->

- **§9 must be identical to `releaseRules`, in both directions.** The reference setup contradicted
  itself: its `.releaserc.json` mapped `style`, `ci`, `chore` and `build` to `patch` while its own doc
  said "no release" for the same four types. Runtime follows the config, so the documentation was wrong
  for four of eleven types. Here the comparison is mechanical: §7.2 extracts the block from both files and
  `diff`s them (R15) — two `grep`s would print lines, not compare them, which is why the markers exist.
- **§9 is extended, not replaced**: it lists six types today (`feat, fix, docs, test, refactor, chore`)
  and needs eleven plus the two rules the table cannot express; without the amendment the documents
  disagree the first time somebody writes `perf:`, `style:` or `build:`. Same PR as the workflow
  (`AGENTS.md` §8). The `README.md:378` counters belong to the implementation PR (R15); this spec change
  updates `specs/INDEX.md` only.

### 2.11 Version examples in the other specs are illustrative, not literals
**Six occurrences in four specs** read `__version__` and inherit whatever the installation reports:

| # | Surface | Location |
|---|---|---|
| 1 | `hifa doctor --json`, the `version` field | SPEC-050 §2.7, the `console` block at 050:908 |
| 2 | `hifa serve` banner (`hifa 0.1.0 on http://…`) | SPEC-050 §2.7, the block at 050:913 |
| 3 | `hifa serve --host 0.0.0.0` AC ("the warning appears before `hifa 0.1.0 on`") | SPEC-050 §7, 050:1326 — an **ordering** assertion |
| 4 | `GET /health`, the `version` field | SPEC-060 §2.7, the block at 060:833 |
| 5 | read-model `version` | SPEC-070 §2.4, 070:237 |
| 6 | export `app_version` | SPEC-080 §2.5, 080:333 |

- They say `0.1.0` because that was the declared version when they were written: correct for an
  installation of `v0.1.0`, and a **shape**, not an assertion. The only contract tying a number to an
  artefact is §2.2. SPEC-050 §2.2 (`hifa --version`) has **no** example block with a literal — its
  contract is prose, which is why it is absent here.
- Refreshing them needs a `spec_revision` bump of SPEC-050/060/070/080 and stays **accepted debt**
  (D-038).

## 3. Data it touches

No SQLite table: this component never opens a database, never reads `.hifa.toml`, never touches
`~/.local/share/hifa/hifa.db`.

| Artefact | Operation | Invariant |
|---|---|---|
| `refs/tags/v*` | CREATE (annotated, SSH-signed) | never moved, deleted or re-created; one tag per release |
| `refs/tags/preflight-*` | CREATE then DELETE, locally | never pushed; deleted in the same command (§2.3.1) |
| `refs/heads/main` | one commit appended per release (admin bypass) | exactly one commit per release, always `chore(release): X [skip ci]` |
| `refs/notes/semantic-release` | CREATE/UPDATE by the engine | pushed with `--tags`; carries no product data |
| `CHANGELOG.md` | CREATE/UPDATE | machine-owned; one header per version, newest first (§2.6) |
| `pyproject.toml` | one line rewritten | `version = "X.Y.Z"` equals the tag of the release commit (§2.5) |
| `package.json`, `package-lock.json` | read by `npm ci` | never modified by the pipeline; `version` stays `0.0.0` |
| GitHub Release for a tag | CREATE once | one release per tag, body = the notes |

## 4. Business rules

- **R1 — One product version literal (REQ-F110-01).** §2.1: `pyproject.toml` carries the only release
  version literal, written exclusively by the `prepareCmd`; `__version__` derives from installed metadata;
  `package.json` carries npm's `0.0.0` placeholder. Verified by §7.2 and the AC "the literal and the tag
  agree".
- **R2 — Bump mapping (REQ-F110-02).** §2.5.3 is total for the eleven types of §2.10 plus the breaking and
  revert shapes, and silent for everything else: every commit maps to exactly one bump level or to none.
- **R3 — Zero-version semantics (REQ-F110-03).** The engine bumps from the last tag: `feat` ⇒ `0.(Y+1).0`,
  `fix` ⇒ `0.Y.(Z+1)`, a `BREAKING CHANGE:` footer ⇒ `1.0.0`; with **no tag** it answers `1.0.0`
  (measured, §9), and the bootstrap (R12) keeps the line at `0.x`.
- **R4 — Nothing to release is success (REQ-F110-04).** No relevant commits ⇒ `no relevant changes`,
  exit `0`, no tag, no commit, no release (measured): a `docs:`-only merge must not paint the repository
  red.
- **R5 — `main` only (REQ-F110-05).** `push` to `main` and manual dispatch; no `pull_request` trigger,
  because a release cut from an unmerged branch would tag code nobody reviewed and the tag is immutable.
- **R6 — Immutable tag (REQ-F110-06).** §2.3.2: never moved, deleted, re-created or force-pushed, not even
  an unverified one. The engine refuses to *create* an existing tag, but that refusal happens **after**
  `prepare` pushed the commit (§2.3.1); the partial state is repaired by §5.5, never a re-run.
- **R7 — Signed or nothing (REQ-F110-07).** The commit is signed (`commit.gpgSign=true`), the tag is signed
  at creation (`tag.gpgSign` + `GIT_EDITOR`, §2.3), and signing is **pre-flighted in `verifyConditions`**
  so an unusable key aborts before any write (§2.3.1). A tag that is not an annotated object still fails
  afterwards (`release_tag_not_signed`). There is no unsigned path.
- **R8 — Secret hygiene (REQ-S110-01, REQ-S110-03).** §2.7: the key is never printed, lives in
  `$RUNNER_TEMP` with mode `0600`, is removed with `if: always()`; the token reaches neither `.git/config`
  nor `$GITHUB_ENV` (§2.4.1, §2.4.3); both absences are hard failures, and the second is asserted **after**
  the write that could violate it (`release_credential_persisted`).
- **R9 — Notes and changelog are engine output (REQ-F110-08).** The release body and the `CHANGELOG.md`
  entry come from the notes generator and the changelog plugin over `LAST_TAG..HEAD`; the job asserts they
  exist and mention the version, never their layout (§2.6).
- **R10 — `--version` matches the tag (REQ-F110-09).** On the tagged commit `hifa --version` prints
  `hifa X.Y.Z` and the wheel from that commit declares `Version: X.Y.Z`; an off-tag install reports the
  last release, never a claim about HEAD (§2.2).
- **R11 — Exactly one write to `main`, and only that one (REQ-F110-10).** One commit and one tag per
  release; never a second commit, an amend, a force-push, or any file other than `CHANGELOG.md` and the
  `version` line — authorised by the admin bypass (§2.7.1, D-039) and by nothing else.
- **R12 — The baseline tag is manual, one time, and lands on a commit that carries the literal
  (REQ-F110-11).** §5.2: signed `v0.1.0` on the merge commit of the PR that restores `version = "0.1.0"`,
  with `hifa --version` verified to print `hifa 0.1.0` at that tag. Without the tag the first automatic
  `feat:` release is `1.0.0` (measured); on a commit that does **not** carry the literal, R10 is violated
  by the baseline itself.
- **R13 — Re-runnable, and honest about what re-running does not fix (REQ-F110-12).** Re-running at a
  released commit is a no-op (measured) and the workflow ignores its own commit (§2.4), so no loop. A
  **missing GitHub Release is not recreated** (`gh release create "$TAG" --title "$TAG" --notes-file
  <body of the release commit>`, in `README.md`), and a **partial state is not repaired** either (§5.5).
- **R14 — CI neutrality (REQ-F110-13).** `ci.yml` and `signatures.yml` are not modified and the release job
  is not a required check. With the literal restored CI installs `X.Y.Z` instead of a `devN` version, and
  `test` and `firmas` stay version-agnostic because **no test may assert a version value** (R17), not
  because none happens to.
- **R15 — The documentation ships with the pipeline, and is compared mechanically (REQ-F110-14).** The
  implementation PR updates `AGENTS.md` §6/§9 and `README.md` as §2.10 requires, and §7.2 diffs the marked
  block between `AGENTS.md` and this spec, so a divergence fails a check instead of a review.
- **R16 — The bypass is scoped, auditable and revocable (REQ-S110-05).** It exists for the release commit
  only (§2.7.1), is revocable in one API call (`bypass_actors: []`), and the four rules keep applying to
  everyone else.

- **R17 — The version test asserts the shape and the uniqueness, never the value (REQ-F110-15).** §2.1.1:
  `tests/unit/test_version.py` asserts that the literal matches `^[0-9]+\.[0-9]+\.[0-9]+$` (or is the
  documented fallback), that exactly one such line exists, and that it agrees with what `tomllib` parses; a
  concrete number is a defect. Verified by the simulation of §7.2 — with `version = "0.2.0"` in a scratch
  copy the suite is green — because a test that pins the current version breaks the first release of the
  design it protects.
- **R18 — A secret is visible only to the step that uses it (REQ-S110-06).** §2.4.3: `env:` per step and
  never at workflow or job level; the token in steps 3 and 8 only, the key in step 6 only, `GITHUB_TOKEN`
  in step 8 only; **`npm ci --ignore-scripts` mandatory**; `$GITHUB_ENV` carries no secret, no credential
  and no base64 of either. Verified by §7.2 (the workflow is parsed, not grepped) and by the classifier's
  evaluation order 1.
- **R19 — Exactly one code per run, and the owner of the line is named (REQ-F110-16).** §2.4.2: catalogue
  ids 1 and 2 belong to steps 3 and 6, which print the code, write the dedupe mark and exit red; every
  other catalogue id belongs to the classifier, which prints nothing at all when the mark exists. Verified
  by the outcome suite (a fixture with the mark present produces no classification line) and by the AC
  "exactly one `release_` code appears in the log".
- **R20 — The outcome classifier is a governed script, not text in YAML (REQ-F110-17, D-040).** §2.4.2
  states its interface — the environment inputs including `TAG`, the three exact green lines, and the fact
  that it takes no arguments and reads no stdin — §2.9 lists it among the files this component governs, and
  pytest executes it against fixture logs and a throwaway repository: one fixture per evaluation order and
  at least one negative fixture per order. **The criterion is the six semantic mutations of D-040**: the six
  that kept every asserted string byte-identical and passed rev 5's document-shaped suite **31/31**. D-040
  names two of them explicitly — a raw PAT written into the header instead of the basic-auth pair, and the
  release-commit signature check turned off — and the criterion is the six, not the two. **A fixture per
  order proves the classifier and cannot catch a mutation of the workflow text**, because no fixture
  observes `release.yml`: for those the assert has to be named — the `GIT_CONFIG_VALUE_0` header being the
  basic pair and not the bare token, `commit.gpgSign true`, order 6 actually reaching `verify-commit`, the
  pre-flight's opening line. §7.2 carries them. A rule that exists only as a string inside the workflow is
  not executable and therefore not verified (P8).
### 4.1 Non-functional requirements

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N110-01** | The release job completes in **< 6 min** p50 on `ubuntu-latest`. | `gh run list --workflow release.yml --json durationMs` |
| **REQ-N110-02** | `hifa --version` stays **< 250 ms** p50 warm (SPEC-050 REQ-N050-02 is not weakened by reading metadata). | `hyperfine --warmup 3 '.venv/bin/hifa --version'` |
| **REQ-N110-03** | The tooling adds **0 bytes** to the runtime dependency set: `[project.dependencies]` unchanged and no `semantic-release` reference in the wheel. | `python -c "import importlib.metadata as m; print(m.requires('hifa'))"` |
| **REQ-N110-04** | The workflow is static: the version comes from the commit log, never from a matrix, an input or a hand-typed number. | review of `release.yml` + AC "a dry run writes nothing" |
| **REQ-N110-05** | `npm ci` is the only install path, from the versioned lockfile, with the pinned Node (§2.8). | `grep -n 'npm ci' .github/workflows/release.yml`; `grep -c 'npm install'` ⇒ `0` |

### 4.2 Security requirements

| ID | Requirement | Verification |
|---|---|---|
| **REQ-S110-01** | The signing key is read only from `HIFA_RELEASE_SIGNING_KEY`, never printed, never persisted in the workspace, removed with `if: always()`; its absence fails the run before any write. | AC "a release without the signing secret fails without publishing" + grep of `release.yml` |
| **REQ-S110-02** | Nothing of the tooling is imported at runtime: `import hifa` loads no Node module and performs no network call. | `python -X importtime -c "import hifa"`; `sys.modules` after `hifa --version` |
| **REQ-S110-03** | Least privilege, at its real boundary: the PAT carries `repo` and nothing else (§2.7.2); `persist-credentials: false`; the token travels in `GIT_CONFIG_*` and never in `.git/config`, `.git/credentials` or the remote URL; no plugin option triggers an `issues`/`pull-requests` call. The `permissions:` block is **not** the control (§2.7.2). | the PAT's scope list; `grep -c extraheader .git/config` ⇒ `0`; `grep -n 'successComment' -A 2 .releaserc.json` ⇒ three `false` |
| **REQ-S110-04** | A release is verifiable by a third party from the repository alone: tag signature against the committed `allowed_signers`, signed release commit, notes in the Release, version in the wheel. | `git verify-tag` + `git verify-commit` on a fresh clone |
| **REQ-S110-05** | The admin bypass is deliberate, documented and minimal: four rules stay active, the only exempt actor class is `RepositoryRole admin`, and the token is used by no other workflow. | `gh api repos/ginkelte/hifa/rulesets/27053963` ⇒ four rules + one `bypass_actors` entry; AC "the ruleset still rejects a direct push from a non-admin actor" |
| **REQ-S110-06** | Least privilege per step: no `env:` at workflow or job level, the token only in steps 3 and 8, the signing key only in step 6, `GITHUB_TOKEN` only in step 8, `npm ci --ignore-scripts`, and no secret, credential or base64 of either in `$GITHUB_ENV` (§2.4.3). | the workflow parsed as YAML (§7.2 asserts the `env:` map of every step); `grep -c 'npm ci --ignore-scripts'` ⇒ `1`; classifier order 1 (`release_credential_persisted`) |

## 5. Flow

### 5.1 Nominal release

```
 1-7.  steps 1-7 of §2.4: checkout, node 22.22.3, token gate (§2.4.3 step-level env), npm ci
       --ignore-scripts, dry-run split, identity and signing (private key in $RUNNER_TEMP, §2.4.1),
       credential scope check
 8.    ./node_modules/.bin/semantic-release, with GIT_CONFIG_* exported inside its own shell only
       (§2.4.1), in the engine's own order (§2.3.1):
         verifyConditions => signing pre-flight (preflight tag signed, verified, deleted)
         analyzer         => LAST_TAG..HEAD => "next release version is 0.2.0"
         notes/changelog  => body + CHANGELOG.md regenerated, new header first
         exec             => sed writes version = "0.2.0" in pyproject.toml
         git              => commit chore(release): 0.2.0 [skip ci] (signed) AND push to main
         engine           => git tag v0.2.0 <release commit>, then git push --tags origin
         github           => GitHub Release v0.2.0 with the notes
 9-10. .github/release/assert-outcome.sh (if: always(), §2.4.2) prints the one line of this run, then
       cleanup (key and preflight refs removed)
```

### 5.2 Bootstrap of the first tag (one time, manual, two PRs)

The literal and the workflow cannot land in the same merge: the push that introduces `release.yml` is
itself a release trigger, and a tag created afterwards loses the race. So:

```
 1. PR-A: restore version = "0.1.0"; requires = ["hatchling"] (drop hatch-vcs and setuptools-scm);
    drop dynamic, [tool.hatch.version], the PSR row and [tool.semantic_release]; update
    tests/unit/test_version.py.  Merge it — no workflow exists yet, so nothing auto-releases.
 2. git switch main && git pull --ff-only
 3. TARGET_SHA=$(git rev-parse HEAD)                    # the merge commit of PR-A
    test "$(grep -cE '^version = "0.1.0"$' pyproject.toml)" = 1
 4. git tag -s -m "v0.1.0" v0.1.0 "$TARGET_SHA"         # names the commit, does not mark HEAD
 5. git verify-tag v0.1.0 && git push origin refs/tags/v0.1.0
 6. verify R10 at that tag: build a wheel from "$TARGET_SHA", install it in a scratch venv,
    hifa --version  =>  "hifa 0.1.0"
 7. PR-B: add release.yml, .releaserc.json, package.json + package-lock.json, tag-message.sh, the
    allowed_signers line, AGENTS.md §6/§9 and README "Contributing".  Merge it: its push is the first
    release run, it sees v0.1.0, and it can only produce 0.1.x / 0.2.0 (or nothing) — never 1.0.0.
```

- **The tag names its commit.** Not `git tag -s v0.1.0`, which would mark whatever `HEAD` is: the
  precondition is that the commit carries `version = "0.1.0"` and `[build-system].requires ==
  ["hatchling"]`, so no git-derived backend can override the literal. Tagging a commit with `dynamic =
  ["version"]` would make the baseline itself violate R10.
- **Why it is mandatory:** measured, a tag-free history makes the engine answer `1.0.0` for the first
  `feat:`, and HIFA-22's "a `feat` produces `0.x.0`" only holds with a seeded `0.1.0`.
- **Why it is manual:** it is the one version no commit can derive, and a tag is an assertion about what
  shipped.

### 5.3 Nothing to release

A `docs:`-only (or `test:`-only, or unmapped-type) merge takes G3 of §2.4.2: the analyzer
maps every commit to "no release", the engine logs `There are no relevant changes, so no new version is
released.` and exits `0`, and nothing is written — no commit, no tag, no `CHANGELOG` entry, no Release.
A re-run at an already released commit takes the same path (R13).

### 5.4 Failure and recovery

| Failure | State left behind | Recovery |
|---|---|---|
| `release_token_missing` / `release_signing_key_missing` | nothing written | set the secret, re-run |
| `release_signing_key_invalid` — the **pre-flight** (step 8, `verifyConditions`) | **nothing written, nothing pushed** | fix the key or the helper, re-run (§2.3.1) |
| a prepare-phase failure (the commit cannot be signed, the `sed` fails, the commit is refused) | nothing pushed: `add`/`commit` precede `push` in `prepare` | fix, re-run from a clean checkout |
| **the commit push is rejected** (bypass removed, expired token, no `contents: write`) | the release commit exists **locally only** and **no tag exists**: the tag is created after `prepare` (§2.3.1), so the failure lands before it. Nothing of this release is on the remote; `main` unchanged | restore the bypass/token, re-run: it recomputes the same version, nothing is duplicated because nothing arrived |
| **the tag push is rejected**, or `git tag` itself fails (key, empty message, broken editor, tag already exists) | commit + literal on `main`, **no tag**: the partial state | §5.5 — never a re-run |
| **the notes push is rejected** (`refs/notes/semantic-release` not a fast-forward, or a ruleset on the notes namespace) | **the release itself is complete**: commit, tag and Release are public; only the notes ref is missing | one `git push origin refs/notes/semantic-release` from a checkout that fetched the remote ref; the notes are also the body of the release commit, so nothing is rebuilt (step 4 makes the push a fast-forward in the first place) |
| release creation failed after the tag was pushed | tag on the remote, no Release, a re-run is a no-op | `gh release create "$TAG" --title "$TAG" --notes-file <body of the release commit>` (R13) |
| `release_version_mismatch` — the guard in `verifyConditions` | **nothing written, nothing pushed** | fix the literal's spelling in `pyproject.toml` and re-run (§2.5.1) |
| `release_version_mismatch` — the literal at the tag | tag and commit on the remote, literal not bumped | fix §2.5 / the literal's spelling, then release the next version (never move the tag) |
| `release_credential_persisted` | the release may have succeeded; `$GITHUB_ENV` or `.git/config` carried the token or the key | treat the credential as compromised: rotate the token (and the key if it was the leaked one), then repair the workflow (§2.4.1) |
| `release_tag_not_signed` | tag on the remote, unsigned or lightweight | rotate the key / fix `tag.gpgSign`; report and stop, never delete (§6) |

### 5.5 The partial state: a release commit on `main` with no tag
**Detection** (step 9, `if: always()`): `origin/main`'s head subject is `chore(release): X.Y.Z [skip ci]`,
`git show origin/main:pyproject.toml` carries `version = "X.Y.Z"`, and
`git ls-remote --tags origin "refs/tags/vX.Y.Z"` is empty. Step 9 prints `release_partial_state`, exit `1`.

**Repair — by hand, once, without recomputing the version and without touching the changelog:**

```
 1. git fetch --tags origin
 2. test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"            # on the release commit
 3. test "$(grep -cE '^version = "X\.Y\.Z"$' pyproject.toml)" = 1            # the literal is there
 4. test "$(git show 'HEAD:CHANGELOG.md' | grep -cE '^# \[X\.Y\.Z\]')" = 1   # exactly one header
 5. git tag -s -m "vX.Y.Z" vX.Y.Z "$(git rev-parse HEAD)"                    # names the commit, signed
 6. git verify-tag vX.Y.Z
 7. git push origin refs/tags/vX.Y.Z
 8. gh release create vX.Y.Z --title vX.Y.Z --notes-file <(git log -1 --format=%b HEAD)
```

- **Why not a re-run.** The range `v0.1.0..HEAD` still contains the `feat:`/`fix:` that produced `X.Y.Z`,
  so the analyzer computes the **same** version; the changelog plugin then prepends a second
  `# [X.Y.Z](…)` header above the existing one (it concatenates `notes` with the current content and never
  de-duplicates — code-read, §9), the `sed` is a no-op, and `@semantic-release/git` appends a **second**
  `chore(release): X.Y.Z [skip ci]` commit before the tag step finally succeeds. The run ends green with a
  duplicated header and two release commits — worse than the state it repaired, and irreversible without
  rewriting `main`.
- **Why the tag names the commit.** The literal lives in the release commit, so a tag anywhere else breaks
  R10 — the same rule as the bootstrap (§5.2). Steps 2–4 are the precondition: they prove the tagged commit
  is the one carrying `X.Y.Z`, with exactly one changelog header; if step 4 finds zero rows, the recovery is
  the missing-Release row of §5.4 instead.
- **Not repaired here:** a missing Release on an existing tag (step 8 does it) and an existing unsigned tag
  (`release_tag_not_signed`: report, never delete).

## 6. Errors

The component has no CLI exit codes: it fails a **workflow run**. Codes are prefixed `release_`. **The
producing step and the precedence live only in §2.4.2**; this table is the catalogue of *when* each code
fires and *what it leaves behind*.

| Code | When | Effect on the repository |
|---|---|---|
| `release_token_missing` | `HIFA_RELEASE_TOKEN` absent or empty | nothing written |
| `release_signing_key_missing` | `HIFA_RELEASE_SIGNING_KEY` absent or empty | nothing written |
| `release_signing_key_invalid` | the pre-flight cannot sign or verify the `preflight-*` tag (`ssh-keygen -y`, `git tag`, `git verify-tag`) | **nothing written, nothing pushed** |
| `release_partial_state` | a release commit with literal `X.Y.Z` is on `main` and no `vX.Y.Z` tag exists | commit on `main`, no tag → §5.5 |
| `release_engine_aborted` | the engine exited without a release and without the `no relevant changes` token (§9's silent-abort class) | depends on the phase; reported instead of passing as a no-op |
| `release_version_mismatch` | the literal guard in `verifyConditions` found a count other than one, or (at the tag) the literal ≠ the tag or `grep -c '^version = "'` ≠ `1` | nothing written when the guard fired; otherwise tag + commit on the remote and the next release fixes it |
| `release_credential_persisted` | `$GITHUB_ENV` or `.git/config` carries the token, the key, an `extraheader` or a base64 of either, **after** step 8 ran | the repository state is whatever the engine produced; the credential is rotation debt, not a repository defect |
| `release_tag_not_signed` | `git cat-file -t refs/tags/$TAG` is not `tag`, or `git verify-tag` fails | tag is public and immutable: **not** deleted |
| `release_commit_unsigned` | `git verify-commit "$TAG^{commit}"` fails against `allowed_signers` | commit on `main`; the report names it so it can be re-signed forward |
| `release_tag_push_rejected` | the remote rejected the tag push (ruleset, expired token, no `contents: write`), verified: the tag the log says was created is absent from `git ls-remote --tags origin`. A rejected push of `refs/notes/` is `release_notes_push_rejected`, not this code; the message never echoes the credential | commit on `main`, no tag → §5.5 |
| `release_notes_push_rejected` | the remote rejected the push of `refs/notes/semantic-release` while `git ls-remote --tags origin` lists the tag the log says was created: the commit, the tag and the Release are public and only the notes ref is not | nothing to rebuild: `git push origin refs/notes/semantic-release` restores the ref, and the notes are also the body of the release commit |
| `release_forbidden` | the API refused release creation (403) | tag exists, no Release (§5.4) |
| `release_notes_missing` | `CHANGELOG.md` at the tag does not mention the version — the Release body is not observable from the runner, so the changelog is its local proxy (§2.4.2, catalogue id 8) | tag + commit exist; the body is recoverable from the commit message (R13) |

**A red run means a human must look.** The accepted outcomes are G1–G3 of §2.4.2 and all three say so in
the log; anything else is a failure by construction of step 9 — there is no fourth "quiet" state.

## 7. Acceptance Criteria

### 7.1 Scenarios
Each scenario has exactly one `When`. Engine-level ones were reproduced in a scratch repository with
`semantic-release 21.1.2` (§9); workflow-level ones run against the real repository.

```gherkin
@SPEC-110
Feature: Semantic release pipeline
  Scenario: a documentation-only merge releases nothing
    Given main has the signed tag "v0.2.0" and a PR whose only commit is "docs: fix the contributing section" is merged
    When the "Release" workflow runs for that merge
    Then the run succeeds and the log contains "There are no relevant changes"
    And no new tag, no new GitHub Release and no new commit on main exist
  Scenario: a merged feature produces a minor release
    Given main has the signed tag "v0.1.0" and a PR with the commit "feat: add signed release tags" is merged
    When the "Release" workflow runs for that merge
    Then the tag "v0.2.0" exists on the remote and points at the release commit
    And exactly one commit was appended to main by the workflow
  Scenario: every patch type produces a patch release
    Given main has the signed tag "v0.2.0" and a commit of each type "fix", "perf", "style", "refactor", "build", "ci" and "chore" is on main
    When "./node_modules/.bin/semantic-release --no-ci --dry-run" is executed
    Then the output contains "next release version is 0.2.1"
    And the log reports "patch" for each of those commits
    And the notes the dry run prints contain "### Bug Fixes"
    And the tree is untouched: no "chore(release):" commit, no tag "v0.2.1", CHANGELOG.md not written
  Scenario: a breaking change with a footer produces a major release
    Given main has the signed tag "v0.2.2" and a PR with "feat: drop the legacy flag" and the footer "BREAKING CHANGE: the flag is gone" is merged
    When the "Release" workflow runs for that merge
    Then the tag "v1.0.0" exists on the remote and CHANGELOG.md contains "BREAKING CHANGES"
  Scenario: a fix with a breaking footer produces a major release, not a patch
    Given main has the signed tag "v1.0.0" and a commit "fix: change the status enum" with the footer "BREAKING CHANGE: todo is now open" is on main
    When "./node_modules/.bin/semantic-release --no-ci --dry-run" is executed
    Then the output contains "next release version is 2.0.0"
  Scenario: a git-revert commit also produces a patch release
    Given main has the signed tag "v0.2.0" and a commit created by "git revert" whose header is 'Revert "feat: add the search box"' is on main
    When "./node_modules/.bin/semantic-release --no-ci --dry-run" is executed
    Then the output contains "next release version is 0.2.1"
  Scenario: a breaking subject without a footer releases nothing and is not in the changelog
    Given main has the signed tag "v0.2.2" and a commit whose subject is "feat!: drop the legacy flag" and whose body has no footer is on main
    When "./node_modules/.bin/semantic-release --no-ci --dry-run" is executed
    Then the output contains "There are no relevant changes"
    And the commit does not appear in the generated notes
  Scenario: without the bootstrap tag the first release is 1.0.0
    Given a clone of the repository with no tag at all and a conventional commit "feat: probe" on main
    When "./node_modules/.bin/semantic-release --no-ci --dry-run" is executed in that clone
    Then the output contains "There is no previous release, the next release version is 1.0.0"
  Scenario: the first automatic release after the bootstrap stays on the 0.x line, never 1.0.0
    Given the bootstrap tag "v0.1.0" sits on the literal PR and the release.yml PR (or a later "feat:" PR) is merged
    When the "Release" workflow runs
    Then the computed version is "0.1.1", "0.2.0", or there is no release
    And no tag "v1.0.0" exists
  Scenario: the bootstrap tag carries the literal it claims
    Given the merge commit of the literal PR, tagged "v0.1.0"
    When a wheel is built from that tag and "hifa --version" is executed
    Then the output is exactly "hifa 0.1.0"
    And pyproject.toml at that tag has requires = ["hatchling"]
  Scenario: an existing tag is never re-created
    Given main has the signed tag "v0.2.0" with its GitHub Release
    When the "Release" workflow is re-run for the same commit
    Then the run succeeds with "There are no relevant changes"
    And exactly one tag "v0.2.0" exists and it still points at the same commit
  Scenario: a version whose tag exists aborts after the commit was pushed
    Given the tag "v0.3.0" sits on an older commit and a "feat:" commit is on main after "v0.2.0"
    When the "Release" workflow runs and computes "0.3.0"
    Then the run fails with "release_partial_state"
    And main has one additional commit "chore(release): 0.3.0 [skip ci]"
    And no tag "v0.3.0" exists for that commit
  Scenario: a signing failure is caught before anything is written
    Given the key cannot sign or GIT_EDITOR points at a helper that exits 1, and a feature was merged into main
    When the "Release" workflow runs
    Then the run fails with "release_signing_key_invalid" — catalogue id 3 of §2.4.2, printed once by the classifier of step 9
    And the remote main still points at the merged commit, with no tag and no "preflight-" ref
    And no "chore(release):" commit and no tag were pushed
  Scenario: the partial state is repaired by tagging the release commit, not by a re-run
    Given main has "chore(release): 0.2.0 [skip ci]" with the literal, and no tag "v0.2.0" exists
    When the maintainer follows §5.5 and tags that commit
    Then "git verify-tag v0.2.0" prints a good SSH signature
    And the tag points at the commit that carries the literal
    And main still has one "chore(release):" commit and CHANGELOG.md one header for "0.2.0"
  Scenario: the release artefacts are signed
    Given a release run that created the tag "v0.2.0" and the commit "chore(release): 0.2.0 [skip ci]"
    When "git cat-file -t", "git verify-tag" and "git verify-commit" run on a fresh clone
    Then the object type is "tag" and both signatures verify
    And the principal is "hifa-release" and the commit body contains the notes
    And no "preflight-" ref exists on the remote
  Scenario: the tag, the literal and hifa --version agree
    Given the tagged commit of "v0.2.0"
    When "grep -E '^version = "' pyproject.toml runs at that tag and a wheel is built from it
    Then the output is exactly 'version = "0.2.0"'
    And the wheel declares "Version: 0.2.0"
    And "hifa --version" prints "hifa 0.2.0" with exit code 0
  Scenario: CHANGELOG is regenerated without a duplicated header
    Given main has "v0.2.0" and a CHANGELOG.md starting with its header, and a "fix: repair the board query" PR is merged
    When the "Release" workflow runs for that merge
    Then CHANGELOG.md starts with a single header line for "0.2.1"
    And the header line for "0.2.0" appears exactly once, with no hand-written "# Changelog"
  Scenario: the release commit triggers no workflow at all
    Given a release run that pushed the commit "chore(release): 0.2.0 [skip ci]"
    When GitHub evaluates the workflows for that push
    Then no "Release" run and no "CI" run are created for that commit
    And release.yml declares no job-level "if:" — the "[skip ci]" token and the engine's "no relevant changes" are the only loop breakers
  Scenario: a release without its secrets fails without publishing
    Given "HIFA_RELEASE_TOKEN" or "HIFA_RELEASE_SIGNING_KEY" is not set, and a feature was merged into main
    When the "Release" workflow runs
    Then the run fails before any write with "release_token_missing" or "release_signing_key_missing"
    And no tag was pushed and no GitHub Release was created
  Scenario: a dry run writes nothing
    Given main has the signed tag "v0.2.0" and a merged "fix: repair the board query" commit, and the dispatch keeps the default dry_run
    When "gh workflow run release.yml" is executed without overriding dry_run
    Then the run succeeds and the log contains "next release version is"
    And the classifier required exit status 0 and that signal before printing "green: nothing written"
    And no new tag, no new commit on main and no change to the literal exist
  Scenario: the post-condition step runs even when the engine fails
    Given a run in which "./node_modules/.bin/semantic-release" exits non-zero
    When the workflow log is inspected
    Then the post-condition step ran and exactly one "release_" code appears in the log
  Scenario: the token is not persisted and the workflow never runs on a pull request
    Given a successful release run and the workflow file ".github/workflows/release.yml"
    When the workspace, the git configuration and the triggers are inspected
    Then "grep -c extraheader .git/config" prints "0"
    And "git config --get remote.origin.url" contains no "@" and the log no token value
    And the workflow declares "push" restricted to "main" plus "workflow_dispatch", and no "pull_request"
  Scenario: the ruleset still rejects a direct push from a non-admin actor
    Given the ruleset "main is PR-only" with one bypass actor of type "RepositoryRole"
    When a collaborator without the admin role pushes a commit to main
    Then the push is rejected with "Changes must be made through a pull request"
    And the required checks are still "test" and "firmas"
    And "non_fast_forward" and "deletion" are still enabled
  Scenario: a tag that exists without its release is recovered by hand, not by a re-run
    Given the tag "v0.2.0" exists on the remote and no GitHub Release exists for it
    When the "Release" workflow is re-run for that commit
    Then the run succeeds with "There are no relevant changes"
    And no GitHub Release "v0.2.0" is created by the workflow
    And the release commit body contains the notes used for the manual recovery
  Scenario: the documentation is the same table as the configuration
    Given the repository after the implementation PR
    When the "release-types" block of AGENTS.md §9 is diffed against the block of SPEC-110 §2.10
    Then the diff is empty
    And "AGENTS.md" §9 requires a "BREAKING CHANGE:" footer for a breaking change
    And "AGENTS.md" §6 mentions the workflow and that it appends one release commit
    And "README.md" "Contributing" explains the release, the tag verification and both recoveries
  Scenario: the version suite survives a release it never saw
    Given a working copy of the repository whose "pyproject.toml" carries 'version = "0.2.0"', the state the release commit leaves on main
    When "pytest tests/unit/test_version.py" is executed in that copy
    Then the suite is green
    And no assertion in it compares a version to a concrete number
  Scenario: a secret is visible only to the step that uses it
    Given the workflow file ".github/workflows/release.yml"
    When the workflow is parsed as YAML
    Then no "env" key exists at workflow level or at job level
    And exactly the token gate and the release step declare "HIFA_RELEASE_TOKEN"
    And only the identity step declares "HIFA_RELEASE_SIGNING_KEY"
    And only the release step declares "GITHUB_TOKEN"
    And every other step declares no variable at all
    And the install step runs "npm ci --ignore-scripts"
  Scenario: the credential never reaches the environment file
    Given a release run that pushed the tag "v0.2.0"
    When "$GITHUB_ENV" and ".git/config" are inspected after the engine ran
    Then neither file contains the token, the signing key, an "extraheader" or a base64 of either
    And the classifier prints "release_credential_persisted" when either one does
  Scenario: a dry run that never computed a plan is red
    Given a dispatch that keeps the default dry_run and a token the GitHub API refuses
    When the engine exits non-zero without printing "next release version is"
    Then the run fails and the repository is unchanged
    And the classifier prints no "green: nothing written"
  Scenario: a missing secret is classified exactly once
    Given one run without "HIFA_RELEASE_TOKEN" and one run without "HIFA_RELEASE_SIGNING_KEY"
    When each workflow log is inspected
    Then "release_token_missing" appears exactly once in the first
    And "release_signing_key_missing" appears exactly once in the second
    And the classifier added no code of its own to either run
  Scenario: an ambiguous version literal aborts before anything is written
    Given a "pyproject.toml" whose version line reads "version = '0.1.0'" and a merged "feat:" commit
    When the "Release" workflow runs
    Then the run fails with "release_version_mismatch" during "verifyConditions"
    And main has no new commit, no new tag, and "CHANGELOG.md" is unchanged
  Scenario: the classifier reproduces every evaluation order of the outcome table
    Given the fixture logs of "tests/fixtures/release" and a throwaway git repository
    When "pytest tests/e2e/test_release_outcome.py" is executed
    Then every evaluation order of §2.4.2 is produced by at least one fixture
    And no order is produced by the fixtures of an order with higher precedence
    And the exit status is 0 for G1, G2 and G3 only
    And each green equals its literal: "green: nothing written", "green: no relevant changes", "green: released <tag>"
    And the six mutations of D-040 turn the suite red instead of passing it
  Scenario: a scheduled run with nothing to release is green
    Given a checkout whose HEAD is the release commit the previous run wrote, with its tag on "origin"
    When the engine answers "no relevant changes" and creates no tag
    Then the classifier prints "green: no relevant changes" and exits 0
    And a run that created a tag, or that moved HEAD away from "PRE_RUN_HEAD", prints no green at all
    And a run without "PRE_RUN_HEAD" cannot claim that green either
  Scenario: a refused notes push is not a refused tag push
    Given a run whose log shows "Created tag v0.2.0" and then "[remote rejected] refs/notes/semantic-release"
    When the classifier runs against that log and "git ls-remote --tags origin" lists "v0.2.0"
    Then it prints "release_notes_push_rejected"
    And it never prints "release_tag_push_rejected": the commit and the tag are on the remote
    And a log whose refusal names the tag, with the tag absent from the remote, prints "release_tag_push_rejected"
    And a refusal message with the tag on the remote prints neither code
  Scenario: the tag helper messages the name git is creating
    Given the ".github/release/tag-message.sh" helper and a message file seeded by git
    When the helper is executed with that file as its only argument
    Then it writes back the "vX.Y.Z" name git put in the header
    And it accepts a "preflight-" name, which no literal in the tree could produce
    And it exits non-zero for zero arguments, two arguments, a header with no name, or a name such as "latest"
    And it reads no "pyproject.toml" at all
```

### 7.2 Verification commands
`$TAG` is the tag under test; `.venv` is the project virtualenv (`/home/administrator/hifa-main/.venv`).

```bash
# §2.1 / R1 — one literal, and the build backend compared as a LIST (the three removals)
grep -nE '^version = "[0-9]+\.[0-9]+\.[0-9]+"$' pyproject.toml                 # exactly one line
grep -nE '^dynamic|hatch-vcs|setuptools-scm|\[tool\.hatch\.version\]|\[tool\.semantic_release\]' pyproject.toml
.venv/bin/python - <<'PY'
import pathlib, tomllib, re, json
d = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
assert d["build-system"] == {"requires": ["hatchling"], "build-backend": "hatchling.build"}, d["build-system"]
assert re.fullmatch(r"\d+\.\d+\.\d+", d["project"]["version"]), d["project"].get("version")
assert "dynamic" not in d["project"]
assert "version" not in d.get("tool", {}).get("hatch", {})       # [tool.hatch.version] gone
assert "semantic_release" not in d.get("tool", {})
assert "python-semantic-release" not in " ".join(d.get("dependency-groups", {}).get("dev", []))
init = pathlib.Path("src/hifa/__init__.py").read_text()
assert "importlib.metadata" in init and '"0.0.0+unknown"' in init
pkg = json.load(open("package.json"))
assert pkg["version"] == "0.0.0" and pkg["private"] is True
print("ok: one literal, requires == ['hatchling'], no PSR")
PY
# §2.5 — the config is the contract: order, rules, tagFormat, pre-flight
.venv/bin/python - <<'PY'
import json
c = json.load(open(".releaserc.json"))
assert c["tagFormat"] == "v${version}" and c["branches"] == ["main"]
names = [p if isinstance(p, str) else p[0] for p in c["plugins"]]
assert names == ["@semantic-release/commit-analyzer", "@semantic-release/release-notes-generator",
                 "@semantic-release/changelog", "@semantic-release/exec",
                 "@semantic-release/git", "@semantic-release/github"], names
rules = c["plugins"][0][1]["releaseRules"]
# R2/R15: the table is compared as a COMPLETE set. Filtering by "type" drops {"revert": true} — the
# only row that matches a `git revert` header (§2.5.3) — and any rule added by accident
expected_rules = [
    {"breaking": True, "release": "major"},
    {"revert": True, "release": "patch"},
    {"type": "feat", "release": "minor"},
    {"type": "fix", "release": "patch"},      {"type": "style", "release": "patch"},
    {"type": "refactor", "release": "patch"}, {"type": "perf", "release": "patch"},
    {"type": "build", "release": "patch"},    {"type": "ci", "release": "patch"},
    {"type": "chore", "release": "patch"},    {"type": "revert", "release": "patch"},
    {"type": "docs", "release": False},       {"type": "test", "release": False},
]
dump = lambda rs: sorted(json.dumps(r, sort_keys=True) for r in rs)
assert len(rules) == 13 and dump(rules) == dump(expected_rules), rules
assert {"revert": True, "release": "patch"} in rules      # `Revert "…"`: no `type` key at all
cfg = c["plugins"][3][1]
assert "git verify-tag" in cfg["verifyConditionsCmd"] and "git tag -d" in cfg["verifyConditionsCmd"]
assert "expected exactly one version literal line" in cfg["verifyConditionsCmd"]   # the guard, §2.5.1
assert "release_version_mismatch" in cfg["verifyConditionsCmd"] and "release_version_mismatch" in cfg["prepareCmd"]
assert "${nextRelease.version}" in cfg["prepareCmd"]
print("ok: 13 rows, literal guard + pre-flight present, tagFormat as specified")
PY
# §2.10 / R15 — the block is compared, not grepped (the markers are anchored: an unanchored pattern
# also matches this very command, which is two lines inside the same file, and the diff never empties)
diff -u <(sed -n '/^<!-- release-types:begin -->$/,/^<!-- release-types:end -->$/p' AGENTS.md) \
        <(sed -n '/^<!-- release-types:begin -->$/,/^<!-- release-types:end -->$/p' specs/110-release.md)   # empty diff
# §2.3 / R7 — the tag is an annotated signed object, created by the engine
git config gpg.ssh.allowedSignersFile .github/signers/allowed_signers && git fetch --tags --force
git cat-file -t "refs/tags/$TAG"                       # "tag", never "commit"
git verify-tag "$TAG"                                  # Good "git" signature for hifa-release
git log -1 --format='%G? %s' "$TAG^{commit}"           # "G chore(release): ..."
git ls-remote --tags origin 'refs/tags/preflight-*'    # empty (§2.3.1)
# §2.5 — the bump is real and unique, at the tag
git show "$TAG:pyproject.toml" | grep -cE '^version = "'                     # 1
git show "$TAG:pyproject.toml" | grep -E '^version = "'                      # version = "X.Y.Z"
git show "$TAG:package.json" | python3 -c "import json,sys;print(json.load(sys.stdin)['version'])"  # 0.0.0
# R2 / R3 / R4 — the bump mapping, without writing anything
./node_modules/.bin/semantic-release --no-ci --dry-run
#   feat -> 0.2.0 | fix -> 0.1.1 | fix + BREAKING CHANGE -> major | git revert -> patch
#   docs:/test: only -> "There are no relevant changes" | no tag + feat -> 1.0.0
# §2.4 step 9 — the outcome assertion, on a real run
gh run view <run-id> --log > /tmp/release.log
grep -cE 'Created tag v[0-9]+\.[0-9]+\.[0-9]+|no relevant changes' /tmp/release.log              # >= 1
grep -cE 'release_(partial_state|engine_aborted|version_mismatch|tag_not_signed)' /tmp/release.log  # 0
grep -cE '(green: |release_[a-z_]+$)' /tmp/release.log                                            # exactly 1
# (`gh run view --log` prefixes every line with job/step/timestamp, so a `^` anchor never matches; use
#  `gh run view <id> --json jobs --jq '.jobs[].steps[].name'` + the raw job log if you need one line per step)
# §2.4.2 / R19 / R20 / D-040 — the classifier is executed, not read: one fixture per evaluation order
bash -n .github/release/assert-outcome.sh && ls -l .github/release/assert-outcome.sh | cut -c1-10  # -rwxr-xr-x
.github/release/assert-outcome.sh --help >/dev/null 2>&1; echo $?                             # 1 — an argument is a usage, never a green
.venv/bin/pytest -q tests/e2e/test_release_outcome.py                  # every order of §2.4.2 + precedence
.venv/bin/pytest -q tests/e2e/test_release_contract.py                 # the document-level contract
# §2.4.2 orders 3 and 10 — the two refusals and the delta, each fixture proved against a state:
grep -c 'refs/notes/semantic-release' tests/fixtures/release/order10_notes_push_rejected.log      # >= 1
grep -c 'Created tag' tests/fixtures/release/order10_notes_push_rejected.log                      # 1 — the tag exists in the same log
grep -c 'refs/notes/' tests/fixtures/release/order10_tag_push_rejected.log                        # 0 — this refusal names the tag
grep -c 'no relevant changes' tests/fixtures/release/order03_no_relevant_changes.log              # 1 — and no `Created tag` line
# The classifier on the scheduled-run shape: HEAD is the previous release commit, the tag is on the
# remote, the engine answered `no relevant changes` and created nothing -> green, exit 0.
grep -c 'PRE_RUN_HEAD' .github/release/assert-outcome.sh                                           # >= 2 — G1 and G3 both read the baseline
grep -c 'ls-remote --tags' .github/release/assert-outcome.sh                                       # >= 3 — the fact behind every tag code
grep -c 'refs/notes/' .github/release/assert-outcome.sh                                            # >= 1 — the notes refusal is its own code
grep -c 'git fetch --no-tags origin' .github/workflows/release.yml                                 # 1 — the notes ref is adopted before the engine
# §2.4.2 — the precedence is a SEQUENCE, not a set: the contract suite exchanges two whole order blocks
# (5↔6 and 9↔10, each marker keeping its own body) and requires the checks to go red, because `bash`
# reads the file top to bottom.
grep -c 'sequence == list(CLASSIFIER_ORDERS)' tests/e2e/test_release_contract.py               # 1 — the order in the file is the precedence
# R20 / D-040 — the six mutations that passed rev 5's document-shaped suite must turn this one red.
# A fixture per evaluation order proves the CLASSIFIER and observes no line of release.yml, so a mutation
# of the workflow text is only caught by an assert that names it: the two D-040 names, and the others.
grep -n 'GIT_CONFIG_VALUE_0=' .github/workflows/release.yml | grep -c 'AUTHORIZATION: basic.*base64'  # 1 — not the bare token
grep -c 'commit.gpgSign true' .github/workflows/release.yml                                    # 1 — the release commit is signed
grep -c 'git verify-commit' .github/release/assert-outcome.sh                                  # >= 1 — order 6 is implemented, not disabled
grep -c 'signing pre-flight: starting' .releaserc.json                                         # 1 — order 4 has its opening token
# REQ-S110-03 / §2.7.2 — nothing persisted; the block is documentation, the PAT is the control
grep -c extraheader .git/config                                        # 0
git config --get remote.origin.url | grep -c '@'                       # 0
grep -nE 'persist-credentials: *true|npm install|git push -f' .github/workflows/release.yml   # no output
grep -n 'successComment' -A 2 .releaserc.json                          # false / false / false
# §2.4.3 / R18 — the workflow is PARSED, not grepped: a grep cannot tell a step-level env from a
# workflow-level one, and that difference is the whole control
.venv/bin/python - <<'PY'
import pathlib, re, yaml
w = yaml.safe_load(open(".github/workflows/release.yml"))
assert "env" not in w, "workflow-level env is forbidden (§2.4.3)"
job = w["jobs"]["release"]
assert "env" not in job, "job-level env is forbidden (§2.4.3)"
steps = job["steps"]                      # the ORDER is the contract (§2.4), not the names
assert len(steps) == 10, len(steps)
envs = [set(s.get("env", {})) for s in steps]
assert envs[2] == {"HIFA_RELEASE_TOKEN"}                       # step 3, token gate
assert envs[5] == {"HIFA_RELEASE_SIGNING_KEY"}                 # step 6, identity and signing
assert envs[7] == {"HIFA_RELEASE_TOKEN", "GITHUB_TOKEN"}       # step 8, the engine
assert all(e == set() for i, e in enumerate(envs) if i not in (2, 5, 7)), envs
assert "npm ci --ignore-scripts" in steps[3]["run"]            # step 4, the install
text = pathlib.Path(".github/workflows/release.yml").read_text()
allowed = {"SEMANTIC_RELEASE_ARGS", "RELEASE_DRY_RUN", "RELEASE_LOG", "RELEASE_EXIT_CODE",
           "PRE_RUN_HEAD", "PRE_RUN_MAIN", "PRE_RUN_TAGS", "GIT_EDITOR",
           "SSH_AUTH_SOCK", "SSH_AGENT_PID"}
written = set(re.findall(r'echo "([A-Z_]+)=', text))
assert written <= allowed, written - allowed
assert not any("GIT_CONFIG" in l or "AUTHORIZATION" in l for l in text.splitlines() if "GITHUB_ENV" in l)
print("ok: secrets per step, no secret key in $GITHUB_ENV, install runs no lifecycle script")
PY
# REQ-N110-05 / §2.8 / R18 — npm ci from the versioned lockfile, no lifecycle script, pinned Node
grep -nE 'npm ci --ignore-scripts|node-version' .github/workflows/release.yml   # both present
grep -c '"hasInstallScript"' package-lock.json                        # 0 (§2.4.3: the cost of the flag is zero)
git ls-files --error-unmatch package.json package-lock.json .releaserc.json
python3 -c "import json;print(json.load(open('package-lock.json'))['lockfileVersion'])"   # 3
# REQ-S110-05 / D-039 — the bypass is exactly one admin role, and the four rules survive
gh api repos/ginkelte/hifa/rulesets/27053963 --jq '{rules: [.rules[].type], bypass: .bypass_actors}'
#   rules: pull_request, required_status_checks, non_fast_forward, deletion; bypass: [{actor_id: 5, …}]
# R10 / REQ-N110-03 / REQ-N110-01 — wheel, runtime and budgets
uv build --wheel --out-dir /tmp/vercheck-dist
python3 -m venv /tmp/vercheck && /tmp/vercheck/bin/pip install -q /tmp/vercheck-dist/hifa-*.whl
/tmp/vercheck/bin/hifa --version                                       # "hifa X.Y.Z"
unzip -p /tmp/vercheck-dist/hifa-*.whl 'hifa-*.dist-info/METADATA' | grep '^Version:'
.venv/bin/python -c "import importlib.metadata as m; print(m.requires('hifa'))"
.venv/bin/python -c "import sys, hifa; print('semantic_release' in sys.modules)"   # False
hyperfine --warmup 3 '.venv/bin/hifa --version'                        # p50 < 250 ms
gh run list --workflow release.yml --json displayTitle,durationMs,conclusion
# R14 / R17 / §2.1.1 — no test pins a version VALUE, and the WHOLE suite is green with the released literal
grep -rnE '(version|__version__) *== *"[0-9]+\.[0-9]+\.[0-9]+"' tests/            # no output
cp pyproject.toml pyproject.toml.bak
trap 'mv -f pyproject.toml.bak pyproject.toml' EXIT        # a red suite must not leave the tree bumped
sed -i 's/^version = ".*"$/version = "0.2.0"/' pyproject.toml                     # what the release leaves
.venv/bin/pytest -q                                                               # the whole suite, green with 0.2.0
trap - EXIT; mv -f pyproject.toml.bak pyproject.toml
# §2.5.1 — the guard refuses an ambiguous literal before any write (nothing to push, nothing to repair)
tmpd=$(mktemp -d); cp pyproject.toml "$tmpd/pyproject.toml"
sed -i 's/^version = ".*"$/version = "0.1.0"x/' "$tmpd/pyproject.toml"
(cd "$tmpd" && grep -cE '^version = "[0-9]+\.[0-9]+\.[0-9]+"$' pyproject.toml)   # 0 → the guard aborts
```

**Local rehearsal of the engine path.** It needs the real manifests, a real signing key and the real
helper; a bare repository is not enough:

```bash
set -eu
REPO=/home/administrator/hifa-main; W=/tmp/sr-rehearsal
rm -rf "$W" && mkdir -p "$W/remote.git" && git init -q --bare "$W/remote.git"
git clone -q "$W/remote.git" "$W/work" && cd "$W/work"
cp "$REPO/package.json" "$REPO/package-lock.json" "$REPO/.releaserc.json" .   # the real inputs
mkdir -p .github/release .github/signers
cp "$REPO/.github/release/tag-message.sh" .github/release/ && chmod +x .github/release/tag-message.sh
ssh-keygen -q -t ed25519 -f "$W/key" -N "" -C hifa-release                    # throwaway key
printf 'hifa-release namespaces="git" %s\n' "$(cat "$W/key.pub")" > .github/signers/allowed_signers
git config user.name "hifa release" && git config user.email "hifa-release@users.noreply.github.com"
git config gpg.format ssh && git config user.signingkey "$W/key"              # the PRIVATE key
git config gpg.ssh.allowedSignersFile "$PWD/.github/signers/allowed_signers"
git config commit.gpgSign true && git config tag.gpgSign true
export GIT_EDITOR="$PWD/.github/release/tag-message.sh"
printf '[project]\nname = "hifa"\nversion = "0.1.0"\n' > pyproject.toml        # the literal
git add -A && git commit -qm "hifa: local issue tracker" && git push -q -u origin main
git tag -s -m "v0.1.0" v0.1.0 && git verify-tag v0.1.0 && git push -q origin refs/tags/v0.1.0
git commit -q --allow-empty -m "feat: add signed release tags" && git push -q origin main
npm ci                                        # exact, from the committed lockfile
./node_modules/.bin/semantic-release --no-ci  # pinned binary, never a floating npx
#   -> "signing pre-flight ok", "Prepared Git release: v0.2.0", "Created tag v0.2.0"
git cat-file -t refs/tags/v0.2.0 && git verify-tag v0.2.0      # tag + Good signature
git log -1 --format='%G? %s'                                   # G chore(release): 0.2.0 [skip ci]
git show 'v0.2.0:pyproject.toml' | grep '^version'             # version = "0.2.0"
git ls-remote --tags origin 'refs/tags/preflight-*'            # empty
# negative probe: break GIT_EDITOR, re-run from a fresh clone -> main must not advance (§2.3.1)
```

`npx` would resolve a floating version (use `./node_modules/.bin/semantic-release`); `git tag -s` and
`git verify-tag` are meaningless without `gpg.format=ssh`, `user.signingkey` **and**
`gpg.ssh.allowedSignersFile` as above; and the manifests, `.releaserc.json` and `tag-message.sh` must be
copied in, because the engine reads them from the working tree.
## 8. Regeneration checklist

1. Read `SPEC-000` (§3 packaging ownership per D-038/D-039, §8 step 1) and this spec in full.
2. `pyproject.toml`: restore `version = "0.1.0"`; in `[build-system].requires` remove **both**
   `hatch-vcs` and `setuptools-scm==10.3.4`, leaving `["hatchling"]`; remove `dynamic`,
   `[tool.hatch.version]`, the `python-semantic-release` row of `[dependency-groups] dev` and
   `[tool.semantic_release]`. Leave `[project.dependencies]`, extras and the entrypoint untouched.
3. `src/hifa/__init__.py`: the block of §2.1 (unchanged). `cli/main.py`: unchanged.
4. `package.json` + `package-lock.json`: the `devDependencies` of §2.8 with exact versions, generated
   locally with Node 22.22.3 / npm 10.9.8 (`npm install`), both committed (§2.8 policy).
5. `.releaserc.json`: §2.5 verbatim, including `verifyConditionsCmd`.
6. `.github/release/tag-message.sh`: executable; requires exactly one argument; derives the tag name from
   the `# Write a message for tag:` header of the message file git passes as `$1`; accepts `vX.Y.Z` and
   `preflight-*` and nothing else; exits non-zero on anything ambiguous and never writes an empty message;
   never reads `pyproject.toml` (§2.3.3).
7. `.github/workflows/release.yml`: the properties and ten steps of §2.4, with §2.4.1 verbatim for steps
   3, 4, 5, 6, 7 and 8 — `env:` per step and nowhere else, `npm ci --ignore-scripts`, no job-level `if:` —
   and step 9 = `if: always()` + the command of item 8 behind an `[ -x ]` existence test.
8. `.github/release/assert-outcome.sh`: executable; the interface and the precedence of §2.4.2; **exactly
   one** classification line — one of the three literals (`green: nothing written`, `green: no relevant
   changes`, `green: released <tag>`) or `release_<code>`; **takes no arguments — an argument is refused,
   exit `1`, no line — and reads no stdin**;
   `TAG` comes only from the log line `Created tag <tag>` (absent ⇒ order 13); `RELEASE_EXIT_CODE` unset
   reads as the **input** sentinel `255`, never `0`, while the script's own exit status is `0` only for
   G1/G2/G3 and `1` for every code; **no network writes** — `git ls-remote` is read only (two queries,
   `refs/heads/main` and `refs/tags/*`), no `fetch`, no `push`; no write to the repository.
9. `.github/signers/allowed_signers`: add one line, principal `hifa-release`, `namespaces="git"`, the
   public half of the secret's keypair. Keep the `ginkelte` line.
10. `CHANGELOG.md`: it must **not** exist in this commit (§2.6).
11. Tests: `pytest tests/unit/test_version.py tests/e2e/test_release_contract.py
    tests/e2e/test_release_outcome.py` green — the shape and single occurrence of the literal (never its
    value, R17), the fallback, the `--version` shape, `[build-system].requires == ["hatchling"]`, the
    thirteen `releaseRules` rows including `{"revert": true}`, `tagFormat`, the plugin order, the literal
    guard, the pre-flight, and one fixture per evaluation order of §2.4.2.
12. **Order (§5.2):** merge the literal PR first, push the signed `v0.1.0` **on its merge commit**,
    verify `hifa --version` ⇒ `hifa 0.1.0`, and only then merge the workflow PR.
13. `gh workflow run release.yml -f dry_run=true`: the computed version must be the one §2.5.3 predicts.
14. `python scripts/spec_contract_check.py --all; lint-imports` green.
15. Documentation of §2.10, including the marked block, plus the `README.md` counters (R15).

## 9. Provenance
Engine facts were measured on this host with **`semantic-release 21.1.2`** on **Node 22.22.3 / npm
10.9.8**, in scratch repositories with a bare `origin`, a signed `v0.1.0` and conventional commits on top
— unless the row says otherwise. Plugin internals are cited from the packages in the local npm cache; a
row that is a code-read rather than a run says so.

| Fact | Evidence |
|---|---|
| phase order is `verifyConditions → … → prepare → tag → push` | `semantic-release/lib/index.js`, code-read |
| `@semantic-release/git` pushes **inside** `prepare` | `add` → `commit` → `push(repositoryUrl, branch.name)` in `git-10.0.1/lib/prepare.js`; that push is `git push --tags <url>` (`semantic-release/lib/git.js`), code-read |
| **the commit reaches the remote before the tag exists** | run with `GIT_EDITOR` exiting 1: remote `main` = `chore(release): 0.2.0 [skip ci]`, `pyproject` = `0.2.0`, remote tags = `v0.1.0` only (measured) |
| **a `verifyConditionsCmd` pre-flight stops the same failure before any write** | same broken editor, pre-flight signing a `preflight-*` tag: the run dies in `verifyConditions`, remote `main` unchanged, `pyproject` still `0.1.0`, no tag (measured) |
| the tag points at the release commit, not at the pre-prepare HEAD | `nextRelease.gitHead` is re-read after each prepare plugin (`lib/definitions/plugins.js`); measured: `git cat-file -p refs/tags/v0.2.0` ⇒ `object <release commit>` |
| the engine tags with a bare `git tag <name> <ref>`; `tag.gpgSign=true` alone breaks that call, with a `GIT_EDITOR` helper it signs | `lib/git.js` (code-read); *"Please supply the message"*, no ref; then `git cat-file -t` ⇒ `tag`, message = `v0.5.0`, `verify-tag` ⇒ `Good "git" signature` |
| `user.signingkey` must hold the **private** key | a `.pub` path with no agent makes `git tag` fail with *"Could not read SSH key"*, reported as `release_signing_key_invalid` |
| the tagger identity is the engine's bot | `%(taggername)` ⇒ `semantic-release-bot`, `%(taggeremail)` ⇒ `<semantic-release-bot@martynus.net>` |
| `feat` ⇒ minor, `fix`/`chore`/`ci` ⇒ patch | `next release version is 0.2.0` from `v0.1.0`; `The release type for the commit is patch` |
| `BREAKING CHANGE:` footer ⇒ major, **also on a `fix`** | `lib/analyze-commit.js` returns the highest matching type (code-read); `1.0.0` and `2.0.0` measured |
| `releaseRules` order is **not** semantic | `analyze-commit.js` filters *all* matching rules and returns the highest via `compareReleaseTypes`, early exit at `major` (code-read). Rev 4's "first match wins" rationale was wrong; the configuration is correct |
| `release: false` suppresses the fallback to the built-in rules | `analyzeCommit` returns `false`, not `undefined`, and only `undefined` triggers `DEFAULT_RELEASE_RULES` (code-read) |
| a `git revert` commit sets `commit.revert`, not `type` | `Revert "…"` does not match `headerPattern`; `{"revert": true}` is the matching row (code-read) |
| `feat!:` without the footer ⇒ nothing **and no changelog entry** | no `!` in `headerPattern`, no `breakingHeaderPattern`; the writer discards a commit with no type and no notes (code-read). `no relevant changes` measured |
| `--dry-run` writes nothing; the `exec` `prepareCmd` runs **before** the tag | checksums unchanged, `Skip v0.2.0 tag creation in dry-run mode`; log order `Call script sed …` → `Prepared Git release` → `Created tag v0.2.0` |
| `CHANGELOG.md` header and bullet shapes | `# [0.2.0](<compare-url>) (2026-11-11)`, sections `### Bug Fixes` / `### Features`, bullets `* subject ([sha](url))` |
| the changelog plugin does not de-duplicate a header | it writes `${notes}\n\n${currentContent}` with no version check (`changelog-6.0.3/lib/prepare.js`, code-read) — the reason §5.5 forbids a re-run as repair |
| an untracked `CHANGELOG.md` is committed; `preset: "conventionalcommits"` aborted the analysis here | `git ls-files -m -o` (code-read); the step printed nothing after `Start step "analyzeCommits"` and exited `0`, while `angular` analysed the same commits |
| the compare link follows the `origin` URL | with a local-path remote the link came out as `https:/tmp/srprobe/remote/compare/...` (§2.6) |
| `[skip ci]` also skips `ci.yml` | **documented, not measured**: GitHub's "Skipping workflow runs" lists `push` and `pull_request` as the skipped events (§2.4) |
| git seeds the message file of an editor-driven `git tag` | `git tag -a v9.9.9` with a `GIT_EDITOR` that prints its arguments: the editor is invoked with **one** argument (`$1` = `.git/TAG_EDITMSG`) and the file carries `# Write a message for tag:` followed by `#   v9.9.9` (measured, git 2.53.0). No `GIT_REFLOG_ACTION` is exported to the editor, which is why the name is read from the file and not from the environment (§2.3.3) |
| the literal guard refuses an ambiguous line **before** any write | the `prepareCmd` of §2.5.1 executed against a scratch `pyproject.toml`: `version = "0.1.0"` ⇒ rewritten to `0.2.0`; `version = "0.1.0"x` ⇒ exit 1 with `release_version_mismatch` and the file untouched; two literal lines ⇒ exit 1 and the file untouched; no `*.tmp` left behind (measured) |
| the lockfile carries no install script | `grep -c '"hasInstallScript"' package-lock.json` ⇒ `0` across 616 packages, `lockfileVersion` 3 (measured): the cost of `npm ci --ignore-scripts` is zero today (§2.4.3) |
| a step-level `env:` reaches that step only | **documented, not measured**: workflow- and job-level `env` are exported to every step, a step-level `env` only to that step's processes (§2.4.3) |

- **The engine change is D-039.** The reference configuration is the user's
  `Vichoko/python-semantic-release-cookiecutter` `.releaserc.json` (read, not quoted from memory); its
  plugin set, order, `tagFormat` and `sed` are kept. Three of its defects are corrected and named here:
  the config/documentation contradiction (§2.10), the corrupted `CHANGELOG.md` header (§2.6) and the
  inline `package.json` with `^` ranges and no lockfile (§2.8).
- **`main is PR-only` with the admin bypass** (D-036, D-039): the ruleset `27053963` reports
  `bypass_actors: [{actor_id: 5, …}]` and `current_user_can_bypass: always`, while `pull_request`,
  `required_status_checks`, `non_fast_forward` and `deletion` stay enabled.
- **Ownership of `pyproject.toml` and `src/hifa/__init__.py`**: D-038's split survives, with the
  version-producing keys now being `[project].version` plus the absences listed in §2.9; SPEC-000 §3
  cites the same list, key by key.
- **Issue HIFA-22** is the requirement source. Its clause "bump the version and **commit** it" now holds
  literally (D-039); it is the pull-request part that does not, and the bypass is the deliberate
  exception.

## 10. Out of scope

- Publishing to PyPI or any index; `uv publish`, twine, tokens, trusted publishing, npm publish.
- Prerelease channels (`-rc`, `-beta`), release candidates, hotfix branches, nightly/cron builds; releases
  from PRs, from hand-pushed tags (other than the bootstrap) or from any branch but `main`;
  `workflow_dispatch` on an arbitrary commit.
- A **GitHub App** as the bypass actor (§2.7.1); a second ruleset protecting `refs/tags/v*` (§2.5.4
  point 3); automatic recreation of a missing GitHub Release and automatic repair of the partial state
  (§5.4, §5.5).
- A second copy of the outcome classifier inside `release.yml`: `.github/release/assert-outcome.sh` is the
  only place a code is decided (D-040), and no check may compare a version to a value (§2.1.1).
- GPG signing, keybase, multiple signature schemes (SSH only); hand-rendered notes or changelog; a version
  generated at run time from `git describe`.
- Rewriting or re-tagging published versions; migrating the history to conventional commits.
- Binary artefacts, wheels attached to the release, SBOM, provenance attestations, Changesets/Lerna or any
  second release engine.

## 11. Interface behaviour (P3)

**Not applicable: this component has no user interface.** It adds no `hifa` command, no HTTP route and no
template, so it has no empty/loading/error states, keyboard path or breakpoints.

| Surface | Empty | Error |
|---|---|---|
| `hifa --version` (§2.2) | never empty: one line, `0.0.0+unknown` at worst | always exit 0; a broken installation prints the fallback, not a traceback |
| `CHANGELOG.md` (§2.6) | not possible: created by the first release, with its header | a conflicting file is regenerated, never hand-merged |
| Release page (§2.6) | a release without a body fails the run (`release_notes_missing`) | the summary names the failing step and the §6 code |

## 12. Notes

- **spec_revision 9 (2026-11-11): a green states the delta of the run, and a refused push is classified
  by the ref it names.** `version` stays 3 — no signature moved. In terms of the system: (1) G3 compares
  the checkout with the `PRE_RUN_HEAD` baseline instead of the shape of `HEAD` — a scheduled run starts on
  the release commit the previous run wrote, so a state-shaped row refused the green on every schedule tick
  and the six-hour fallback could never succeed, while an empty baseline is a baseline error and grants
  nothing (the criterion G1 already used); (2) step 4 adopts the remote notes ref before the engine runs,
  because the runner's clone carries none and the engine's push of `refs/notes/semantic-release` was
  therefore not a fast-forward and was refused *after* the tag became public; (3) the push-rejection row
  now asks `git ls-remote --tags origin` which state actually holds before it prints a code, and splits
  into two: a refusal of `refs/notes/` with the tag on the remote is the new catalogue id 13
  `release_notes_push_rejected` (commit, tag and Release public, notes ref missing, one push repairs it),
  and `release_tag_push_rejected` is emitted only when the tag the log claims is absent from the remote.
  The invariant the row now carries: **a code is emitted on an observed fact, never on a substring of the
  log** — the same refusal text is produced by a refusal of any ref. Two fixtures pin the shapes: a
  rejected notes push with the tag present, and a rejected tag push with the tag absent.

- **spec_revision 8 (2026-11-11): a push that is skipped is now released within six hours.**
  `version` stays 3 — no signature moved. In terms of the system: GitHub honours the skip token as a
  case-insensitive substring of the pushed commit's message and suppresses **every** workflow for that
  push — no run, no check, no error, nothing to classify and nothing to alert on. The release commit
  carries the token by design, so the suppression is invisible until a commit that *mentions* the token
  arrives, and then the release for that merge simply never happens. `on.schedule` (every six hours)
  makes the pipeline self-healing, and the message rule — never write the literal token in a commit
  message, a PR body or a squash message — is normative because it is the only part a human controls.

- **spec_revision 7 (2026-11-11): the review of rev 6 turned two guarantees into executable ones and
  narrowed two rows to what a run can observe.** `version` stays 3 — no signature moved. In terms of the
  system: (1) evaluation order 1 detects the credential **by value**, not only by name — it decodes every
  base64-shaped value of `$GITHUB_ENV` and matches a GitHub token by its prefix — because the leak that
  row exists to forbid is `base64("x-access-token:<PAT>")`, which carries no key name at all and used to
  pass a name-shaped pattern, printing `green: released v0.2.0`; (2) the precedence is asserted as a
  **sequence**, not as a set of order numbers: `bash` runs the file top to bottom, so exchanging two whole
  blocks (5↔6 and 9↔10, each marker keeping its body) is now a red suite, where comparing the *set* of
  sections left it 50/50 green; (3) row 9 is narrowed to the half it can observe — `CHANGELOG.md` at the
  tag — and no longer fires when the log reports a rejected push, because a tag the remote refused is row
  10's state; (4) row 10 is narrowed to the state it names: `failed to push some refs`, `protected branch`
  and `cannot lock ref` also appear when the **release commit**'s push failed for auth or network reasons,
  and §5.5 repairs a state in which that commit exists; (5) step 9 tests that the classifier is on disk
  (`[ -x ]`) before running it, so a failed checkout prints `release_engine_aborted` instead of dying with
  a shell error; (6) the classifier **refuses an argument** — exit `1`, no line — where `--help` once
  answered `green: released v0.2.0`, exit `0`, i.e. it failed open in the one place the pipeline fails
  closed; and (7) "no network" becomes "no network **writes**": the classifier makes read-only
  `ls-remote` queries (`refs/heads/main` and `refs/tags/*`), which rows 2, 11 and 12 require, so the
  earlier wording was simply false.
- **spec_revision 6 (2026-11-11): the pipeline's guarantees become executable, and four of them change
  shape.** `version` 2 → 3: the contract gains a governed file, a per-step secret scope and a new error
  class. In terms of the system: (1) the version test asserts **shape and uniqueness, never the value**
  (§2.1.1, R17) — the release rewrites that literal in a commit no CI run sees, so a test that pins the
  value breaks the first release it protects; (2) secrets are declared **per step**, and the install runs
  `npm ci --ignore-scripts` (§2.4.3, R18), so no dependency lifecycle script ever runs with the admin PAT
  or the signing key in its environment; (3) the push credential is exported **inside the shell of step 8**
  and never written to `$GITHUB_ENV` (§2.4.1), and its absence is asserted *after* the write by the
  classifier (`release_credential_persisted`); (4) the outcome table gains an **owner per catalogue id** and
  the invariant *exactly one classification line per run* with its mechanism — catalogue ids 1 and 2 belong
  to the gates, every other catalogue id to the classifier, which stays silent when the mark exists
  (§2.4.2, R19); (5) G1, G2 and G3 require `RELEASE_EXIT_CODE == 0` **and** a positive signal in the log,
  plus a non-zero sentinel for "step 8 never ran", so a dispatch that computed nothing is red instead of
  reporting "green: nothing written"; (6) the literal guard moves into `verifyConditions` and `prepareCmd`
  fails when the substitution is not exactly one line (§2.5.1), so an ambiguous literal aborts before the
  commit exists; (7) the
  classifier is a **script executed by pytest** against fixtures — one per evaluation order, plus a
  negative per order for
  the precedence (§2.9, D-040) — instead of a block of YAML that only a review could check; and (8) the
  message helper takes exactly one argument and reads the tag name git is creating rather than the literal
  in the tree (§2.3.3). The job-level `if:` disappears, because the `[skip ci]` token is the loop breaker
  and a `contains()` copy of it also *skips* a legitimate release whose squash body quotes the phrase.
  `PRE_RUN_*` are required non-empty (a failed capture is not a baseline), and §7.2 compares the thirteen
  `releaseRules` rows as a complete set, so `{"revert": true}` can no longer fall out of the comparison.
  **Nothing moves in §2.1's literal, §2.2's output or §2.3's tag contract, and the packaging keys of
  SPEC-000 are untouched.**
- **spec_revision 5 (2026-11-11): the token's scopes are recorded as measured.** No contract moved;
  §2.7.2 now states what is installed instead of what is required. `HIFA_RELEASE_TOKEN` comes from the
  `gh` CLI device flow, whose OAuth app grants `gist` and `read:org` to every token it issues and cannot
  drop them, so the effective set is `repo` (required) + `gist` + `read:org`. The scopes that carry the
  weight are the absent ones — no `admin:ssh_signing_key`, no `workflow` — and the deviation is accepted
  and documented (D-039, corrigendum), not claimed as compliance. The R15 extraction of §7.2 is anchored
  to the marker lines in the same revision: the unanchored pattern also matched the verification command
  itself, two lines further down this file, so the `diff` could never come out empty.
- **spec_revision 4 (2026-11-11): the engine changes from `python-semantic-release` to the npm
  `semantic-release`, and the version is a literal again (D-039).** It **reverts** revisions 1-3 where
  they rested on premises that no longer hold: `.releaserc.json` is the contract (§2.5), and the literal
  written by the `sed` replaces `dynamic`, `[tool.hatch.version]`, `hatch-vcs`, `setuptools-scm` and
  `[tool.semantic_release]`, so `hifa --version` reports the last release instead of `devN+g<sha>`.
- **This pass corrects the model of the plugin lifecycle, and that is the substance of the revision.**
  The earlier text treated `git tag` as the gate ("signed or nothing", "the engine aborts before
  writing"). `@semantic-release/git` pushes the release commit inside `prepare`, before the tag exists, so
  a tag-phase failure leaves `main` carrying a version no tag records: §2.3.1 states the real order, the
  signing pre-flight moves the signing class of failures in front of the write, and §5.5 documents the
  residual state and its repair. Three smaller rationales were wrong for the same reason — "first match
  wins" (§2.5.3), "`[skip ci]` only stops this workflow" (§2.4) and "`permissions:` bounds the token"
  (§2.7.2) — and are corrected rather than deleted, because each justified a real decision.
- **The bootstrap is load-bearing, and it is two PRs.** The literal and the workflow cannot land in the
  same merge, so §5.2 sequences them and the tag names its commit: `git tag -s v0.1.0` on whatever `HEAD`
  is would put the baseline on a commit whose `pyproject.toml` derives its version from git, violating R10
  at the first tag.
- **The seven-row guard (A–G) is deleted, not patched.** It classified states in which PSR could be read
  two ways; the npm engine has one outcome space, and §2.4.2 states it. The write to `main` is likewise a
  stated exception (D-039): R11 of revisions 1-3 said "the job writes no commit", R11 now says *exactly
  one* commit per release, and §2.7.1 draws the line. D-036 is **not edited** — D-039 carries the
  corrigendum that narrows its scope.
- **`specs/INDEX.md` is updated here; `README.md` is not** — its counters belong to the implementation PR
  (R15). The `acceptance:` file does not exist yet (Gherkin inline in §7.1, `.feature` file is debt P-01),
  and nothing yet verifies a workflow against a spec: `scripts/ci_drift_check.py` compares the DDL of
  SPEC-010 only, so §7.1/§7.2 are the check and a future gate over the triggers, permissions and push
  credential of `release.yml` is follow-up debt.
