#!/usr/bin/env bash
# SPEC-110 §2.3 / §2.9 — the `GIT_EDITOR` helper used by the release pipeline.
#
# The engine tags with a bare `git tag <tagName> <ref>` and no `-m`. With `tag.gpgSign=true`
# that call aborts ("Please supply the message"); with `tag.gpgSign=true` *and* `GIT_EDITOR`
# pointing here it produces an annotated, SSH-signed tag whose message is the tag name.
#
# Contract: read the single version literal from `pyproject.toml` (§2.1), write `v<version>`
# into the message file git passes as `$1`, and exit non-zero when the literal is missing —
# an empty message must never be written, because it would sign a tag with no message.

set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
pyproject="$repo_root/pyproject.toml"

if [ ! -f "$pyproject" ]; then
    echo "tag-message: pyproject.toml not found at $pyproject" >&2
    exit 1
fi

# §2.1: the literal starts at column 0, with double quotes and exactly one space on each
# side of `=`. Any other spelling is not the literal and is rejected here as well.
versions=$(sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"$/\1/p' "$pyproject")
count=$(printf '%s' "$versions" | grep -c . || true)

if [ "$count" -ne 1 ]; then
    echo "tag-message: expected exactly one 'version = \"X.Y.Z\"' line in $pyproject, found $count" >&2
    exit 1
fi

message_file=${1:-}
if [ -z "$message_file" ]; then
    echo "tag-message: no message file argument (git runs the editor as: <editor> <file>)" >&2
    exit 1
fi

printf 'v%s\n' "$versions" > "$message_file"
