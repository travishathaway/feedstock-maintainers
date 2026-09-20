"""Tests for the Anaconda Package Data monthly download-stats fetch/aggregation logic.

Network access is monkeypatched via httpx.MockTransport throughout -- no real requests.
"""

from __future__ import annotations

import asyncio
import io
from datetime import date

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from feedstock_maintainers import package_downloads as pd


def _make_parquet(rows: list[dict]) -> bytes:
    table = pa.table(
        {
            "data_source": [r["data_source"] for r in rows],
            "pkg_name": [r["pkg_name"] for r in rows],
            "pkg_version": [r.get("pkg_version", "1.0") for r in rows],
            "pkg_platform": [r.get("pkg_platform", "linux-64") for r in rows],
            "pkg_python": [r.get("pkg_python", "3.10") for r in rows],
            "counts": [r["counts"] for r in rows],
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


# --- trailing_months -------------------------------------------------------------------------


def test_trailing_months_returns_first_of_month_for_each_trailing_month():
    months = pd.trailing_months(date(2026, 9, 20), 3)
    assert months == [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]


def test_trailing_months_handles_year_rollover():
    months = pd.trailing_months(date(2026, 2, 1), 3)
    assert months == [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]


def test_trailing_months_single_month():
    months = pd.trailing_months(date(2026, 9, 20), 1)
    assert months == [date(2026, 9, 1)]


# --- aggregate_month --------------------------------------------------------------------------


def test_aggregate_month_filters_by_data_source_and_sums_across_variants():
    parquet_bytes = _make_parquet(
        [
            {"data_source": "conda-forge", "pkg_name": "numpy", "pkg_version": "1.0", "counts": 5},
            {"data_source": "conda-forge", "pkg_name": "numpy", "pkg_version": "2.0", "counts": 7},
            {"data_source": "conda-forge", "pkg_name": "scipy", "counts": 3},
            {"data_source": "anaconda", "pkg_name": "numpy", "counts": 1000},
        ]
    )

    result = pd.aggregate_month(parquet_bytes)

    assert result == {"numpy": 12, "scipy": 3}


def test_aggregate_month_respects_custom_data_source_argument():
    parquet_bytes = _make_parquet(
        [
            {"data_source": "bioconda", "pkg_name": "samtools", "counts": 4},
            {"data_source": "conda-forge", "pkg_name": "samtools", "counts": 999},
        ]
    )

    result = pd.aggregate_month(parquet_bytes, data_source="bioconda")

    assert result == {"samtools": 4}


# --- fetch_month_parquet ----------------------------------------------------------------------


def _fetch_month(month, transport, cache_dir=None, force=False):
    async def run():
        client = httpx.AsyncClient(transport=transport)
        try:
            return await pd.fetch_month_parquet(month, client, cache_dir=cache_dir, force=force)
        finally:
            await client.aclose()

    return asyncio.run(run())


def test_fetch_month_parquet_returns_bytes_on_200():
    body = b"fake-parquet-bytes"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))

    result = _fetch_month(date(2026, 1, 1), transport)

    assert result == body


def test_fetch_month_parquet_returns_none_on_404():
    transport = httpx.MockTransport(lambda request: httpx.Response(404))

    result = _fetch_month(date(2016, 1, 1), transport)

    assert result is None


def test_fetch_month_parquet_raises_on_500():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    with pytest.raises(pd.PackageDownloadsFetchError):
        _fetch_month(date(2026, 1, 1), transport)


def test_fetch_month_parquet_uses_cache_dir_when_present(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "2026-08.parquet").write_bytes(b"cached-bytes")

    def blow_up(request):
        raise AssertionError("should not hit the network when cache is present")

    transport = httpx.MockTransport(blow_up)

    result = _fetch_month(date(2026, 8, 1), transport, cache_dir=cache_dir)

    assert result == b"cached-bytes"


def test_fetch_month_parquet_force_bypasses_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "2026-08.parquet").write_bytes(b"stale-bytes")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fresh-bytes"))

    result = _fetch_month(date(2026, 8, 1), transport, cache_dir=cache_dir, force=True)

    assert result == b"fresh-bytes"
    assert (cache_dir / "2026-08.parquet").read_bytes() == b"fresh-bytes"


def test_fetch_month_parquet_writes_to_cache_dir_after_download(tmp_path):
    cache_dir = tmp_path / "cache"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"downloaded"))

    result = _fetch_month(date(2026, 8, 1), transport, cache_dir=cache_dir)

    assert result == b"downloaded"
    assert (cache_dir / "2026-08.parquet").read_bytes() == b"downloaded"


# --- fetch_monthly_downloads ------------------------------------------------------------------


def test_fetch_monthly_downloads_merges_multiple_months(monkeypatch):
    jan_bytes = _make_parquet([{"data_source": "conda-forge", "pkg_name": "numpy", "counts": 5}])
    feb_bytes = _make_parquet(
        [
            {"data_source": "conda-forge", "pkg_name": "numpy", "counts": 8},
            {"data_source": "conda-forge", "pkg_name": "scipy", "counts": 2},
        ]
    )
    by_month = {date(2026, 1, 1): jan_bytes, date(2026, 2, 1): feb_bytes}

    async def fake_fetch_month_parquet(
        month, client, cache_dir=None, force=False, on_progress=None
    ):
        return by_month[month]

    monkeypatch.setattr(pd, "fetch_month_parquet", fake_fetch_month_parquet)

    months = [date(2026, 1, 1), date(2026, 2, 1)]
    result = asyncio.run(pd.fetch_monthly_downloads(months, timeout=5.0))

    assert result == {
        "numpy": {"2026-01": 5, "2026-02": 8},
        "scipy": {"2026-02": 2},
    }


def test_fetch_monthly_downloads_skips_404_months_without_raising(monkeypatch):
    feb_bytes = _make_parquet([{"data_source": "conda-forge", "pkg_name": "numpy", "counts": 8}])
    by_month = {date(2026, 1, 1): None, date(2026, 2, 1): feb_bytes}

    async def fake_fetch_month_parquet(
        month, client, cache_dir=None, force=False, on_progress=None
    ):
        return by_month[month]

    monkeypatch.setattr(pd, "fetch_month_parquet", fake_fetch_month_parquet)

    months = [date(2026, 1, 1), date(2026, 2, 1)]
    result = asyncio.run(pd.fetch_monthly_downloads(months, timeout=5.0))

    assert result == {"numpy": {"2026-02": 8}}
