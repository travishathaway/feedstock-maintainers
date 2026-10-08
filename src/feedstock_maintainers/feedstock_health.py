"""Compute a relative, explainable "health" score per feedstock.

Pure and offline (no file I/O or network; `generate feedstock-health` handles that). Inputs are
the artifacts of the existing pipeline plus the signals from `health_signals.py`.

The score answers "does this feedstock look actively looked after?" -- not "is this feedstock bad".
Several design decisions follow from feedback on earlier attempts (osl-incubator/
conda-forge-warning#2) and are deliberate:

  - Recency is the heaviest component, measured as time since the last sign of upkeep: a human
    commit or comment, or any merged PR. Bare bot *commits* and comments are excluded, but a PR
    that a bot opened and that was merged (e.g. an autotick-bot rebuild that passed CI and was
    auto-merged) counts -- it shows the feedstock is still being kept current. The last
    *human*-involved activity is reported separately (`last_human_activity_at`).
  - Draft PRs are never penalised; bot version-update PRs are neutral (their absence can mean a
    slow-release package or a failed bot, so it is neither rewarded nor penalised); only
    human-authored open PRs and piled-up bot migration PRs count against a feedstock.
  - Open issues carry a tiny weight.
  - Dependency exposure does not change the score. It only moves the tier cutoff: the same score
    is flagged sooner for a feedstock many others depend on.
  - Only a *clearly dormant* feedstock (no commits, comments or merged PRs for a year+) can ever
    be labelled `needs_attention`, and a small whitelist is `exempt` from labelling entirely.
    Wording is neutral by design -- this must not become a blunt hammer against active
    maintainers.
  - A feedstock without collected signals (outside the "top" tier) gets no entry at all, which is
    different from a low score.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

TIER_ACTIVE = "active"
TIER_QUIET = "quiet"
TIER_NEEDS_ATTENTION = "needs_attention"
TIER_EXEMPT = "exempt"

# Feedstocks that never go stale by design, or that are under constant attention but accumulate
# long-lived PRs because they are very hard to build.
EXEMPT_FEEDSTOCKS = frozenset(
    {
        "conda-forge-pinning",
        "conda-forge-repodata-patches",
        "pytorch-cpu",
        "pytorch-gpu",
        "tensorflow",
        "arrow-cpp",
    }
)

WEIGHTS = {"recency": 0.45, "maintainers": 0.30, "open_prs": 0.20, "issues": 0.05}

RECENCY_FULL_DAYS = 90  # at or below this: full marks
RECENCY_ZERO_DAYS = 730  # at or beyond this: zero
DORMANT_DAYS = 365  # `needs_attention` requires at least this long without any upkeep activity
QUIET_BELOW = 65.0
NEEDS_ATTENTION_BASE = 35.0  # cutoff for a feedstock nobody depends on...
NEEDS_ATTENTION_EXPOSURE_BONUS = 20.0  # ...raised by up to this much at full exposure
EXPOSURE_FULL_DEPENDENTS = 10_000  # transitive dependents that count as "full" exposure

_HUMAN_PR_PENALTY = 0.10
_MIGRATION_PR_PENALTY = 0.10
_MIGRATION_PR_FREE = 2  # migrations in flight are normal; only a pile-up counts
_ISSUES_SATURATION = 40
_LISTED_SATURATION = 3
_ACTIVE_SATURATION = 2


def health_config() -> dict[str, Any]:
    """The tunable constants, emitted alongside the data so the docs page never drifts."""
    return {
        "weights": WEIGHTS,
        "recency_full_days": RECENCY_FULL_DAYS,
        "recency_zero_days": RECENCY_ZERO_DAYS,
        "dormant_days": DORMANT_DAYS,
        "quiet_below": QUIET_BELOW,
        "needs_attention_base": NEEDS_ATTENTION_BASE,
        "needs_attention_exposure_bonus": NEEDS_ATTENTION_EXPOSURE_BONUS,
        "exposure_full_dependents": EXPOSURE_FULL_DEPENDENTS,
        "human_pr_penalty": _HUMAN_PR_PENALTY,
        "migration_pr_penalty": _MIGRATION_PR_PENALTY,
        "migration_pr_free": _MIGRATION_PR_FREE,
        "issues_saturation": _ISSUES_SATURATION,
        "exempt_feedstocks": sorted(EXEMPT_FEEDSTOCKS),
    }


# --- Components (each returns a 0..1 score, 1 = healthy) ----------------------------------------


def recency_score(days_since_activity: float | None) -> float:
    """1.0 within `RECENCY_FULL_DAYS`, linear decay to 0.0 at `RECENCY_ZERO_DAYS`.
    `None` (no activity found at all) scores 0."""
    if days_since_activity is None:
        return 0.0
    if days_since_activity <= RECENCY_FULL_DAYS:
        return 1.0
    span = RECENCY_ZERO_DAYS - RECENCY_FULL_DAYS
    return max(0.0, 1.0 - (days_since_activity - RECENCY_FULL_DAYS) / span)


def maintainers_score(listed_count: int, active_count: int | None) -> float:
    """Half from listed (non-team) maintainers, half from recently active ones. If activity wasn't
    collected, the listed half is rescaled to the full range rather than punishing the gap."""
    listed = min(listed_count, _LISTED_SATURATION) / _LISTED_SATURATION
    if active_count is None:
        return listed
    active = min(active_count, _ACTIVE_SATURATION) / _ACTIVE_SATURATION
    return 0.5 * listed + 0.5 * active


def open_prs_score(open_prs: dict[str, int]) -> float:
    """Penalise human PRs and piled-up bot migrations. Drafts, version updates and other bot PRs
    are neutral."""
    penalty = _HUMAN_PR_PENALTY * open_prs.get("human", 0)
    penalty += _MIGRATION_PR_PENALTY * max(0, open_prs.get("migration", 0) - _MIGRATION_PR_FREE)
    return max(0.0, 1.0 - penalty)


def issues_score(open_issues: int) -> float:
    return max(0.0, 1.0 - min(open_issues, _ISSUES_SATURATION) / _ISSUES_SATURATION)


def exposure(transitive_dependents: int | None) -> float:
    """0..1 log-scaled measure of how many other feedstocks transitively depend on this one."""
    if not transitive_dependents:
        return 0.0
    return min(
        1.0, math.log10(1 + transitive_dependents) / math.log10(1 + EXPOSURE_FULL_DEPENDENTS)
    )


def needs_attention_cutoff(exposure_value: float) -> float:
    return NEEDS_ATTENTION_BASE + NEEDS_ATTENTION_EXPOSURE_BONUS * exposure_value


def assign_tier(
    score: float, days_since_activity: float | None, exposure_value: float, exempt: bool
) -> str:
    if exempt:
        return TIER_EXEMPT
    dormant = days_since_activity is None or days_since_activity >= DORMANT_DAYS
    if dormant and score < needs_attention_cutoff(exposure_value):
        return TIER_NEEDS_ATTENTION
    if score < QUIET_BELOW:
        return TIER_QUIET
    return TIER_ACTIVE


# --- Assembly -------------------------------------------------------------------------------------


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_activity(*timestamps: str | None) -> str | None:
    """Most recent of the given ISO timestamps, or None."""
    parsed = [(p, t) for t in timestamps if (p := _parse(t)) is not None]
    return max(parsed)[1] if parsed else None


def compute_feedstock_health(
    name: str,
    signals: dict[str, Any],
    listed_maintainers: list[str],
    listed_count_override: int | None,
    active_maintainer_count: int | None,
    last_merged_pr_at: str | None,
    transitive_dependents: int | None,
    transitive_only_ratio: float | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Score one feedstock. `listed_maintainers` may contain team handles (excluded from counts).
    `listed_count_override` is the head count including team members (see
    `teams.listed_maintainer_counts`); team membership itself is never passed in."""
    listed_count = (
        listed_count_override
        if listed_count_override is not None
        else sum(1 for login in listed_maintainers if "/" not in login)
    )
    last_human_at = last_activity(
        signals.get("last_human_commit_at"),
        signals.get("last_human_comment_at"),
        signals.get("last_human_merged_pr_at"),
        last_merged_pr_at,  # from feedstock-activity-raw.json, which only keeps human-involved PRs
    )
    last_at = last_activity(last_human_at, signals.get("last_merged_pr_at"))
    last_dt = _parse(last_at)
    days = (as_of - last_dt).total_seconds() / 86400 if last_dt else None
    open_prs = signals.get("open_prs") or {}
    open_issues = signals.get("open_issues_count", 0)
    exposure_value = exposure(transitive_dependents)

    raw = {
        "recency": (days, recency_score(days)),
        "maintainers": (
            {"listed": listed_count, "active": active_maintainer_count},
            maintainers_score(listed_count, active_maintainer_count),
        ),
        "open_prs": (open_prs, open_prs_score(open_prs)),
        "issues": (open_issues, issues_score(open_issues)),
    }
    components = {
        key: {"value": value, "score": round(score, 3), "weight": WEIGHTS[key]}
        for key, (value, score) in raw.items()
    }
    score = round(100 * sum(WEIGHTS[key] * s for key, (_, s) in raw.items()), 1)
    exempt = name in EXEMPT_FEEDSTOCKS

    return {
        "score": score,
        "tier": assign_tier(score, days, exposure_value, exempt),
        "exempt": exempt,
        "last_activity_at": last_at,
        "last_human_activity_at": last_human_at,
        "components": components,
        "exposure": {
            "transitive_dependents": transitive_dependents,
            "transitive_only_ratio": transitive_only_ratio,
            "value": round(exposure_value, 3),
        },
    }


def build_feedstock_health(
    signals: dict[str, dict[str, Any]],
    maintainers: dict[str, list[str]],
    listed_counts: dict[str, int],
    activity: dict[str, dict[str, Any]],
    last_merged_pr_at: dict[str, str],
    feedstock_exposure: dict[str, dict[str, Any]],
    generated_at: str,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Score every non-archived feedstock that has collected `signals`.

    `activity` is `feedstock-activity.json`'s `"feedstocks"` sub-object; `feedstock_exposure` maps
    feedstock -> `{"transitive_dependents", "transitive_only_ratio"}` (max across its packages).
    """
    as_of = as_of or datetime.now(timezone.utc)
    feedstocks: dict[str, Any] = {}
    for name, fs_signals in signals.items():
        if fs_signals.get("archived"):
            continue  # nobody is expected to maintain an archived repository
        act = activity.get(name)
        exp = feedstock_exposure.get(name, {})
        feedstocks[name] = compute_feedstock_health(
            name,
            fs_signals,
            maintainers.get(name, []),
            listed_counts.get(name),
            len(act["active_maintainers"]) if act else None,
            last_merged_pr_at.get(name),
            exp.get("transitive_dependents"),
            exp.get("transitive_only_ratio"),
            as_of,
        )
    return {"generated_at": generated_at, "config": health_config(), "feedstocks": feedstocks}
