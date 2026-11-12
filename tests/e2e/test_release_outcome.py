"""SPEC-110 §2.4.2 / §2.9 — the outcome classifier is executed, not read.

`assert-outcome.sh` is the only step that classifies a release run, and it runs on `main` with a
tag possibly already published. This module therefore *runs* it, once per evaluation order of the
§2.4.2 table and once more per order in which it must not fire, on a throwaway git repository with
a throwaway signing key:

* the fixture logs under `tests/fixtures/release/` are synthetic — the shape of the engine's
  output, not a measured run of this repository;
* the repository state is what separates the orders that share a log (`release_tag_not_signed`
  vs `release_commit_unsigned` vs `green: released`), so each case builds the state it needs;
* every case asserts **exactly one line** on stdout, the exact line, and the exit status: `0` only
  for the three greens, `1` for every code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "release" / "assert-outcome.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "release"

# The three greens are literals, so a fixture asserts equality instead of a prefix.
GREEN_NOTHING_WRITTEN = "green: nothing written"
GREEN_NO_CHANGES = "green: no relevant changes"
GREEN_RELEASED = "green: released v0.2.0"
GREENS = (GREEN_NOTHING_WRITTEN, GREEN_NO_CHANGES, GREEN_RELEASED)

RELEASE_SUBJECT = "chore(release): 0.2.0 [skip ci]"
BASE_LITERAL = "0.1.0"

# The order of the table: `release_credential_persisted` is catalogue id 12 and is
# evaluated first; ids 3-10 sit at `order = id - 1`; id 11 is the residual at order 13.
ORDERS = range(1, 14)


def _run(
    argv: list[str], cwd: Path, *, env: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(argv)} failed ({proc.returncode}):\n{proc.stderr}")
    return proc


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", *args], cwd).stdout.strip()


@dataclass(frozen=True)
class Repo:
    """The repository state one case needs. The defaults are a completed, fully valid release."""

    literal: str = "0.2.0"
    head: str = (
        "release"  # "release" -> a `chore(release): … [skip ci]` commit, "docs" -> a docs one
    )
    tag: str | None = "v0.2.0"  # the tag this run created, per the log line `Created tag <tag>`
    tag_signed: bool = True
    tag_pushed: bool = True
    tag_on_previous: bool = False  # the tag names the pre-release commit, whose literal differs
    commit_signed: bool = True
    changelog: str | None = "0.2.0"  # the version the CHANGELOG header at the tag names


RELEASED = Repo()
NOTHING = Repo(literal=BASE_LITERAL, head="docs", tag=None)
LIGHTWEIGHT = Repo(tag_signed=False)
UNSIGNED_BOTH = Repo(tag_signed=False, commit_signed=False)
UNSIGNED_COMMIT = Repo(commit_signed=False)
TAG_ON_OLD = Repo(tag_on_previous=True)
NO_CHANGELOG = Repo(changelog=None)
LOCAL_TAG_ONLY = Repo(tag_pushed=False)
RELEASE_NO_TAG = Repo(tag=None)
# Two failures at once, for the precedence fixtures below.
TAG_ON_OLD_NO_NOTES = Repo(tag_on_previous=True, changelog=None)
LOCAL_TAG_NO_NOTES = Repo(tag_pushed=False, changelog=None)


@dataclass(frozen=True)
class Case:
    """One classifier run: the repository, the log, the environment, the one line it prints."""

    order: int
    title: str
    repo: Repo
    fixture: str
    expect: str
    status: str = "0"
    dry_run: str = "false"
    baseline: bool = False
    github_env: str = ""
    github_env_file: str = ""  # a fixture under tests/fixtures/release/, copied verbatim
    extraheader: bool = False
    marker: bool = False
    unset_exit_code: bool = False
    move_head: bool = False
    dirty: bool = False
    forbidden: str | None = None  # set on a negative: the line this state must NOT produce


def _write_project(work: Path, literal: str, changelog: str | None) -> None:
    (work / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        f'[project]\nname = "hifa"\nversion = "{literal}"\n',
        encoding="utf-8",
    )
    if changelog is None:
        (work / "CHANGELOG.md").write_text(
            "# Changelog\n\nA hand-written header.\n", encoding="utf-8"
        )
        return
    (work / "CHANGELOG.md").write_text(
        f"# [{changelog}](https://github.com/ginkelte/hifa/compare/v0.1.0...v{changelog}) "
        "(2026-11-11)\n\n### Features\n\n* a feature\n",
        encoding="utf-8",
    )


def _build(root: Path, spec: Repo) -> Path:
    """A throwaway clone with a bare `origin` and a throwaway signing key, in `$TMPDIR`."""
    (root / "home").mkdir()
    work = root / "work"
    remote = root / "remote.git"
    _run(["git", "init", "--bare", "-q", str(remote)], root)
    _run(["git", "init", "-q", "-b", "main", str(work)], root)

    key = root / "signing_key"
    _run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(key), "-N", "", "-C", "hifa-release"],
        root,
    )
    public = (root / "signing_key.pub").read_text(encoding="utf-8").strip()
    signers = root / "allowed_signers"
    signers.write_text(f'hifa-release namespaces="git" {public}\n', encoding="utf-8")

    def git(*args: str) -> str:
        return _git(work, *args)

    git("config", "user.name", "hifa release")
    git("config", "user.email", "hifa-release@users.noreply.github.com")
    git("config", "gpg.format", "ssh")
    git("config", "user.signingkey", str(key))
    git("config", "gpg.ssh.allowedSignersFile", str(signers))
    git("config", "commit.gpgSign", "true" if spec.commit_signed else "false")
    git("config", "tag.gpgSign", "true")
    git("remote", "add", "origin", str(remote))

    _write_project(work, BASE_LITERAL, BASE_LITERAL)
    git("add", "-A")
    git("commit", "-qm", "hifa: local issue tracker")
    git("push", "-q", "-u", "origin", "main")
    git("tag", "-a", "-m", "v0.1.0", "v0.1.0")
    git("push", "-q", "origin", "refs/tags/v0.1.0")

    if spec.head == "release":
        _write_project(work, spec.literal, spec.changelog)
        git("add", "-A")
        git("commit", "-qm", RELEASE_SUBJECT)
    else:
        (work / "README.md").write_text("hifa\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "docs: fix a typo in the README")
    git("push", "-q", "origin", "main")

    if spec.tag is not None:
        target = "HEAD~1" if spec.tag_on_previous else "HEAD"
        if spec.tag_signed:
            git("tag", "-a", "-m", spec.tag, spec.tag, target)
        else:
            git("-c", "tag.gpgSign=false", "tag", spec.tag, target)
        if spec.tag_pushed:
            git("push", "-q", "origin", f"refs/tags/{spec.tag}")
    return work


def _baseline(work: Path, root: Path) -> dict[str, str]:
    """What step 4 captures before the engine runs: HEAD, `origin/main` and the tag list."""
    tags = _run(["git", "ls-remote", "--tags", "origin"], work).stdout
    tags_file = root / "tags_before.txt"
    tags_file.write_text("".join(sorted(tags.splitlines(keepends=True))), encoding="utf-8")
    main = _run(["git", "ls-remote", "origin", "refs/heads/main"], work).stdout.split()[0]
    return {
        "PRE_RUN_HEAD": _git(work, "rev-parse", "HEAD"),
        "PRE_RUN_MAIN": main,
        "PRE_RUN_TAGS": str(tags_file),
    }


def _classify(root: Path, case: Case) -> tuple[str, int, str]:
    """Run the classifier as step 9 runs it, and return `(line, exit status, stderr)`."""
    work = _build(root, case.repo)
    log = root / case.fixture
    shutil.copyfile(FIXTURES / case.fixture, log)

    marker = root / "release_code_emitted"
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(root / "home"),
        "RELEASE_LOG": str(log),
        "RELEASE_CODE_MARKER": str(marker),
        "RELEASE_DRY_RUN": case.dry_run,
    }
    if not case.unset_exit_code:
        env["RELEASE_EXIT_CODE"] = case.status
    if case.baseline:
        env.update(_baseline(work, root))
    if case.github_env or case.github_env_file:
        github_env = root / "github_env"
        if case.github_env_file:
            shutil.copyfile(FIXTURES / case.github_env_file, github_env)
        else:
            github_env.write_text(case.github_env, encoding="utf-8")
        env["GITHUB_ENV"] = str(github_env)
    if case.extraheader:
        _git(work, "config", "http.extraheader", "AUTHORIZATION: basic ZXhhbXBsZQ==")
    if case.marker:
        marker.write_text("1\n", encoding="utf-8")
    if case.move_head:
        (work / "moved.txt").write_text("x\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", "feat: move HEAD under the dry run")
    if case.dirty:
        (work / "untracked.txt").write_text("x\n", encoding="utf-8")

    proc = _run([str(SCRIPT)], work, env=env, check=False)
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, (
        f"the classifier must print exactly one classification line, got {lines}\n{proc.stderr}"
    )
    return lines[0], proc.returncode, proc.stderr


# --- one positive per evaluation order -----------------------------------------------

POSITIVE_CASES = [
    Case(
        1,
        "credential-persisted-in-github-env",
        RELEASED,
        "order01_credential_persisted.log",
        "release_credential_persisted",
        github_env="HIFA_RELEASE_TOKEN=ghp_example\n",
    ),
    Case(
        1,
        "credential-persisted-in-git-config",
        RELEASED,
        "order01_credential_persisted.log",
        "release_credential_persisted",
        extraheader=True,
    ),
    # The credential by *value*: the key names nothing, the payload is the leak. Both shapes are
    # what step 8 builds (`base64("x-access-token:<PAT>")`) or what a careless `echo >> $GITHUB_ENV`
    # leaves behind, and neither carries a string the name-based half of order 1 can see.
    Case(
        1,
        "credential-by-value-base64-in-a-neutral-key",
        RELEASED,
        "order01_credential_persisted.log",
        "release_credential_persisted",
        github_env_file="order01_env_base64_credential.env",
    ),
    Case(
        1,
        "credential-by-value-raw-pat-in-a-neutral-key",
        RELEASED,
        "order01_credential_persisted.log",
        "release_credential_persisted",
        github_env_file="order01_env_raw_pat.env",
    ),
    Case(
        2,
        "nothing-written",
        NOTHING,
        "order02_nothing_written.log",
        GREEN_NOTHING_WRITTEN,
        dry_run="true",
        baseline=True,
    ),
    Case(
        3,
        "no-relevant-changes",
        NOTHING,
        "order03_no_relevant_changes.log",
        GREEN_NO_CHANGES,
        baseline=True,
    ),
    # The scheduled run: the checkout starts on the release commit the previous run wrote, the tag
    # is on the remote, the engine answers `no relevant changes` and creates nothing. The delta is
    # empty, so this is G3 — the state at HEAD is the normal state of every six-hour tick.
    Case(
        3,
        "no-relevant-changes-on-the-previous-release-commit",
        RELEASED,
        "order03_no_relevant_changes.log",
        GREEN_NO_CHANGES,
        baseline=True,
    ),
    Case(
        4,
        "signing-key-invalid",
        NOTHING,
        "order04_signing_key_invalid.log",
        "release_signing_key_invalid",
        status="1",
    ),
    Case(5, "tag-not-signed", LIGHTWEIGHT, "order05_tag_not_signed.log", "release_tag_not_signed"),
    Case(
        6,
        "commit-unsigned",
        UNSIGNED_COMMIT,
        "order06_commit_unsigned.log",
        "release_commit_unsigned",
    ),
    Case(
        7,
        "version-mismatch-at-the-tag",
        TAG_ON_OLD,
        "order07_version_mismatch.log",
        "release_version_mismatch",
    ),
    Case(
        7,
        "version-mismatch-the-guard-fired",
        NOTHING,
        "order07_guard_refused.log",
        "release_version_mismatch",
        status="1",
    ),
    Case(8, "forbidden", RELEASED, "order08_forbidden.log", "release_forbidden", status="1"),
    Case(
        9,
        "notes-missing",
        NO_CHANGELOG,
        "order09_notes_missing.log",
        "release_notes_missing",
    ),
    Case(
        10,
        "tag-push-rejected",
        LOCAL_TAG_ONLY,
        "order10_tag_push_rejected.log",
        "release_tag_push_rejected",
        status="1",
    ),
    # The other refused-push shape: the log says the tag was created and published, the refusal
    # names `refs/notes/semantic-release`, and `ls-remote` lists the tag. The release is public and
    # only the notes ref is missing — its own code, never the tag-push one.
    Case(
        10,
        "notes-push-rejected",
        RELEASED,
        "order10_notes_push_rejected.log",
        "release_notes_push_rejected",
        status="1",
    ),
    # The engine exited zero: nothing was refused, and the words in the log are a commit message the
    # analyzer echoed back. A refusal is a line git writes, never a sentence someone typed.
    Case(
        10,
        "prose-is-not-a-refusal",
        RELEASED,
        "order10_prose_is_not_a_refusal.log",
        GREEN_RELEASED,
    ),
    Case(
        11,
        "partial-state",
        LOCAL_TAG_ONLY,
        "order11_partial_state.log",
        "release_partial_state",
        status="1",
    ),
    Case(12, "released", RELEASED, "order12_released.log", GREEN_RELEASED),
    Case(
        13,
        "engine-aborted",
        NOTHING,
        "order13_engine_aborted.log",
        "release_engine_aborted",
        status="1",
    ),
    # --- two signals competing for the same run: the earlier order owns the line -----------------
    # A fixture per order proves each order fires; these prove *which* one fires when two of them
    # are true at once, i.e. that the table is a precedence and not a set of independent branches.
    Case(
        5,
        "two-signals-an-unsigned-tag-beats-an-unsigned-commit",
        UNSIGNED_BOTH,
        "order05_tag_not_signed.log",
        "release_tag_not_signed",
    ),
    Case(
        5,
        "two-signals-an-unsigned-tag-over-a-signed-commit",
        LIGHTWEIGHT,
        "order05_tag_not_signed.log",
        "release_tag_not_signed",
    ),
    Case(
        7,
        "two-signals-a-version-mismatch-beats-missing-notes",
        TAG_ON_OLD_NO_NOTES,
        "order07_version_mismatch.log",
        "release_version_mismatch",
    ),
    Case(
        8,
        "two-signals-a-forbidden-api-beats-missing-notes",
        NO_CHANGELOG,
        "order08_forbidden.log",
        "release_forbidden",
        status="1",
    ),
    Case(
        10,
        "two-signals-a-rejected-tag-push-beats-missing-notes",
        LOCAL_TAG_NO_NOTES,
        "order10_tag_push_rejected.log",
        "release_tag_push_rejected",
        status="1",
    ),
]

# --- at least one negative per evaluation order: the precedence does not cross -----------------

NEGATIVE_CASES = [
    Case(
        1,
        "a-clean-environment-is-not-a-leak",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_credential_persisted",
    ),
    Case(
        1,
        "a-paths-only-environment-is-not-a-leak",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        github_env_file="order01_env_paths_only.env",
        forbidden="release_credential_persisted",
    ),
    Case(
        2,
        "a-non-zero-exit-is-not-a-green",
        NOTHING,
        "order02_nothing_written.log",
        "release_engine_aborted",
        status="2",
        dry_run="true",
        baseline=True,
        forbidden=GREEN_NOTHING_WRITTEN,
    ),
    Case(
        2,
        "no-positive-signal-in-the-log",
        NOTHING,
        "order13_engine_aborted.log",
        "release_engine_aborted",
        dry_run="true",
        baseline=True,
        forbidden=GREEN_NOTHING_WRITTEN,
    ),
    Case(
        2,
        "head-moved-under-the-dry-run",
        NOTHING,
        "order02_nothing_written.log",
        "release_engine_aborted",
        dry_run="true",
        baseline=True,
        move_head=True,
        forbidden=GREEN_NOTHING_WRITTEN,
    ),
    Case(
        2,
        "a-dirty-tree-is-not-nothing-written",
        NOTHING,
        "order02_nothing_written.log",
        "release_engine_aborted",
        dry_run="true",
        baseline=True,
        dirty=True,
        forbidden=GREEN_NOTHING_WRITTEN,
    ),
    # A run that moved HEAD created something: it cannot answer "nothing to release", whatever the
    # log says. The state it leaves is order 11's.
    Case(
        3,
        "head-moved-during-the-run",
        RELEASE_NO_TAG,
        "order03_no_relevant_changes.log",
        "release_partial_state",
        baseline=True,
        move_head=True,
        forbidden=GREEN_NO_CHANGES,
    ),
    # No baseline, no delta: the answer cannot be told from the state, so this is not a green even
    # though HEAD is a release commit and the log says there was nothing to release.
    Case(
        3,
        "a-run-without-the-baseline-cannot-claim-g3",
        RELEASE_NO_TAG,
        "order03_no_relevant_changes.log",
        "release_partial_state",
        forbidden=GREEN_NO_CHANGES,
    ),
    Case(
        4,
        "the-pre-flight-confirmed-a-signature",
        NOTHING,
        "order02_dry_run_aborted.log",
        "release_engine_aborted",
        status="1",
        forbidden="release_signing_key_invalid",
    ),
    Case(
        5,
        "an-annotated-signed-tag",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_tag_not_signed",
    ),
    Case(
        6,
        "a-signed-release-commit",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_commit_unsigned",
    ),
    Case(
        7,
        "the-literal-equals-the-tag",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_version_mismatch",
    ),
    Case(
        8,
        "no-403-in-the-log",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_forbidden",
    ),
    Case(
        9,
        "the-changelog-names-the-version",
        RELEASED,
        "order12_released.log",
        GREEN_RELEASED,
        forbidden="release_notes_missing",
    ),
    Case(
        10,
        "no-rejection-message-in-the-log",
        LOCAL_TAG_ONLY,
        "order11_partial_state.log",
        "release_partial_state",
        status="1",
        forbidden="release_tag_push_rejected",
    ),
    # The message alone is not the state: here the *commit*'s push failed (no `Created tag` line),
    # so the tag does not exist locally and `origin/main` carries no release commit. §5.5 does not
    # apply and the run must not be sent there — it is an aborted engine.
    Case(
        10,
        "a-rejected-push-message-without-the-state-it-names",
        NOTHING,
        "order10_commit_push_failed.log",
        "release_engine_aborted",
        status="1",
        forbidden="release_tag_push_rejected",
    ),
    # The verification, isolated: the same tag-refusal message, but `ls-remote` lists the
    # tag push did not fail. No tag-push code may be emitted on the log line alone.
    Case(
        10,
        "a-refused-push-whose-tag-is-on-the-remote",
        RELEASED,
        "order10_tag_push_rejected.log",
        "release_engine_aborted",
        status="1",
        forbidden="release_tag_push_rejected",
    ),
    # A notes refusal is not a tag refusal either, in the same direction as ^.
    Case(
        10,
        "a-notes-refusal-is-not-a-tag-refusal",
        RELEASED,
        "order10_notes_push_rejected.log",
        "release_notes_push_rejected",
        status="1",
        forbidden="release_tag_push_rejected",
    ),
    Case(
        11,
        "main-does-not-carry-a-release-commit",
        NOTHING,
        "order13_engine_aborted.log",
        "release_engine_aborted",
        status="1",
        forbidden="release_partial_state",
    ),
    Case(
        12,
        "a-non-zero-exit-is-never-released",
        RELEASED,
        "order12_released.log",
        "release_engine_aborted",
        status="1",
        forbidden=GREEN_RELEASED,
    ),
    Case(
        13,
        "a-green-is-provable",
        NOTHING,
        "order03_no_relevant_changes.log",
        GREEN_NO_CHANGES,
        baseline=True,
        forbidden="release_engine_aborted",
    ),
]


def _case_id(case: Case) -> str:
    return f"order{case.order:02d}-{case.title}"


def _expected_exit(line: str) -> int:
    return 0 if line in GREENS else 1


@pytest.mark.parametrize("case", POSITIVE_CASES, ids=[_case_id(c) for c in POSITIVE_CASES])
def test_each_evaluation_order_fires_on_its_fixture(case: Case, tmp_path: Path) -> None:
    """§2.4.2: the first match wins, its line is exact, and `0` is reserved for the greens."""
    line, status, stderr = _classify(tmp_path, case)

    assert line == case.expect, f"{case.title}: {line}\n{stderr}"
    assert status == _expected_exit(case.expect), f"{case.title}: exit {status} for {line!r}"


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=[_case_id(c) for c in NEGATIVE_CASES])
def test_each_evaluation_order_stays_silent_when_it_must_not_fire(
    case: Case, tmp_path: Path
) -> None:
    """R20: the precedence is asserted, not just the branches — one negative per order."""
    line, status, stderr = _classify(tmp_path, case)

    assert case.forbidden is not None
    assert line != case.forbidden, f"{case.title}: {case.forbidden} fired on a state it must not"
    assert line == case.expect, f"{case.title}: {line}\n{stderr}"
    assert status == _expected_exit(case.expect)


def test_every_evaluation_order_has_a_positive_and_a_negative() -> None:
    """The coverage criterion, asserted instead of described."""
    positives = {case.order for case in POSITIVE_CASES}
    negatives = {case.order for case in NEGATIVE_CASES}

    assert positives == set(ORDERS), f"orders without a fixture: {sorted(set(ORDERS) - positives)}"
    assert negatives == set(ORDERS), f"orders without a negative: {sorted(set(ORDERS) - negatives)}"


# --- the interface itself ------------------------------------------------------------


def test_the_script_is_executable_and_uses_bash(tmp_path: Path) -> None:
    """§2.9: executable, `bash`, and `bash -n` clean — step 9 invokes it as a command."""
    mode = SCRIPT.stat().st_mode
    assert mode & 0o111, "assert-outcome.sh must be executable (0755)"
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")
    _run(["bash", "-n", str(SCRIPT)], tmp_path)


def test_without_a_log_the_run_is_never_green(tmp_path: Path) -> None:
    """§2.4.2: a missing or empty log can never produce a green — and the script takes no args."""
    work = _build(tmp_path, NOTHING)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "RELEASE_CODE_MARKER": str(tmp_path / "release_code_emitted"),
        "RELEASE_EXIT_CODE": "0",
    }
    proc = _run([str(SCRIPT)], work, env=env, check=False)

    assert proc.stdout.splitlines() == ["release_engine_aborted"]
    assert proc.returncode == 1


def test_an_argument_is_refused_instead_of_classified(tmp_path: Path) -> None:
    """§2.4.2: the script takes no arguments — `--help` is a usage it does not have, not a green.

    Measured before this was fixed: `assert-outcome.sh --help` on the state of a completed release
    printed `green: released v0.2.0` and exited `0`, i.e. it failed open in the one place the
    pipeline is meant to fail closed. Every input is an environment variable plus the working
    directory, so an argument is a usage error: no classification line, exit `1`.
    """
    work = _build(tmp_path, RELEASED)
    log = tmp_path / "order12_released.log"
    shutil.copyfile(FIXTURES / "order12_released.log", log)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "RELEASE_LOG": str(log),
        "RELEASE_CODE_MARKER": str(tmp_path / "release_code_emitted"),
        "RELEASE_EXIT_CODE": "0",
        "RELEASE_DRY_RUN": "false",
    }
    for argument in ("--help", "v0.2.0", ""):
        proc = _run([str(SCRIPT), argument], work, env=env, check=False)

        assert proc.stdout == "", f"{argument!r} was classified: {proc.stdout!r}"
        assert proc.returncode == 1, f"{argument!r} exited {proc.returncode}"
        assert "no arguments" in proc.stderr


def test_an_empty_log_is_not_a_green_either(tmp_path: Path) -> None:
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    case = Case(13, "empty-log", NOTHING, "order12_released.log", "release_engine_aborted")
    work = _build(tmp_path, case.repo)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "RELEASE_LOG": str(empty),
        "RELEASE_CODE_MARKER": str(tmp_path / "release_code_emitted"),
        "RELEASE_EXIT_CODE": "0",
    }
    proc = _run([str(SCRIPT)], work, env=env, check=False)

    assert proc.stdout.splitlines() == ["release_engine_aborted"]
    assert proc.returncode == 1


def test_an_unset_exit_code_reads_as_the_sentinel_not_as_zero(tmp_path: Path) -> None:
    """§2.4.2 / §8 step 8: `${RELEASE_EXIT_CODE:-0}` makes "never ran" look like "succeeded"."""
    case = Case(
        13,
        "sentinel",
        RELEASED,
        "order12_released.log",
        "release_engine_aborted",
        unset_exit_code=True,
    )
    line, status, stderr = _classify(tmp_path, case)

    assert line == "release_engine_aborted", stderr
    assert status == 1


def test_the_mark_suppresses_the_line_and_keeps_the_run_red(tmp_path: Path) -> None:
    """R19: the gate that printed the code owns the line; the classifier prints nothing at all."""
    case = Case(12, "marked", RELEASED, "order12_released.log", GREEN_RELEASED, marker=True)
    work = _build(tmp_path, case.repo)
    log = tmp_path / case.fixture
    shutil.copyfile(FIXTURES / case.fixture, log)
    marker = tmp_path / "release_code_emitted"
    marker.write_text("1\n", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "RELEASE_LOG": str(log),
        "RELEASE_CODE_MARKER": str(marker),
        "RELEASE_EXIT_CODE": "0",
        "RELEASE_DRY_RUN": "false",
    }
    proc = _run([str(SCRIPT)], work, env=env, check=False)

    assert proc.stdout == "", f"a marked run must print no classification line: {proc.stdout!r}"
    assert proc.returncode == 1, "suppressing the line never turns the run green"
    assert "release_code_emitted" in proc.stderr
