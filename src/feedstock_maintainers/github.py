"""Fetch just the recipe file for a feedstock straight from GitHub.

Avoids checking out any submodules: for each feedstock we hit either
raw.githubusercontent.com (anonymous) or the authenticated Contents API
(when a token is supplied) for the exact path (recipe/recipe.yaml or
recipe/meta.yaml) on the branch recorded in .gitmodules.

Both paths share one retry/backoff core built from two coordination
primitives:

- Cooldown: a reactive, shared "pause every worker until T" gate, set from
  Retry-After / X-RateLimit-Reset response headers. One worker discovering
  a rate limit pauses everyone, instead of each retrying independently into
  the same wall.
- RatePacer: a proactive, steady-state max-requests/second gate, so we
  generally avoid tripping the limit in the first place.
"""

from __future__ import annotations

import asyncio
import base64
import random
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .gitmodules import FeedstockSource

_RECIPE_PATHS = ("recipe/recipe.yaml", "recipe/meta.yaml", "recipe/meta.yml")
_RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
_CONTENTS_URL = "https://api.github.com/repos/{owner}/{repo}/contents/{path}"
_USER_URL = "https://api.github.com/users/{username}"
_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class FetchedRecipe:
    filename: str
    text: str


class FetchError(Exception):
    """Raised when a recipe couldn't be retrieved for a reason other than 404."""


class Cooldown:
    """Shared 'pause all workers until this time' gate, coordinated across tasks."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._resume_at: float = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._resume_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    async def set_from_retry_after(self, seconds: float) -> None:
        await self._bump(time.monotonic() + seconds)

    async def set_from_reset_epoch(self, reset_epoch: float) -> None:
        delay = max(0.0, reset_epoch - time.time()) + 1.0
        await self._bump(time.monotonic() + delay)

    async def _bump(self, candidate_resume_at: float) -> None:
        async with self._lock:
            self._resume_at = max(self._resume_at, candidate_resume_at)


class RatePacer:
    """Caps steady-state throughput to `rate` requests/second across all workers.

    Independent of Cooldown: Cooldown reacts to being told to back off,
    RatePacer just spaces requests out so we rarely get told that in the
    first place. rate <= 0 disables pacing entirely.
    """

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate if rate and rate > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_slot: float = 0.0

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self._interval
            delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)


async def _note_rate_limit(response: httpx.Response, cooldown: Cooldown) -> None:
    """Update the shared cooldown from whatever rate-limit signal a response carries."""
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            await cooldown.set_from_retry_after(float(retry_after))
            return
        except ValueError:
            pass  # some servers send an HTTP-date instead of seconds; ignore, fall through

    remaining = response.headers.get("x-ratelimit-remaining")
    reset = response.headers.get("x-ratelimit-reset")
    if remaining == "0" and reset is not None:
        try:
            await cooldown.set_from_reset_epoch(float(reset))
        except ValueError:
            pass


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> Optional[httpx.Response]:
    """Return the response, None on 404, or raise FetchError after exhausting retries."""
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        await cooldown.wait()
        await pacer.acquire()
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            last_exc = exc
        else:
            if response.status_code == 200:
                return response
            if response.status_code == 404:
                return None
            if response.status_code in (403, 429):
                await _note_rate_limit(response, cooldown)
                last_exc = FetchError(f"HTTP {response.status_code} for {url}")
            elif response.status_code >= 500:
                last_exc = FetchError(f"HTTP {response.status_code} for {url}")
            else:
                raise FetchError(f"HTTP {response.status_code} for {url}")

        if attempt < retries:
            base = 0.5 * (2**attempt)
            await asyncio.sleep(base + random.uniform(0, base * 0.5))

    raise FetchError(str(last_exc) if last_exc else f"failed to fetch {url}")


async def _fetch_via_raw(
    client: httpx.AsyncClient,
    source: FeedstockSource,
    path: str,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
) -> Optional[str]:
    url = _RAW_URL.format(owner=source.owner, repo=source.repo, branch=source.branch, path=path)
    response = await _get_with_retries(client, url, retries, cooldown, pacer)
    return response.text if response is not None else None


async def _fetch_via_contents_api(
    client: httpx.AsyncClient,
    source: FeedstockSource,
    path: str,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str,
) -> Optional[str]:
    url = _CONTENTS_URL.format(owner=source.owner, repo=source.repo, path=path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    response = await _get_with_retries(
        client, url, retries, cooldown, pacer, params={"ref": source.branch}, headers=headers
    )
    if response is None:
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(f"non-JSON response from Contents API for {url}") from exc

    # Recipe files are always small text files, well under the Contents API's
    # 1 MB inline-content limit, so we don't need to handle "encoding": "none".
    if payload.get("encoding") != "base64" or "content" not in payload:
        raise FetchError(f"unexpected Contents API payload shape for {url}")

    return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")


async def fetch_recipe(
    client: httpx.AsyncClient,
    source: FeedstockSource,
    cooldown: Cooldown,
    pacer: RatePacer,
    retries: int = 3,
    token: Optional[str] = None,
) -> Optional[FetchedRecipe]:
    """Return the first recipe file found for a feedstock, or None if all paths 404."""
    last_error: Optional[FetchError] = None

    for path in _RECIPE_PATHS:
        try:
            if token:
                text = await _fetch_via_contents_api(client, source, path, retries, cooldown, pacer, token)
            else:
                text = await _fetch_via_raw(client, source, path, retries, cooldown, pacer)
        except FetchError as exc:
            last_error = exc
            continue
        if text is not None:
            return FetchedRecipe(filename=path.rsplit("/", 1)[-1], text=text)

    if last_error is not None:
        raise last_error
    return None


async def fetch_user_info(
    client: httpx.AsyncClient,
    username: str,
    cooldown: Cooldown,
    pacer: RatePacer,
    retries: int = 3,
    token: Optional[str] = None,
) -> Optional[dict]:
    """Return the GitHub user object for `username`, or None if the account is missing (404)."""
    url = _USER_URL.format(username=username)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = await _get_with_retries(client, url, retries, cooldown, pacer, headers=headers)
    if response is None:
        return None

    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"non-JSON response from Users API for {url}") from exc
