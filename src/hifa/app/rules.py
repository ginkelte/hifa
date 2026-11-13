"""Application-layer rules owned by no other module.

`slugify` and `branch_name` produce derived values the use cases store (`url_key`,
`branch_name`). They live in `app/` so the domain does not carry a formula only the
application consumes.
Pure functions, stdlib only.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from hifa.domain.entities import Issue
from hifa.domain.enums import WorkflowStateType

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


def apply_milestones(
    *,
    current: str | None,
    target: str,
    issue: Issue,
    now: datetime,
) -> dict[str, datetime | None]:
    """Milestone columns a move from `current` to `target` changes; `{}` when the type repeats.

    Entering `started` stamps `started_at` only when it was empty; entering a terminal type
    stamps its own timestamp and clears the other one; going back to an open type clears both
    terminal timestamps and keeps `started_at`. Only the columns whose value actually changes
    are returned. `current=None` is a brand-new issue, whose three milestone columns are empty,
    so the same table stamps the state it is created in.
    """
    target_type = WorkflowStateType(target)
    if current is not None and WorkflowStateType(current) is target_type:
        return {}

    candidates: list[tuple[str, datetime | None]] = []
    if target_type is WorkflowStateType.STARTED:
        if issue.started_at is None:
            candidates.append(("started_at", now))
    elif target_type is WorkflowStateType.COMPLETED:
        candidates.append(("completed_at", now))
        candidates.append(("canceled_at", None))
    elif target_type is WorkflowStateType.CANCELED:
        candidates.append(("canceled_at", now))
        candidates.append(("completed_at", None))
    else:
        candidates.append(("completed_at", None))
        candidates.append(("canceled_at", None))
    return {column: value for column, value in candidates if getattr(issue, column) != value}


__all__ = ["SLUG_MAX", "apply_milestones", "branch_name", "slugify"]
