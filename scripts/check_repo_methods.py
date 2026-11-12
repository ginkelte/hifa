#!/usr/bin/env python
"""Compare the repo methods with the ones the application services declare as consumed.

Usage: python scripts/check_repo_methods.py [--strict]

Exits 0 when no method is missing; 1 (with `--strict`) when one is. Methods that
a spec names and no use case consumes are listed as "extra" (not an error).
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

REPOS = {
    "teams": ("hifa.adapters.sqlite.team_repo", "TeamRepo"),
    "states": ("hifa.adapters.sqlite.state_repo", "StateRepo"),
    "labels": ("hifa.adapters.sqlite.label_repo", "LabelRepo"),
    "members": ("hifa.adapters.sqlite.member_repo", "MemberRepo"),
    "issues": ("hifa.adapters.sqlite.issue_repo", "IssueRepo"),
    "comments": ("hifa.adapters.sqlite.comment_repo", "CommentRepo"),
    "history": ("hifa.adapters.sqlite.history_repo", "HistoryRepo"),
}

CALL_RE = re.compile(r"\b(?:tx|uow|t)\.(?P<repo>" + "|".join(REPOS) + r")\.(?P<method>[a-z_]+)\(")
EXPLICIT_RE = re.compile(
    r"\b(?P<cls>Team|Issue|Label|Member|Comment|History)Repo\.(?P<method>[a-z_]+)"
)


def public_methods(module: str, class_name: str) -> list[str]:
    sys.path.insert(0, str(SRC))
    imported = __import__(module, fromlist=[class_name])
    cls = getattr(imported, class_name)
    return sorted(
        name
        for name, value in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    )


def main() -> int:
    spec = (REPO_ROOT / "specs" / "040-application-services.md").read_text(encoding="utf-8")
    consumed: dict[str, set[str]] = {repo: set() for repo in REPOS}
    for match in CALL_RE.finditer(spec):
        consumed[match.group("repo")].add(match.group("method"))
    by_class = {f"{cls}Repo": repo for repo, (_, cls) in REPOS.items()}
    for match in EXPLICIT_RE.finditer(spec):
        repo = by_class.get(f"{match.group('cls')}Repo")
        if repo:
            consumed[repo].add(match.group("method"))

    missing_total = 0
    print(f"{'repo':10} {'consumed':>10} {'implemented':>13}  missing")
    for repo, (module, class_name) in REPOS.items():
        implemented = set(public_methods(module, class_name))
        missing = sorted(consumed[repo] - implemented)
        missing_total += len(missing)
        print(
            f"{repo:10} {len(consumed[repo]):>10} {len(implemented):>13}  "
            f"{', '.join(missing) if missing else '-'}"
        )
        extra = sorted(implemented - consumed[repo])
        if extra:
            print(f"{'':10} extras: {', '.join(extra)}")

    print(f"\nmissing in total: {missing_total}")
    if "--strict" in sys.argv and missing_total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
