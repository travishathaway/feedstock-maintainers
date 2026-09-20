"""Tests for the monthly unique-maintainer history backfill/append logic.

Network access (fetch_recipe, fetch_gitmodules, the commits/trees REST endpoints) is monkeypatched
throughout -- no real requests, no token needed.
"""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from feedstock_maintainers import maintainer_history as mh
from feedstock_maintainers.github import Cooldown, FetchError, RatePacer


def _gitmodules_text(*names: str) -> str:
    return "\n".join(
        f'[submodule "{name}"]\n'
        f"\tpath = {name}\n"
        f"\turl = https://github.com/conda-forge/{name}.git\n"
        "\tbranch = main\n"
        for name in names
    )


def _state(shas: dict[str, str], maintainers: dict[str, list[str]]) -> mh.MaintainerHistoryState:
    return mh.MaintainerHistoryState(
        last_snapshot_date=None, submodule_shas=shas, maintainers_by_feedstock=maintainers
    )


def _build_snapshot(previous, target_date):
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        try:
            return await mh.build_snapshot(
                previous, target_date, client, Cooldown(), RatePacer(rate=0)
            )
        finally:
            await client.aclose()

    return asyncio.run(run())


# --- monthly_snapshot_dates ----------------------------------------------------------------


def test_monthly_snapshot_dates_returns_last_day_of_each_month():
    dates = mh.monthly_snapshot_dates(date(2024, 1, 15), date(2024, 4, 30))
    assert dates == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)]


def test_monthly_snapshot_dates_stops_before_end_when_month_incomplete():
    dates = mh.monthly_snapshot_dates(date(2024, 1, 1), date(2024, 2, 15))
    assert dates == [date(2024, 1, 31)]


def test_monthly_snapshot_dates_handles_leap_year_february():
    dates = mh.monthly_snapshot_dates(date(2024, 2, 1), date(2024, 2, 29))
    assert dates == [date(2024, 2, 29)]


# --- diff_submodule_trees ------------------------------------------------------------------


def test_diff_submodule_trees_added_removed_changed():
    previous = {"widget": "sha1", "gadget": "sha2", "gizmo": "sha3"}
    current = {"widget": "sha1", "gadget": "sha2-new", "thingamajig": "sha4"}

    added, removed, changed = mh.diff_submodule_trees(previous, current)

    assert added == {"thingamajig"}
    assert removed == {"gizmo"}
    assert changed == {"gadget"}


def test_diff_submodule_trees_no_changes():
    shas = {"widget": "sha1"}
    assert mh.diff_submodule_trees(shas, dict(shas)) == (set(), set(), set())


# --- resolve_commit_at / fetch_submodule_tree (mocked httpx.get) ---------------------------


def test_resolve_commit_at_returns_first_commit_sha(monkeypatch):
    def fake_get(url, params=None, headers=None, follow_redirects=True):
        assert params["until"] == "2024-01-31T23:59:59Z"
        return httpx.Response(200, json=[{"sha": "abc123"}])

    monkeypatch.setattr(httpx, "get", fake_get)

    assert mh.resolve_commit_at(date(2024, 1, 31)) == "abc123"


def test_resolve_commit_at_returns_none_when_no_commits_yet(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=[]))
    assert mh.resolve_commit_at(date(2010, 1, 1)) is None


def test_fetch_submodule_tree_filters_to_submodule_entries(monkeypatch):
    # Real shape: submodules live one level down under feedstocks/, not at the repo root
    # (which holds only .gitmodules/README/LICENSE) -- a regression test for a bug where a
    # non-recursive root-tree fetch silently found zero submodules every time.
    payload = {
        "tree": [
            {"path": ".gitmodules", "type": "blob", "sha": "sha-gm"},
            {"path": "README.md", "type": "blob", "sha": "sha-readme"},
            {"path": "feedstocks", "type": "tree", "sha": "sha-dir"},
            {"path": "feedstocks/widget", "type": "commit", "sha": "sha1"},
            {"path": "feedstocks/gadget", "type": "commit", "sha": "sha3"},
        ],
        "truncated": False,
    }
    seen_params: dict[str, str] = {}

    def fake_get(url, params=None, headers=None, follow_redirects=True):
        seen_params.update(params or {})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = mh.fetch_submodule_tree("deadbeef")

    assert result == {"widget": "sha1", "gadget": "sha3"}
    assert seen_params == {"recursive": "1"}  # non-recursive would miss nested submodules


def test_fetch_submodule_tree_raises_on_truncation(monkeypatch):
    payload = {"tree": [], "truncated": True}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=payload))

    with pytest.raises(FetchError, match="truncated"):
        mh.fetch_submodule_tree("deadbeef")


def test_fetch_submodule_tree_raises_when_commit_missing(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(404))

    with pytest.raises(FetchError):
        mh.fetch_submodule_tree("deadbeef")


# --- build_snapshot: the core incremental-fetch behavior -----------------------------------


def test_build_snapshot_only_fetches_added_and_changed_feedstocks(monkeypatch):
    previous = _state(
        shas={"widget": "sha1", "gadget": "sha2", "gizmo": "sha3"},
        maintainers={"widget": ["alice"], "gadget": ["bob"], "gizmo": ["carol"]},
    )
    current_shas = {"widget": "sha1", "gadget": "sha2-new", "thingamajig": "sha4"}

    monkeypatch.setattr(mh, "resolve_commit_at", lambda *a, **k: "commit-sha")
    monkeypatch.setattr(mh, "fetch_submodule_tree", lambda *a, **k: current_shas)
    monkeypatch.setattr(
        mh,
        "fetch_gitmodules",
        lambda ref: _gitmodules_text("widget", "gadget", "gizmo", "thingamajig"),
    )

    fetched_names: list[str] = []

    class _Fetched:
        def __init__(self, text):
            self.filename = "recipe.yaml"
            self.text = text

    async def fake_fetch_recipe(client, source, cooldown, pacer, retries=3, token=None):
        fetched_names.append(source.name)
        if source.name == "gadget":
            return _Fetched("extra:\n  recipe-maintainers: [bob, dave]\n")
        if source.name == "thingamajig":
            return _Fetched("extra:\n  recipe-maintainers: [erin]\n")
        raise AssertionError(f"should not fetch unchanged feedstock {source.name}")

    monkeypatch.setattr(mh, "fetch_recipe", fake_fetch_recipe)

    new_state = _build_snapshot(previous, date(2024, 2, 29))

    assert set(fetched_names) == {"gadget", "thingamajig"}  # widget carried forward, unfetched
    assert new_state.maintainers_by_feedstock == {
        "widget": ["alice"],  # carried forward unchanged
        "gadget": ["bob", "dave"],  # re-fetched (changed sha)
        "thingamajig": ["erin"],  # newly added
        # gizmo dropped entirely (removed from current_shas)
    }
    assert new_state.submodule_shas == current_shas
    assert new_state.last_snapshot_date == "2024-02-29"


def test_build_snapshot_returns_none_before_history_starts(monkeypatch):
    monkeypatch.setattr(mh, "resolve_commit_at", lambda *a, **k: None)

    result = _build_snapshot(mh.MaintainerHistoryState.empty(), date(2010, 1, 31))

    assert result is None


def test_build_snapshot_treats_removed_feedstock_recipe_as_empty(monkeypatch):
    """A feedstock whose path exists in `added`/`changed` but 404s on all recipe paths (e.g. it
    was archived) contributes no maintainers rather than raising."""
    previous = mh.MaintainerHistoryState.empty()
    monkeypatch.setattr(mh, "resolve_commit_at", lambda *a, **k: "commit-sha")
    monkeypatch.setattr(mh, "fetch_submodule_tree", lambda *a, **k: {"widget": "sha1"})
    monkeypatch.setattr(mh, "fetch_gitmodules", lambda ref: _gitmodules_text("widget"))

    async def fake_fetch_recipe(client, source, cooldown, pacer, retries=3, token=None):
        return None

    monkeypatch.setattr(mh, "fetch_recipe", fake_fetch_recipe)

    new_state = _build_snapshot(previous, date(2024, 1, 31))
    assert new_state.maintainers_by_feedstock == {"widget": []}


def test_build_snapshot_keeps_prior_maintainers_on_fetch_error(monkeypatch):
    previous = _state(shas={"widget": "sha1"}, maintainers={"widget": ["alice"]})
    monkeypatch.setattr(mh, "resolve_commit_at", lambda *a, **k: "commit-sha")
    monkeypatch.setattr(mh, "fetch_submodule_tree", lambda *a, **k: {"widget": "sha2"})
    monkeypatch.setattr(mh, "fetch_gitmodules", lambda ref: _gitmodules_text("widget"))

    async def fake_fetch_recipe(client, source, cooldown, pacer, retries=3, token=None):
        raise FetchError("boom")

    monkeypatch.setattr(mh, "fetch_recipe", fake_fetch_recipe)

    new_state = _build_snapshot(previous, date(2024, 2, 29))
    assert new_state.maintainers_by_feedstock == {"widget": ["alice"]}


def test_build_snapshot_handles_parse_error_gracefully(monkeypatch):
    previous = mh.MaintainerHistoryState.empty()
    monkeypatch.setattr(mh, "resolve_commit_at", lambda *a, **k: "commit-sha")
    monkeypatch.setattr(mh, "fetch_submodule_tree", lambda *a, **k: {"widget": "sha1"})
    monkeypatch.setattr(mh, "fetch_gitmodules", lambda ref: _gitmodules_text("widget"))

    class _Fetched:
        filename = "meta.yaml"
        text = "extra: [unterminated\n"

    async def fake_fetch_recipe(client, source, cooldown, pacer, retries=3, token=None):
        return _Fetched()

    monkeypatch.setattr(mh, "fetch_recipe", fake_fetch_recipe)

    new_state = _build_snapshot(previous, date(2024, 1, 31))
    assert new_state.maintainers_by_feedstock == {"widget": []}


# --- unique_maintainer_count -----------------------------------------------------------------


def test_unique_maintainer_count_deduplicates_across_feedstocks():
    state = _state(
        shas={},
        maintainers={"widget": ["alice", "bob"], "gadget": ["bob", "carol"]},
    )
    assert mh.unique_maintainer_count(state) == 3  # alice, bob, carol


# --- append_current_point ----------------------------------------------------------------


def test_append_current_point_adds_new_entry_for_new_month():
    history = [{"date": "2024-01-31", "unique_maintainer_count": 10}]
    maintainers = {"widget": ["alice", "bob"]}

    result = mh.append_current_point(maintainers, history, date(2024, 2, 15))

    assert result == [
        {"date": "2024-01-31", "unique_maintainer_count": 10},
        {"date": "2024-02-15", "unique_maintainer_count": 2},
    ]


def test_append_current_point_replaces_entry_in_same_month():
    history = [
        {"date": "2024-01-31", "unique_maintainer_count": 10},
        {"date": "2024-02-15", "unique_maintainer_count": 2},
    ]
    maintainers = {"widget": ["alice", "bob", "carol"]}

    result = mh.append_current_point(maintainers, history, date(2024, 2, 20))

    assert result == [
        {"date": "2024-01-31", "unique_maintainer_count": 10},
        {"date": "2024-02-20", "unique_maintainer_count": 3},
    ]


def test_append_current_point_force_always_appends():
    history = [{"date": "2024-02-15", "unique_maintainer_count": 2}]
    maintainers = {"widget": ["alice"]}

    result = mh.append_current_point(maintainers, history, date(2024, 2, 20), force=True)

    assert len(result) == 2
    assert result[-1] == {"date": "2024-02-20", "unique_maintainer_count": 1}


def test_append_current_point_empty_history_appends_first_entry():
    result = mh.append_current_point({"widget": ["alice"]}, [], date(2024, 1, 1))
    assert result == [{"date": "2024-01-01", "unique_maintainer_count": 1}]


# --- MaintainerHistoryState persistence ---------------------------------------------------


def test_state_round_trips_through_save_and_load(tmp_path):
    state = _state(shas={"widget": "sha1"}, maintainers={"widget": ["alice"]})
    state = mh.MaintainerHistoryState(
        last_snapshot_date="2024-01-31",
        submodule_shas=state.submodule_shas,
        maintainers_by_feedstock=state.maintainers_by_feedstock,
    )
    path = tmp_path / "state.json"

    state.save(path)
    loaded = mh.MaintainerHistoryState.load(path)

    assert loaded == state


def test_state_load_missing_file_returns_empty(tmp_path):
    loaded = mh.MaintainerHistoryState.load(tmp_path / "missing.json")
    assert loaded == mh.MaintainerHistoryState.empty()
