"""Fetch conda-forge repodata.json.zst for one or more platforms, straight from the channel.

Each platform's `repodata.json.zst` (the zstd-compressed conda channel index) is downloaded from
`https://conda.anaconda.org/conda-forge/<platform>/repodata.json.zst` and decompressed in memory
-- no local file needed. Platforms are fetched concurrently.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable

import httpx
import zstandard

_REPODATA_URL = "https://conda.anaconda.org/conda-forge/{platform}/repodata.json.zst"


class RepodataFetchError(Exception):
    """Raised when a platform's repodata.json.zst couldn't be downloaded or decompressed."""


async def fetch_repodata(
    platform: str,
    client: httpx.AsyncClient,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    """Download and decompress conda-forge's repodata.json.zst for a single platform.

    If given, `on_progress` is called with (bytes_downloaded, total_bytes_or_none) as each chunk
    arrives -- intended for driving a per-platform progress bar. `total_bytes` is None if the
    server didn't send a Content-Length header.
    """
    progress = on_progress or (lambda _downloaded, _total: None)
    url = _REPODATA_URL.format(platform=platform)

    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length is not None else None
            compressed = bytearray()
            downloaded = 0
            progress(downloaded, total_bytes)
            async for chunk in response.aiter_bytes():
                compressed.extend(chunk)
                downloaded += len(chunk)
                progress(downloaded, total_bytes)
    except httpx.HTTPError as exc:
        raise RepodataFetchError(f"{platform}: failed to download {url}: {exc}") from exc

    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(bytes(compressed))) as reader:
            decompressed = reader.read()
    except zstandard.ZstdError as exc:
        raise RepodataFetchError(f"{platform}: failed to decompress repodata: {exc}") from exc

    return json.loads(decompressed)


async def fetch_all_repodata(
    platforms: list[str],
    timeout: float,
    on_progress: Callable[[str, int, int | None], None] | None = None,
) -> dict[str, dict]:
    """Download repodata.json.zst for each of `platforms` concurrently.

    Returns {platform: parsed repodata}. If given, `on_progress` is called for each platform as
    `fetch_repodata` describes, with the platform name as the first argument.
    """
    progress = on_progress or (lambda _platform, _downloaded, _total: None)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def bound(platform: str) -> dict:
            return await fetch_repodata(
                platform, client, on_progress=lambda d, t: progress(platform, d, t)
            )

        results = await asyncio.gather(*(bound(platform) for platform in platforms))

    return dict(zip(platforms, results, strict=True))
