"""Prototype: fetch merged-PR activity for feedstocks in one BigQuery query against the public
`githubarchive` dataset, instead of `activity.py`'s one-GraphQL-request-per-feedstock approach
(the reason that command is restricted to the "top" tier -- see `feedstock_tiers.py`).

Investigated in issue #3. The key economics, worth internalizing before reading the query: a
`githubarchive.day.*` row's `payload` column holds the *entire* webhook JSON body, and BigQuery
bills for every referenced column's bytes across every row in the scanned partitions -- it cannot
skip rows before reading them just because `type <> 'PullRequestEvent'` or `repo.name` doesn't
match. So `WHERE type = 'PullRequestEvent' AND repo.name LIKE 'conda-forge/%-feedstock'` narrows
what comes *back*, but the query is still billed for scanning `payload` across *every* public
GitHub event on GitHub.com in the date range -- not just conda-forge's. Concretely: this means
the marginal bytes-scanned cost of covering all ~29k feedstocks instead of just the "top" tier is
*zero* -- the query already pays to scan all of GitHub's activity for that date range regardless
of how many (or how few) repos it matches. The lever that actually controls cost is the date
range, not the feedstock count.

Two pieces:

- `build_activity_query`: the SQL itself, parameterized by a day-partition range so a full
  historical backfill and a cheap incremental "since last run" refresh use the same query shape
  with a different --start-date/--end-date.
- `rows_to_events`: turn the flat (repo, pr_number, merged_at, login) rows BigQuery returns into
  the same `{feedstock: [{"number", "merged_at", "logins"}, ...]}` shape `activity.ActivityStore`
  already expects, so this can feed the existing `generate feedstock-activity` step unchanged.

Running this for real needs the `google-cloud-bigquery` client and a GCP project with billing
enabled (see the CLI's `fetch feedstock-activity-bq --dry-run`, and the cost writeup on issue #3
for why billing is required even to stay inside the free tier).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from .activity import BOT_LOGIN_DENYLIST

_REPO_PREFIX = "conda-forge/"
_REPO_SUFFIX = "-feedstock"

# BigQuery's public mirror of https://www.gharchive.org/, day-partitioned since 2011-02-12.
_TABLE = "githubarchive.day.*"


def build_activity_query(start_date: date, end_date: date) -> str:
    """Build the SQL query for merged conda-forge feedstock PRs in [`start_date`, `end_date`).

    Scans `_TABLE` restricted to the `_TABLE_SUFFIX` range for the given dates (BigQuery's
    partition pruning for wildcard tables -- the only lever that reduces bytes billed, see the
    module docstring). Two credit sources per merged PR -- the author and whoever merged it,
    matching `activity.py`'s `author`/`mergedBy` signal minus the approving-reviewer credit
    (reviews are a separate `PullRequestReviewEvent` type; adding them means scanning a second
    event type's `payload`, roughly doubling the bytes billed for a signal that's a smaller
    fraction of total credited activity -- left as a follow-up, not in this prototype).
    """
    start_suffix = start_date.strftime("%Y%m%d")
    end_suffix = end_date.strftime("%Y%m%d")
    bot_logins_sql = ", ".join(f"'{login}'" for login in sorted(BOT_LOGIN_DENYLIST))

    # Safe from SQL injection (see the S608 ignore in pyproject.toml): every interpolated value
    # below is either a `date` formatted with strftime (digits only) or a login from our own
    # BOT_LOGIN_DENYLIST constant -- no user input ever reaches this string.
    return f"""
WITH pr_events AS (
  SELECT
    repo.name AS repo_name,
    JSON_EXTRACT_SCALAR(payload, '$.pull_request.number') AS pr_number,
    JSON_EXTRACT_SCALAR(payload, '$.pull_request.merged_at') AS merged_at,
    JSON_EXTRACT_SCALAR(payload, '$.pull_request.user.login') AS author_login,
    JSON_EXTRACT_SCALAR(payload, '$.pull_request.merged_by.login') AS merger_login
  FROM `{_TABLE}`
  WHERE _TABLE_SUFFIX BETWEEN '{start_suffix}' AND '{end_suffix}'
    AND type = 'PullRequestEvent'
    AND repo.name LIKE '{_REPO_PREFIX}%{_REPO_SUFFIX}'
    AND JSON_EXTRACT_SCALAR(payload, '$.action') = 'closed'
    AND JSON_EXTRACT_SCALAR(payload, '$.pull_request.merged') = 'true'
),
credited AS (
  SELECT repo_name, pr_number, merged_at, author_login AS login
  FROM pr_events
  WHERE author_login IS NOT NULL
  UNION DISTINCT
  SELECT repo_name, pr_number, merged_at, merger_login AS login
  FROM pr_events
  WHERE merger_login IS NOT NULL
)
SELECT repo_name, pr_number, merged_at, login
FROM credited
WHERE login NOT IN ({bot_logins_sql})
  AND NOT ENDS_WITH(login, '[bot]')
ORDER BY repo_name, pr_number, login
""".strip()


@dataclass(frozen=True)
class BigQueryActivityRow:
    repo_name: str
    pr_number: str
    merged_at: str
    login: str


def _feedstock_name(repo_name: str) -> str | None:
    """ "conda-forge/widget-feedstock" -> "widget"; None for anything not matching that shape
    (defensive -- the query's own `LIKE` clause should already guarantee this, but a raw row list
    from BigQuery is an external boundary worth not trusting blindly)."""
    if not (repo_name.startswith(_REPO_PREFIX) and repo_name.endswith(_REPO_SUFFIX)):
        return None
    return repo_name[len(_REPO_PREFIX) : -len(_REPO_SUFFIX)]


def rows_to_events(rows: Any) -> dict[str, list[dict]]:
    """Group BigQuery's flat (repo, pr_number, merged_at, login) rows into
    `{feedstock: [{"number": int, "merged_at": str, "logins": [str, ...]}, ...]}` --
    the same per-feedstock event shape `activity.ActivityStore.record` accepts, so a BigQuery
    fetch can be merged into the same store the GraphQL path already populates.

    `rows` is anything iterable of objects/mappings with `repo_name`, `pr_number`, `merged_at`,
    `login` fields -- a `google.cloud.bigquery.table.RowIterator`'s rows satisfy this via
    attribute access, and so does a plain list of dicts (what the tests use).
    """
    by_feedstock_pr: dict[str, dict[str, dict]] = defaultdict(dict)

    for row in rows:
        repo_name = row["repo_name"] if isinstance(row, dict) else row.repo_name
        pr_number = row["pr_number"] if isinstance(row, dict) else row.pr_number
        merged_at = row["merged_at"] if isinstance(row, dict) else row.merged_at
        login = row["login"] if isinstance(row, dict) else row.login

        feedstock = _feedstock_name(repo_name)
        if feedstock is None or pr_number is None:
            continue

        events_by_pr = by_feedstock_pr[feedstock]
        event = events_by_pr.setdefault(
            pr_number, {"number": int(pr_number), "merged_at": merged_at, "logins": set()}
        )
        event["logins"].add(login)

    return {
        feedstock: [
            {**event, "logins": sorted(event["logins"])}
            for event in sorted(events_by_pr.values(), key=lambda e: e["number"])
        ]
        for feedstock, events_by_pr in by_feedstock_pr.items()
    }


def estimate_cost_usd(bytes_processed: int, free_tier_bytes: int, usd_per_tebibyte: float) -> float:
    """Estimated on-demand query cost: `bytes_processed` beyond `free_tier_bytes` (BigQuery's
    monthly 1 TiB on-demand allowance -- shared across *all* queries in the billing project that
    month, not per query, so this is only accurate for the first query run in a given month) at
    `usd_per_tebibyte`. Confirm the current free tier and per-TiB rate at
    https://cloud.google.com/bigquery/pricing before relying on this for a real budget decision --
    both have changed over BigQuery's history.
    """
    billable_bytes = max(0, bytes_processed - free_tier_bytes)
    tebibytes = billable_bytes / (1024**4)
    return tebibytes * usd_per_tebibyte
