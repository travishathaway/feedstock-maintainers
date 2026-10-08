"""Fetch conda-forge repodata.json.zst for one or more platforms, straight from the channel.

Each platform's `repodata.json.zst` (the zstd-compressed conda channel index) is downloaded from
`https://conda.anaconda.org/conda-forge/<platform>/repodata.json.zst` and decompressed in memory
-- no local file needed. Platforms are fetched concurrently.

Optionally, the compressed bytes (plus the response ETag) are cached on disk so that several
commands run in sequence share one download: a cache entry younger than `max_age` seconds is used
as-is, and an older one is revalidated with `If-None-Match` (a 304 reuses it).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx2
import zstandard

_REPODATA_URL = "https://conda.anaconda.org/conda-forge/{platform}/repodata.json.zst"


DEFAULT_MAX_AGE_SECONDS = 3600


class RepodataFetchError(Exception):
    """Raised when a platform's repodata.json.zst couldn't be downloaded or decompressed."""


async def fetch_repodata(
    platform: str,
    client: httpx2.AsyncClient,
    on_progress: Callable[[int, int | None], None] | None = None,
    cache_dir: Path | None = None,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
) -> dict:
    """Download and decompress conda-forge's repodata.json.zst for a single platform.

    If given, `on_progress` is called with (bytes_downloaded, total_bytes_or_none) as each chunk
    arrives -- intended for driving a per-platform progress bar. `total_bytes` is None if the
    server didn't send a Content-Length header.

    If `cache_dir` is given, `<cache_dir>/<platform>.json.zst` (and a `.etag` sidecar) is used as
    described in the module docstring.
    """
    progress = on_progress or (lambda _downloaded, _total: None)
    url = _REPODATA_URL.format(platform=platform)

    cache_file = cache_dir / f"{platform}.json.zst" if cache_dir else None
    etag_file = cache_dir / f"{platform}.etag" if cache_dir else None
    headers: dict[str, str] = {}
    if cache_file is not None and cache_file.exists():
        if time.time() - cache_file.stat().st_mtime < max_age:
            return _decompress(platform, cache_file.read_bytes())
        if etag_file is not None and etag_file.exists():
            headers["If-None-Match"] = etag_file.read_text(encoding="utf-8").strip()

    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code == 304 and cache_file is not None:
                cache_file.touch()
                return _decompress(platform, cache_file.read_bytes())
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
    except httpx2.HTTPError as exc:
        raise RepodataFetchError(f"{platform}: failed to download {url}: {exc}") from exc

    result = _decompress(platform, bytes(compressed))
    if cache_file is not None and etag_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_bytes(bytes(compressed))
        tmp.replace(cache_file)
        etag = response.headers.get("ETag")
        if etag:
            etag_file.write_text(etag, encoding="utf-8")
        else:
            etag_file.unlink(missing_ok=True)
    return result


def _decompress(platform: str, compressed: bytes) -> dict:
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
            decompressed = reader.read()
    except zstandard.ZstdError as exc:
        raise RepodataFetchError(f"{platform}: failed to decompress repodata: {exc}") from exc
    return json.loads(decompressed)


async def fetch_all_repodata(
    platforms: list[str],
    timeout: float,
    on_progress: Callable[[str, int, int | None], None] | None = None,
    cache_dir: Path | None = None,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, dict]:
    """Download repodata.json.zst for each of `platforms` concurrently.

    Returns {platform: parsed repodata}. If given, `on_progress` is called for each platform as
    `fetch_repodata` describes, with the platform name as the first argument.
    """
    progress = on_progress or (lambda _platform, _downloaded, _total: None)

    async with httpx2.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def bound(platform: str) -> dict:
            return await fetch_repodata(
                platform,
                client,
                on_progress=lambda d, t: progress(platform, d, t),
                cache_dir=cache_dir,
                max_age=max_age,
            )

        results = await asyncio.gather(*(bound(platform) for platform in platforms))

    return dict(zip(platforms, results, strict=True))
