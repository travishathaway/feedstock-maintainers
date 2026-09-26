"""Fetch and aggregate a second, activity-based maintainer signal: which GitHub logins have
actually authored, merged, or approved-reviewed merged pull requests on a feedstock in the
trailing 12 months, and how often -- alongside the purely recipe-declared `maintainers.json`.

Only feasible for a small, popularity-thresholded tier of feedstocks (see `feedstock_tiers.py`):
running this for all ~29k feedstocks would blow through GitHub's rate limits (see NOTES.md's
maintainer-info refresh timings for the scale of that problem). Three pieces:

- `build_activity_query`/`parse_activity_page`: build one batched GraphQL query aliasing a
  `search(type: ISSUE, query: "repo:... is:pr is:merged merged:>=...")` connection per feedstock
  (search, not the `pullRequests` connection, because it supports a server-side date filter --
  avoids paginating through years of bot-driven history on a chatty feedstock), and parse one
  alias's page of results into bot-filtered events.
- `fetch_activity_batch`/`run_activity_fetch`: the async fetch loop, reusing `github.py`'s
  `Cooldown`/`RatePacer` machinery so a rate-limit signal on this GraphQL endpoint pauses every
  worker the same way it already does for the REST recipe/user fetches.
- `ActivityStore`: a flat-file, manifest-style cache (one JSON file, unlike `RecipeCache`'s
  one-file-per-feedstock layout -- each feedstock's payload here is tiny) that lets a fetch run
  resume/incrementally update without re-querying feedstocks whose data is still fresh.
- `build_feedstock_activity`: the pure, offline "generate" step that prunes each feedstock's raw
  events to the trailing window and joins against `maintainers.json`.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .github import Cooldown, FetchError, RatePacer

_GRAPHQL_URL = "https://api.github.com/graphql"
_OWNER = "conda-forge"

# GitHub's own `__typename == "Bot"` check catches GitHub Apps, but conda-forge's automation
# accounts aren't all verified to be Bot-typed on GitHub's side -- this denylist is a documented
# gap (plan OQ-1) to spot-check against live GraphQL responses before relying on it in production.
# Public (no leading underscore) so `bigquery_activity.py`'s SQL-side filtering can reuse the same
# list instead of drifting out of sync with a second copy.
BOT_LOGIN_DENYLIST = frozenset(
    {
        "regro-cf-autotick-bot",
        "conda-forge-admin",
        "conda-forge-linter",
        "conda-forge-curator[bot]",
        "dependabot[bot]",
        "github-actions[bot]",
        "pre-commit-ci[bot]",
    }
)

_DEFAULT_RAW_RETENTION_MONTHS = 13  # generate's default 12-month window + 1 month of slack


# --- GraphQL query construction / response parsing ------------------------------------------


def _search_query_string(feedstock: str, since_date: str) -> str:
    return f"repo:{_OWNER}/{feedstock}-feedstock is:pr is:merged merged:>={since_date}"


def build_activity_query(
    feedstocks: list[str],
    since_date: str,
    cursors: dict[str, str | None],
    prs_first: int = 50,
    reviews_first: int = 5,
) -> tuple[str, dict[str, Any]]:
    """Build one GraphQL query aliasing a `search` connection per feedstock in `feedstocks`.

    `since_date` is a plain "YYYY-MM-DD" (GitHub's search `merged:>=` qualifier is date-grained,
    not timestamp-grained). `cursors` maps feedstock -> the `after` cursor to resume that
    feedstock's search from (`None` for the first page). Returns (query_string, variables) ready
    to POST as `{"query": ..., "variables": ...}`.
    """
    var_decls: list[str] = []
    aliases: list[str] = []
    variables: dict[str, Any] = {}

    for index, feedstock in enumerate(feedstocks):
        cursor_var = f"cursor{index}"
        var_decls.append(f"${cursor_var}: String")
        variables[cursor_var] = cursors.get(feedstock)
        search_query = _search_query_string(feedstock, since_date)
        aliases.append(
            f"  r{index}: search(query: {json.dumps(search_query)}, type: ISSUE, "
            f"first: {prs_first}, after: ${cursor_var}) {{\n"
            "    nodes {\n"
            "      ... on PullRequest {\n"
            "        number\n"
            "        mergedAt\n"
            "        author { login __typename }\n"
            "        mergedBy { login __typename }\n"
            f"        reviews(states: APPROVED, first: {reviews_first}) {{\n"
            "          nodes { author { login __typename } }\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "    pageInfo { hasNextPage endCursor }\n"
            "  }"
        )

    query = (
        f"query({', '.join(var_decls)}) {{\n"
        "  rateLimit { cost remaining resetAt }\n" + "\n".join(aliases) + "\n}"
    )
    return query, variables


def _is_bot(actor: dict | None) -> bool:
    if actor is None:
        return False
    if actor.get("__typename") == "Bot":
        return True
    return actor.get("login") in BOT_LOGIN_DENYLIST


def _human_login(actor: dict | None) -> str | None:
    if actor is None or _is_bot(actor):
        return None
    return actor.get("login")


@dataclass(frozen=True)
class ParsedActivityPage:
    events: list[dict]  # {"number": int, "merged_at": str, "logins": [str, ...]}
    bot_merged_count: int
    has_next_page: bool
    end_cursor: str | None


def parse_activity_page(alias_data: dict) -> ParsedActivityPage:
    """Parse one aliased `search` connection's page into bot-filtered per-PR events.

    A merged PR becomes one event with the deduplicated set of human logins who authored it,
    merged it, or approved it in review -- three ways one PR can credit the same person, collapsed
    to one. A PR touched only by bots/automation (the common case -- most feedstock merges are
    version-bump PRs auto-merged with no human review) contributes to `bot_merged_count` instead
    and is dropped, keeping the stored payload small.
    """
    events: list[dict] = []
    bot_merged_count = 0

    for node in alias_data.get("nodes", []):
        logins: set[str] = set()
        author_login = _human_login(node.get("author"))
        merger_login = _human_login(node.get("mergedBy"))
        if author_login:
            logins.add(author_login)
        if merger_login:
            logins.add(merger_login)
        for review in node.get("reviews", {}).get("nodes", []):
            reviewer_login = _human_login(review.get("author"))
            if reviewer_login:
                logins.add(reviewer_login)

        if logins:
            events.append(
                {"number": node["number"], "merged_at": node["mergedAt"], "logins": sorted(logins)}
            )
        else:
            bot_merged_count += 1

    page_info = alias_data.get("pageInfo", {})
    return ParsedActivityPage(
        events=events,
        bot_merged_count=bot_merged_count,
        has_next_page=page_info.get("hasNextPage", False),
        end_cursor=page_info.get("endCursor"),
    )


# --- Async fetch loop, reusing github.py's Cooldown/RatePacer --------------------------------


async def _apply_rate_limit_headers(response: httpx.Response, cooldown: Cooldown) -> None:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            await cooldown.set_from_retry_after(float(retry_after))
            return
        except ValueError:
            pass

    remaining = response.headers.get("x-ratelimit-remaining")
    reset = response.headers.get("x-ratelimit-reset")
    if remaining == "0" and reset is not None:
        try:
            await cooldown.set_from_reset_epoch(float(reset))
        except ValueError:
            pass


async def _post_graphql(
    client: httpx.AsyncClient,
    query: str,
    variables: dict[str, Any],
    token: str | None,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
) -> dict[str, Any]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        await cooldown.wait()
        await pacer.acquire()
        try:
            response = await client.post(
                _GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers
            )
        except httpx.TransportError as exc:
            last_exc = exc
        else:
            if response.status_code == 200:
                payload = response.json()
                if not payload.get("data"):
                    raise FetchError(f"GraphQL errors: {payload.get('errors')}")
                return payload["data"]
            if response.status_code in (403, 429):
                await _apply_rate_limit_headers(response, cooldown)
                last_exc = FetchError(f"HTTP {response.status_code} for {_GRAPHQL_URL}")
            elif response.status_code >= 500:
                last_exc = FetchError(f"HTTP {response.status_code} for {_GRAPHQL_URL}")
            else:
                raise FetchError(f"HTTP {response.status_code} for {_GRAPHQL_URL}")

        if attempt < retries:
            base = 0.5 * (2**attempt)
            await asyncio.sleep(base + random.uniform(0, base * 0.5))  # noqa: S311

    raise FetchError(str(last_exc) if last_exc else f"failed to fetch {_GRAPHQL_URL}")


@dataclass(frozen=True)
class ActivityBatchResult:
    events_by_feedstock: dict[str, list[dict]]
    bot_merged_count_by_feedstock: dict[str, int]
    last_cost: int
    last_remaining: int


async def fetch_activity_batch(
    client: httpx.AsyncClient,
    feedstocks: list[str],
    since_date: str,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
    retries: int = 3,
    prs_first: int = 50,
    reviews_first: int = 5,
    on_cost: Callable[[int, int], None] | None = None,
) -> ActivityBatchResult:
    """Fetch merged-PR activity for every feedstock in `feedstocks`, paginating each one's
    `search` connection independently (within repeated aliased queries) until every feedstock's
    pages are exhausted. `on_cost(cost, remaining)`, if given, is called after every request with
    GitHub's own self-reported GraphQL point cost -- the intended way to calibrate `--batch-size`/
    `reviews_first` empirically rather than guessing (plan OQ-6)."""
    cursors: dict[str, str | None] = dict.fromkeys(feedstocks)
    pending = list(feedstocks)
    events_by_feedstock: dict[str, list[dict]] = defaultdict(list)
    bot_merged_by_feedstock: Counter[str] = Counter()
    last_cost, last_remaining = 0, -1

    while pending:
        query, variables = build_activity_query(
            pending, since_date, cursors, prs_first=prs_first, reviews_first=reviews_first
        )
        data = await _post_graphql(client, query, variables, token, retries, cooldown, pacer)

        rate_limit = data.get("rateLimit") or {}
        last_cost = rate_limit.get("cost", last_cost)
        last_remaining = rate_limit.get("remaining", last_remaining)
        if on_cost:
            on_cost(last_cost, last_remaining)

        still_pending = []
        for index, name in enumerate(pending):
            alias_data = data.get(f"r{index}")
            if alias_data is None:
                continue  # missing repo (renamed/transferred/deleted) -- drop it, don't retry
            page = parse_activity_page(alias_data)
            events_by_feedstock[name].extend(page.events)
            bot_merged_by_feedstock[name] += page.bot_merged_count
            if page.has_next_page:
                cursors[name] = page.end_cursor
                still_pending.append(name)
        pending = still_pending

    return ActivityBatchResult(
        events_by_feedstock=dict(events_by_feedstock),
        bot_merged_count_by_feedstock=dict(bot_merged_by_feedstock),
        last_cost=last_cost,
        last_remaining=last_remaining,
    )


# --- ActivityStore: flat-file manifest cache --------------------------------------------------


@dataclass(frozen=True)
class ActivityEntry:
    tier: str
    fetched_at: str
    events: list[dict]
    bot_merged_count: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivityStore:
    """Flat-file cache of raw per-feedstock activity events, read/written as one JSON file.

    Unlike `RecipeCache`'s one-file-per-feedstock layout, each feedstock's payload here is tiny (a
    handful of PR events), so the whole tier fits in one file with per-feedstock
    `tier`/`fetched_at` metadata inline. New events from an incremental (`--since`) fetch are
    merged into any existing entry by PR number (`record`'s default `merge=True`), not replaced --
    otherwise every incremental run would silently discard everything fetched before it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, ActivityEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(raw, dict):
            return
        for name, fields in raw.items():
            if isinstance(fields, dict) and "fetched_at" in fields:
                self._entries[name] = ActivityEntry(
                    tier=fields.get("tier", "long_tail"),
                    fetched_at=fields["fetched_at"],
                    events=fields.get("events", []),
                    bot_merged_count=fields.get("bot_merged_count", 0),
                )

    def should_fetch(
        self,
        name: str,
        updated_feedstocks: set[str] | None,
        force: bool,
        as_of: datetime,
        staleness: timedelta = timedelta(days=35),
    ) -> bool:
        """True if `name` needs a network fetch: forced, never seen, flagged as updated since the
        last run (via `github.fetch_updated_feedstocks`), or stale past `staleness` -- a safety
        net for anything the updated-feedstocks check might miss (e.g. a merge that didn't move
        the default branch pointer the mono-repo bot watches)."""
        if force:
            return True
        if updated_feedstocks is not None and name in updated_feedstocks:
            return True
        entry = self._entries.get(name)
        if entry is None:
            return True
        return as_of - datetime.fromisoformat(entry.fetched_at) > staleness

    def record(
        self, name: str, tier: str, events: list[dict], bot_merged_count: int, merge: bool = True
    ) -> None:
        existing = self._entries.get(name)
        if merge and existing is not None:
            by_number = {event["number"]: event for event in existing.events}
            for event in events:
                by_number[event["number"]] = event
            merged_events = list(by_number.values())
            # Not deduplicated against prior runs (bot PR numbers aren't retained, to keep the
            # store small) -- an approximate "how much bot noise vs. human activity" indicator,
            # not a precise windowed count, and never used in a maintainer-count decision.
            merged_bot_count = existing.bot_merged_count + bot_merged_count
        else:
            merged_events = events
            merged_bot_count = bot_merged_count

        self._entries[name] = ActivityEntry(
            tier=tier,
            fetched_at=_now_iso(),
            events=merged_events,
            bot_merged_count=merged_bot_count,
        )

    def prune_old_events(
        self, as_of: date, retention_months: int = _DEFAULT_RAW_RETENTION_MONTHS
    ) -> None:
        """Drop events older than `retention_months` from every entry, so the raw store doesn't
        grow unboundedly across years of incremental runs. Kept slightly longer than `generate`'s
        default 12-month display window so late-arriving data (a delayed review, a retried fetch)
        isn't lost before the next `generate` run prunes it for display."""
        cutoff = _shift_month(as_of, -retention_months)
        for name, entry in self._entries.items():
            kept = [event for event in entry.events if event["merged_at"][:7] >= cutoff]
            if len(kept) != len(entry.events):
                self._entries[name] = ActivityEntry(
                    tier=entry.tier,
                    fetched_at=entry.fetched_at,
                    events=kept,
                    bot_merged_count=entry.bot_merged_count,
                )

    def entries(self) -> dict[str, ActivityEntry]:
        return dict(self._entries)

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            name: {
                "tier": entry.tier,
                "fetched_at": entry.fetched_at,
                "events": entry.events,
                "bot_merged_count": entry.bot_merged_count,
            }
            for name, entry in self._entries.items()
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)


def _shift_month(as_of: date, delta_months: int) -> str:
    """Return "YYYY-MM" for `as_of`'s month shifted by `delta_months` (may be negative)."""
    total = as_of.year * 12 + (as_of.month - 1) + delta_months
    year, month = divmod(total, 12)
    return f"{year:04d}-{month + 1:02d}"


async def run_activity_fetch(
    feedstocks: list[str],
    since_date: str,
    store: ActivityStore,
    tier_by_feedstock: dict[str, str],
    token: str | None,
    batch_size: int = 20,
    prs_first: int = 50,
    reviews_first: int = 5,
    requests_per_second: float = 1.0,
    retries: int = 3,
    timeout: float = 30.0,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Fetch activity for every feedstock in `feedstocks`, `batch_size` at a time, recording each
    into `store` (flushed after every batch, so an interrupted run loses at most one batch)."""
    step = on_step or (lambda _description: None)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)
    batches = [feedstocks[i : i + batch_size] for i in range(0, len(feedstocks), batch_size)]

    async with httpx.AsyncClient(timeout=timeout) as client:
        for batch_index, batch in enumerate(batches, start=1):
            step(f"Fetching activity batch {batch_index}/{len(batches)} ({len(batch)} feedstocks)")
            result = await fetch_activity_batch(
                client,
                batch,
                since_date,
                cooldown,
                pacer,
                token,
                retries=retries,
                prs_first=prs_first,
                reviews_first=reviews_first,
                on_cost=lambda cost, remaining: step(
                    f"GraphQL cost {cost}, {remaining} points remaining"
                ),
            )
            for name in batch:
                store.record(
                    name,
                    tier=tier_by_feedstock.get(name, "long_tail"),
                    events=result.events_by_feedstock.get(name, []),
                    bot_merged_count=result.bot_merged_count_by_feedstock.get(name, 0),
                )
            store.flush()


# --- Pure "generate" step: window pruning + join against maintainers.json --------------------


def _trailing_month_keys(as_of: date, window_months: int) -> set[str]:
    return {_shift_month(as_of, -offset) for offset in range(window_months)}


def build_feedstock_activity(
    raw_entries: dict[str, ActivityEntry],
    maintainers: dict[str, list[str]],
    generated_at: str,
    as_of: date | None = None,
    window_months: int = 12,
) -> dict[str, Any]:
    """Prune each feedstock's raw activity events to the trailing `window_months` and join
    against declared `maintainers.json` -- pure, offline, no network.

    `active_not_declared` matters as much as `declared_not_active`: it surfaces people actively
    shipping PRs to a feedstock who were never added to `recipe-maintainers`.
    """
    as_of = as_of or date.today()
    valid_months = _trailing_month_keys(as_of, window_months)

    feedstocks: dict[str, Any] = {}
    for name, entry in raw_entries.items():
        monthly_by_login: dict[str, Counter[str]] = defaultdict(Counter)
        human_merged_pr_count = 0

        for event in entry.events:
            month = event["merged_at"][:7]
            if month not in valid_months:
                continue
            human_merged_pr_count += 1
            for login in event["logins"]:
                monthly_by_login[login][month] += 1

        active_maintainers = {
            login: {"total_events": sum(months.values()), "monthly": dict(sorted(months.items()))}
            for login, months in monthly_by_login.items()
        }

        declared = set(maintainers.get(name, []))
        active = set(active_maintainers)
        feedstocks[name] = {
            "tier": entry.tier,
            "last_fetched_at": entry.fetched_at,
            "active_maintainers": active_maintainers,
            "declared_and_active": sorted(declared & active),
            "declared_not_active": sorted(declared - active),
            "active_not_declared": sorted(active - declared),
            "human_merged_pr_count": human_merged_pr_count,
            "bot_merged_pr_count": entry.bot_merged_count,
        }

    return {"generated_at": generated_at, "window_months": window_months, "feedstocks": feedstocks}
