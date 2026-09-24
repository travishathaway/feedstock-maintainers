"""Tests for the feedstock activity signal: GraphQL query building/parsing, bot filtering, the
flat-file ActivityStore cache, and the pure window-pruning/join step.

Network access is mocked via httpx.MockTransport -- no real requests, no token needed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from feedstock_maintainers import activity as act
from feedstock_maintainers.github import Cooldown, RatePacer


def _user(login: str) -> dict:
    return {"login": login, "__typename": "User"}


def _bot(login: str) -> dict:
    return {"login": login, "__typename": "Bot"}


# --- build_activity_query ---------------------------------------------------------------------


def test_build_activity_query_aliases_one_search_per_feedstock():
    query, variables = act.build_activity_query(
        ["widget", "gadget"], "2025-09-23", {"widget": None, "gadget": "cursor-1"}
    )
    assert "r0: search(" in query
    assert "r1: search(" in query
    assert "repo:conda-forge/widget-feedstock is:pr is:merged merged:>=2025-09-23" in query
    assert variables == {"cursor0": None, "cursor1": "cursor-1"}


def test_build_activity_query_includes_rate_limit_block():
    query, _ = act.build_activity_query(["widget"], "2025-09-23", {"widget": None})
    assert "rateLimit { cost remaining resetAt }" in query


def test_build_activity_query_reviews_first_is_configurable():
    query, _ = act.build_activity_query(["widget"], "2025-09-23", {"widget": None}, reviews_first=7)
    assert "reviews(states: APPROVED, first: 7)" in query


# --- parse_activity_page ------------------------------------------------------------------------


def _node(number: int, author=None, merged_by=None, review_authors=()) -> dict:
    return {
        "number": number,
        "mergedAt": "2026-08-01T00:00:00Z",
        "author": author,
        "mergedBy": merged_by,
        "reviews": {"nodes": [{"author": a} for a in review_authors]},
    }


def test_parse_activity_page_dedupes_author_and_merger_into_one_event():
    page = act.parse_activity_page(
        {"nodes": [_node(1, author=_user("alice"), merged_by=_user("alice"))], "pageInfo": {}}
    )
    assert len(page.events) == 1
    assert page.events[0]["logins"] == ["alice"]


def test_parse_activity_page_credits_author_merger_and_reviewer_separately():
    page = act.parse_activity_page(
        {
            "nodes": [
                _node(
                    1,
                    author=_user("alice"),
                    merged_by=_user("bob"),
                    review_authors=[_user("carol")],
                )
            ],
            "pageInfo": {},
        }
    )
    assert page.events[0]["logins"] == ["alice", "bob", "carol"]


def test_parse_activity_page_excludes_bot_typed_actors():
    page = act.parse_activity_page(
        {
            "nodes": [_node(1, author=_bot("regro-cf-autotick-bot"), merged_by=_user("alice"))],
            "pageInfo": {},
        }
    )
    assert page.events[0]["logins"] == ["alice"]


def test_parse_activity_page_excludes_denylisted_login_even_if_user_typed():
    page = act.parse_activity_page(
        {
            "nodes": [_node(1, author=_user("regro-cf-autotick-bot"), merged_by=_user("alice"))],
            "pageInfo": {},
        }
    )
    assert page.events[0]["logins"] == ["alice"]


def test_parse_activity_page_pr_with_only_bots_counts_as_bot_merged_not_an_event():
    page = act.parse_activity_page(
        {
            "nodes": [_node(1, author=_bot("dependabot[bot]"), merged_by=_bot("dependabot[bot]"))],
            "pageInfo": {},
        }
    )
    assert page.events == []
    assert page.bot_merged_count == 1


def test_parse_activity_page_reads_page_info():
    page = act.parse_activity_page(
        {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "abc"}}
    )
    assert page.has_next_page is True
    assert page.end_cursor == "abc"


# --- fetch_activity_batch: pagination + batching over a mocked transport -----------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_activity_batch_merges_events_across_feedstocks():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-09-23T01:00:00Z"},
                    "r0": {
                        "nodes": [_node(1, author=_user("alice"))],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                    "r1": {
                        "nodes": [_node(2, author=_user("bob"))],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            },
        )

    async def run():
        async with _client(handler) as client:
            return await act.fetch_activity_batch(
                client, ["widget", "gadget"], "2025-09-23", Cooldown(), RatePacer(rate=0), None
            )

    result = asyncio.run(run())
    assert result.events_by_feedstock["widget"][0]["logins"] == ["alice"]
    assert result.events_by_feedstock["gadget"][0]["logins"] == ["bob"]
    assert result.last_cost == 1
    assert result.last_remaining == 4999


def test_fetch_activity_batch_follows_pagination_per_feedstock():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["variables"])
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "x"},
                        "r0": {
                            "nodes": [_node(1, author=_user("alice"))],
                            "pageInfo": {"hasNextPage": True, "endCursor": "page-2"},
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4998, "resetAt": "x"},
                    "r0": {
                        "nodes": [_node(2, author=_user("bob"))],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            },
        )

    async def run():
        async with _client(handler) as client:
            return await act.fetch_activity_batch(
                client, ["widget"], "2025-09-23", Cooldown(), RatePacer(rate=0), None
            )

    result = asyncio.run(run())
    assert len(calls) == 2
    assert calls[1]["cursor0"] == "page-2"
    logins = {event["logins"][0] for event in result.events_by_feedstock["widget"]}
    assert logins == {"alice", "bob"}


def test_fetch_activity_batch_missing_repo_alias_is_dropped_not_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "x"},
                    "r0": None,
                }
            },
        )

    async def run():
        async with _client(handler) as client:
            return await act.fetch_activity_batch(
                client, ["renamed-or-deleted"], "2025-09-23", Cooldown(), RatePacer(rate=0), None
            )

    result = asyncio.run(run())
    assert result.events_by_feedstock == {}


# --- ActivityStore --------------------------------------------------------------------------


def test_activity_store_should_fetch_never_seen():
    store = act.ActivityStore(Path("/nonexistent/feedstock-activity-raw.json"))
    now = datetime.now(timezone.utc)
    assert store.should_fetch("widget", None, force=False, as_of=now) is True


def test_activity_store_should_fetch_false_when_fresh_and_not_updated(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record("widget", tier="top", events=[], bot_merged_count=0)
    now = datetime.now(timezone.utc)
    assert store.should_fetch("widget", set(), force=False, as_of=now) is False


def test_activity_store_should_fetch_true_when_in_updated_set(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record("widget", tier="top", events=[], bot_merged_count=0)
    now = datetime.now(timezone.utc)
    assert store.should_fetch("widget", {"widget"}, force=False, as_of=now) is True


def test_activity_store_should_fetch_true_when_stale(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record("widget", tier="top", events=[], bot_merged_count=0)
    far_future = datetime.now(timezone.utc) + timedelta(days=100)
    assert (
        store.should_fetch(
            "widget", set(), force=False, as_of=far_future, staleness=timedelta(days=35)
        )
        is True
    )


def test_activity_store_should_fetch_true_when_forced(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record("widget", tier="top", events=[], bot_merged_count=0)
    now = datetime.now(timezone.utc)
    assert store.should_fetch("widget", set(), force=True, as_of=now) is True


def test_activity_store_record_merges_new_events_by_pr_number_not_replace(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record(
        "widget",
        tier="top",
        events=[{"number": 1, "merged_at": "2026-07-01T00:00:00Z", "logins": ["alice"]}],
        bot_merged_count=2,
    )
    store.record(
        "widget",
        tier="top",
        events=[{"number": 2, "merged_at": "2026-08-01T00:00:00Z", "logins": ["bob"]}],
        bot_merged_count=1,
    )
    entry = store.entries()["widget"]
    numbers = {event["number"] for event in entry.events}
    assert numbers == {1, 2}
    assert entry.bot_merged_count == 3


def test_activity_store_record_updates_existing_pr_number_in_place(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record(
        "widget",
        tier="top",
        events=[{"number": 1, "merged_at": "2026-07-01T00:00:00Z", "logins": ["alice"]}],
        bot_merged_count=0,
    )
    store.record(
        "widget",
        tier="top",
        events=[{"number": 1, "merged_at": "2026-07-01T00:00:00Z", "logins": ["alice", "bob"]}],
        bot_merged_count=0,
    )
    entry = store.entries()["widget"]
    assert len(entry.events) == 1
    assert entry.events[0]["logins"] == ["alice", "bob"]


def test_activity_store_flush_and_reload_round_trips(tmp_path):
    path = tmp_path / "raw.json"
    store = act.ActivityStore(path)
    store.record(
        "widget",
        tier="top",
        events=[{"number": 1, "merged_at": "2026-07-01T00:00:00Z", "logins": ["alice"]}],
        bot_merged_count=5,
    )
    store.flush()

    reloaded = act.ActivityStore(path)
    entry = reloaded.entries()["widget"]
    assert entry.tier == "top"
    assert entry.bot_merged_count == 5
    assert entry.events[0]["logins"] == ["alice"]


def test_activity_store_prune_old_events_drops_beyond_retention(tmp_path):
    store = act.ActivityStore(tmp_path / "raw.json")
    store.record(
        "widget",
        tier="top",
        events=[
            {"number": 1, "merged_at": "2024-01-01T00:00:00Z", "logins": ["alice"]},
            {"number": 2, "merged_at": "2026-08-01T00:00:00Z", "logins": ["bob"]},
        ],
        bot_merged_count=0,
    )
    store.prune_old_events(date(2026, 9, 23), retention_months=13)
    remaining = {event["number"] for event in store.entries()["widget"].events}
    assert remaining == {2}


def test_activity_store_missing_file_loads_empty():
    store = act.ActivityStore(Path("/nonexistent/dir/raw.json"))
    assert store.entries() == {}


# --- build_feedstock_activity -----------------------------------------------------------------


def test_build_feedstock_activity_prunes_events_outside_window():
    entries = {
        "widget": act.ActivityEntry(
            tier="top",
            fetched_at="2026-09-23T00:00:00Z",
            events=[
                {"number": 1, "merged_at": "2020-01-01T00:00:00Z", "logins": ["alice"]},
                {"number": 2, "merged_at": "2026-08-01T00:00:00Z", "logins": ["bob"]},
            ],
            bot_merged_count=0,
        )
    }
    result = act.build_feedstock_activity(
        entries, {}, "2026-09-23T00:00:00Z", as_of=date(2026, 9, 23), window_months=12
    )
    active = result["feedstocks"]["widget"]["active_maintainers"]
    assert set(active) == {"bob"}
    assert result["feedstocks"]["widget"]["human_merged_pr_count"] == 1


def test_build_feedstock_activity_monthly_counts_and_total_events():
    entries = {
        "widget": act.ActivityEntry(
            tier="top",
            fetched_at="2026-09-23T00:00:00Z",
            events=[
                {"number": 1, "merged_at": "2026-08-01T00:00:00Z", "logins": ["alice"]},
                {"number": 2, "merged_at": "2026-08-15T00:00:00Z", "logins": ["alice"]},
                {"number": 3, "merged_at": "2026-09-01T00:00:00Z", "logins": ["alice"]},
            ],
            bot_merged_count=0,
        )
    }
    result = act.build_feedstock_activity(
        entries, {}, "2026-09-23T00:00:00Z", as_of=date(2026, 9, 23)
    )
    alice = result["feedstocks"]["widget"]["active_maintainers"]["alice"]
    assert alice["total_events"] == 3
    assert alice["monthly"] == {"2026-08": 2, "2026-09": 1}


def test_build_feedstock_activity_declared_vs_active_set_differences():
    entries = {
        "widget": act.ActivityEntry(
            tier="top",
            fetched_at="2026-09-23T00:00:00Z",
            events=[{"number": 1, "merged_at": "2026-08-01T00:00:00Z", "logins": ["bob"]}],
            bot_merged_count=0,
        )
    }
    maintainers = {"widget": ["alice", "bob"]}
    result = act.build_feedstock_activity(
        entries, maintainers, "2026-09-23T00:00:00Z", as_of=date(2026, 9, 23)
    )
    record = result["feedstocks"]["widget"]
    assert record["declared_and_active"] == ["bob"]
    assert record["declared_not_active"] == ["alice"]
    assert record["active_not_declared"] == []


def test_build_feedstock_activity_active_not_declared():
    entries = {
        "widget": act.ActivityEntry(
            tier="top",
            fetched_at="2026-09-23T00:00:00Z",
            events=[{"number": 1, "merged_at": "2026-08-01T00:00:00Z", "logins": ["newcomer"]}],
            bot_merged_count=0,
        )
    }
    maintainers = {"widget": ["alice"]}
    result = act.build_feedstock_activity(
        entries, maintainers, "2026-09-23T00:00:00Z", as_of=date(2026, 9, 23)
    )
    assert result["feedstocks"]["widget"]["active_not_declared"] == ["newcomer"]


def test_build_feedstock_activity_carries_bot_merged_count_and_tier():
    entries = {
        "widget": act.ActivityEntry(
            tier="top", fetched_at="2026-09-23T00:00:00Z", events=[], bot_merged_count=42
        )
    }
    result = act.build_feedstock_activity(
        entries, {}, "2026-09-23T00:00:00Z", as_of=date(2026, 9, 23)
    )
    record = result["feedstocks"]["widget"]
    assert record["bot_merged_pr_count"] == 42
    assert record["tier"] == "top"
