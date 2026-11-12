#!/usr/bin/env bash
# The outcome classifier of step 9.
#
# One run, one line. It reads the repository and the engine log step 8 captured, applies the
# evaluation orders in their precedence, and prints exactly one of:
#
#   green: nothing written        (G1 — a dry run that proved it wrote nothing)
#   green: no relevant changes    (G3 — nothing to release)
#   green: released <tag>         (G2 — the residual green of a completed release)
#   release_<code>                (catalogue ids 3-12)
#
# Exit status: 0 only for the three greens, 1 for every code. Diagnostics go to stderr and never
# carry a secret or the credential.
#
# Interface — environment plus the working directory. No arguments, no stdin:
#
#   RELEASE_LOG          log captured by step 8                        required; empty is never green
#   RELEASE_EXIT_CODE    ${PIPESTATUS[0]}; unset reads as the 255 sentinel, never 0
#   RELEASE_DRY_RUN      true | false, written by step 5
#   PRE_RUN_HEAD         step 4 baseline; must be non-empty to grant G1
#   PRE_RUN_MAIN         step 4 baseline; must be non-empty to grant G1
#   PRE_RUN_TAGS         step 4 tag list; must exist and be non-empty to grant G1
#   RELEASE_CODE_MARKER  dedupe mark; default $RUNNER_TEMP/release_code_emitted
#   TAG                  an accepted alias only: the tag is the `Created tag <tag>` line of the log,
#                        which is its only source. A TAG without that line invents nothing.
#   GITHUB_ENV           read, never written: one of the two places a credential can persist
#
# Catalogue ids 1 and 2 (`release_token_missing`, `release_signing_key_missing`) are not evaluated
# here: each is owned by the gate that printed it, which wrote the mark this script reads.
#
# No network writes, no write to the repository: `git` is used for reads only (cat-file, show, log,
# verify-tag, verify-commit, ls-remote, status).

set -euo pipefail

log=${RELEASE_LOG:-}
marker=${RELEASE_CODE_MARKER:-${RUNNER_TEMP:-/tmp}/release_code_emitted}

note() { printf 'assert-outcome: %s\n' "$1" >&2; }
emit() {
    printf '%s\n' "$1"
    exit 1
}
green() {
    printf '%s\n' "$1"
    exit 0
}

# --- no arguments: an argument is a usage this script does not have -----------------------------
# Every input is an environment variable plus the working directory. Failing open here
# would classify `--help` as a release, in the one place the pipeline is supposed to fail closed.
if [ "$#" -ne 0 ]; then
    note "this classifier takes no arguments (§2.4.2); got $# argument(s): $*"
    exit 1
fi

# --- one line per run: the gate that printed a code owns the line ------------------------------
# A mark suppresses a *line*, never a *failure*: the run stays red, it just does not repeat itself.
if [ -f "$marker" ]; then
    note "the mark $marker is present: a gate already printed the code of this run, printing none"
    exit 1
fi

# --- the log is the positive signal of every green ---------------------------------------------
if [ -z "$log" ] || [ ! -s "$log" ]; then
    note "RELEASE_LOG is unset, missing or empty: no green can be proven without the engine log"
    emit release_engine_aborted
fi

has() { grep -qF -- "$1" "$log"; }
has_re() { grep -qE -- "$1" "$log"; }

# --- RELEASE_EXIT_CODE has no benign default: absence reads as the 255 sentinel ----------------
status=${RELEASE_EXIT_CODE:-255}
case $status in
'' | *[!0-9]*)
    note "RELEASE_EXIT_CODE='$status' is not a number: reading it as the 255 sentinel"
    status=255
    ;;
esac
dry_run=${RELEASE_DRY_RUN:-false}

# --- TAG comes from the log line, and from nowhere else ---------------------------------------
# `semantic-release` prints `Created tag v0.2.0` after `git tag` succeeded, behind its own
# timestamp/plugin prefix (and behind the runner's, when the log comes from `gh run view --log`),
# so the pattern is not anchored. Never "the last tag", never the subject of the head commit,
# never `tagFormat` plus a computed version.
tag=$(grep -oE 'Created tag [^[:space:]]+' "$log" 2>/dev/null | tail -n 1 | awk '{print $3}' || true)
if [ -n "${TAG:-}" ] && [ "${TAG}" != "${tag}" ]; then
    note "TAG=$TAG is not the tag of the log (Created tag ${tag:-none}); the log is the only source"
fi

release_subject_re='^chore\(release\): [0-9]+\.[0-9]+\.[0-9]+ \[skip ci\]$'
head_subject=$(git log -1 --format=%s 2>/dev/null || true)

# --- Order 1 — catalogue id 12, release_credential_persisted ----------------------------------
# The credential existed only in the memory of step 8. `$GITHUB_ENV` is a plain file every later
# step sources and `.git/config` is on disk, so either one is a leak that invalidates every green
# below it, and a token that must be rotated.
#
# A name is not a value, so there are two observables and both are required:
#   (a) a key or a literal that *names* the credential (`HIFA_RELEASE_TOKEN`, `extraheader`, …);
#   (b) a value that *is* the credential: a GitHub token by its prefix, or a base64 blob that
#       decodes to the basic-auth pair or to a private key. Step 8 builds
#       `base64("x-access-token:<PAT>")`, which carries no key name at all — (a) alone cannot see
#       the leak whose only purpose is that it never happens.
credential_re='HIFA_RELEASE_TOKEN|HIFA_RELEASE_SIGNING_KEY|GIT_CONFIG_|AUTHORIZATION|extraheader|BEGIN [A-Z ]*PRIVATE KEY|ssh-ed25519|ssh-rsa'
token_prefix_re='gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
base64_re='^[A-Za-z0-9+/]+={0,2}$'

# Is this *value* a credential? The reason is printed, the value never is.
value_is_credential() {
    local value=$1 decoded
    if printf '%s' "$value" | grep -qE -- "$token_prefix_re"; then
        note "a value of the environment file matches a GitHub token prefix"
        return 0
    fi
    # Only a strict base64 alphabet, a length that can hide a token and a decodable payload are
    # considered. A path such as `RELEASE_LOG=/ruta/con/letras` is alphabet-legal and decodes to
    # noise; noise is not a leak. The payload is what decides.
    printf '%s' "$value" | grep -qE -- "$base64_re" || return 1
    [ "${#value}" -ge 20 ] || return 1
    [ $((${#value} % 4)) -eq 0 ] || return 1
    decoded=$(printf '%s' "$value" | base64 -d 2>/dev/null || true)
    [ -n "$decoded" ] || return 1
    if printf '%s' "$decoded" | grep -qE 'x-access-token:|BEGIN [A-Z ]*PRIVATE KEY'; then
        note "a value of the environment file is base64 of the credential"
        return 0
    fi
    return 1
}

# One `KEY=VALUE` per line, as `$GITHUB_ENV` is written and sourced.
environment_file_is_leaky() {
    local file=$1 line value
    while IFS= read -r line || [ -n "$line" ]; do
        value=${line#*=}
        [ -n "$value" ] || continue
        value_is_credential "$value" && return 0
    done <"$file"
    return 1
}

if [ -n "${GITHUB_ENV:-}" ] && [ -f "${GITHUB_ENV}" ]; then
    if grep -qE -- "$credential_re" "${GITHUB_ENV}"; then
        note "$GITHUB_ENV names a secret, the push credential or an extraheader"
        emit release_credential_persisted
    fi
    if environment_file_is_leaky "${GITHUB_ENV}"; then
        emit release_credential_persisted
    fi
fi
if [ -f .git/config ] && grep -qE 'extraheader' .git/config; then
    note ".git/config carries an extraheader: the credential reached the repository configuration"
    emit release_credential_persisted
fi

# --- Order 2 — G1, green: nothing written -----------------------------------------------------
# The plan was computed AND nothing was written: both, or it is not green. A dry run whose engine
# died leaves the repository identical, so a state-only assertion would call that run green.
if [ "$dry_run" = "true" ] && [ "$status" -eq 0 ] \
    && has_re 'next release version is|no relevant changes'; then
    if [ -n "${PRE_RUN_HEAD:-}" ] && [ -n "${PRE_RUN_MAIN:-}" ] \
        && [ -n "${PRE_RUN_TAGS:-}" ] && [ -s "${PRE_RUN_TAGS}" ]; then
        head_now=$(git rev-parse HEAD 2>/dev/null || true)
        main_now=$(git ls-remote origin refs/heads/main 2>/dev/null | cut -f1 || true)
        tree_now=$(git status --porcelain 2>/dev/null || true)
        tags_before=$(sort -- "${PRE_RUN_TAGS}")
        tags_now=$(git ls-remote --tags origin 2>/dev/null | sort || true)
        if [ "$head_now" = "${PRE_RUN_HEAD}" ] && [ "$main_now" = "${PRE_RUN_MAIN}" ] \
            && [ -z "$tree_now" ] && [ "$tags_now" = "$tags_before" ]; then
            green "green: nothing written"
        fi
        note "the dry run cannot be proven to have written nothing (head ${head_now:-none}, main ${main_now:-none}, dirty ${tree_now:+yes}, tags ${tags_now:+present})"
    else
        note "PRE_RUN_HEAD, PRE_RUN_MAIN or PRE_RUN_TAGS is missing or empty: an empty baseline is not a baseline"
    fi
fi

# --- Order 3 — G3, green: no relevant changes ------------------------------------------------
# The question is what *this run* did, not what the checkout looks like: a scheduled run starts on
# the release commit the previous run wrote, and answers `no relevant changes` because nothing
# follows the tag. An empty delta — no tag created, head not moved — is the green. The baseline of
# step 4 is what makes the delta observable, and an empty baseline is a baseline error, exactly as
# in order 2: it proves nothing and must not be read as a green.
if [ "$status" -eq 0 ] && has 'no relevant changes'; then
    if [ -z "${PRE_RUN_HEAD:-}" ]; then
        note "PRE_RUN_HEAD is missing or empty: without the run's baseline the answer cannot be told from the state"
    else
        head_now=$(git rev-parse HEAD 2>/dev/null || true)
        if [ -z "$tag" ] && [ "$head_now" = "${PRE_RUN_HEAD}" ]; then
            green "green: no relevant changes"
        fi
        note "the run answered 'no relevant changes' but it created tag ${tag:-none} or moved head to ${head_now:-none} from ${PRE_RUN_HEAD}"
    fi
fi

# --- Order 4 — catalogue id 3, release_signing_key_invalid ------------------------------------
# `signing pre-flight: starting` without the closing `signing pre-flight ok` means the pre-flight
# was *reached* and failed: `main` is untouched and nothing was pushed.
if [ "$status" -ne 0 ] && has 'signing pre-flight: starting' && ! has 'signing pre-flight ok'; then
    note "the signing pre-flight was reached and never confirmed a signature: nothing was written"
    emit release_signing_key_invalid
fi

# --- Order 5 — catalogue id 4, release_tag_not_signed -----------------------------------------
# A tag created in this run must be an annotated object with a good SSH signature. It is public
# and immutable: it is reported, never deleted.
if [ -n "$tag" ]; then
    object_type=$(git cat-file -t "refs/tags/$tag" 2>/dev/null || true)
    if [ "$object_type" != "tag" ]; then
        note "refs/tags/$tag is '${object_type:-missing}', not an annotated tag object"
        emit release_tag_not_signed
    fi
    if ! git verify-tag "$tag" >/dev/null 2>&1; then
        note "git verify-tag $tag failed against .github/signers/allowed_signers"
        emit release_tag_not_signed
    fi
fi

# --- Order 6 — catalogue id 5, release_commit_unsigned ----------------------------------------
if [ -n "$tag" ] && ! git verify-commit "$tag^{commit}" >/dev/null 2>&1; then
    note "git verify-commit $tag^{commit} failed: the release commit is not signed by an allowed principal"
    emit release_commit_unsigned
fi

# --- Order 7 — catalogue id 6, release_version_mismatch ---------------------------------------
# (a) the literal guard of `verifyConditions` fired and no release commit exists: nothing was
# written. (b) the literal at the tag disagrees with the tag, or there is more than one.
if has 'expected exactly one version literal line' && [ -z "$tag" ] \
    && ! printf '%s' "$head_subject" | grep -qE -- "$release_subject_re"; then
    note "the literal guard refused pyproject.toml before any write: nothing was written"
    emit release_version_mismatch
fi
if [ -n "$tag" ]; then
    literal=$(git show "$tag:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\([^"]*\)"$/\1/p' || true)
    literal_count=$(git show "$tag:pyproject.toml" 2>/dev/null | grep -cE '^version = "' || true)
    if [ "$literal" != "${tag#v}" ] || [ "$literal_count" != "1" ]; then
        note "the literal at $tag is '${literal:-none}' in ${literal_count:-0} line(s); the tag names ${tag#v}"
        emit release_version_mismatch
    fi
fi

# --- Order 8 — catalogue id 7, release_forbidden ----------------------------------------------
# The API refused the Release creation: the tag exists, the Release does not.
if has_re 'Resource not accessible by integration|status: 403|403 Forbidden|must have admin rights'; then
    note "the API refused the Release creation (403): the tag exists, the Release does not (§5.4)"
    emit release_forbidden
fi

# --- Order 9 — catalogue id 8, release_notes_missing ------------------------------------------
# The changelog at the tag must mention the version. Two limits, so the row cannot be read wider
# than it is: the Release body has no observable in the repository or in the log, and a tag the
# remote refused is a different state — the notes are not missing, they were never published, so
# order 10 is the diagnosis and this row must not take that line from it.
push_rejected_re='remote rejected|pre-receive hook declined|failed to push some refs|protected branch|GH013|cannot lock ref'
if [ -n "$tag" ] && ! has_re "$push_rejected_re"; then
    if ! git show "$tag:CHANGELOG.md" 2>/dev/null | grep -qF -- "# [${tag#v}]"; then
        note "CHANGELOG.md at $tag does not mention ${tag#v}"
        emit release_notes_missing
    fi
fi

# --- Order 10 — catalogue ids 13 and 9, the refused push ---------------------------------------
# The engine pushes two refs that can be refused independently: the tag (with the release commit)
# and `refs/notes/semantic-release`. `failed to push some refs`, `protected branch` and
# `cannot lock ref` are produced by a refusal of either one, and also when the *release commit*'s
# own push failed, so the message is a signal and never the fact: the row asks the remote which
# state it left behind.
#
#   id 9  — the tag the log says was created is not on the remote: the commit is on `main` and the
#           tag is not, the state the tag-push repair is written for.
#   id 13 — that tag IS on the remote, and the refusal names the notes ref: the commit, the tag and
#           the Release are published, only `refs/notes/semantic-release` is missing. Reporting this
#           as a tag refusal claims two things that are false.
#
# Neither code is emitted without that `ls-remote` query, and a run with no `Created tag` line can
# reach neither: the failed push was then the release commit's. The message never echoes the
# credential.
main_sha=$(git ls-remote origin refs/heads/main 2>/dev/null | cut -f1 || true)
main_ref=${main_sha:-origin/main}
main_subject=$(git log -1 --format=%s "$main_ref" 2>/dev/null || true)
# A refusal implies a failed engine: an exit code of zero means nothing was refused, and any
# refusal quoted in the log is then somebody else's prose — a commit message body, for instance,
# which the analyzer echoes back line by line.
if [ "$status" -ne 0 ] && has_re "$push_rejected_re"; then
    tag_on_remote=false
    if [ -n "$tag" ] && git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null | grep -q .; then
        tag_on_remote=true
    fi
    # Anchored to the shape git writes, never to the words: prose that mentions the ref is not
    # a refusal of it.
    notes_rejected=false
    if has_re '^[[:space:]]*![[:space:]]*\[(remote )?rejected\].*refs/notes/'; then
        notes_rejected=true
    fi
    if [ "$tag_on_remote" = false ]; then
        if [ -n "$tag" ]; then
            note "the remote rejected the tag push: the release commit is on main and refs/tags/$tag is not on the remote"
            emit release_tag_push_rejected
        fi
        note "the log reports a rejected push, but no tag was created and origin/main carries no release commit: the failed push was the release commit's, not the tag's"
    elif [ "$notes_rejected" = true ]; then
        note "the remote refused refs/notes/semantic-release while refs/tags/$tag is on the remote: the release is published, its notes ref is not"
        emit release_notes_push_rejected
    else
        note "the log reports a rejected push while refs/tags/$tag is on the remote, and the refusal names neither the tag nor the notes ref: nothing to repair"
    fi
fi

# --- Order 11 — catalogue id 10, release_partial_state ---------------------------------------
# `origin/main`'s head is a release commit whose literal is its version, and the tag it names does
# not exist on the remote: a state to repair, never a re-run.
if printf '%s' "$main_subject" | grep -qE -- "$release_subject_re"; then
    version=${main_subject#chore(release): }
    version=${version% \[skip ci\]}
    main_literal=$(git show "$main_ref:pyproject.toml" 2>/dev/null | sed -n 's/^version = "\([^"]*\)"$/\1/p' || true)
    remote_tag=$(git ls-remote --tags origin "refs/tags/v$version" 2>/dev/null || true)
    if [ "$main_literal" = "$version" ] && [ -z "$remote_tag" ]; then
        note "origin/main is the release commit $version with the literal, and refs/tags/v$version does not exist"
        emit release_partial_state
    fi
fi

# --- Order 12 — G2, green: released <tag> ----------------------------------------------------
# The residual green of a completed release: reachable only when no failure matched, so its
# assertions ARE orders 5-9 (annotated signed tag, signed commit, literal == tag, notes). A red
# run is never laundered green, so the engine's exit code is required explicitly.
g2_exit_ok=false
if [ "$status" -eq 0 ]; then g2_exit_ok=true; fi
if [ "$g2_exit_ok" = true ] && [ -n "$tag" ]; then
    remote_tag=$(git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null || true)
    if [ -n "$remote_tag" ]; then
        green "green: released $tag"
    fi
    note "the run claims $tag but refs/tags/$tag is not on the remote"
fi

# --- Order 13 — catalogue id 11, release_engine_aborted --------------------------------------
# None of the above: the engine exited without a release and without the `no relevant changes`
# token, or a green cannot be proven. Reported instead of passing as a no-op.
note "no evaluation order matched: exit $status, tag ${tag:-none}, 'no relevant changes' $([ "$status" -eq 0 ] && has 'no relevant changes' && echo yes || echo no)"
emit release_engine_aborted
