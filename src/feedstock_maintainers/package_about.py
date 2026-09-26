"""Fetch package-level "about" metadata (description, homepage, docs, recipe maintainers, ...)
straight from conda.anaconda.org, using py-rattler's `AboutJson.from_remote_url` -- the one
capability neither this project's JS dependency solver (`@conda-org/rattler`, which exposes no
equivalent) nor anything else in this codebase can do.

Mirrors the `graph_data.py`/`repodata.py` split: the "which build do we want" logic
(`index_latest_builds_by_name`, `should_fetch_about`, `about_json_to_record`) is pure and
unit-tested with literal fixtures; the actual network I/O (`fetch_about_json`,
`fetch_package_about`) is not, same as `repodata.py`/`package_downloads.py`'s `fetch_*` functions.

Only the latest version of each package is considered, using a single representative platform
build per version (`noarch` preferred, else `linux-64` -- see `index_latest_builds_by_name`):
`info/about.json` is derived from the recipe shared by every build of a version, so it does not
meaningfully vary across platforms in practice, and this keeps fetch volume proportional to the
number of packages rather than packages x versions x platforms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from rattler import AboutJson, Client, Version

from .graph_data import REPODATA_PACKAGE_KEYS

_ARCHIVE_URL_TEMPLATE = "https://conda.anaconda.org/conda-forge/{subdir}/{filename}"

# Platforms to search for each package's latest build, in preference order: a noarch build's
# about.json is preferred over a platform-specific one for the same version, since every build of
# a version is generated from the same recipe.
DEFAULT_PLATFORMS = ("noarch", "linux-64")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def index_latest_builds_by_name(
    repodata_by_platform: dict[str, dict],
    platform_order: Iterable[str] = DEFAULT_PLATFORMS,
) -> dict[str, dict[str, str]]:
    """For every package name appearing in any of `repodata_by_platform`'s `packages`/
    `packages.conda` entries, return `{"version": str, "subdir": str, "filename": str}` describing
    its latest version's representative build.

    `repodata_by_platform` is `{subdir: <parsed repodata.json>}` (as returned by
    `repodata.fetch_all_repodata`). Subdirs are visited in `platform_order` (default: noarch
    before linux-64); a build only replaces the current best for a package when its version is
    strictly greater (compared with `rattler.Version`, not string sort) -- so among builds that
    tie on "latest version", the most-preferred subdir visited first wins. A package absent from
    every given subdir is omitted from the result entirely.
    """
    best: dict[str, tuple[Version, str, str, str]] = {}

    for subdir in platform_order:
        repodata = repodata_by_platform.get(subdir)
        if repodata is None:
            continue
        for key in REPODATA_PACKAGE_KEYS:
            for filename, info in repodata.get(key, {}).items():
                name = info["name"]
                version_str = info["version"]
                version = Version(version_str)

                current = best.get(name)
                if current is None or version > current[0]:
                    best[name] = (version, version_str, subdir, filename)

    return {
        name: {"version": version_str, "subdir": subdir, "filename": filename}
        for name, (_version, version_str, subdir, filename) in best.items()
    }


def should_fetch_about(
    name: str,
    latest: dict[str, str] | None,
    existing_entry: dict[str, Any] | None,
    force: bool,
) -> bool:
    """True if `name` needs a fresh `about.json` fetch: `latest` (its newest known version/build,
    from `index_latest_builds_by_name`) is `None` means the package isn't in the fetched repodata
    at all, so there's nothing to do regardless of `force`. Otherwise: `force`; never fetched
    before; the last attempt errored (always retried, mirrors `cache.RecipeCache.should_fetch`);
    or the latest version has changed since the last successful fetch."""
    if latest is None:
        return False
    if force:
        return True
    if existing_entry is None:
        return True
    if existing_entry.get("status") == "error":
        return True
    return existing_entry.get("version") != latest["version"]


def about_json_to_record(about: AboutJson, version: str, subdir: str) -> dict[str, Any]:
    """Convert a fetched `AboutJson` into the JSON-serializable record stored in
    `package-about.json`. `dev_url`/`doc_url`/`home` are each a list in conda's about.json schema
    (multiple URLs are allowed), but conda-forge recipes practically always declare at most one --
    only the first is kept here, `None` when the list is empty.

    Tolerant of malformed recipe `extra` metadata: a real-world about.json can have `"extra":
    {"recipe-maintainers": null}` (an explicit JSON `null`, not a missing key -- `dict.get(key,
    default)` only falls back to `default` for a *missing* key, not a present-but-`None` value) or,
    in principle, a non-list value there. Either case degrades to an empty list rather than raising.
    """

    def first(urls: list[str]) -> str | None:
        return urls[0] if urls else None

    recipe_maintainers = about.extra.get("recipe-maintainers") or []
    if not isinstance(recipe_maintainers, list):
        recipe_maintainers = []

    return {
        "status": "found",
        "version": version,
        "subdir": subdir,
        "fetched_at": _now(),
        "description": about.description,
        "summary": about.summary,
        "home": first(about.home),
        "dev_url": first(about.dev_url),
        "doc_url": first(about.doc_url),
        "recipe_maintainers": recipe_maintainers,
    }


async def fetch_about_json(client: Client, subdir: str, filename: str) -> AboutJson | None:
    """Stream `info/about.json` out of one package archive, without downloading the whole file.

    Returns `None` if the archive has no `info/about.json` (rare/malformed package). Raises on a
    network/HTTP failure (e.g. the archive itself doesn't exist) -- observed as `OSError` for
    py-rattler 0.26, but not documented as part of its API, so callers should treat any exception
    here as a per-package failure, not assume the exact type (see `fetch_package_about`).
    """
    url = _ARCHIVE_URL_TEMPLATE.format(subdir=subdir, filename=filename)
    return await AboutJson.from_remote_url(client, url)


async def fetch_package_about(
    package_names: Iterable[str],
    repodata_by_platform: dict[str, dict],
    existing: dict[str, dict],
    force: bool,
    concurrency: int,
    on_progress: Callable[[int, int], None] | None = None,
    on_flush: Callable[[dict[str, dict]], None] | None = None,
    flush_every: int = 25,
) -> dict[str, dict]:
    """Fetch `info/about.json` metadata for the latest version of each of `package_names`, reusing
    `existing` entries whose version hasn't changed (see `should_fetch_about`) instead of
    re-fetching. Returns the full merged `{name: record}` dict -- every input name gets an entry:
    freshly fetched, carried over unchanged from `existing`, or `{"status": "not_found", ...}` if
    the name isn't in `repodata_by_platform` at all.

    A single package's fetch/parse failure is recorded as `{"status": "error", ...}` for that
    package alone (any exception -- not just a network error -- since a parsing/data bug here
    should never take down an otherwise-successful run of thousands of other packages) and never
    propagates out of this function; only a failure in the up-front repodata indexing (before any
    network I/O starts) can still raise.

    If given, `on_progress` is called with `(done, total)` as each of the packages actually needing
    a fetch (i.e. `total` excludes carried-over/not-found entries) completes. If given, `on_flush`
    is called with the current full result dict (same shape as this function's return value) every
    `flush_every` completions, and once more with the final result before returning -- intended for
    persisting partial progress to disk as the run goes, so a crash or interruption partway through
    a large run doesn't lose everything already fetched (the caller decides how/where to write it;
    see `cli.py`'s `_run_fetch_package_about`, which passes an atomic-write-to--output callback).
    """
    progress = on_progress or (lambda _done, _total: None)

    latest_by_name = index_latest_builds_by_name(repodata_by_platform)

    result: dict[str, dict] = dict(existing)
    todo: list[tuple[str, dict[str, str]]] = []
    for name in package_names:
        latest = latest_by_name.get(name)
        if latest is None:
            result[name] = {"status": "not_found", "fetched_at": _now()}
            continue
        if should_fetch_about(name, latest, existing.get(name), force):
            todo.append((name, latest))
        # else: name already has an up-to-date entry in `existing`/`result` -- leave it untouched.

    client = Client.default_client(max_retries=3)
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    pending_flush = 0
    total = len(todo)
    progress(done, total)

    async def bound(name: str, latest: dict[str, str]) -> None:
        nonlocal done, pending_flush
        async with semaphore:
            try:
                about = await fetch_about_json(client, latest["subdir"], latest["filename"])
            except Exception as exc:
                # Isolate one package's failure (network, HTTP, or a bug in parsing its response)
                # from the rest of the run, whatever form it takes -- see docstring.
                result[name] = {"status": "error", "message": str(exc), "fetched_at": _now()}
            else:
                result[name] = (
                    about_json_to_record(about, latest["version"], latest["subdir"])
                    if about is not None
                    # Rare: the archive exists but has no info/about.json. Record `version` too
                    # (unlike the "package absent from repodata entirely" not_found case above),
                    # so `should_fetch_about` treats this as stable/terminal for this version
                    # rather than retrying every run -- it'll only be retried once a newer
                    # version appears.
                    else {"status": "not_found", "version": latest["version"], "fetched_at": _now()}
                )
            done += 1
            pending_flush += 1
            progress(done, total)
            if on_flush is not None and pending_flush >= flush_every:
                on_flush(result)
                pending_flush = 0

    await asyncio.gather(*(bound(name, latest) for name, latest in todo))

    if on_flush is not None:
        on_flush(result)

    return result
