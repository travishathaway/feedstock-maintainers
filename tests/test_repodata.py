"""Tests for the on-disk repodata cache in `repodata.fetch_repodata` (no real network)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx2
import pytest
import zstandard

from feedstock_maintainers.repodata import fetch_repodata

PAYLOAD = {"packages": {}, "packages.conda": {"a-1-0.conda": {"name": "a", "depends": []}}}


def _compressed() -> bytes:
    return zstandard.ZstdCompressor().compress(json.dumps(PAYLOAD).encode())


def _client(handler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


async def _fetch(handler, tmp_path: Path, **kwargs) -> dict:
    async with _client(handler) as client:
        return await fetch_repodata("linux-64", client, cache_dir=tmp_path, **kwargs)


def test_cache_miss_downloads_and_writes_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, content=_compressed(), headers={"ETag": '"abc"'})

    assert _run(_fetch(handler, tmp_path)) == PAYLOAD
    assert len(calls) == 1
    assert (tmp_path / "linux-64.json.zst").exists()
    assert (tmp_path / "linux-64.etag").read_text() == '"abc"'


def test_fresh_cache_skips_network(tmp_path):
    (tmp_path / "linux-64.json.zst").write_bytes(_compressed())

    def handler(request):
        pytest.fail("network should not be used for a fresh cache entry")

    assert _run(_fetch(handler, tmp_path)) == PAYLOAD


def test_stale_cache_revalidates_with_etag(tmp_path):
    cache_file = tmp_path / "linux-64.json.zst"
    cache_file.write_bytes(_compressed())
    (tmp_path / "linux-64.etag").write_text('"abc"')
    old = time.time() - 7200
    os.utime(cache_file, (old, old))
    seen = {}

    def handler(request):
        seen["if_none_match"] = request.headers.get("If-None-Match")
        return httpx2.Response(304)

    assert _run(_fetch(handler, tmp_path)) == PAYLOAD
    assert seen["if_none_match"] == '"abc"'
    assert time.time() - cache_file.stat().st_mtime < 60
