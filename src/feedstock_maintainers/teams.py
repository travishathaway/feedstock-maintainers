"""Resolve GitHub team handles (e.g. `conda-forge/r`) in `recipe-maintainers` to their members.

A recipe that lists `conda-forge/r` is maintained by every current member of that team, but
`maintainers.json` only carries the handle, so on its own it reads as "no individual
maintainers". Two pieces fix that without losing the original information:

- `fetch_team_members`/`run_team_members_fetch`: look up the *current* members of each team
  handle via the GitHub Teams API (a team's membership changes over time, so this is refreshed
  on every update run).
- `expand_team_handles`: pure; returns a maintainer list with each team's members added **and the
  team handle itself kept**, so consumers can both count/show the real people and still say "this
  recipe is managed by the conda-forge/r team".

`maintainers.json` itself is deliberately left untouched (it is the raw recipe truth); expansion
happens where maintainers are consumed -- see `generate package-maintainers`, `generate
feedstock-health` and `generate feedstock-activity`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from .github import _API_VERSION, Cooldown, FetchError, RatePacer, _get_with_retries

_TEAM_MEMBERS_URL = "https://api.github.com/orgs/{org}/teams/{slug}/members"
_PER_PAGE = 100
_MAX_PAGES = 20  # 2,000 members; far beyond any conda-forge team


def team_handles_in(maintainers: dict[str, list[str]]) -> list[str]:
    """Sorted distinct team handles (any login containing "/") across `maintainers`."""
    return sorted({login for logins in maintainers.values() for login in logins if "/" in login})


def expand_team_handles(logins: Iterable[str], team_members: dict[str, list[str]]) -> list[str]:
    """`logins` with each known team's current members added after it, de-duplicated.

    The team handle stays in the result -- that is the point -- so a caller can tell a team-managed
    recipe apart from an individually-maintained one. Handles with no entry in `team_members`
    (not fetched, or the team is unreadable) are kept as-is. Order: original logins first, then
    new members in team order.
    """
    result: list[str] = []
    seen: set[str] = set()
    pending: list[str] = []
    for login in logins:
        if login not in seen:
            seen.add(login)
            result.append(login)
        pending.extend(team_members.get(login, []))
    for member in pending:
        if member not in seen:
            seen.add(member)
            result.append(member)
    return result


def expand_maintainers(
    maintainers: dict[str, list[str]], team_members: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Apply `expand_team_handles` to every feedstock's maintainer list."""
    if not team_members:
        return maintainers
    return {name: expand_team_handles(logins, team_members) for name, logins in maintainers.items()}


async def fetch_team_members(
    client: httpx.AsyncClient,
    handle: str,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
    retries: int = 3,
) -> list[str] | None:
    """Current member logins of `org/slug`, sorted, or None if the team doesn't exist (404).

    Raises `FetchError` when the team can't be read (e.g. the token lacks access to the org's
    teams), which callers should treat as "keep whatever we had before".
    """
    org, _, slug = handle.partition("/")
    url = _TEAM_MEMBERS_URL.format(org=org, slug=slug)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    members: list[str] = []
    for page in range(1, _MAX_PAGES + 1):
        response = await _get_with_retries(
            client,
            url,
            retries,
            cooldown,
            pacer,
            params={"per_page": _PER_PAGE, "page": page},
            headers=headers,
        )
        if response is None:
            return None
        batch = response.json()
        members.extend(user["login"] for user in batch)
        if len(batch) < _PER_PAGE:
            break
    return sorted(members)


def load_team_members(path: Path) -> dict[str, list[str]]:
    """Read `team-members.json` ({handle: [login, ...]}); {} if absent or unreadable."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, list)} if isinstance(raw, dict) else {}


async def run_team_members_fetch(
    handles: list[str],
    previous: dict[str, list[str]],
    token: str | None,
    requests_per_second: float = 1.0,
    retries: int = 3,
    timeout: float = 30.0,
    on_step: Callable[[str], None] | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Fetch every handle. Returns `(members_by_handle, failed_handles)`.

    A handle that can't be read keeps its entry from `previous` (stale members beat none); a team
    that no longer exists (404) is dropped. Only handles in `handles` appear in the result.
    """
    step = on_step or (lambda _description: None)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)
    result: dict[str, list[str]] = {}
    failed: list[str] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        for index, handle in enumerate(handles, start=1):
            step(f"Fetching team {handle} ({index}/{len(handles)})")
            try:
                members = await fetch_team_members(client, handle, cooldown, pacer, token, retries)
            except FetchError:
                failed.append(handle)
                if handle in previous:
                    result[handle] = previous[handle]
                continue
            if members is not None:
                result[handle] = members
    return result, failed


def write_team_members(path: Path, team_members: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(team_members, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
