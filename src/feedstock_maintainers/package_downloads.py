"""Fetch conda-forge package download counts, aggregated by month, from Anaconda Package Data.

Each month's pre-aggregated parquet file is downloaded anonymously (no AWS credentials or request
signing needed -- the anaconda-package-data S3 bucket is publicly readable over plain HTTPS) from
https://anaconda-package-data.s3.amazonaws.com/conda/monthly/{year}/{year}-{month:02d}.parquet,
filtered to a single data source (default "conda-forge"), and grouped by pkg_name to collapse
across pkg_version/pkg_platform/pkg_python. Months are fetched concurrently.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pyarrow.compute as pc
import pyarrow.parquet as pq

_MONTHLY_URL = (
    "https://anaconda-package-data.s3.amazonaws.com/conda/monthly/{year}/{year}-{month:02d}.parquet"
)
_DEFAULT_DATA_SOURCE = "conda-forge"
_PARQUET_COLUMNS = ["data_source", "pkg_name", "counts"]


class PackageDownloadsFetchError(Exception):
    """Raised when a month's parquet file couldn't be downloaded or parsed."""


def trailing_months(end: date, months: int) -> list[date]:
    """Return the first-of-month date for each of the `months` complete calendar months ending
    with `end`'s month, oldest first.

    E.g. trailing_months(date(2026, 9, 20), 3) -> [date(2026, 7, 1), date(2026, 8, 1),
    date(2026, 9, 1)] -- the month `end` falls in is included even though it's likely incomplete
    in the underlying dataset (the most recent monthly parquet file is still accumulating
    downloads throughout the month), so callers should treat the last entry as provisional.
    """
    year, month = end.year, end.month
    result = []
    for _ in range(months):
        result.append(date(year, month, 1))
        month -= 1
        if month < 1:
            month, year = 12, year - 1
    return list(reversed(result))


def aggregate_month(
    parquet_bytes: bytes, data_source: str = _DEFAULT_DATA_SOURCE
) -> dict[str, int]:
    """Return {pkg_name: total_downloads} for one month's parquet bytes.

    Filtered to `data_source` and summed across pkg_version/pkg_platform/pkg_python.
    """
    table = pq.read_table(io.BytesIO(parquet_bytes), columns=_PARQUET_COLUMNS)
    filtered = table.filter(pc.equal(table["data_source"], data_source))
    grouped = filtered.group_by("pkg_name").aggregate([("counts", "sum")])
    names = grouped["pkg_name"].to_pylist()
    totals = grouped["counts_sum"].to_pylist()
    return {name: int(total) for name, total in zip(names, totals, strict=True)}


async def fetch_month_parquet(
    month: date,
    client: httpx.AsyncClient,
    cache_dir: Path | None = None,
    force: bool = False,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> bytes | None:
    """Download one month's monthly parquet file as raw bytes, or None on 404.

    A 404 means the month is outside the dataset's actual coverage (e.g. before 2017, or a future
    month with no file published yet) -- not an error.

    If `cache_dir` is given, a previously-downloaded month is read from
    `{cache_dir}/{year}-{month:02d}.parquet` instead of re-fetching, unless `force` is True.
    Successful downloads are always written into `cache_dir` when it's given.
    """
    progress = on_progress or (lambda _downloaded, _total: None)
    cache_path = (
        cache_dir / f"{month.year}-{month.month:02d}.parquet" if cache_dir is not None else None
    )

    if cache_path is not None and not force and cache_path.exists():
        cached = cache_path.read_bytes()
        progress(len(cached), len(cached))
        return cached

    url = _MONTHLY_URL.format(year=month.year, month=month.month)

    try:
        async with client.stream("GET", url) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length is not None else None
            downloaded_bytes = bytearray()
            downloaded = 0
            progress(downloaded, total_bytes)
            async for chunk in response.aiter_bytes():
                downloaded_bytes.extend(chunk)
                downloaded += len(chunk)
                progress(downloaded, total_bytes)
    except httpx.HTTPError as exc:
        raise PackageDownloadsFetchError(
            f"{month.year}-{month.month:02d}: failed to download {url}: {exc}"
        ) from exc

    data = bytes(downloaded_bytes)

    if cache_path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(cache_path)

    return data


async def fetch_monthly_downloads(
    months: list[date],
    timeout: float,
    data_source: str = _DEFAULT_DATA_SOURCE,
    cache_dir: Path | None = None,
    force: bool = False,
    on_progress: Callable[[date, int, int | None], None] | None = None,
) -> dict[str, dict[str, int]]:
    """Fetch and aggregate each of `months` concurrently.

    Returns {pkg_name: {"YYYY-MM": total_downloads}}. Months whose parquet file 404s (outside the
    dataset's coverage) are silently skipped -- not an error.
    """
    progress = on_progress or (lambda _month, _downloaded, _total: None)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def bound(month: date) -> tuple[date, bytes | None]:
            data = await fetch_month_parquet(
                month,
                client,
                cache_dir=cache_dir,
                force=force,
                on_progress=lambda d, t: progress(month, d, t),
            )
            return month, data

        results = await asyncio.gather(*(bound(month) for month in months))

    merged: dict[str, dict[str, int]] = {}
    for month, parquet_bytes in results:
        if parquet_bytes is None:
            continue
        key = f"{month.year}-{month.month:02d}"
        for pkg_name, total in aggregate_month(parquet_bytes, data_source=data_source).items():
            merged.setdefault(pkg_name, {})[key] = total
    return merged
