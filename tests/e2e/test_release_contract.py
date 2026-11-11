"""Release contract (SPEC-110 §2.4, §2.5, §2.7, §2.9): the pipeline as the spec states it.

Nothing here runs the engine and nothing touches the network: every assertion is a read of the
files the component governs — `.releaserc.json`, `.github/workflows/release.yml`,
`.github/signers/allowed_signers`, `pyproject.toml` and `package.json`. What it protects is the
contract itself: the bump table, the plugin order, the ten steps in order, the two secrets, the
signing key bound to `user.signingkey` as the *private* half, and one error code per run in the
precedence of §2.4.2. A workflow that drifts from the spec fails here instead of at release time,
on `main`, with a tag already pushed.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASERC = REPO_ROOT / ".releaserc.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
SIGNERS = REPO_ROOT / ".github" / "signers" / "allowed_signers"
PACKAGE_JSON = REPO_ROOT / "package.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# §2.5: the lifecycle runs in array order — `verifyConditions` for all plugins first (the
# pre-flight), then `prepare`. `exec` must precede `git` so the `sed` writes the literal before
# it is staged.
PLUGIN_ORDER = [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/changelog",
    "@semantic-release/exec",
    "@semantic-release/git",
    "@semantic-release/github",
]

# §2.5.3 / §2.5.1: the table, row by row and in the order the file declares it. `breaking` is first
# for readability only — the analyzer keeps every match and returns the highest type (§2.5.3).
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

# §2.4.2: the precedence is the table order, and step 9 prints the first match only.
ERROR_PRECEDENCE = [
    "release_token_missing",
    "release_signing_key_missing",
    "release_signing_key_invalid",
    "release_tag_not_signed",
    "release_commit_unsigned",
    "release_version_mismatch",
    "release_forbidden",
    "release_notes_missing",
    "release_tag_push_rejected",
    "release_partial_state",
    "release_engine_aborted",
]

# §2.8 / §2.4 step 2: the pair the lockfile was generated with.
NODE_VERSION = "22.22.3"
RELEASE_LITERAL = re.compile(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)


def _release_config() -> dict[str, Any]:
    return json.loads(RELEASERC.read_text(encoding="utf-8"))


def _plugin_names(config: dict[str, Any]) -> list[str]:
    return [p if isinstance(p, str) else p[0] for p in config["plugins"]]


def _plugin_options(config: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in config["plugins"]:
        if isinstance(entry, list) and entry[0] == name:
            return entry[1]
    raise AssertionError(f"{name} is not configured")


def _workflow_code() -> str:
    """The workflow without its comment lines.

    A comment may *name* something the contract forbids (`npm install`, `npx`) in order to explain
    why it is forbidden, so anything that asserts an absence reads this, not the raw file.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """`on:` arrives as the key `True` under YAML 1.1, where `on` is a boolean."""
    for key in ("on", True):
        if key in workflow:
            return workflow[key]
    raise AssertionError("release.yml declares no triggers")


def _steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["release"]["steps"]


def _step(index: int) -> dict[str, Any]:
    return _steps()[index]


def _step_run(index: int) -> str:
    run = _step(index).get("run", "")
    assert isinstance(run, str) and run.strip(), f"step {index + 1} carries no shell script"
    return run


def _job() -> dict[str, Any]:
    return _workflow()["jobs"]["release"]


# --- §2.5: the engine configuration is the contract -------------------------------------------


def test_branches_and_tag_format_are_main_and_three_part_tags() -> None:
    """§2.3.2 / §2.5: `main` only (R5), `v{major}.{minor}.{patch}`, never a partial tag."""
    config = _release_config()

    assert config["branches"] == ["main"]
    assert config["tagFormat"] == "v${version}"


def test_plugin_order_is_analyzer_notes_changelog_exec_git_github() -> None:
    """§2.5: array order is the lifecycle order; `exec` must land before `git`."""
    names = _plugin_names(_release_config())

    assert names == PLUGIN_ORDER, names
    assert names.index("@semantic-release/exec") < names.index("@semantic-release/git")


def test_release_rules_are_the_thirteen_rows_of_the_bump_table() -> None:
    """§2.5.3: total for the eleven types plus the breaking and revert shapes, in file order."""
    rules = _plugin_options(_release_config(), "@semantic-release/commit-analyzer")["releaseRules"]

    assert rules == RELEASE_RULES, rules
    assert len(rules) == 13
    assert rules[0] == {"breaking": True, "release": "major"}


def test_every_patch_type_is_a_patch_and_docs_and_test_release_nothing() -> None:
    """§2.5.3: seven patch types plus the literal `revert:` subject; `docs`/`test` are `false`."""
    rules = _plugin_options(_release_config(), "@semantic-release/commit-analyzer")["releaseRules"]
    by_type = {rule["type"]: rule["release"] for rule in rules if "type" in rule}

    assert by_type == {
        "feat": "minor",
        "fix": "patch",
        "style": "patch",
        "refactor": "patch",
        "perf": "patch",
        "build": "patch",
        "ci": "patch",
        "chore": "patch",
        "revert": "patch",
        "docs": False,
        "test": False,
    }
    assert by_type["docs"] is False and by_type["test"] is False


def test_the_angular_preset_drives_analysis_and_notes() -> None:
    """§2.5: `conventionalcommits` aborted the analysis on this toolchain (§9); `angular` is it."""
    config = _release_config()

    assert _plugin_options(config, "@semantic-release/commit-analyzer")["preset"] == "angular"
    assert (
        _plugin_options(config, "@semantic-release/release-notes-generator")["preset"] == "angular"
    )


def test_the_exec_plugin_carries_the_pre_flight_and_the_literal_writer() -> None:
    """§2.5 / §2.3.1: sign, verify and delete a disposable `preflight-*` tag; then the `sed`."""
    options = _plugin_options(_release_config(), "@semantic-release/exec")

    verify = options["verifyConditionsCmd"]
    assert "git tag" in verify
    assert "git verify-tag" in verify
    assert "git tag -d" in verify
    assert "preflight" in verify
    assert "git push" not in verify, "the pre-flight must never push"

    prepare = options["prepareCmd"]
    assert "nextRelease.version" in prepare
    assert "sed 's/^version = \"" in prepare, "the `sed` must match the literal at column 0"
    assert "pyproject.toml" in prepare


def test_the_git_plugin_commits_exactly_the_two_release_assets() -> None:
    """§2.5: `CHANGELOG.md` and `pyproject.toml`; the message carries the `[skip ci]` token."""
    options = _plugin_options(_release_config(), "@semantic-release/git")

    assert options["assets"] == ["CHANGELOG.md", "pyproject.toml"]
    assert options["message"].startswith("chore(release): ${nextRelease.version} [skip ci]")
    assert "${nextRelease.notes}" in options["message"], "the body is the recovery source (R13)"


def test_the_github_plugin_disables_the_three_comment_and_label_options() -> None:
    """§2.5 / REQ-S110-03: no `issues` or `pull-requests` call is ever made."""
    options = _plugin_options(_release_config(), "@semantic-release/github")

    assert options["successComment"] is False
    assert options["failComment"] is False
    assert options["releasedLabels"] is False


# --- §2.4: triggers, permissions, job-level properties ----------------------------------------


def test_the_workflow_name_and_trigger_set() -> None:
    """§2.4: `Release`, push to `main` and nothing else, plus a `dry_run` dispatch input."""
    workflow = _workflow()
    triggers = _triggers(workflow)

    assert workflow["name"] == "Release"
    assert triggers["push"] == {"branches": ["main"]}
    assert "pull_request" not in triggers, "a PR trigger here is a contract violation (§7.1)"
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_workflow_dispatch_defaults_to_a_dry_run() -> None:
    """§2.4 / §2.4.2: the default dispatch path writes nothing; a dispatch cannot release."""
    inputs = _triggers(_workflow())["workflow_dispatch"]["inputs"]

    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["dry_run"]["default"] is True
    assert inputs["dry_run"]["description"] == (
        "Compute and print the release plan; write nothing."
    )


def test_permissions_concurrency_timeout_and_the_skip_ci_condition() -> None:
    """§2.4: read at workflow level, write on the job, one never-cancelled release at a time."""
    workflow = _workflow()
    job = _job()

    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "write"}
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 20
    assert workflow["concurrency"] == {"group": "release", "cancel-in-progress": False}
    assert "contains(github.event.head_commit.message, '[skip ci]')" in job["if"]


def test_both_secrets_are_declared_once_at_workflow_level() -> None:
    """§2.4 / §2.7: the PAT is the engine's `GITHUB_TOKEN`; the key is the signing secret."""
    env = _workflow()["env"]

    assert env["GITHUB_TOKEN"] == "${{ secrets.HIFA_RELEASE_TOKEN }}"
    assert env["HIFA_RELEASE_TOKEN"] == "${{ secrets.HIFA_RELEASE_TOKEN }}"
    assert env["HIFA_RELEASE_SIGNING_KEY"] == "${{ secrets.HIFA_RELEASE_SIGNING_KEY }}"


# --- §2.4: the ten steps, in order ------------------------------------------------------------


def test_the_job_has_exactly_ten_steps() -> None:
    """§2.4: ten steps; a step may be renamed, the effects may not be reordered or dropped."""
    assert len(_steps()) == 10


def test_step_1_checkout_fetches_the_full_history_without_persisting_credentials() -> None:
    """§2.4 step 1: `LAST_TAG..HEAD` needs the history; the PAT may not reach `.git/config`."""
    step = _step(0)

    assert step["uses"] == "actions/checkout@v4"
    assert step["with"]["fetch-depth"] == 0
    assert step["with"]["persist-credentials"] is False


def test_step_2_pins_the_node_version_the_lockfile_was_generated_with() -> None:
    """§2.4 step 2 / §2.8: another Node major is another dependency tree."""
    step = _step(1)

    assert step["uses"] == "actions/setup-node@v4"
    assert step["with"]["node-version"] == NODE_VERSION
    assert step["with"]["cache"] == "npm"
    assert step["with"]["cache-dependency-path"] == "package-lock.json"


def test_step_3_fails_closed_when_the_token_is_missing() -> None:
    """§2.4 step 3 / §2.4.2 row 1: one code, `exit 1`, before any write."""
    run = _step_run(2)

    assert "release_token_missing" in run
    assert "exit 1" in run
    assert "-z" in run, "the gate must test for an empty value, not for a set variable"


def test_step_4_installs_with_npm_ci_from_the_versioned_lockfile() -> None:
    """§2.4 step 4 / REQ-N110-05: `npm ci`, never `npm install`, never an inline manifest."""
    run = _step_run(3)

    assert "npm ci" in run
    assert "node_modules/.package-lock.json" in run
    assert "git diff --exit-code" in run
    assert "npm install" not in _workflow_code()


def test_step_5_splits_the_dry_run_on_the_dispatch_input() -> None:
    """§2.4 step 5: `inputs.dry_run` true ⇒ `--dry-run`, otherwise no arguments."""
    run = _step_run(4)

    assert "inputs.dry_run" in run
    assert "--dry-run" in run
    assert "SEMANTIC_RELEASE_ARGS" in run
    assert "GITHUB_ENV" in run


def test_step_6_materialises_the_private_key_and_binds_it_to_user_signingkey() -> None:
    """§2.4.1: the key in `$RUNNER_TEMP` at 0600, the PRIVATE half as `user.signingkey`."""
    run = _step_run(5)

    assert "release_signing_key_missing" in run, "§2.4.2 row 2 is produced here"
    assert 'printf \'%s\\n\' "$HIFA_RELEASE_SIGNING_KEY" > "$RUNNER_TEMP/hifa_signing_key"' in run
    assert 'chmod 600 "$RUNNER_TEMP/hifa_signing_key"' in run
    assert 'ssh-keygen -y -f "$RUNNER_TEMP/hifa_signing_key"' in run
    assert 'git config user.signingkey "$RUNNER_TEMP/hifa_signing_key"' in run
    assert 'git config user.signingkey "$RUNNER_TEMP/hifa_signing_key.pub"' not in run, (
        "binding the .pub is what makes `git tag` fail with 'Could not read SSH key'"
    )
    assert "git config gpg.format ssh" in run
    assert "git config gpg.ssh.allowedSignersFile .github/signers/allowed_signers" in run
    assert "git config commit.gpgSign true" in run
    assert "git config tag.gpgSign true" in run
    assert "GIT_EDITOR=" in run and ".github/release/tag-message.sh" in run


def test_step_7_keeps_the_push_credential_in_the_environment() -> None:
    """§2.4.1: `GIT_CONFIG_*` carries the header; `.git/config` never sees it."""
    run = _step_run(6)

    assert "GIT_CONFIG_COUNT=1" in run
    assert "GIT_CONFIG_KEY_0=http.extraheader" in run
    assert "GIT_CONFIG_VALUE_0=$header" in run
    assert "AUTHORIZATION: basic" in run
    assert "base64" in run
    assert "x-access-token:" in run
    assert "grep -c extraheader .git/config" in run, "step 1 and step 7 share this observable"
    assert "set +x" in run


def test_step_8_runs_the_pinned_binary_and_captures_its_log() -> None:
    """§2.4 step 8 / §7.2: `./node_modules/.bin/semantic-release`, never a floating `npx`."""
    run = _step_run(7)

    assert "./node_modules/.bin/semantic-release" in run
    assert "SEMANTIC_RELEASE_ARGS" in run
    assert "semantic-release.log" in run, "step 9 separates two states by reading this log"
    assert "release_signing_key_invalid" in run, "§2.4.2 row 3 is produced here"


def test_step_9_is_the_always_running_outcome_assertion_in_precedence_order() -> None:
    """§2.4.2: `if: always()`, exactly one code, first match in the table order."""
    step = _step(8)
    run = _step_run(8)

    assert step["if"] == "always()"
    positions = []
    for code in ERROR_PRECEDENCE:
        assert code in run, f"step 9 never emits {code}"
        positions.append(run.index(code))
    assert positions == sorted(positions), "the codes are not in the §2.4.2 precedence order"
    assert "green: nothing written" in run, "G1"
    assert "green: release" in run, "G2"
    assert "no relevant changes" in run, "G3"


def test_step_10_cleans_up_unconditionally() -> None:
    """§2.4 step 10 / REQ-S110-01: key, `.pub`, `preflight-*` refs and the agent, red or green."""
    step = _step(9)
    run = _step_run(9)

    assert step["if"] == "always()"
    assert 'rm -f "$RUNNER_TEMP/hifa_signing_key" "$RUNNER_TEMP/hifa_signing_key.pub"' in run
    assert "preflight-*" in run
    assert "git tag -d" in run
    assert "ssh-agent -k" in run


# --- §2.7: secret hygiene ---------------------------------------------------------------------


def test_the_workflow_never_traces_or_prints_a_secret() -> None:
    """REQ-S110-01: no `set -x`, no `npx`, no persisted credential, no token on the remote URL."""
    code = _workflow_code()

    assert "set -x" not in code
    assert "npx" not in code
    assert "persist-credentials: true" not in code
    assert "git push -f" not in code
    assert 'echo "$HIFA_RELEASE_TOKEN"' not in code
    assert 'echo "$HIFA_RELEASE_SIGNING_KEY"' not in code
    assert 'cat "$RUNNER_TEMP/hifa_signing_key"' not in code


def test_allowed_signers_carries_the_release_principal_with_the_git_namespace() -> None:
    """§2.7: one allowlist, two checks — `hifa-release` verifies commits and tags."""
    lines = [line for line in SIGNERS.read_text(encoding="utf-8").splitlines() if line.strip()]
    principals = [line.split()[0] for line in lines]

    assert "ginkelte" in principals, "the human signing key must stay in the allowlist"
    assert principals.count("hifa-release") == 1
    release = next(line for line in lines if line.startswith("hifa-release "))
    assert 'namespaces="git"' in release, "without the namespace git will not verify with it"
    parts = release.split()
    assert parts[1] == 'namespaces="git"'
    assert parts[2] == "ssh-ed25519"
    assert re.fullmatch(r"AAAA[0-9A-Za-z+/]+[=]{0,2}", parts[3]), parts[3]


# --- §2.1 / §2.9: the two files the pipeline reads and rewrites ---------------------------------


def test_build_system_is_hatchling_only_and_the_version_is_a_literal() -> None:
    """§2.1 / §7.2: `requires` compared as a **list**, and a literal `version = "X.Y.Z"`."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    text = PYPROJECT.read_text(encoding="utf-8")

    assert data["build-system"] == {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }, data["build-system"]
    assert "dynamic" not in data["project"], '`dynamic = ["version"]` is back'
    assert RELEASE_LITERAL.search(text) is not None
    assert len(RELEASE_LITERAL.findall(text)) == 1, "exactly one literal may exist (§2.1)"


def test_package_json_stays_private_with_npms_zero_placeholder() -> None:
    """§2.1: `package.json` is not a version carrier; neither npm nor the pipeline bumps it."""
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert package["private"] is True
    assert package["version"] == "0.0.0"


# --- R15: AGENTS.md §9 and `releaseRules` are the same contract -------------------------------
#
# P8 says a rule that is not executable is not a rule. R15 promises the documented commit types and
# the engine's bump table never diverge; this is that promise as a test. The reference project this
# pipeline was modelled on documents `chore:`/`ci:`/`style:` as "no release" while its config gives
# them `patch`, so the docs and the released versions disagreed. Comparing the two structures is the
# only thing that prevents a repeat.

_AGENTS = REPO_ROOT / "AGENTS.md"
_TYPES = (
    "feat",
    "fix",
    "docs",
    "test",
    "refactor",
    "chore",
    "style",
    "perf",
    "build",
    "ci",
    "revert",
)


def _agents_section_9() -> str:
    text = _AGENTS.read_text(encoding="utf-8")
    start = text.index("## 9.")
    end = text.index("## 10.")
    return text[start:end]


def _bumps_from_agents() -> dict[str, str]:
    """Read the §9 table: `| types | version |` rows, plus the breaking-change row."""
    bumps: dict[str, str] = {}
    for line in _agents_section_9().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("|- "):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 2:
            continue
        left, right = cells
        bump = "none" if "none" in right else right.strip("*").strip()
        if "BREAKING CHANGE" in left:
            bumps["breaking"] = bump
            continue
        for token in re.findall(r"`([a-z]+)`", left):
            if token in _TYPES:
                bumps[token] = bump
    return bumps


def _bumps_from_releaserc() -> dict[str, str]:
    rules = _plugin_options(_release_config(), "@semantic-release/commit-analyzer")["releaseRules"]
    bumps: dict[str, str] = {}
    for rule in rules:
        release = rule.get("release")
        bump = "none" if release is False else str(release)
        if rule.get("breaking") is True:
            bumps["breaking"] = bump
        elif "type" in rule:
            bumps[rule["type"]] = bump
    return bumps


def test_agents_section_9_matches_the_release_rules() -> None:
    """R15: the documented table and the engine's table are the same mapping."""
    documented = _bumps_from_agents()
    engine = _bumps_from_releaserc()

    assert documented, "AGENTS.md §9 has no commit table to compare"
    assert set(documented) == set(engine), (
        f"AGENTS.md §9 and .releaserc.json disagree on which types exist: "
        f"only documented={sorted(set(documented) - set(engine))}, "
        f"only in the engine={sorted(set(engine) - set(documented))}"
    )
    differing = {t: (documented[t], engine[t]) for t in documented if documented[t] != engine[t]}
    assert not differing, f"AGENTS.md §9 and .releaserc.json disagree on the bump: {differing}"


def test_agents_documents_the_breaking_footer_not_the_bang() -> None:
    """§2.5: the preset drops a `feat!:` header, so the footer is the only breaking marker."""
    section = _agents_section_9()
    assert "BREAKING CHANGE" in section
    assert "feat!:" in section
