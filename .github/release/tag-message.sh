#!/usr/bin/env bash
# SPEC-110 §2.3.3 / §2.9 — the `GIT_EDITOR` helper used by the release pipeline.
#
# The engine tags with a bare `git tag <tagName> <ref>` and no `-m`. With `tag.gpgSign=true` that
# call aborts ("Please supply the message"); with `tag.gpgSign=true` *and* `GIT_EDITOR` pointing
# here it produces an annotated, SSH-signed tag whose message is the tag name.
#
# Contract (§2.3.3), four rules:
#   1. git runs the editor as `<editor> <file>`: exactly one argument. Zero or more exits non-zero.
#   2. the message comes from the file git seeds (`$1`), whose header carries the name git is
#      creating — never from a value in the working tree.
#   3. accepted names: `vX.Y.Z` (a release tag, §2.3.2) or `preflight-*` (the disposable tag of
#      the pre-flight, §2.3.1). Anything else is refused.
#   4. fail closed: no file, no header, zero or more than one name, or a name outside the two
#      patterns => exit non-zero and write nothing. An empty message is never signed.
#
# It never reads `pyproject.toml`. A helper that messages the tag with the tree literal signs
# whatever the tree says, and the mismatch becomes detectable only after `prepare` has already
# committed and pushed. The pre-flight tag is proof: `preflight-<sha>-<pid>` is a name no tree
# read could have produced.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "tag-message: expected exactly one argument (git runs: <editor> <message-file>), got $#" >&2
    exit 1
fi

message_file=$1

if [ ! -f "$message_file" ] || [ ! -r "$message_file" ]; then
    echo "tag-message: '$message_file' is not a readable message file" >&2
    exit 1
fi

# git seeds the file with a comment block: the line after `# Write a message for tag:` is the name.
names=$(awk '
    /^# Write a message for tag:$/ { want = 1; next }
    want                          { if ($0 ~ /^#   /) { sub(/^#   /, ""); print } want = 0 }
' "$message_file")

count=$(printf '%s' "$names" | grep -c . || true)
if [ "$count" -ne 1 ]; then
    echo "tag-message: expected exactly one '#   <name>' line under '# Write a message for tag:', found $count" >&2
    exit 1
fi

release_re='^v[0-9]+\.[0-9]+\.[0-9]+$'
preflight_re='^preflight-[A-Za-z0-9._-]+$'
if ! printf '%s\n' "$names" | grep -qE -- "$release_re" \
    && ! printf '%s\n' "$names" | grep -qE -- "$preflight_re"; then
    echo "tag-message: '$names' is neither vX.Y.Z nor preflight-*; refusing to write a message" >&2
    exit 1
fi

# The name is the message. Nothing else is written: a comment-only file is "no tag message".
printf '%s\n' "$names" > "$message_file"
