from datetime import datetime, timedelta, timezone

import pytest

from feedstock_maintainers import feedstock_health as fh

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _signals(
    commit_days=None,
    comment_days=None,
    open_prs=None,
    issues=0,
    merged_days=None,
    human_merged_days=None,
):
    return {
        "last_merged_pr_at": _ago(merged_days) if merged_days is not None else None,
        "last_human_merged_pr_at": _ago(human_merged_days)
        if human_merged_days is not None
        else None,
        "last_human_commit_at": _ago(commit_days) if commit_days is not None else None,
        "last_human_comment_at": _ago(comment_days) if comment_days is not None else None,
        "open_prs": open_prs or {},
        "open_issues_count": issues,
    }


def _compute(name="foo", signals=None, listed=("a", "b", "c"), active=2, merged=None, dependents=0):
    return fh.compute_feedstock_health(
        name, signals or _signals(10), list(listed), active, merged, dependents, 0.0, NOW
    )


@pytest.mark.parametrize(
    ("days", "expected"),
    [(None, 0.0), (0, 1.0), (90, 1.0), (730, 0.0), (5000, 0.0)],
)
def test_recency_score_edges(days, expected):
    assert fh.recency_score(days) == expected


def test_recency_score_decays_linearly():
    midpoint = (fh.RECENCY_FULL_DAYS + fh.RECENCY_ZERO_DAYS) / 2
    assert fh.recency_score(midpoint) == pytest.approx(0.5)


def test_maintainers_score_rescales_when_activity_not_collected():
    assert fh.maintainers_score(3, None) == 1.0
    assert fh.maintainers_score(3, 0) == 0.5
    assert fh.maintainers_score(0, 2) == 0.5


def test_open_prs_score_ignores_drafts_version_updates_and_small_migration_counts():
    neutral = {"draft": 9, "version_update": 9, "bot_other": 9, "migration": 2}
    assert fh.open_prs_score(neutral) == 1.0


def test_open_prs_score_penalises_human_prs_and_migration_pileups():
    assert fh.open_prs_score({"human": 3}) == pytest.approx(0.7)
    assert fh.open_prs_score({"migration": 5}) == pytest.approx(0.7)
    assert fh.open_prs_score({"human": 100}) == 0.0


def test_issues_score_is_capped():
    assert fh.issues_score(0) == 1.0
    assert fh.issues_score(10_000) == 0.0


def test_weights_sum_to_one():
    assert sum(fh.WEIGHTS.values()) == pytest.approx(1.0)


def test_exposure_is_log_scaled_and_capped():
    assert fh.exposure(None) == 0.0
    assert fh.exposure(0) == 0.0
    assert 0 < fh.exposure(100) < fh.exposure(1000) < 1.0
    assert fh.exposure(10**9) == 1.0


def test_last_activity_picks_the_latest_timestamp():
    assert fh.last_activity(None, "2026-01-01T00:00:00Z", "2026-03-01T00:00:00+00:00") == (
        "2026-03-01T00:00:00+00:00"
    )
    assert fh.last_activity(None, None) is None


def test_healthy_feedstock_is_active():
    result = _compute()
    assert result["tier"] == fh.TIER_ACTIVE
    assert result["score"] > 90
    assert set(result["components"]) == set(fh.WEIGHTS)


def test_dormant_feedstock_needs_attention():
    result = _compute(signals=_signals(commit_days=900), listed=("a",), active=0)
    assert result["tier"] == fh.TIER_NEEDS_ATTENTION


def test_low_score_but_recently_active_is_never_needs_attention():
    result = _compute(
        signals=_signals(commit_days=5, open_prs={"human": 10}, issues=100), listed=(), active=0
    )
    assert result["tier"] != fh.TIER_NEEDS_ATTENTION


def test_exposure_raises_the_needs_attention_cutoff():
    assert fh.needs_attention_cutoff(1.0) > fh.needs_attention_cutoff(0.0)
    # score 45: fine for an obscure feedstock, flagged for a load-bearing one.
    assert fh.assign_tier(45, 500, 0.0, exempt=False) == fh.TIER_QUIET
    assert fh.assign_tier(45, 500, 1.0, exempt=False) == fh.TIER_NEEDS_ATTENTION


def test_exempt_feedstock_is_never_flagged():
    result = _compute(
        name="conda-forge-pinning", signals=_signals(commit_days=2000), listed=(), active=0
    )
    assert result["exempt"] is True
    assert result["tier"] == fh.TIER_EXEMPT


def test_no_human_activity_at_all_counts_as_dormant():
    result = _compute(signals=_signals(), listed=("a",), active=0)
    assert result["last_activity_at"] is None
    assert result["tier"] == fh.TIER_NEEDS_ATTENTION


def test_merged_pr_counts_as_human_activity():
    result = _compute(signals=_signals(commit_days=900), merged=_ago(20))
    assert result["components"]["recency"]["score"] == 1.0


def test_team_handles_are_excluded_from_listed_count():
    result = _compute(listed=("a", "conda-forge/core"), active=None)
    assert result["components"]["maintainers"]["value"]["listed"] == 1


def test_build_feedstock_health_joins_inputs_and_orders_nothing_for_uncollected():
    result = fh.build_feedstock_health(
        signals={"foo": _signals(10)},
        maintainers={"foo": ["a", "b"], "bar": ["x"]},
        activity={"foo": {"active_maintainers": {"a": {}}}},
        last_merged_pr_at={},
        feedstock_exposure={"foo": {"transitive_dependents": 50, "transitive_only_ratio": 0.4}},
        generated_at="t",
        as_of=NOW,
    )
    assert set(result["feedstocks"]) == {"foo"}
    foo = result["feedstocks"]["foo"]
    assert foo["components"]["maintainers"]["value"] == {"listed": 2, "active": 1}
    assert foo["exposure"]["transitive_dependents"] == 50
    assert result["config"]["weights"] == fh.WEIGHTS


def test_build_feedstock_health_skips_archived_feedstocks():
    result = fh.build_feedstock_health(
        signals={"live": _signals(10), "pynio": {"archived": True}},
        maintainers={},
        activity={},
        last_merged_pr_at={},
        feedstock_exposure={},
        generated_at="t",
        as_of=NOW,
    )
    assert set(result["feedstocks"]) == {"live"}


def test_bot_merged_pr_counts_as_upkeep_but_not_as_human_activity():
    # r-munsell: only bots commit; the last sign of life is an autotick rebuild PR auto-merged.
    result = _compute(signals=_signals(merged_days=30))
    assert result["components"]["recency"]["score"] == 1.0
    assert result["last_activity_at"] == _ago(30)
    assert result["last_human_activity_at"] is None


def test_human_merged_pr_from_signals_counts_as_human_activity():
    # xorg-xproto: bot-authored PR merged by a human.
    result = _compute(signals=_signals(merged_days=100, human_merged_days=100))
    assert result["last_human_activity_at"] == _ago(100)


def test_dormant_feedstock_with_recent_bot_merge_is_not_flagged():
    result = _compute(signals=_signals(commit_days=900, merged_days=20), listed=("a",), active=0)
    assert result["tier"] != fh.TIER_NEEDS_ATTENTION
