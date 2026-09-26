"""Tests for the pure "which build do we want" logic behind the package-about.json fetcher, plus
`fetch_package_about`'s per-package error isolation and progress-flushing (with `fetch_about_json`
monkeypatched out, following `tests/test_cli.py`'s convention for faking network-calling
functions). The real network I/O inside `fetch_about_json` itself (an actual py-rattler `Client`/
`AboutJson.from_remote_url` call) is deliberately not unit-tested here, same as
`repodata.py`/`package_downloads.py`'s `fetch_*` functions -- see `package_about.py`'s module
docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import feedstock_maintainers.package_about as package_about_module
from feedstock_maintainers.package_about import (
    about_json_to_record,
    fetch_package_about,
    index_latest_builds_by_name,
    should_fetch_about,
)


def _repodata(packages: dict[str, dict] | None = None) -> dict:
    return {"packages": {}, "packages.conda": packages or {}}


def _artifact(name: str, version: str) -> dict:
    return {"name": name, "version": version, "depends": []}


# ── index_latest_builds_by_name ──────────────────────────────────────────────────────────────


def test_index_latest_builds_prefers_noarch_over_linux64_for_same_version():
    repodata_by_platform = {
        "noarch": _repodata({"pkg-1.0.0-0.conda": _artifact("pkg", "1.0.0")}),
        "linux-64": _repodata({"pkg-1.0.0-h123_0.conda": _artifact("pkg", "1.0.0")}),
    }
    result = index_latest_builds_by_name(repodata_by_platform)
    assert result["pkg"] == {
        "version": "1.0.0",
        "subdir": "noarch",
        "filename": "pkg-1.0.0-0.conda",
    }


def test_index_latest_builds_uses_linux64_when_no_noarch_build_exists():
    repodata_by_platform = {
        "noarch": _repodata({}),
        "linux-64": _repodata({"numpy-2.1.3-py312h_0.conda": _artifact("numpy", "2.1.3")}),
    }
    result = index_latest_builds_by_name(repodata_by_platform)
    assert result["numpy"] == {
        "version": "2.1.3",
        "subdir": "linux-64",
        "filename": "numpy-2.1.3-py312h_0.conda",
    }


def test_index_latest_builds_picks_newest_version_using_conda_version_ordering():
    # A naive string sort would put "2.0.10" before "2.0.2" and "2.0.9" -- must use real version
    # comparison, not lexicographic.
    repodata_by_platform = {
        "noarch": _repodata(
            {
                "pkg-2.0.2-0.conda": _artifact("pkg", "2.0.2"),
                "pkg-2.0.9-0.conda": _artifact("pkg", "2.0.9"),
                "pkg-2.0.10-0.conda": _artifact("pkg", "2.0.10"),
            }
        ),
    }
    result = index_latest_builds_by_name(repodata_by_platform)
    assert result["pkg"]["version"] == "2.0.10"


def test_index_latest_builds_prefers_newer_version_over_platform_preference():
    # linux-64 has a newer version than noarch -- "latest version" beats "preferred platform".
    repodata_by_platform = {
        "noarch": _repodata({"pkg-1.0.0-0.conda": _artifact("pkg", "1.0.0")}),
        "linux-64": _repodata({"pkg-1.1.0-h_0.conda": _artifact("pkg", "1.1.0")}),
    }
    result = index_latest_builds_by_name(repodata_by_platform)
    assert result["pkg"] == {
        "version": "1.1.0",
        "subdir": "linux-64",
        "filename": "pkg-1.1.0-h_0.conda",
    }


def test_index_latest_builds_omits_package_absent_from_every_platform():
    repodata_by_platform = {"noarch": _repodata({}), "linux-64": _repodata({})}
    result = index_latest_builds_by_name(repodata_by_platform)
    assert result == {}


# ── should_fetch_about ───────────────────────────────────────────────────────────────────────

_LATEST = {"version": "2.0.0", "subdir": "noarch", "filename": "pkg-2.0.0-0.conda"}


def test_should_fetch_about_false_when_package_not_in_repodata():
    assert should_fetch_about("pkg", None, None, force=False) is False
    assert should_fetch_about("pkg", None, None, force=True) is False


def test_should_fetch_about_true_when_forced():
    existing = {"status": "found", "version": "2.0.0"}
    assert should_fetch_about("pkg", _LATEST, existing, force=True) is True


def test_should_fetch_about_true_when_never_fetched():
    assert should_fetch_about("pkg", _LATEST, None, force=False) is True


def test_should_fetch_about_true_when_previous_attempt_errored():
    existing = {"status": "error", "message": "boom"}
    assert should_fetch_about("pkg", _LATEST, existing, force=False) is True


def test_should_fetch_about_true_when_version_changed():
    existing = {"status": "found", "version": "1.9.0"}
    assert should_fetch_about("pkg", _LATEST, existing, force=False) is True


def test_should_fetch_about_false_when_version_unchanged():
    existing = {"status": "found", "version": "2.0.0"}
    assert should_fetch_about("pkg", _LATEST, existing, force=False) is False


def test_should_fetch_about_false_when_not_found_and_version_unchanged():
    # The archive existed but had no info/about.json -- `version` is still recorded so this is
    # treated as stable for this version rather than retried every run.
    existing = {"status": "not_found", "version": "2.0.0"}
    assert should_fetch_about("pkg", _LATEST, existing, force=False) is False


def test_should_fetch_about_true_when_not_found_and_version_changed():
    existing = {"status": "not_found", "version": "1.9.0"}
    assert should_fetch_about("pkg", _LATEST, existing, force=False) is True


# ── about_json_to_record ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeAboutJson:
    """Stands in for `rattler.AboutJson` -- exposes exactly the attributes
    `about_json_to_record` reads, no real py-rattler object needed."""

    description: str | None = None
    summary: str | None = None
    home: list[str] = field(default_factory=list)
    dev_url: list[str] = field(default_factory=list)
    doc_url: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def test_about_json_to_record_takes_first_url_of_each_list():
    about = _FakeAboutJson(
        description="A package.",
        summary="A short summary.",
        home=["https://example.org", "https://example.org/mirror"],
        dev_url=["https://github.com/example/example"],
        doc_url=["https://example.org/docs"],
        extra={"recipe-maintainers": ["alice", "bob"]},
    )
    record = about_json_to_record(about, version="1.2.3", subdir="noarch")

    assert record == {
        "status": "found",
        "version": "1.2.3",
        "subdir": "noarch",
        "fetched_at": record["fetched_at"],
        "description": "A package.",
        "summary": "A short summary.",
        "home": "https://example.org",
        "dev_url": "https://github.com/example/example",
        "doc_url": "https://example.org/docs",
        "recipe_maintainers": ["alice", "bob"],
    }


def test_about_json_to_record_none_for_empty_url_lists_and_missing_maintainers():
    about = _FakeAboutJson(description=None, summary=None)
    record = about_json_to_record(about, version="1.0.0", subdir="linux-64")

    assert record["home"] is None
    assert record["dev_url"] is None
    assert record["doc_url"] is None
    assert record["recipe_maintainers"] == []
    assert record["description"] is None
    assert record["summary"] is None


def test_about_json_to_record_explicit_null_recipe_maintainers_treated_as_empty():
    # Regression: a real about.json can have "extra": {"recipe-maintainers": null} -- an explicit
    # JSON null, not a missing key. dict.get(key, default) only falls back to `default` for a
    # *missing* key, so `.get("recipe-maintainers", [])` still returns `None` here, which used to
    # crash `list(None)` with `TypeError: 'NoneType' object is not iterable`.
    about = _FakeAboutJson(extra={"recipe-maintainers": None})
    record = about_json_to_record(about, version="1.0.0", subdir="noarch")
    assert record["recipe_maintainers"] == []


def test_about_json_to_record_non_list_recipe_maintainers_treated_as_empty():
    about = _FakeAboutJson(extra={"recipe-maintainers": "not-a-list"})
    record = about_json_to_record(about, version="1.0.0", subdir="noarch")
    assert record["recipe_maintainers"] == []


# ── fetch_package_about: per-package error isolation and progress flushing ──────────────────


def test_fetch_package_about_isolates_one_packages_failure(monkeypatch):
    repodata_by_platform = {
        "noarch": _repodata(
            {
                "good-1.0.0-0.conda": _artifact("good", "1.0.0"),
                "bad-1.0.0-0.conda": _artifact("bad", "1.0.0"),
            }
        )
    }

    async def fake_fetch_about_json(client, subdir, filename):
        if filename.startswith("bad"):
            # The exact bug from the traceback: a non-network, non-OSError exception raised while
            # turning a successfully-fetched AboutJson into a record.
            raise TypeError("'NoneType' object is not iterable")
        return _FakeAboutJson(summary="ok")

    monkeypatch.setattr(package_about_module, "fetch_about_json", fake_fetch_about_json)

    result = asyncio.run(
        fetch_package_about(
            ["good", "bad"], repodata_by_platform, existing={}, force=False, concurrency=2
        )
    )

    assert result["good"]["status"] == "found"
    assert result["bad"]["status"] == "error"
    assert "NoneType" in result["bad"]["message"]


def test_fetch_package_about_flushes_progress_periodically(monkeypatch):
    names = ["pkg-a", "pkg-b", "pkg-c"]
    repodata_by_platform = {
        "noarch": _repodata({f"{name}-1.0.0-0.conda": _artifact(name, "1.0.0") for name in names})
    }

    async def fake_fetch_about_json(client, subdir, filename):
        return _FakeAboutJson(summary="ok")

    monkeypatch.setattr(package_about_module, "fetch_about_json", fake_fetch_about_json)

    flushes: list[dict] = []
    result = asyncio.run(
        fetch_package_about(
            names,
            repodata_by_platform,
            existing={},
            force=False,
            concurrency=1,  # sequential, so flush timing below is deterministic
            on_flush=lambda partial: flushes.append(dict(partial)),
            flush_every=2,
        )
    )

    # 3 packages, flush_every=2 -> one mid-run flush (after the 2nd completion) plus one final
    # flush with everything -- never zero flushes just because a run finished uninterrupted.
    assert len(flushes) == 2
    assert len(flushes[0]) == 2
    assert len(flushes[-1]) == 3
    assert flushes[-1] == result
