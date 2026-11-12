"""The release pipeline as a contract: what it must do, asserted as checks over text.

Every governed file is read once and handed to a **pure function over its text** —
`check_workflow`, `check_releaserc`, `check_package_json`, `check_classifier`, `check_agents` —
that returns the list of violations it found. Empty means the file honours the contract; the tests
read the file, hand the text over and assert `== []`.

Why this shape. A suite of `assert "string" in text` cannot tell the pipeline from a
document *about* the pipeline: six semantic mutations of `release.yml` and of the classifier — a raw
PAT in the header instead of the basic-auth pair, the secret printed with a prefix, the release
commit signature switched off, `npm ci` without `--ignore-scripts`, the token back in a
workflow-level `env:`, and the exit-code guard removed from the G2 row — kept every asserted string
byte-identical and passed the previous, document-shaped suite 31/31. Here every one of them is a
difference in the text these functions read, and
`test_the_named_semantic_mutations_turn_the_checks_red` applies all six to the real files.

Nothing here runs the engine and nothing touches the network; `.github/release/assert-outcome.sh` is
*executed* by `tests/e2e/test_release_outcome.py`, and this module only checks that the file still
declares the precedence and the guard that suite proves behaviourally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASERC = REPO_ROOT / ".releaserc.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
CLASSIFIER = REPO_ROOT / ".github" / "release" / "assert-outcome.sh"
SIGNERS = REPO_ROOT / ".github" / "signers" / "allowed_signers"
PACKAGE_JSON = REPO_ROOT / "package.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"
AGENTS = REPO_ROOT / "AGENTS.md"
SPEC = REPO_ROOT / "specs" / "110-release.md"

# The lifecycle runs in array order — `verifyConditions` for all plugins first (the
# pre-flight), then `prepare`. `exec` must precede `git` so the `sed` writes the literal before it
# is staged and the tag is created after both.
PLUGIN_ORDER = [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/changelog",
    "@semantic-release/exec",
    "@semantic-release/git",
    "@semantic-release/github",
]

# The table, row by row. `breaking` is first for readability only — the analyzer keeps
# every match and returns the highest type, and `{"revert": true}` is the only row that matches the
# header `Revert "…"` that `git revert` writes.
RELEASE_RULES = [
    {"breaking": True, "release": "major"},
    {"revert": True, "release": "patch"},
    {"type": "feat", "release": "minor"},
    {"type": "fix", "release": "patch"},
    {"type": "style", "release": "patch"},
    {"type": "refactor", "release": "patch"},
    {"type": "perf", "release": "patch"},
    {"type": "build", "release": "patch"},
    {"type": "ci", "release": "patch"},
    {"type": "chore", "release": "patch"},
    {"type": "revert", "release": "patch"},
    {"type": "docs", "release": False},
    {"type": "test", "release": False},
]

# The `env:` map of every step, in step order. Nothing at workflow or job level, and each
# secret in exactly the step that uses it.
STEP_ENVS: list[set[str]] = [
    set(),
    set(),
    {"HIFA_RELEASE_TOKEN"},
    set(),
    set(),
    {"HIFA_RELEASE_SIGNING_KEY"},
    set(),
    {"HIFA_RELEASE_TOKEN", "GITHUB_TOKEN"},
    set(),
    set(),
]

# The only keys `$GITHUB_ENV` may carry, all of them non-secret.
ALLOWED_GITHUB_ENV_KEYS = {
    "SEMANTIC_RELEASE_ARGS",
    "RELEASE_DRY_RUN",
    "RELEASE_LOG",
    "RELEASE_EXIT_CODE",
    "PRE_RUN_HEAD",
    "PRE_RUN_MAIN",
    "PRE_RUN_TAGS",
    "GIT_EDITOR",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
}

# The evaluation order of the classifier, with the token each section must carry. The
# numbers are evaluation orders, not catalogue ids: id 12 is evaluated first, ids 3-10 sit at
# `order = id - 1`, and id 11 is the residual at order 13.
CLASSIFIER_ORDERS = {
    1: "release_credential_persisted",
    2: "green: nothing written",
    3: "green: no relevant changes",
    4: "release_signing_key_invalid",
    5: "release_tag_not_signed",
    6: "release_commit_unsigned",
    7: "release_version_mismatch",
    8: "release_forbidden",
    9: "release_notes_missing",
    10: "release_tag_push_rejected",
    11: "release_partial_state",
    12: "green: released",
    13: "release_engine_aborted",
}

# The twelve catalogue ids, of which 1 and 2 belong to the gates and never appear in
# the classifier.
GATE_CODES = {"release_token_missing", "release_signing_key_missing"}

SECRET_VARS = ("HIFA_RELEASE_TOKEN", "HIFA_RELEASE_SIGNING_KEY")
# A command whose stdout is the log leaks the value it expands; a redirection makes it a file write.
PRINT_COMMANDS = {"echo", "printf", "cat", "tee", "printenv", "env", "set"}

NODE_VERSION = "22.22.3"
RELEASE_LITERAL = re.compile(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
VERSION_ASSIGNMENT = re.compile(r'^version = "[^"]*"$', re.MULTILINE)
BEGIN_MARKER = "<!-- release-types:begin -->"
END_MARKER = "<!-- release-types:end -->"


def _needs(violations: list[str], condition: object, message: str) -> None:
    if not condition:
        violations.append(message)


def _code_lines(text: str) -> list[str]:
    """The text without comment lines and blanks: a comment may *name* what the contract forbids."""
    return [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


# --- The workflow: shape, steps and text -----------------------------------------------------


def _triggers(workflow: dict[str, Any]) -> Any:
    """`on:` arrives as the key `True` under YAML 1.1, where `on` is a boolean."""
    for key in ("on", True):
        if key in workflow:
            return workflow[key]
    return None


def _check_workflow_shape(workflow: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    triggers = _triggers(workflow)

    _needs(violations, workflow.get("name") == "Release", "The workflow must be named Release")
    _needs(
        violations,
        isinstance(triggers, dict) and triggers.get("push") == {"branches": ["main"]},
        "The only push trigger is `branches: [main]`",
    )
    _needs(
        violations,
        isinstance(triggers, dict) and "pull_request" not in triggers,
        "A pull_request trigger is a contract violation",
    )
    _needs(
        violations,
        isinstance(triggers, dict) and set(triggers) == {"push", "workflow_dispatch", "schedule"},
        "The trigger set is push + workflow_dispatch + schedule and nothing else",
    )
    # A push whose commit message quotes the skip token is suppressed by GitHub with no run at
    # all — nothing to classify, nothing to alert on. The schedule is the only defence that does not
    # depend on the message that caused the skip, so its absence is a violation, not a preference.
    schedule = (triggers or {}).get("schedule") if isinstance(triggers, dict) else None
    _needs(
        violations,
        isinstance(schedule, list) and any("0 */6 * * *" in str(entry) for entry in schedule),
        "The workflow also runs on a six-hour schedule (a skipped push self-heals)",
    )
    dispatch = (triggers or {}).get("workflow_dispatch") if isinstance(triggers, dict) else None
    dry_run = ((dispatch or {}).get("inputs") or {}).get("dry_run") or {}
    _needs(
        violations,
        dry_run.get("type") == "boolean" and dry_run.get("default") is True,
        "`workflow_dispatch.dry_run` must be a boolean that defaults to true",
    )
    _needs(
        violations,
        dry_run.get("description") == "Compute and print the release plan; write nothing.",
        "The dry_run description is part of the contract, verbatim",
    )
    _needs(
        violations,
        "env" not in workflow,
        "A workflow-level `env:` gives every step both secrets",
    )
    _needs(
        violations,
        workflow.get("permissions") == {"contents": "read"},
        "Workflow permissions are `contents: read`",
    )
    _needs(
        violations,
        workflow.get("concurrency") == {"group": "release", "cancel-in-progress": False},
        "`concurrency: {group: release, cancel-in-progress: false}`",
    )

    job = (workflow.get("jobs") or {}).get("release") or {}
    _needs(violations, job.get("permissions") == {"contents": "write"}, "Job `contents: write`")
    _needs(violations, job.get("runs-on") == "ubuntu-latest", "`runs-on` is ubuntu-latest")
    _needs(violations, job.get("timeout-minutes") == 20, "`timeout-minutes` is 20")
    _needs(
        violations,
        "env" not in job,
        "A job-level `env:` gives every step both secrets",
    )
    _needs(
        violations,
        "if" not in job,
        "No job-level `if:` — the loop breaker is the `[skip ci]` token, and a `contains()`"
        " copy of that rule skips a legitimate release instead of preventing a loop",
    )
    return violations


def _check_workflow_steps(steps: Any) -> list[str]:
    violations: list[str] = []
    if not isinstance(steps, list) or len(steps) != 10:
        found = len(steps) if isinstance(steps, list) else 0
        violations.append(f"The job has ten steps; found {found}")
        return violations

    def run(index: int) -> str:
        step = steps[index]
        value = step.get("run", "") if isinstance(step, dict) else ""
        return value if isinstance(value, str) else ""

    def env(index: int) -> set[str]:
        step = steps[index]
        return set(step.get("env") or {}) if isinstance(step, dict) else set()

    # The per-step env map is the control; a workflow-level `env:` would pass the same
    # assertion for a step and hide the leak, which is why the map is compared, not grepped.
    for index, expected in enumerate(STEP_ENVS):
        _needs(
            violations,
            env(index) == expected,
            f"Step {index + 1} declares env {sorted(env(index))}, expected {sorted(expected)}",
        )

    # Step 1 — full history, no persisted credentials.
    step = steps[0] if isinstance(steps[0], dict) else {}
    _needs(violations, step.get("uses") == "actions/checkout@v4", "Step 1: actions/checkout@v4")
    _needs(violations, (step.get("with") or {}).get("fetch-depth") == 0, "Step 1: `fetch-depth: 0`")
    _needs(
        violations,
        (step.get("with") or {}).get("persist-credentials") is False,
        "Step 1: persist-credentials must be false",
    )

    # Step 2 — the Node/npm pair the lockfile was generated with.
    step = steps[1] if isinstance(steps[1], dict) else {}
    with_ = step.get("with") or {}
    _needs(violations, step.get("uses") == "actions/setup-node@v4", "Step 2: setup-node@v4")
    _needs(
        violations,
        with_.get("node-version") == NODE_VERSION,
        f"`node-version` must be the pinned {NODE_VERSION}",
    )
    _needs(violations, with_.get("cache") == "npm", "Step 2: `cache: npm`")
    _needs(
        violations,
        with_.get("cache-dependency-path") == "package-lock.json",
        "Step 2: `cache-dependency-path: package-lock.json`",
    )

    # Step 3 — the token gate owns catalogue id 1: code, mark, red.
    gate = run(2)
    _needs(violations, "release_token_missing" in gate, "Step 3: prints release_token_missing")
    _needs(violations, '-z "${HIFA_RELEASE_TOKEN:-}"' in gate, "Step 3: an empty token fails")
    _needs(
        violations,
        "release_code_emitted" in gate,
        "The gate writes the dedupe mark it owns the line with",
    )
    _needs(violations, "exit 1" in gate, "Step 3: fails closed before any write")

    # Step 4 — exact install, no lifecycle script, and no empty baseline.
    install = run(3)
    _needs(
        violations,
        "npm ci --ignore-scripts" in install,
        "The install is `npm ci --ignore-scripts`",
    )
    _needs(violations, "npm install" not in install, "Never `npm install`")
    _needs(
        violations,
        "node_modules/.package-lock.json" in install,
        "Step 4: proves the tree came from the lockfile",
    )
    _needs(violations, "git diff --exit-code" in install, "Step 4: the lockfile is untouched")
    _needs(
        violations,
        'test -n "$head_sha"' in install and 'test -n "$main_sha"' in install,
        "PRE_RUN_HEAD and PRE_RUN_MAIN are asserted non-empty when captured",
    )
    _needs(
        violations,
        "PRE_RUN_HEAD=" in install and "PRE_RUN_MAIN=" in install and "PRE_RUN_TAGS=" in install,
        "Step 4: the baseline the classifier reads is captured here",
    )
    _needs(
        violations,
        "test -s" in install,
        "The tag list is asserted non-empty — an empty capture is not a baseline",
    )

    # Step 5 — the dry-run split writes the flag the classifier reads.
    split = run(4)
    _needs(violations, "inputs.dry_run" in split, "Step 5: the split reads inputs.dry_run")
    _needs(violations, "--dry-run" in split, "Step 5: the dry-run argument set")
    _needs(
        violations,
        "RELEASE_DRY_RUN=$dry" in split,
        "RELEASE_DRY_RUN is what the classifier reads",
    )
    _needs(
        violations,
        "dry=true" in split and "dry=false" in split,
        "Step 5: both branches of the flag must be written",
    )

    # Step 6 — identity and signing; the key is visible to this step and to no other.
    signing = run(5)
    _needs(
        violations,
        "release_signing_key_missing" in signing and "release_code_emitted" in signing,
        "Step 6 owns catalogue id 2 and writes the mark",
    )
    _needs(
        violations,
        'printf \'%s\\n\' "$HIFA_RELEASE_SIGNING_KEY" > "$RUNNER_TEMP/hifa_signing_key"' in signing,
        "The key is materialised in $RUNNER_TEMP",
    )
    _needs(
        violations,
        'chmod 600 "$RUNNER_TEMP/hifa_signing_key"' in signing,
        "The key is mode 0600",
    )
    _needs(
        violations,
        'ssh-keygen -y -f "$RUNNER_TEMP/hifa_signing_key"' in signing,
        "The key is proven readable before it signs anything",
    )
    _needs(
        violations,
        'git config user.signingkey "$RUNNER_TEMP/hifa_signing_key"' in signing,
        "`user.signingkey` is the PRIVATE half",
    )
    _needs(
        violations,
        'git config user.signingkey "$RUNNER_TEMP/hifa_signing_key.pub"' not in signing,
        "Binding the .pub makes `git tag` fail with 'Could not read SSH key'",
    )
    _needs(violations, "git config gpg.format ssh" in signing, "SSH signing, not GPG")
    _needs(
        violations,
        "git config gpg.ssh.allowedSignersFile .github/signers/allowed_signers" in signing,
        "One allowlist, two checks",
    )
    _needs(
        violations,
        "git config commit.gpgSign true" in signing,
        "The release commit is signed (`commit.gpgSign true`)",
    )
    _needs(
        violations,
        "git config tag.gpgSign true" in signing,
        "The tag is signed at creation (`tag.gpgSign true`)",
    )
    _needs(
        violations,
        "GIT_EDITOR=" in signing and ".github/release/tag-message.sh" in signing,
        "GIT_EDITOR is the tag-message helper",
    )

    # Step 7 — proves that nothing persisted the credential, and writes nothing.
    scope = run(6)
    _needs(violations, "grep -qE" in scope, "Step 7: the environment file is grepped")
    _needs(
        violations,
        "HIFA_RELEASE_TOKEN" in scope and "HIFA_RELEASE_SIGNING_KEY" in scope,
        "Step 7: both secrets are named in the pattern",
    )
    _needs(violations, "GIT_CONFIG_" in scope and "AUTHORIZATION" in scope, "Step 7: the header")
    _needs(violations, "extraheader" in scope and ".git/config" in scope, "Step 7: `.git/config`")
    _needs(
        violations,
        "GITHUB_ENV" not in scope.replace('"$GITHUB_ENV"', "", 1)
        or '>> "$GITHUB_ENV"' not in scope,
        "Step 7: a check that runs before the write must not write either",
    )

    # Step 8 — the engine, with the credential in the memory of this shell only.
    engine = run(7)
    _needs(
        violations,
        "./node_modules/.bin/semantic-release" in engine,
        "The pinned binary, never a floating npx",
    )
    _needs(violations, "npx" not in engine, "Never npx")
    _needs(violations, "semantic-release.log" in engine, "Step 9 separates states by this log")
    _needs(violations, "RELEASE_LOG=" in engine, "The classifier reads RELEASE_LOG")
    _needs(
        violations,
        "RELEASE_EXIT_CODE=$status" in engine and 'exit "$status"' in engine,
        "The engine's own exit status is recorded and propagated",
    )
    _needs(
        violations,
        not re.search(r"^\s*echo\s+\"?release_[a-z_]+\"?\s*$", engine, re.MULTILINE),
        "Step 8 prints no code — step 9 owns every line it does not delegate",
    )

    # The header is built from the token with printf+base64, inline, in this shell.
    header_lines = [line for line in _code_lines(engine) if "GIT_CONFIG_VALUE_0=" in line]
    _needs(
        violations,
        len(header_lines) == 1,
        f"Exactly one GIT_CONFIG_VALUE_0 assignment, found {len(header_lines)}",
    )
    for line in header_lines:
        _needs(
            violations,
            "AUTHORIZATION: basic" in line,
            "The header is the basic-auth pair, not the bare token",
        )
        _needs(
            violations,
            "printf" in line and "base64" in line,
            "The header is built with `printf` + `base64`, not by embedding the PAT",
        )
        _needs(
            violations,
            not re.search(r"basic\s+\"?\$\{?HIFA_RELEASE_TOKEN", line),
            "`AUTHORIZATION: basic $HIFA_RELEASE_TOKEN` is the raw PAT, not a basic pair",
        )
    _needs(violations, "::add-mask::" in engine, "The header is masked in the log")
    _needs(violations, "set +x" in engine, "No tracing over the credential")

    # Step 9 — the classifier behind an existence guard, always, one command.
    step9 = steps[8] if isinstance(steps[8], dict) else {}
    classifier_step = run(8)
    _needs(violations, step9.get("if") == "always()", "Step 9: `if: always()`")
    _needs(
        violations,
        classifier_step.count("./.github/release/assert-outcome.sh") == 1,
        "Step 9 runs the governed classifier, declared once",
    )
    _needs(
        violations,
        '[ -x "$classifier" ]' in classifier_step,
        "Step 9 tests that the classifier is on disk before running it",
    )
    _needs(
        violations,
        "release_engine_aborted" in classifier_step and "exit 1" in classifier_step,
        "A classifier that is not on disk is `release_engine_aborted`, exit 1 —"
        " a failed checkout would otherwise die with a shell error and classify nothing",
    )
    codes_in_step9 = re.findall(r"release_[a-z_]+", classifier_step)
    _needs(
        violations,
        codes_in_step9 == ["release_engine_aborted"],
        "Step 9 carries no classification logic — the only code it may print is"
        f" the zero-line path's, found {codes_in_step9}",
    )

    # Step 10 — cleanup on red and on green.
    step10 = steps[9] if isinstance(steps[9], dict) else {}
    cleanup = run(9)
    _needs(violations, step10.get("if") == "always()", "Step 10: cleanup runs `if: always()`")
    _needs(
        violations,
        'rm -f "$RUNNER_TEMP/hifa_signing_key" "$RUNNER_TEMP/hifa_signing_key.pub"' in cleanup,
        "The key and its .pub are removed unconditionally",
    )
    _needs(violations, "preflight-*" in cleanup, "A local pre-flight ref is cleaned up")
    _needs(violations, "git tag -d" in cleanup, "Step 10: the pre-flight ref is deleted")
    return violations


def _check_workflow_text(lines: list[str]) -> list[str]:
    """The checks that read the file as text: keys written, secrets printed, commands refused."""
    violations: list[str] = []
    text = "\n".join(lines)

    written = set(re.findall(r'echo "([A-Z_]+)=', text))
    _needs(
        violations,
        written <= ALLOWED_GITHUB_ENV_KEYS,
        f"$GITHUB_ENV carries only non-secret keys;"
        f" unexpected {sorted(written - ALLOWED_GITHUB_ENV_KEYS)}",
    )

    # No *write* to `$GITHUB_ENV` may carry the credential or a base64 of it. The
    # scope check of step 7 names `$GITHUB_ENV` on a line that only reads it, which is why this is
    # a check on the write redirect, not on the word.
    for line in lines:
        if not re.search(r'>>?\s*"?\$GITHUB_ENV"?', line):
            continue
        _needs(
            violations,
            not re.search(
                r"GIT_CONFIG|AUTHORIZATION|base64|HIFA_RELEASE_TOKEN|HIFA_RELEASE_SIGNING_KEY", line
            ),
            f"A credential reaches $GITHUB_ENV: {line.strip()}",
        )

    # Never printed, never traced.
    for line in lines:
        for var in SECRET_VARS:
            if f"${var}" not in line and "${" + var not in line:
                continue
            command = line.strip().split()[0] if line.strip().split() else ""
            if command in PRINT_COMMANDS and ">" not in line:
                violations.append(f"`{line.strip()}` prints ${var} to the log")

    _needs(violations, "set -x" not in text, "`set -x` would trace the credential")
    _needs(violations, "npx" not in text, "Never npx")
    _needs(
        violations,
        "persist-credentials: true" not in text,
        "The PAT must not be persisted on disk",
    )
    _needs(violations, "git push -f" not in text, "A tag is never force-pushed")
    _needs(
        violations,
        "${RELEASE_EXIT_CODE:-0}" not in text,
        "RELEASE_EXIT_CODE has no benign default — absence reads as 255",
    )
    _needs(
        violations,
        text.count("npm ci --ignore-scripts") == 1,
        "Exactly one `npm ci --ignore-scripts`, and no other install path",
    )
    return violations


def check_workflow(text: str) -> list[str]:
    """Everything the release contract requires of `.github/workflows/release.yml`."""
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError as error:  # pragma: no cover - a broken file fails loudly instead
        return [f"`release.yml` is not valid YAML: {error}"]
    if not isinstance(workflow, dict):
        return ["`release.yml` is not a YAML mapping"]

    job = (workflow.get("jobs") or {}).get("release") or {}
    violations = _check_workflow_shape(workflow)
    violations += _check_workflow_steps(job.get("steps"))
    violations += _check_workflow_text(_code_lines(text))
    return violations


# --- The engine config: `.releaserc.json` -----------------------------------------------------


def _plugin_options(config: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in config.get("plugins", []):
        if isinstance(entry, list) and entry and entry[0] == name:
            options = entry[1] if len(entry) > 1 else {}
            return options if isinstance(options, dict) else {}
    return {}


def check_releaserc(text: str) -> list[str]:
    """Everything the release contract requires of `.releaserc.json`."""
    try:
        config = json.loads(text)
    except json.JSONDecodeError as error:  # pragma: no cover - a broken file fails loudly instead
        return [f".releaserc.json is not valid JSON: {error}"]
    if not isinstance(config, dict):
        return [".releaserc.json is not a JSON object"]

    violations: list[str] = []
    _needs(violations, config.get("branches") == ["main"], "`branches: [main]`")
    _needs(violations, config.get("tagFormat") == "v${version}", "`tagFormat: v${version}`")

    names = [entry if isinstance(entry, str) else entry[0] for entry in config.get("plugins", [])]
    _needs(violations, names == PLUGIN_ORDER, f"The plugin order is the lifecycle order: {names}")

    analyzer = _plugin_options(config, "@semantic-release/commit-analyzer")
    notes = _plugin_options(config, "@semantic-release/release-notes-generator")
    _needs(violations, analyzer.get("preset") == "angular", "`preset: angular` on the analyzer")
    _needs(violations, notes.get("preset") == "angular", "`preset: angular` on the notes")

    rules = analyzer.get("releaseRules") or []
    _needs(
        violations,
        len(rules) == 13,
        f"The bump table has thirteen rows, found {len(rules)}",
    )
    _needs(violations, rules == RELEASE_RULES, f"The bump table is the contract: {rules}")

    exec_options = _plugin_options(config, "@semantic-release/exec")
    verify = str(exec_options.get("verifyConditionsCmd", ""))
    prepare = str(exec_options.get("prepareCmd", ""))
    violations += _check_preflight(verify)
    violations += _check_prepare(prepare)

    git_options = _plugin_options(config, "@semantic-release/git")
    _needs(
        violations,
        git_options.get("assets") == ["CHANGELOG.md", "pyproject.toml"],
        "Exactly the two files a release writes",
    )
    message = str(git_options.get("message", ""))
    _needs(
        violations,
        message.startswith("chore(release): ${nextRelease.version} [skip ci]"),
        "The release commit message carries the `[skip ci]` loop breaker",
    )
    _needs(violations, "${nextRelease.notes}" in message, "The body is the recovery source")

    github = _plugin_options(config, "@semantic-release/github")
    for option in ("successComment", "failComment", "releasedLabels"):
        _needs(
            violations,
            github.get(option) is False,
            f"`{option}: false` keeps the run from needing issues/PR scopes",
        )
    return violations


def _check_preflight(cmd: str) -> list[str]:
    """The literal guard first, then a signed, verified, deleted probe tag."""
    violations: list[str] = []
    _needs(
        violations,
        cmd.startswith("set -eu; count=$(grep -cE"),
        "The guard is the first statement",
    )
    _needs(
        violations,
        '"$count" != 1' in cmd,
        "The guard refuses a count other than exactly one",
    )
    _needs(
        violations,
        "release_version_mismatch: expected exactly one version literal line" in cmd,
        "The refusal names the code the classifier reports",
    )
    _needs(
        violations,
        "signing pre-flight: starting" in cmd,
        "Order 4: the pre-flight announces itself before it creates the tag",
    )
    _needs(violations, "git verify-tag" in cmd, "The probe is verified against allowed_signers")
    _needs(violations, "git tag -d" in cmd, "The probe is deleted in the same command")
    _needs(violations, "git push" not in cmd, "The pre-flight must never push")

    starting = cmd.find("signing pre-flight: starting")
    probe = cmd.find('git tag "$probe"')
    _needs(
        violations,
        0 <= starting < probe,
        "Order 4 keys on `signing pre-flight: starting` *before* the tag is created: without"
        " that order the closing line alone cannot tell 'never reached' from 'started and failed'",
    )
    return violations


def _check_prepare(cmd: str) -> list[str]:
    """The guarded `sed` — it fails unless the substitution is exactly one line."""
    violations: list[str] = []
    _needs(
        violations,
        'expected="${nextRelease.version}"' in cmd,
        "The version comes from the engine, never from the tree",
    )
    _needs(
        violations,
        "before=$(grep -cE" in cmd and '"$before" != 1' in cmd,
        "The input must carry exactly one candidate line before the substitution",
    )
    _needs(
        violations,
        "sed 's/^version = \"" in cmd,
        "The `sed` rewrites only a literal at column 0",
    )
    _needs(
        violations,
        "after=$(grep -cxF" in cmd and '"$after" != 1' in cmd,
        "A substitution count other than one fails the command",
    )
    _needs(violations, "rm -f pyproject.toml.tmp" in cmd, "A refused run leaves nothing behind")
    _needs(
        violations,
        cmd.rstrip().endswith("mv pyproject.toml.tmp pyproject.toml"),
        "The file is replaced by `mv`, so nothing is half-written",
    )
    return violations


# --- `package.json` ---------------------------------------------------------------------------


def check_package_json(text: str) -> list[str]:
    """`package.json` is tooling: private, pinned and not a version carrier."""
    try:
        package = json.loads(text)
    except json.JSONDecodeError as error:  # pragma: no cover - a broken file fails loudly instead
        return [f"`package.json` is not valid JSON: {error}"]

    violations: list[str] = []
    _needs(violations, package.get("private") is True, "`private: true`")
    _needs(violations, package.get("version") == "0.0.0", "npm's `0.0.0` placeholder")
    expected = {
        "semantic-release": "21.1.2",
        "@semantic-release/changelog": "6.0.3",
        "@semantic-release/exec": "6.0.3",
        "@semantic-release/git": "10.0.1",
        "@semantic-release/github": "9.2.6",
    }
    _needs(
        violations,
        package.get("devDependencies") == expected,
        f"The engine is pinned exactly: {package.get('devDependencies')}",
    )
    _needs(
        violations,
        not package.get("dependencies"),
        "The tooling is a devDependency — it never enters the runtime",
    )
    return violations


# --- The classifier -----------------------------------------------------------------------


def _classifier_sections(text: str) -> dict[int, str]:
    """The script split on its `# --- Order N — …` markers, so each order can be inspected."""
    markers = list(re.finditer(r"^# --- Order (\d+) — .*$", text, re.MULTILINE))
    sections: dict[int, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        sections[int(marker.group(1))] = text[marker.end() : end]
    return sections


def _classifier_sequence(text: str) -> list[int]:
    """The order numbers as they *appear* in the file — `bash` runs them top to bottom."""
    return [int(number) for number in re.findall(r"^# --- Order (\d+) — .*$", text, re.MULTILINE)]


def check_classifier(text: str) -> list[str]:
    """The precedence, the greens, the G2 guard and no side effect — structure, not behaviour."""
    violations: list[str] = []
    _needs(
        violations,
        text.startswith("#!/usr/bin/env bash\n"),
        "The classifier is a `bash` script",
    )
    _needs(violations, "set -euo pipefail" in text, "`set -euo pipefail`")

    # The precedence is the *sequence*, not the set: `bash` runs the file top to bottom, so a set
    # comparison passes a file whose blocks 5 and 6 (or 9 and 10) were exchanged while each marker
    # kept its own body. Measured: that mutation left a set-only suite 50/50 green.
    sequence = _classifier_sequence(text)
    _needs(
        violations,
        sequence == list(CLASSIFIER_ORDERS),
        "The evaluation orders are not in precedence in the file —"
        f" found {sequence}, expected {list(CLASSIFIER_ORDERS)}",
    )
    sections = _classifier_sections(text)
    for order, token in CLASSIFIER_ORDERS.items():
        body = sections.get(order, "")
        if not body:
            continue
        _needs(violations, token in body, f"Order {order} does not produce `{token}`")

    # Order 1 inspects *values*, not only names — the leak step 8 builds carries no key
    # name and is base64, so a name-shaped pattern cannot be the whole check.
    order_one = sections.get(1, "")
    _needs(
        violations,
        "base64 -d" in order_one,
        "Order 1 decodes a base64 value instead of trusting the key name",
    )
    _needs(
        violations,
        "gh[pousr]_" in order_one and "github_pat_" in order_one,
        "Order 1 matches a GitHub token by its prefix",
    )
    # `--help` is not a green — the script takes no arguments at all.
    _needs(
        violations,
        '[ "$#" -ne 0 ]' in text,
        "An argument is refused, not classified",
    )

    # Exactly one line per run, owned by the mark, and the gates keep ids 1-2.
    statements = "\n".join(_code_lines(text))
    _needs(violations, "release_code_emitted" in statements, "The dedupe mark is read")
    for code in GATE_CODES:
        _needs(
            violations,
            code not in statements,
            f"`{code}` belongs to its gate; the classifier must not print it twice",
        )

    # A red run is never laundered green: G2 requires the engine's exit code explicitly.
    g2 = sections.get(12, "")
    _needs(violations, "g2_exit_ok=false" in g2, "The G2 verdict starts false")
    _needs(
        violations,
        re.search(r'if \[ "\$status" -eq 0 \]; then g2_exit_ok=true; fi', g2) is not None,
        "G2 requires `RELEASE_EXIT_CODE == 0` — a failed run is never a green",
    )
    _needs(
        violations,
        "RELEASE_EXIT_CODE:-255" in text,
        "An unset RELEASE_EXIT_CODE reads as the 255 sentinel, never 0",
    )

    # Deterministic — reads the repository, never writes to it and never pushes.
    _needs(violations, "git push" not in text, "The classifier never pushes")
    _needs(violations, "git tag -d" not in text, "A tag is never deleted here")
    _needs(violations, "fetch" not in text, "No fetch — the classifier reads the checkout")
    return violations


# --- The documented bump table ----------------------------------------------------------------


def _marked_block(path_text: str) -> str | None:
    lines = path_text.splitlines()
    if BEGIN_MARKER not in lines or END_MARKER not in lines:
        return None
    start = lines.index(BEGIN_MARKER)
    end = lines.index(END_MARKER)
    return "\n".join(lines[start : end + 1])


def _documented_bumps(agents_text: str) -> dict[str, str]:
    block = _marked_block(agents_text) or ""
    bumps: dict[str, str] = {}
    inside = False
    for line in block.splitlines():
        if line == BEGIN_MARKER:
            inside = True
            continue
        if line == END_MARKER:
            break
        if not inside or not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2 or cells[0] == "type":
            continue
        left, right = cells
        if "BREAKING CHANGE" in left:
            bumps["breaking"] = right
            continue
        for name in (n.strip() for n in left.split(",")):
            if name:
                bumps[name] = right
    return bumps


def _engine_bumps(releaserc_text: str) -> dict[str, str]:
    rules = _plugin_options(json.loads(releaserc_text), "@semantic-release/commit-analyzer")
    bumps: dict[str, str] = {}
    for rule in rules.get("releaseRules") or []:
        release = rule.get("release")
        bump = "none" if release is False else str(release)
        if rule.get("breaking") is True:
            bumps["breaking"] = bump
        elif "type" in rule:
            bumps[rule["type"]] = bump
    return bumps


def check_agents(text: str, spec_text: str) -> list[str]:
    """The marked block is compared, not grepped, and equals the spec's."""
    violations: list[str] = []
    _needs(violations, text.count(BEGIN_MARKER) == 1, "Exactly one `release-types:begin` marker")
    _needs(violations, text.count(END_MARKER) == 1, "Exactly one `release-types:end` marker")
    block = _marked_block(text)
    if block is None:
        violations.append("The marked block is missing, so two greps would replace a comparison")
        return violations
    _needs(
        violations,
        block == _marked_block(spec_text),
        "AGENTS.md and specs/110-release.md document different bump tables",
    )
    _needs(violations, "BREAKING CHANGE" in block, "The breaking row is a footer, not a `!`")
    _needs(
        violations,
        "feat!" in text,
        "The document must say a `feat!:` header releases nothing",
    )
    return violations


def check_documented_bumps(agents_text: str, releaserc_text: str) -> list[str]:
    """The documented mapping and the engine's mapping agree, in both directions."""
    documented = _documented_bumps(agents_text)
    engine = _engine_bumps(releaserc_text)
    violations: list[str] = []
    _needs(violations, documented, "The marked block carries no type rows")
    only_documented = sorted(set(documented) - set(engine))
    only_engine = sorted(set(engine) - set(documented))
    _needs(
        violations,
        not only_documented and not only_engine,
        f"AGENTS.md and .releaserc.json disagree on the type set: "
        f"only documented={only_documented}, only in the engine={only_engine}",
    )
    differing = {
        k: (documented[k], engine.get(k)) for k in documented if documented[k] != engine.get(k)
    }
    _needs(violations, not differing, f"The documented bump and the rule disagree: {differing}")
    return violations


# --- the files honour their contract ----------------------------------------------------------


def test_the_workflow_matches_the_contract() -> None:
    """The workflow has ten steps, the effects in order, the secrets per step and nowhere else."""
    assert check_workflow(WORKFLOW.read_text(encoding="utf-8")) == []


def test_the_engine_config_matches_the_contract() -> None:
    """The engine config: plugin order, bump table, tag format, literal guard, guarded `sed`."""
    assert check_releaserc(RELEASERC.read_text(encoding="utf-8")) == []


def test_package_json_is_not_a_version_carrier() -> None:
    """`package.json` is private, `0.0.0`, and the engine pinned exactly."""
    assert check_package_json(PACKAGE_JSON.read_text(encoding="utf-8")) == []


def test_the_classifier_declares_the_precedence_and_the_g2_guard() -> None:
    """The behaviour is proven in test_release_outcome.py; this is its structure."""
    assert check_classifier(CLASSIFIER.read_text(encoding="utf-8")) == []


def test_the_documented_block_is_identical_to_the_spec() -> None:
    """`diff -u` of the two marker ranges must be empty, and the table is total."""
    agents = AGENTS.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")

    assert check_agents(agents, spec) == []
    assert check_documented_bumps(agents, RELEASERC.read_text(encoding="utf-8")) == []


def test_allowed_signers_carries_the_release_principal_with_the_git_namespace() -> None:
    """One allowlist, two checks — `hifa-release` verifies commits and tags."""
    lines = [line for line in SIGNERS.read_text(encoding="utf-8").splitlines() if line.strip()]
    principals = [line.split()[0] for line in lines]

    assert "ginkelte" in principals, "the human signing key must stay in the allowlist"
    assert principals.count("hifa-release") == 1
    release = next(line for line in lines if line.startswith("hifa-release "))
    parts = release.split()
    assert parts[1] == 'namespaces="git"', "without the namespace git will not verify with it"
    assert parts[2] == "ssh-ed25519"
    assert re.fullmatch(r"AAAA[0-9A-Za-z+/]+[=]{0,2}", parts[3]), parts[3]


# --- The six named mutations must turn these checks red ---------------------------------------
#
# Each mutation keeps every string the previous, document-shaped suite asserted, and changes what
# the pipeline *does*. `check_*` must observe the difference; that is the whole point of the
# refactor, and the criterion is that the six are in this test, not in prose.


@dataclass(frozen=True)
class Mutation:
    name: str
    why: str
    target: Path
    old: str
    new: str
    checker: Any


def _workflow_mutation(name: str, why: str, old: str, new: str) -> Mutation:
    return Mutation(name, why, WORKFLOW, old, new, check_workflow)


HEADER_LINE = """$(printf 'x-access-token:%s' "$HIFA_RELEASE_TOKEN" | base64 -w0)"""

MUTATIONS = [
    _workflow_mutation(
        "M1",
        "the header carries the raw PAT instead of the basic-auth pair",
        HEADER_LINE,
        "$HIFA_RELEASE_TOKEN",
    ),
    _workflow_mutation(
        "M2",
        "the secret is printed with a prefix, defeating the byte-exact greps",
        'echo "::add-mask::$GIT_CONFIG_VALUE_0"',
        'echo "::add-mask::$GIT_CONFIG_VALUE_0"\n          echo "leak: $HIFA_RELEASE_TOKEN"',
    ),
    _workflow_mutation(
        "M3",
        "the release commit stops being signed",
        "git config commit.gpgSign true",
        "git config commit.gpgSign false",
    ),
    _workflow_mutation(
        "M4",
        "`--ignore-scripts` is dropped from `npm ci`",
        "npm ci --ignore-scripts\n",
        "npm ci\n",
    ),
    _workflow_mutation(
        "M5",
        "the PAT returns to a workflow-level `env:`, in scope for every step",
        "permissions:\n  contents: read",
        "env:\n  HIFA_RELEASE_TOKEN: ${{ secrets.HIFA_RELEASE_TOKEN }}\n\n"
        "permissions:\n  contents: read",
    ),
    Mutation(
        "M6",
        "the `RELEASE_EXIT_CODE == 0` guard disappears from the G2 row of the classifier",
        CLASSIFIER,
        'if [ "$status" -eq 0 ]; then g2_exit_ok=true; fi',
        "g2_exit_ok=true",
        check_classifier,
    ),
]


@pytest.mark.parametrize("mutation", MUTATIONS, ids=[m.name for m in MUTATIONS])
def test_the_named_semantic_mutations_turn_the_checks_red(mutation: Mutation) -> None:
    """Each mutation is applied to the real file, and the checks must report it."""
    text = mutation.target.read_text(encoding="utf-8")
    assert mutation.checker(text) == [], f"{mutation.target.name} is already non-compliant"
    assert text.count(mutation.old) == 1, (
        f"{mutation.name} does not apply to {mutation.target.name}"
    )

    violations = mutation.checker(text.replace(mutation.old, mutation.new, 1))

    assert violations, f"{mutation.name} passed the checks: {mutation.why}"


# --- The precedence is a sequence, and swapping two blocks must be visible -----------------------
#
# The set-shaped check this replaces compared `sorted(sections)` with `sorted(CLASSIFIER_ORDERS)`:
# exchanging two whole blocks — marker and body together, so every token stays inside its own
# section — left it 50/50 green. `bash` does not read a set; it reads the file top to bottom, so
# the criterion is the sequence and the mutation is applied here, not described.


def _swap_order_blocks(text: str, first: int, second: int) -> str:
    """Exchange the two `# --- Order N — …` blocks whole, leaving every other byte in place."""
    markers = list(re.finditer(r"^# --- Order (\d+) — .*$", text, re.MULTILINE))
    spans: dict[int, tuple[int, int]] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        spans[int(marker.group(1))] = (marker.start(), end)
    (first_start, first_end), (second_start, second_end) = spans[first], spans[second]
    assert first_start < second_start
    return (
        text[:first_start]
        + text[second_start:second_end]
        + text[first_end:second_start]
        + text[first_start:first_end]
        + text[second_end:]
    )


@pytest.mark.parametrize("pair", [(5, 6), (9, 10)], ids=["orders-5-and-6", "orders-9-and-10"])
def test_swapping_two_evaluation_orders_turns_the_checks_red(pair: tuple[int, int]) -> None:
    """Two orders exchanged, each marker keeping its own body, must not pass."""
    text = CLASSIFIER.read_text(encoding="utf-8")
    assert check_classifier(text) == [], "the classifier is already non-compliant"

    swapped = _swap_order_blocks(text, *pair)

    assert swapped != text
    assert sorted(_classifier_sequence(swapped)) == sorted(CLASSIFIER_ORDERS), (
        "the mutation must keep every order present — only their order changes"
    )
    violations = check_classifier(swapped)

    assert violations, f"swapping orders {pair[0]} and {pair[1]} passed the checks"
    assert any("precedence" in violation for violation in violations), violations
