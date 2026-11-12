"""Application-layer rules owned by no other module.

`slugify` and `branch_name` produce derived values the use cases store (`url_key`,
`branch_name`). They live in `app/` so the domain does not carry a formula only the
application consumes.
Pure functions, stdlib only.
"""

from __future__ import annotations

import re
import unicodedata

SLUG_MAX = 60


def slugify(text: str) -> str:
    """`"Fix the SSO login"` -> `"fix-the-sso-login"`.

    NFKD -> ASCII, lowercase, anything outside `[a-z0-9]` collapsed to `-`, truncated to
    `SLUG_MAX` characters at the last `-` and without a trailing `-`.
    """
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(collapsed) <= SLUG_MAX:
        return collapsed
    cut = collapsed[:SLUG_MAX]
    trimmed = cut.rsplit("-", 1)[0]
    return (trimmed or cut).strip("-")


def branch_name(identifier: str, title: str) -> str:
    """`("ENG-12", "Fix login")` -> `"eng-12-fix-login"`."""
    slug = slugify(title)
    base = identifier.lower()
    return f"{base}-{slug}" if slug else base


__all__ = ["SLUG_MAX", "branch_name", "slugify"]
