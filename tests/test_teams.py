import asyncio
import json

import httpx

from feedstock_maintainers import teams
from feedstock_maintainers.github import Cooldown, RatePacer


def test_team_handles_in_collects_distinct_sorted_handles():
    maintainers = {
        "a": ["alice", "conda-forge/r"],
        "b": ["conda-forge/r", "conda-forge/cuda"],
        "c": [],
    }
    assert teams.team_handles_in(maintainers) == ["conda-forge/cuda", "conda-forge/r"]


def test_expand_team_handles_keeps_the_handle_and_adds_members():
    expanded = teams.expand_team_handles(
        ["conda-forge/r"], {"conda-forge/r": ["daler", "mfansler"]}
    )
    assert expanded == ["conda-forge/r", "daler", "mfansler"]


def test_expand_team_handles_dedupes_members_already_listed():
    expanded = teams.expand_team_handles(
        ["mfansler", "conda-forge/r", "alice"], {"conda-forge/r": ["daler", "mfansler"]}
    )
    assert expanded == ["mfansler", "conda-forge/r", "alice", "daler"]


def test_expand_team_handles_leaves_unknown_teams_and_plain_users_alone():
    assert teams.expand_team_handles(["conda-forge/x", "bob"], {}) == ["conda-forge/x", "bob"]


def test_expand_maintainers_returns_input_when_no_teams_known():
    maintainers = {"a": ["conda-forge/r"]}
    assert teams.expand_maintainers(maintainers, {}) is maintainers


def test_expand_maintainers_expands_every_feedstock():
    result = teams.expand_maintainers(
        {"r-munsell": ["conda-forge/r"], "numpy": ["alice"]}, {"conda-forge/r": ["daler"]}
    )
    assert result == {"r-munsell": ["conda-forge/r", "daler"], "numpy": ["alice"]}


def test_load_team_members_tolerates_missing_and_corrupt_files(tmp_path):
    assert teams.load_team_members(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert teams.load_team_members(bad) == {}
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"conda-forge/r": ["a"], "junk": "x"}))
    assert teams.load_team_members(good) == {"conda-forge/r": ["a"]}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_team_members_paginates_and_sorts():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        users = [{"login": f"u{page}-{i:03d}"} for i in range(100 if page == 1 else 3)]
        return httpx.Response(200, json=users)

    async def run():
        async with _client(handler) as client:
            return await teams.fetch_team_members(
                client, "conda-forge/r", Cooldown(), RatePacer(0), None
            )

    members = asyncio.run(run())
    assert members is not None
    assert len(members) == 103
    assert members == sorted(members)


def test_fetch_team_members_returns_none_for_missing_team():
    async def run():
        async with _client(lambda request: httpx.Response(404)) as client:
            return await teams.fetch_team_members(
                client, "conda-forge/gone", Cooldown(), RatePacer(0), None
            )

    assert asyncio.run(run()) is None
