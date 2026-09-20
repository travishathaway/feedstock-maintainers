"""Reconstruct the total number of unique feedstock maintainers, sampled monthly, over time.

Naively re-fetching every feedstock's recipe at every historical month would cost roughly
(feedstock count) x (month count) requests -- millions, for conda-forge's ~20k feedstocks across
a decade-plus of history. Instead, each month is built incrementally from the previous one:

1. Resolve the feedstocks mono-repo's commit as of that month (`resolve_commit_at`).
2. Fetch that commit's root tree (`fetch_submodule_tree`) -- one request yields every feedstock's
   pinned submodule commit SHA at that instant.
3. Diff those SHAs against the previous month's (`diff_submodule_trees`) to find exactly which
   feedstocks were added, removed, or had their pinned commit change.
4. Only fetch recipe content (via the existing `github.fetch_recipe` + `recipe.parse_recipe`, at
   the pinned SHA) for feedstocks that were added or changed; carry every other feedstock's
   maintainer list forward unchanged from the previous month's state.

`build_snapshot` does one month; `run_backfill` walks a whole date range, checkpointing after
every month so an interrupted run only loses at most one month of work.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from .github import Cooldown, FetchError, RatePacer, fetch_gitmodules, fetch_recipe
from .gitmodules import FeedstockSource, parse_gitmodules
from .recipe import ParseError, parse_recipe

_OWNER = "conda-forge"
_REPO = "feedstocks"
_BRANCH = "main"
_API_URL = "https://api.github.com"
_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class MaintainerHistoryState:
    """Carry-forward checkpoint: what we knew as of the last snapshot we successfully built."""

    last_snapshot_date: str | None
    submodule_shas: dict[str, str]
    maintainers_by_feedstock: dict[str, list[str]]

    @classmethod
    def empty(cls) -> MaintainerHistoryState:
        return cls(last_snapshot_date=None, submodule_shas={}, maintainers_by_feedstock={})

    @classmethod
    def load(cls, path: Path) -> MaintainerHistoryState:
        if not path.exists():
            return cls.empty()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            last_snapshot_date=data.get("last_snapshot_date"),
            submodule_shas=data.get("submodule_shas", {}),
            maintainers_by_feedstock=data.get("maintainers_by_feedstock", {}),
        )

    def save(self, path: Path) -> None:
        payload = {
            "last_snapshot_date": self.last_snapshot_date,
            "submodule_shas": self.submodule_shas,
            "maintainers_by_feedstock": self.maintainers_by_feedstock,
        }
        _atomic_write_json(path, payload)


def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def unique_maintainer_count(state: MaintainerHistoryState) -> int:
    unique: set[str] = set()
    for names in state.maintainers_by_feedstock.values():
        unique.update(names)
    return len(unique)


def monthly_snapshot_dates(start: date, end: date) -> list[date]:
    """Return the last calendar day of each month from `start`'s month through `end` (inclusive)."""
    dates = []
    year, month = start.year, start.month
    while True:
        last_day = calendar.monthrange(year, month)[1]
        candidate = date(year, month, last_day)
        if candidate > end:
            break
        dates.append(candidate)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return dates


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _sync_get_with_retries(
    url: str,
    retries: int,
    params: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response | None:
    """Return the response, None on 404, or raise FetchError after exhausting retries.

    A plain synchronous GET with backoff. These calls happen at most a couple of times per month
    across a whole backfill run, so they don't need the shared Cooldown/RatePacer machinery
    `fetch_recipe` uses for its much higher-volume per-feedstock content fetches.
    """
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = httpx.get(url, params=params, headers=headers, follow_redirects=True)
        except httpx.TransportError as exc:
            last_exc = exc
        else:
            if response.status_code == 200:
                return response
            if response.status_code == 404:
                return None
            if response.status_code in (403, 429) or response.status_code >= 500:
                last_exc = FetchError(f"HTTP {response.status_code} for {url}")
            else:
                raise FetchError(f"HTTP {response.status_code} for {url}")

        if attempt < retries:
            base = 0.5 * (2**attempt)
            time.sleep(base + random.uniform(0, base * 0.5))  # noqa: S311

    raise FetchError(str(last_exc) if last_exc else f"failed to fetch {url}")


def resolve_commit_at(target_date: date, token: str | None = None, retries: int = 3) -> str | None:
    """Return the feedstocks mono-repo's most recent commit as of the end of `target_date` (UTC).

    Returns None if the repo/branch had no commits yet by that date -- callers use this to detect
    "before real history starts" and skip the month entirely, rather than needing a hardcoded
    inception date.
    """
    until = f"{target_date.isoformat()}T23:59:59Z"
    url = f"{_API_URL}/repos/{_OWNER}/{_REPO}/commits"
    params = {"sha": _BRANCH, "until": until, "per_page": 1}
    response = _sync_get_with_retries(url, retries, params=params, headers=_headers(token))
    if response is None:
        return None
    commits = response.json()
    return commits[0]["sha"] if commits else None


_SUBMODULE_DIR_PREFIX = "feedstocks/"


def fetch_submodule_tree(
    commit_sha: str, token: str | None = None, retries: int = 3
) -> dict[str, str]:
    """Return {feedstock_name: pinned_commit_sha} for every submodule at `commit_sha` -- one
    request for the whole roster's pointer map at that instant.

    The mono-repo's submodules live one level down, under a `feedstocks/` directory (the repo
    root itself holds only .gitmodules/README/LICENSE), so this fetches the tree recursively
    rather than just the root -- a non-recursive root-tree fetch would silently find zero
    submodule entries and yield an empty roster every time.
    """
    url = f"{_API_URL}/repos/{_OWNER}/{_REPO}/git/trees/{commit_sha}"
    response = _sync_get_with_retries(
        url, retries, params={"recursive": "1"}, headers=_headers(token)
    )
    if response is None:
        raise FetchError(f"commit {commit_sha} not found when fetching its tree")

    payload = response.json()
    if payload.get("truncated"):
        raise FetchError(
            f"tree for commit {commit_sha} was truncated by the GitHub API -- the feedstocks "
            "mono-repo has grown past the recursive tree's entry limit"
        )
    return {
        entry["path"].removeprefix(_SUBMODULE_DIR_PREFIX): entry["sha"]
        for entry in payload.get("tree", [])
        if entry.get("type") == "commit"  # mode 160000 == submodule reference
    }


def diff_submodule_trees(
    previous: dict[str, str], current: dict[str, str]
) -> tuple[set[str], set[str], set[str]]:
    """Return (added, removed, changed) feedstock names between two submodule pointer maps."""
    previous_names, current_names = set(previous), set(current)
    added = current_names - previous_names
    removed = previous_names - current_names
    changed = {name for name in previous_names & current_names if previous[name] != current[name]}
    return added, removed, changed


async def build_snapshot(
    previous: MaintainerHistoryState,
    target_date: date,
    client: httpx.AsyncClient,
    cooldown: Cooldown,
    pacer: RatePacer,
    concurrency: int = 25,
    retries: int = 3,
    token: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> MaintainerHistoryState | None:
    """Build one month's maintainer state from the previous one, fetching only what changed.

    Returns None if `target_date` predates the feedstocks mono-repo's history entirely.
    """
    step = on_step or (lambda _description: None)

    step(f"Resolving commit as of {target_date.isoformat()}")
    commit_sha = resolve_commit_at(target_date, token=token, retries=retries)
    if commit_sha is None:
        return None

    step(f"Fetching submodule tree at {commit_sha[:8]}")
    current_shas = fetch_submodule_tree(commit_sha, token=token, retries=retries)
    added, removed, changed = diff_submodule_trees(previous.submodule_shas, current_shas)

    # Fetched every month (not just when `added` is non-empty): cheap (~1 extra request/month)
    # and sidesteps needing to persist owner/repo across snapshots, which also correctly handles
    # a feedstock being transferred to a different owner while keeping the same submodule path.
    step(f"Fetching .gitmodules at {commit_sha[:8]}")
    all_sources = parse_gitmodules(fetch_gitmodules(ref=commit_sha))

    maintainers_by_feedstock = dict(previous.maintainers_by_feedstock)
    for name in removed:
        maintainers_by_feedstock.pop(name, None)

    to_fetch = sorted(added | changed)
    step(f"Fetching {len(to_fetch)} added/changed recipes")

    semaphore = asyncio.Semaphore(concurrency)

    async def _fetch_one(name: str) -> None:
        source_info = all_sources.get(name)
        if source_info is None:
            maintainers_by_feedstock[name] = []
            return

        source = FeedstockSource(
            name=name,
            owner=source_info.owner,
            repo=source_info.repo,
            branch=current_shas[name],
        )
        async with semaphore:
            try:
                fetched = await fetch_recipe(
                    client, source, cooldown, pacer, retries=retries, token=token
                )
                if fetched is None:
                    maintainers_by_feedstock[name] = []
                    return
                names, _package_names = parse_recipe(fetched.filename, fetched.text)
                maintainers_by_feedstock[name] = names
            except (FetchError, ParseError):
                # Keep whatever we knew before (empty for a newly-added feedstock) rather than
                # aborting the whole snapshot over one feedstock's transient failure.
                maintainers_by_feedstock.setdefault(name, [])

    await asyncio.gather(*(_fetch_one(name) for name in to_fetch))

    return MaintainerHistoryState(
        last_snapshot_date=target_date.isoformat(),
        submodule_shas=current_shas,
        maintainers_by_feedstock=maintainers_by_feedstock,
    )


async def _run_backfill_async(
    start: date,
    end: date,
    checkpoint_path: Path,
    output_path: Path,
    token: str | None,
    concurrency: int,
    requests_per_second: float,
    retries: int,
    timeout: float,
    on_step: Callable[[str], None] | None,
) -> None:
    step = on_step or (lambda _description: None)

    state = MaintainerHistoryState.load(checkpoint_path)
    history: list[dict] = (
        json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else []
    )

    months = monthly_snapshot_dates(start, end)
    if state.last_snapshot_date is not None:
        already_done = date.fromisoformat(state.last_snapshot_date)
        months = [month for month in months if month > already_done]

    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:
        for target_date in months:
            step(f"Building snapshot for {target_date.isoformat()}")
            new_state = await build_snapshot(
                state,
                target_date,
                client,
                cooldown,
                pacer,
                concurrency=concurrency,
                retries=retries,
                token=token,
                on_step=step,
            )
            if new_state is None:
                step(f"No history yet as of {target_date.isoformat()}, skipping")
                continue

            state = new_state
            history.append(
                {
                    "date": state.last_snapshot_date,
                    "unique_maintainer_count": unique_maintainer_count(state),
                }
            )
            state.save(checkpoint_path)
            _atomic_write_json(output_path, history)


def run_backfill(
    start: date,
    end: date,
    checkpoint_path: Path,
    output_path: Path,
    token: str | None = None,
    concurrency: int = 25,
    requests_per_second: float = 5.0,
    retries: int = 3,
    timeout: float = 15.0,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Backfill monthly unique-maintainer snapshots from `start` through `end`, resumably.

    Reads `checkpoint_path` (if present) to resume after the last successfully built month, and
    flushes both it and `output_path` after every month, so an interrupted run loses at most one
    month of work.
    """
    asyncio.run(
        _run_backfill_async(
            start,
            end,
            checkpoint_path,
            output_path,
            token,
            concurrency,
            requests_per_second,
            retries,
            timeout,
            on_step,
        )
    )


def append_current_point(
    maintainers_data: dict[str, list[str]],
    history: list[dict],
    today: date,
    force: bool = False,
) -> list[dict]:
    """Return `history` with today's unique-maintainer count appended or updated in place.

    If the last entry in `history` already falls in `today`'s calendar month (and `force` is
    False), that entry is replaced rather than a new one appended -- so calling this repeatedly
    through a month (e.g. every 3 hours, from `update.yml`) collapses to one entry per month
    instead of accumulating duplicates.
    """
    unique_count = len({name for names in maintainers_data.values() for name in names})
    entry = {"date": today.isoformat(), "unique_maintainer_count": unique_count}

    if history and not force:
        last_date = date.fromisoformat(history[-1]["date"])
        if (last_date.year, last_date.month) == (today.year, today.month):
            return [*history[:-1], entry]

    return [*history, entry]
