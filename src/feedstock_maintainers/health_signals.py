"""Fetch the raw "is anyone home?" signals that feed the feedstock health score: the last
non-bot commit, the last non-bot comment, and the shape of the currently-open PR backlog.

Like `activity.py`, this is only feasible for the popularity-thresholded "top" tier (see
`feedstock_tiers.py`) and reuses its GraphQL transport, bot detection and rate-limit machinery.
Unlike `activity.py` (which searches *merged* PRs), this reads one `repository(...)` object per
feedstock -- no pagination, because every connection is a small fixed-size "most recent N" window:

- `last_human_commit_at`: newest default-branch commit not authored by a bot account.
- `last_human_comment_at`: newest comment (on a recently-updated PR or issue) not by a bot.
- `last_merged_pr_at`: newest merged PR *whoever* authored, merged or approved it. A bot-driven
  rebuild or version bump that CI passed and auto-merged (e.g. opened by `regro-cf-autotick-bot`,
  merged by `conda-forge-admin`) is evidence the feedstock is being kept up to date, so it counts
  as upkeep even though no human was involved.
- `last_human_merged_pr_at`: newest merged PR with at least one human author, merger or approver.
- `open_prs`: counts of open, non-draft PRs split into human PRs, bot version-update PRs, bot
  migration PRs and other bot PRs. Drafts are counted separately and never penalised -- a long
  lived draft is often a larger refactor, not a sign of neglect.
- `open_issues_count`: reported, but deliberately low-weight in scoring.

Archived feedstocks are recorded as `{"archived": True}` only and excluded from scoring.

Scoring lives in `feedstock_health.py`; this module only collects and normalises.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .activity import _BOT_LOGIN_DENYLIST, _OWNER, _post_graphql
from .github import Cooldown, RatePacer

_VERSION_TITLE_RE = re.compile(r"\bv?\d+(\.\d+)+")
_MIGRATION_HINTS = ("migration", "rebuild for", "bot-rerun")
_ACTOR = "author { login __typename }"


# --- GraphQL query construction ---------------------------------------------------------------


def build_health_signals_query(
    feedstocks: list[str],
    commits_first: int = 20,
    open_prs_first: int = 50,
    recent_threads_first: int = 10,
    comments_last: int = 3,
    merged_prs_first: int = 10,
) -> str:
    """Build one GraphQL query aliasing a `repository` object per feedstock in `feedstocks`."""
    comment_fields = f"comments(last: {comments_last}) {{ nodes {{ createdAt {_ACTOR} }} }}"
    aliases: list[str] = []
    for index, feedstock in enumerate(feedstocks):
        aliases.append(
            f"  r{index}: repository(owner: {json.dumps(_OWNER)}, "
            f"name: {json.dumps(feedstock + '-feedstock')}) {{\n"
            "    isArchived\n"
            "    defaultBranchRef { target { ... on Commit {\n"
            f"      history(first: {commits_first}) {{ nodes {{\n"
            "        committedDate\n"
            "        author { name user { login } }\n"
            "      } }\n"
            "    } } }\n"
            f"    openPRs: pullRequests(states: OPEN, first: {open_prs_first}, "
            "orderBy: {field: CREATED_AT, direction: DESC}) {\n"
            "      totalCount\n"
            "      nodes {\n"
            f"        number isDraft title createdAt {_ACTOR}\n"
            "        labels(first: 10) { nodes { name } }\n"
            "      }\n"
            "    }\n"
            f"    recentPRs: pullRequests(first: {recent_threads_first}, "
            "orderBy: {field: UPDATED_AT, direction: DESC}) {\n"
            f"      nodes {{ {comment_fields} }}\n"
            "    }\n"
            f"    mergedPRs: pullRequests(states: MERGED, first: {merged_prs_first}, "
            "orderBy: {field: UPDATED_AT, direction: DESC}) {\n"
            f"      nodes {{ mergedAt {_ACTOR} mergedBy {{ login __typename }}\n"
            f"        reviews(states: APPROVED, last: 3) {{ nodes {{ {_ACTOR} }} }} }}\n"
            "    }\n"
            "    openIssues: issues(states: OPEN) { totalCount }\n"
            f"    recentIssues: issues(first: {recent_threads_first}, "
            "orderBy: {field: UPDATED_AT, direction: DESC}) {\n"
            f"      nodes {{ {comment_fields} }}\n"
            "    }\n"
            "  }"
        )
    return "query {\n  rateLimit { cost remaining resetAt }\n" + "\n".join(aliases) + "\n}"


# --- Parsing ------------------------------------------------------------------------------------


def is_bot_actor(actor: dict | None) -> bool:
    """True for GitHub Bot-typed actors, `[bot]` logins and conda-forge's automation accounts."""
    if actor is None:
        return False
    login = actor.get("login") or ""
    return (
        actor.get("__typename") == "Bot" or login in _BOT_LOGIN_DENYLIST or login.endswith("[bot]")
    )


def _is_bot_commit_author(author: dict | None) -> bool:
    """Commit authors are `GitActor`s: a display `name` plus an optional linked `user`."""
    if author is None:
        return False
    login = (author.get("user") or {}).get("login") or ""
    name = author.get("name") or ""
    return login in _BOT_LOGIN_DENYLIST or login.endswith("[bot]") or name.endswith("[bot]")


def classify_open_pr(node: dict) -> str:
    """Classify an open PR as `draft`, `human`, `migration`, `version_update` or `bot_other`.

    Drafts win over everything else. Among bot PRs, migrations are recognised by label/title hints
    and version updates by a version-looking number in the title -- deliberately conservative:
    anything ambiguous lands in `bot_other`, which is never penalised.
    """
    if node.get("isDraft"):
        return "draft"
    if not is_bot_actor(node.get("author")):
        return "human"
    title = (node.get("title") or "").lower()
    labels = [
        (label.get("name") or "").lower() for label in node.get("labels", {}).get("nodes", [])
    ]
    if any(hint in label for label in labels for hint in _MIGRATION_HINTS) or any(
        hint in title for hint in _MIGRATION_HINTS
    ):
        return "migration"
    if _VERSION_TITLE_RE.search(title):
        return "version_update"
    return "bot_other"


def _latest(*timestamps: str | None) -> str | None:
    present = [t for t in timestamps if t]
    return max(present) if present else None


def _latest_human_comment(threads: dict | None) -> str | None:
    latest: str | None = None
    for thread in (threads or {}).get("nodes", []):
        for comment in thread.get("comments", {}).get("nodes", []):
            if not is_bot_actor(comment.get("author")):
                latest = _latest(latest, comment.get("createdAt"))
    return latest


def _parse_merged_prs(merged: dict | None) -> tuple[str | None, str | None]:
    """`(last merged at by anyone, last merged at with a human involved)` from `mergedPRs`.

    Reviews are included alongside author and merger so a PR a human approved counts as human
    even when a bot opened and merged it.
    """
    last_any: str | None = None
    last_human: str | None = None
    for node in (merged or {}).get("nodes", []):
        merged_at = node.get("mergedAt")
        if not merged_at:
            continue
        last_any = _latest(last_any, merged_at)
        actors = [node.get("author"), node.get("mergedBy")]
        actors += [review.get("author") for review in node.get("reviews", {}).get("nodes", [])]
        if any(actor is not None and not is_bot_actor(actor) for actor in actors):
            last_human = _latest(last_human, merged_at)
    return last_any, last_human


def parse_health_signals(repo_data: dict) -> dict[str, Any]:
    """Parse one aliased `repository` object into the signal dict stored per feedstock."""
    if repo_data.get("isArchived"):
        # Nobody is expected to look after an archived feedstock, so none of the other signals
        # mean anything. Recorded (rather than dropped) so it isn't re-fetched as "never seen".
        return {"archived": True}

    target = (repo_data.get("defaultBranchRef") or {}).get("target") or {}
    last_commit: str | None = None
    for commit in target.get("history", {}).get("nodes", []):
        if not _is_bot_commit_author(commit.get("author")):
            last_commit = _latest(last_commit, commit.get("committedDate"))

    open_prs = repo_data.get("openPRs") or {}
    counts = dict.fromkeys(("human", "draft", "version_update", "migration", "bot_other"), 0)
    for node in open_prs.get("nodes", []):
        counts[classify_open_pr(node)] += 1

    last_merged_any, last_merged_human = _parse_merged_prs(repo_data.get("mergedPRs"))

    return {
        "archived": False,
        "last_human_commit_at": last_commit,
        "last_merged_pr_at": last_merged_any,
        "last_human_merged_pr_at": last_merged_human,
        "last_human_comment_at": _latest(
            _latest_human_comment(repo_data.get("recentPRs")),
            _latest_human_comment(repo_data.get("recentIssues")),
        ),
        "open_prs": {**counts, "total_open": open_prs.get("totalCount", 0)},
        "open_issues_count": (repo_data.get("openIssues") or {}).get("totalCount", 0),
    }


# --- Fetch loop ---------------------------------------------------------------------------------


async def fetch_health_signals_batch(
    client: httpx.AsyncClient,
    feedstocks: list[str],
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
    retries: int = 3,
    on_cost: Callable[[int, int], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch signals for `feedstocks` in one request; missing repos are omitted."""
    query = build_health_signals_query(feedstocks)
    data = await _post_graphql(client, query, {}, token, retries, cooldown, pacer)
    rate_limit = data.get("rateLimit") or {}
    if on_cost:
        on_cost(rate_limit.get("cost", 0), rate_limit.get("remaining", -1))
    return {
        name: parse_health_signals(data[f"r{index}"])
        for index, name in enumerate(feedstocks)
        if data.get(f"r{index}") is not None
    }


# --- Store --------------------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HealthSignalStore:
    """Flat-file cache of the latest signals per feedstock (replace-on-fetch, no history)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict):
                self.entries = {k: v for k, v in raw.items() if isinstance(v, dict)}

    def should_fetch(
        self,
        name: str,
        updated_feedstocks: set[str] | None,
        force: bool,
        as_of: datetime,
        staleness: timedelta = timedelta(days=7),
    ) -> bool:
        """True if forced, never seen, flagged updated since the last run, or stale. Open-PR
        counts and comments change without touching the default branch, so staleness is shorter
        than `activity.py`'s."""
        if force or name not in self.entries:
            return True
        if updated_feedstocks is not None and name in updated_feedstocks:
            return True
        fetched_at = self.entries[name].get("fetched_at")
        if not fetched_at:
            return True
        return as_of - datetime.fromisoformat(fetched_at) > staleness

    def record(self, name: str, tier: str, signals: dict[str, Any]) -> None:
        self.entries[name] = {"tier": tier, "fetched_at": _now_iso(), **signals}

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)


async def run_health_signals_fetch(
    feedstocks: list[str],
    store: HealthSignalStore,
    tier_by_feedstock: dict[str, str],
    token: str | None,
    batch_size: int = 10,
    requests_per_second: float = 1.0,
    retries: int = 3,
    timeout: float = 30.0,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Fetch every feedstock in `feedstocks`, `batch_size` per request, flushing each batch."""
    step = on_step or (lambda _description: None)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)
    batches = [feedstocks[i : i + batch_size] for i in range(0, len(feedstocks), batch_size)]

    async with httpx.AsyncClient(timeout=timeout) as client:
        for batch_index, batch in enumerate(batches, start=1):
            step(f"Fetching health signals batch {batch_index}/{len(batches)}")
            results = await fetch_health_signals_batch(
                client,
                batch,
                cooldown,
                pacer,
                token,
                retries=retries,
                on_cost=lambda cost, remaining: step(
                    f"GraphQL cost {cost}, {remaining} points remaining"
                ),
            )
            for name, signals in results.items():
                store.record(name, tier_by_feedstock.get(name, "long_tail"), signals)
            store.flush()


__all__ = [
    "HealthSignalStore",
    "build_health_signals_query",
    "classify_open_pr",
    "fetch_health_signals_batch",
    "is_bot_actor",
    "parse_health_signals",
    "run_health_signals_fetch",
]
