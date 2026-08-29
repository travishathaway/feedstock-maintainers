"""Parse .gitmodules to recover each feedstock's GitHub repo + branch.

We use this instead of `git submodule` metadata so we never need the
submodules checked out on disk: .gitmodules already tells us, for every
feedstock, exactly which GitHub repo and branch its recipe lives on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL_RE = re.compile(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")
_KEY_RE = re.compile(r"^\s*(url|branch)\s*=\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FeedstockSource:
    name: str
    owner: str
    repo: str
    branch: str


def parse_gitmodules(text: str) -> dict[str, FeedstockSource]:
    """Return {feedstock_name: FeedstockSource} for every submodule entry."""
    sources: dict[str, FeedstockSource] = {}

    for block in re.split(r"(?=^\[submodule )", text, flags=re.MULTILINE):
        header = re.match(r'\[submodule "([^"]+)"\]', block)
        if not header:
            continue
        name = header.group(1)

        fields = dict(_KEY_RE.findall(block))
        url = fields.get("url")
        if not url:
            continue
        repo_match = _URL_RE.search(url)
        if not repo_match:
            continue
        owner, repo = repo_match.group(1), repo_match.group(2)

        branch = fields.get("branch", "refs/heads/main")
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/") :]

        sources[name] = FeedstockSource(name=name, owner=owner, repo=repo, branch=branch)

    return sources
