"""Tests for rate-limit-aware fetching: retry/backoff, shared cooldown, Contents API.

All network access is mocked via httpx.MockTransport -- no real requests, no token needed.
"""

from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from feedstock_maintainers.github import (
    Cooldown,
    FetchError,
    RatePacer,
    _get_with_retries,
    fetch_recipe,
    fetch_updated_feedstocks,
    fetch_user_info,
)
from feedstock_maintainers.gitmodules import FeedstockSource


def _source(name: str = "widget") -> FeedstockSource:
    return FeedstockSource(name=name, owner="conda-forge", repo=f"{name}-feedstock", branch="main")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Cooldown / RatePacer primitives -----------------------------------------------------


def test_cooldown_gates_until_deadline():
    async def run():
        cooldown = Cooldown()
        await cooldown.set_from_retry_after(0.1)
        start = time.monotonic()
        await cooldown.wait()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert elapsed >= 0.09


def test_cooldown_only_extends_never_shortens():
    async def run():
        cooldown = Cooldown()
        await cooldown.set_from_retry_after(0.2)
        await cooldown.set_from_retry_after(0.05)  # shorter -- must not shrink the deadline
        start = time.monotonic()
        await cooldown.wait()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert elapsed >= 0.18


def test_cooldown_shared_across_concurrent_waiters():
    """One worker's discovery of a rate limit must pause every other worker too."""

    async def run():
        cooldown = Cooldown()
        await cooldown.set_from_retry_after(0.1)
        start = time.monotonic()
        elapsed = await asyncio.gather(*(_timed_wait(cooldown, start) for _ in range(5)))
        return elapsed

    async def _timed_wait(cooldown, start):
        await cooldown.wait()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert all(e >= 0.09 for e in elapsed)


def test_rate_pacer_spaces_out_requests():
    async def run():
        pacer = RatePacer(rate=20.0)  # 1 request per 0.05s
        start = time.monotonic()
        await pacer.acquire()
        await pacer.acquire()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert elapsed >= 0.04


def test_rate_pacer_disabled_when_rate_zero():
    async def run():
        pacer = RatePacer(rate=0)
        start = time.monotonic()
        await pacer.acquire()
        await pacer.acquire()
        await pacer.acquire()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert elapsed < 0.05


# --- _get_with_retries: header-driven retry behavior -------------------------------------


def test_retry_after_header_then_success():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0.05"})
        return httpx.Response(200, text="ok")

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            response = await _get_with_retries(
                client, "https://example/test", retries=2, cooldown=cooldown, pacer=pacer
            )
        return response

    response = asyncio.run(run())
    assert response is not None
    assert response.text == "ok"
    assert len(calls) == 2


def test_rate_limit_remaining_zero_waits_for_reset():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            reset = time.time() + 0.05
            return httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
            )
        return httpx.Response(200, text="ok")

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        start = time.monotonic()
        async with _client(handler) as client:
            response = await _get_with_retries(
                client, "https://example/test", retries=2, cooldown=cooldown, pacer=pacer
            )
        return response, time.monotonic() - start

    response, elapsed = asyncio.run(run())
    assert response is not None
    assert response.text == "ok"
    assert len(calls) == 2
    assert elapsed >= 0.04


def test_transient_5xx_falls_back_to_backoff_then_succeeds():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await _get_with_retries(
                client, "https://example/test", retries=3, cooldown=cooldown, pacer=pacer
            )

    response = asyncio.run(run())
    assert response is not None
    assert response.text == "ok"
    assert len(calls) == 3


def test_404_returns_none_without_raising():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await _get_with_retries(
                client, "https://example/test", retries=1, cooldown=cooldown, pacer=pacer
            )

    assert asyncio.run(run()) is None


def test_exhausted_retries_raises_fetch_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            await _get_with_retries(
                client, "https://example/test", retries=1, cooldown=cooldown, pacer=pacer
            )

    with pytest.raises(FetchError):
        asyncio.run(run())


# --- fetch_recipe: candidate-path fallback + Contents API ---------------------------------


def test_fetch_recipe_404_on_all_paths_returns_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await fetch_recipe(client, _source(), cooldown, pacer, retries=0)

    assert asyncio.run(run()) is None


def test_fetch_recipe_falls_back_to_second_path():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("recipe.yaml"):
            return httpx.Response(404)
        if request.url.path.endswith("meta.yaml"):
            return httpx.Response(200, text="extra:\n  recipe-maintainers: [alice]\n")
        return httpx.Response(404)

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await fetch_recipe(client, _source(), cooldown, pacer, retries=0)

    result = asyncio.run(run())
    assert result is not None
    assert result.filename == "meta.yaml"
    assert "alice" in result.text


def test_fetch_recipe_via_contents_api_decodes_base64_and_sends_auth_header():
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["authorization"] = request.headers.get("authorization")
        if not request.url.path.endswith("recipe.yaml"):
            return httpx.Response(404)
        content = base64.b64encode(b"extra:\n  recipe-maintainers: [bob]\n").decode()
        return httpx.Response(200, json={"encoding": "base64", "content": content})

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await fetch_recipe(
                client, _source(), cooldown, pacer, retries=0, token="secret-token"
            )

    result = asyncio.run(run())
    assert result is not None
    assert "bob" in result.text
    assert seen_headers["authorization"] == "Bearer secret-token"


# --- fetch_user_info: Users API -----------------------------------------------------------


def test_fetch_user_info_returns_profile_json():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/alice"
        return httpx.Response(200, json={"login": "alice", "id": 1})

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await fetch_user_info(client, "alice", cooldown, pacer, retries=0)

    result = asyncio.run(run())
    assert result == {"login": "alice", "id": 1}


def test_fetch_user_info_404_returns_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            return await fetch_user_info(client, "ghost", cooldown, pacer, retries=0)

    assert asyncio.run(run()) is None


def test_fetch_user_info_sends_auth_header_only_when_token_given():
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"login": "alice"})

    async def run():
        cooldown, pacer = Cooldown(), RatePacer(rate=0)
        async with _client(handler) as client:
            await fetch_user_info(client, "alice", cooldown, pacer, retries=0, token="secret-token")

    asyncio.run(run())
    assert seen_headers["authorization"] == "Bearer secret-token"


# --- fetch_updated_feedstocks: GraphQL commit-history pagination --------------------------


def _commit(message: str) -> dict:
    return {"oid": "abc", "message": message, "committedDate": "2026-08-01T00:00:00Z"}


def _page(nodes: list[dict], has_next_page: bool, end_cursor: str | None = None) -> dict:
    return {
        "data": {
            "repository": {
                "ref": {
                    "target": {
                        "history": {
                            "nodes": nodes,
                            "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                        }
                    }
                }
            }
        }
    }


def test_fetch_updated_feedstocks_parses_commit_messages(monkeypatch):
    page = _page(
        [
            _commit("Updated the widget-feedstock feedstock."),
            _commit("Merge pull request #123 from conda-forge/regro-cf-autotick-bot"),
            _commit("Updated the gadget-feedstock feedstock."),
        ],
        has_next_page=False,
    )

    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json=page))

    result = fetch_updated_feedstocks("2026-08-01T00:00:00Z")

    assert result == {"widget-feedstock", "gadget-feedstock"}


def test_fetch_updated_feedstocks_paginates(monkeypatch):
    page_one = _page([_commit("Updated the widget-feedstock feedstock.")], True, "cursor-1")
    page_two = _page([_commit("Updated the gadget-feedstock feedstock.")], False)

    seen_cursors = []

    def fake_post(url, json=None, headers=None):
        variables = json["variables"]
        seen_cursors.append(variables["cursor"])
        return httpx.Response(200, json=page_one if variables["cursor"] is None else page_two)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = fetch_updated_feedstocks("2026-08-01T00:00:00Z")

    assert result == {"widget-feedstock", "gadget-feedstock"}
    assert seen_cursors == [None, "cursor-1"]


def test_fetch_updated_feedstocks_sends_auth_header_only_when_token_given(monkeypatch):
    page = _page([], has_next_page=False)
    seen_headers = {}

    def fake_post(url, json=None, headers=None):
        seen_headers["authorization"] = headers.get("Authorization")
        return httpx.Response(200, json=page)

    monkeypatch.setattr(httpx, "post", fake_post)
    fetch_updated_feedstocks("2026-08-01T00:00:00Z", token="secret-token")

    assert seen_headers["authorization"] == "Bearer secret-token"


def test_fetch_updated_feedstocks_reports_progress_for_every_page_including_last(monkeypatch):
    page_one = _page([_commit("Updated the widget-feedstock feedstock.")], True, "cursor-1")
    page_two = _page([_commit("Updated the gadget-feedstock feedstock.")], False)

    def fake_post(url, json=None, headers=None):
        variables = json["variables"]
        return httpx.Response(200, json=page_one if variables["cursor"] is None else page_two)

    monkeypatch.setattr(httpx, "post", fake_post)

    steps: list[str] = []
    fetch_updated_feedstocks("2026-08-01T00:00:00Z", on_step=steps.append)

    assert len(steps) == 2


def test_fetch_updated_feedstocks_retries_transient_errors_then_succeeds(monkeypatch):
    page = _page([_commit("Updated the widget-feedstock feedstock.")], has_next_page=False)
    calls = []

    def fake_post(url, json=None, headers=None):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=page)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    result = fetch_updated_feedstocks("2026-08-01T00:00:00Z", retries=1)

    assert result == {"widget-feedstock"}
    assert len(calls) == 2
