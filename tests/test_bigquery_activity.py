"""Tests for the BigQuery `githubarchive` prototype: SQL query building, row-to-event grouping,
and the local cost estimate helper. No network access, no BigQuery client, no credentials --
`build_activity_query` only returns a string, and `rows_to_events` takes plain dicts standing in
for BigQuery result rows.
"""

from __future__ import annotations

from datetime import date

from feedstock_maintainers import bigquery_activity as bq
from feedstock_maintainers.activity import BOT_LOGIN_DENYLIST

# --- build_activity_query -----------------------------------------------------------------------


def test_build_activity_query_uses_table_suffix_range_from_dates():
    query = bq.build_activity_query(date(2025, 1, 1), date(2025, 12, 31))
    assert "_TABLE_SUFFIX BETWEEN '20250101' AND '20251231'" in query


def test_build_activity_query_filters_to_merged_conda_forge_feedstock_prs():
    query = bq.build_activity_query(date(2025, 1, 1), date(2025, 1, 2))
    assert "type = 'PullRequestEvent'" in query
    assert "repo.name LIKE 'conda-forge/%-feedstock'" in query
    assert "$.pull_request.merged') = 'true'" in query


def test_build_activity_query_credits_both_author_and_merger():
    query = bq.build_activity_query(date(2025, 1, 1), date(2025, 1, 2))
    assert "$.pull_request.user.login'" in query
    assert "$.pull_request.merged_by.login'" in query
    assert "UNION DISTINCT" in query


def test_build_activity_query_excludes_denylisted_bot_logins():
    query = bq.build_activity_query(date(2025, 1, 1), date(2025, 1, 2))
    for login in BOT_LOGIN_DENYLIST:
        assert f"'{login}'" in query
    assert "NOT ENDS_WITH(login, '[bot]')" in query


# --- rows_to_events -------------------------------------------------------------------------------


def _row(repo_name: str, pr_number: str, merged_at: str, login: str) -> dict:
    return {"repo_name": repo_name, "pr_number": pr_number, "merged_at": merged_at, "login": login}


def test_rows_to_events_strips_repo_prefix_and_suffix_to_the_feedstock_name():
    events = bq.rows_to_events(
        [_row("conda-forge/widget-feedstock", "1", "2026-01-01T00:00:00Z", "alice")]
    )
    assert set(events) == {"widget"}


def test_rows_to_events_dedupes_author_and_merger_into_one_events_logins_list():
    events = bq.rows_to_events(
        [
            _row("conda-forge/widget-feedstock", "1", "2026-01-01T00:00:00Z", "alice"),
            _row("conda-forge/widget-feedstock", "1", "2026-01-01T00:00:00Z", "bob"),
        ]
    )
    assert len(events["widget"]) == 1
    assert events["widget"][0]["number"] == 1
    assert events["widget"][0]["logins"] == ["alice", "bob"]


def test_rows_to_events_keeps_separate_prs_as_separate_events():
    events = bq.rows_to_events(
        [
            _row("conda-forge/widget-feedstock", "1", "2026-01-01T00:00:00Z", "alice"),
            _row("conda-forge/widget-feedstock", "2", "2026-02-01T00:00:00Z", "alice"),
        ]
    )
    assert [event["number"] for event in events["widget"]] == [1, 2]


def test_rows_to_events_ignores_rows_not_matching_the_feedstock_repo_shape():
    events = bq.rows_to_events(
        [_row("conda-forge/feedstocks", "1", "2026-01-01T00:00:00Z", "alice")]
    )
    assert events == {}


def test_rows_to_events_accepts_objects_with_attribute_access_like_a_bigquery_row():
    class Row:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    row = Row(
        repo_name="conda-forge/widget-feedstock",
        pr_number="1",
        merged_at="2026-01-01T00:00:00Z",
        login="alice",
    )
    events = bq.rows_to_events([row])
    assert events["widget"][0]["logins"] == ["alice"]


# --- estimate_cost_usd ---------------------------------------------------------------------------


def test_estimate_cost_usd_is_zero_within_the_free_tier():
    one_tebibyte = 1024**4
    cost = bq.estimate_cost_usd(one_tebibyte, free_tier_bytes=one_tebibyte, usd_per_tebibyte=6.25)
    assert cost == 0


def test_estimate_cost_usd_charges_only_the_bytes_over_the_free_tier():
    one_tebibyte = 1024**4
    cost = bq.estimate_cost_usd(
        3 * one_tebibyte, free_tier_bytes=one_tebibyte, usd_per_tebibyte=6.25
    )
    assert cost == 2 * 6.25
