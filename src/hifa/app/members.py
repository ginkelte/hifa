"""Who the caller is: the local member of the workspace, when there is exactly one.

A workspace has no accounts and no sessions. `init` seeds one member and every edge that needs
an author asks the same question — the member `HIFA_MEMBER` names, or the single active member
of the workspace, or nobody. The rule lives here because both edges ask it and neither of them
owns it; an edge that reimplemented it would answer it twice.

`None` is an answer and not a failure: a workspace with three active members has no "me", and a
write that needs an author then goes without one instead of guessing.
"""

from __future__ import annotations

import os

from hifa.adapters.sqlite.uow import Repos
from hifa.domain.entities import Member


def active_members(repos: Repos) -> list[Member]:
    """Every active member of the workspace, in the repository's own order."""
    workspace = repos.teams.workspace()
    if workspace is None:
        return []
    return [member for member in repos.members.list(workspace.id) if member.is_active]


def local_member(repos: Repos, *, ref: str | None = None) -> str | None:
    """The member the caller is, or `None` when the workspace does not say.

    `ref` wins over the environment: it is the explicit answer a caller already resolved. With
    neither, the workspace answers only when it holds exactly one active member — two members
    means two candidates, and picking one of them would be inventing an identity.
    """
    wanted = (ref if ref is not None else os.environ.get("HIFA_MEMBER") or "").strip()
    if wanted:
        member = repos.members.get(wanted) or repos.members.get_by_email(wanted)
        return member.id if member is not None else None
    members = active_members(repos)
    return members[0].id if len(members) == 1 else None
