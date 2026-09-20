"""CLI: `fetch` caches feedstock recipes to disk, `generate` builds artifacts from that cache."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import rich_click as click
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from . import feedstock_count_history as fch
from .cache import RecipeCache
from .github import (
    Cooldown,
    FetchError,
    RatePacer,
    fetch_gitmodules,
    fetch_recipe,
    fetch_updated_feedstocks,
    fetch_user_info,
)
from .gitmodules import FeedstockSource, parse_gitmodules
from .graph_data import (
    build_graph,
    build_package_graph,
    build_package_maintainers,
    compute_maintainer_coverage,
    find_transitive_only_dependencies,
    package_graph_to_json,
)
from .maintainer_history import append_current_point, run_backfill
from .package_downloads import (
    PackageDownloadsFetchError,
    fetch_monthly_downloads,
    trailing_months,
)
from .recipe import ParseError, parse_recipe
from .repodata import RepodataFetchError, fetch_all_repodata
from .site_data import (
    build_maintainer_profiles,
    build_package_profiles,
    compute_maintainer_overview,
    compute_package_overview,
)

_DEFAULT_RATE_LIMIT_NO_TOKEN = 0.5
_DEFAULT_RATE_LIMIT_WITH_TOKEN = 1.2


def _atomic_write(output: Path, data: dict | list) -> None:
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)


def _write_json_fast(output: Path, data: dict | list) -> None:
    """Write JSON without the indent/sort-keys/atomic-rename overhead of `_atomic_write`.

    Used for the tens of thousands of small per-maintainer/per-package files `generate
    site-data` writes -- at that scale, a tmp-file-then-rename plus pretty-printed indentation
    per file adds meaningful syscall and CPU overhead for files nobody hand-edits or diffs.
    """
    output.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path, console: Console) -> None:
    """Copy `src` to `dst` if it exists, else print a warning and continue.

    Used for `generate site-data`'s history-file passthrough: these two files are maintained
    independently (appended to every few hours by `update.yml`, persisted across runs via the
    recipe-cache artifact -- see .github/workflows/update.yml -- never committed to git), so
    they may legitimately be absent (e.g. a fresh checkout before the first `update.yml` run has
    ever produced them). Missing them shouldn't fail the whole `generate site-data` run -- the
    frontend's history chart just shows a "failed to load" state until they exist.
    """
    if not src.exists():
        console.print(
            f"[yellow]Warning:[/] {src} not found, skipping (history chart will 404 until "
            "update.yml produces it)."
        )
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    console.print(f"Copied {src} -> {dst}")


async def _fetch_one(
    source: FeedstockSource,
    client: httpx.AsyncClient,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
    cache: RecipeCache,
) -> None:
    try:
        fetched = await fetch_recipe(client, source, cooldown, pacer, retries=retries, token=token)
    except FetchError as exc:
        cache.record_error(source.name, str(exc))
        return

    if fetched is None:
        cache.record_not_found(source.name)
    else:
        cache.record_found(source.name, fetched.filename, fetched.text)


async def _run_fetch(
    sources: list[FeedstockSource],
    console: Console,
    cache: RecipeCache,
    concurrency: int,
    timeout: float,
    retries: int,
    flush_every: int,
    token: str | None,
    requests_per_second: float,
) -> None:
    pending_flush = 0

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:

        async def bound(source: FeedstockSource) -> None:
            async with semaphore:
                await _fetch_one(source, client, retries, cooldown, pacer, token, cache)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching feedstock recipes", total=len(sources))
            for coro in asyncio.as_completed([bound(source) for source in sources]):
                await coro
                pending_flush += 1
                if pending_flush >= flush_every:
                    cache.flush()
                    pending_flush = 0
                progress.advance(task)

    if pending_flush:
        cache.flush()


async def _fetch_user_one(
    username: str,
    client: httpx.AsyncClient,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
    results: dict[str, dict],
    not_found: dict[str, str],
    errors: list[tuple[str, str]],
) -> None:
    try:
        info = await fetch_user_info(
            client, username, cooldown, pacer, retries=retries, token=token
        )
    except FetchError as exc:
        errors.append((username, str(exc)))
        not_found[username] = f"fetch failed after retries: {exc}"
        return

    if info is None:
        not_found[username] = (
            "GitHub user not found (HTTP 404) -- account may have been deleted or renamed"
        )
    else:
        results[username] = info
        not_found.pop(username, None)


async def _run_fetch_maintainer_info(
    usernames: list[str],
    console: Console,
    output: Path,
    not_found_output: Path,
    results: dict[str, dict],
    not_found: dict[str, str],
    errors: list[tuple[str, str]],
    concurrency: int,
    timeout: float,
    retries: int,
    flush_every: int,
    token: str | None,
    requests_per_second: float,
) -> None:
    pending_flush = 0

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:

        async def bound(username: str) -> None:
            async with semaphore:
                await _fetch_user_one(
                    username, client, retries, cooldown, pacer, token, results, not_found, errors
                )

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching maintainer info", total=len(usernames))
            for coro in asyncio.as_completed([bound(username) for username in usernames]):
                await coro
                pending_flush += 1
                if pending_flush >= flush_every:
                    _atomic_write(output, results)
                    _atomic_write(not_found_output, not_found)
                    pending_flush = 0
                progress.advance(task)

    if pending_flush:
        _atomic_write(output, results)
        _atomic_write(not_found_output, not_found)


async def _run_fetch_repodata(
    platforms: list[str], console: Console, timeout: float
) -> dict[str, dict]:
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        tasks = {
            platform: progress.add_task(f"Fetching {platform} repodata.json.zst", total=None)
            for platform in platforms
        }

        def on_progress(platform: str, downloaded: int, total: int | None) -> None:
            progress.update(tasks[platform], completed=downloaded, total=total)

        return await fetch_all_repodata(platforms, timeout, on_progress=on_progress)


async def _run_fetch_package_downloads(
    months: list[date],
    console: Console,
    timeout: float,
    data_source: str,
    cache_dir: Path | None,
    force: bool,
) -> dict[str, dict[str, int]]:
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        tasks = {
            month: progress.add_task(f"Fetching {month.year}-{month.month:02d}.parquet", total=None)
            for month in months
        }

        def on_progress(month: date, downloaded: int, total: int | None) -> None:
            progress.update(tasks[month], completed=downloaded, total=total)

        return await fetch_monthly_downloads(
            months,
            timeout,
            data_source=data_source,
            cache_dir=cache_dir,
            force=force,
            on_progress=on_progress,
        )


@click.group()
def main() -> None:
    """Track conda-forge feedstock maintainers: fetch recipes, then generate artifacts from them."""


@main.group()
def fetch() -> None:
    """Fetch data from GitHub (feedstock recipes, maintainer profile info) or Anaconda Package
    Data (package download stats)."""


@fetch.command("feedstocks")
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("recipe_cache"),
    show_default=True,
    help="Directory to cache raw recipe files in.",
)
@click.option(
    "--force/--resume",
    default=False,
    help="Re-fetch feedstocks already cached (default: resume, skipping cached found/not_found "
    "entries; feedstocks that previously errored are always retried).",
)
@click.option(
    "--flush-every",
    type=int,
    default=20,
    show_default=True,
    help="Write the cache manifest to disk after this many newly fetched feedstocks.",
)
@click.option(
    "--concurrency",
    type=int,
    default=25,
    show_default=True,
    help="Number of concurrent GitHub requests.",
)
@click.option(
    "--timeout",
    type=float,
    default=15.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    help="Retries for transient errors (403/429/5xx/network) per request.",
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token. Switches fetching from anonymous raw.githubusercontent.com to the "
    "authenticated Contents API (5,000 req/hr instead of a much tighter anonymous limit), "
    "and enables proactive rate-limit pacing from real X-RateLimit-* headers. Prefer setting "
    "the GITHUB_TOKEN environment variable over this flag so the token doesn't end up in your "
    "shell history.",
)
@click.option(
    "--rate-limit",
    "requests_per_second",
    type=float,
    default=None,
    help="Steady-state cap on requests/second across all workers. Default: "
    f"{_DEFAULT_RATE_LIMIT_NO_TOKEN} without --token, {_DEFAULT_RATE_LIMIT_WITH_TOKEN} with it. "
    "Pass 0 to disable pacing entirely.",
)
@click.option(
    "--since",
    default=None,
    help="Timestamp filter to include recently updated feedstocks.",
)
def fetch_feedstocks(
    cache_dir: Path,
    force: bool,
    flush_every: int,
    concurrency: int,
    timeout: float,
    retries: int,
    token: str | None,
    requests_per_second: float | None,
    since: str | None,
) -> None:
    """Download and cache each feedstock's raw recipe file from GitHub.

    Reads feedstock names and their GitHub repo/branch from
    conda-forge/feedstocks' .gitmodules (fetched over the network), then
    fetches only recipe/recipe.yaml (or recipe/meta.yaml) for each -- no
    submodule checkout needed -- and stores the raw text under --cache-dir.
    Run `generate maintainers` afterward to turn the cache into
    maintainers.json.
    """
    console = Console()

    if requests_per_second is None:
        requests_per_second = (
            _DEFAULT_RATE_LIMIT_WITH_TOKEN if token else _DEFAULT_RATE_LIMIT_NO_TOKEN
        )

    mode = "authenticated Contents API" if token else "anonymous raw.githubusercontent.com"
    console.print(f"Fetching via {mode}, paced at {requests_per_second:g} req/s")

    gitmodules = fetch_gitmodules()
    all_sources = parse_gitmodules(gitmodules)
    console.print(f"Discovered {len(all_sources)} feedstocks in .gitmodules")

    if since is not None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"Fetching recently updated feedstocks since {since}...", total=None
            )
            updated_recipes = fetch_updated_feedstocks(
                since,
                token=token,
                on_step=lambda description: progress.update(task, description=description + "..."),
            )
    else:
        updated_recipes = set()

    cache = RecipeCache(cache_dir, recipes_to_update=updated_recipes)
    todo = [
        source for name, source in sorted(all_sources.items()) if cache.should_fetch(name, force)
    ]

    console.print(
        f"{len(todo)} to fetch ({len(all_sources) - len(todo)} already cached in {cache_dir})"
    )

    if not todo:
        console.print("[green]Nothing to do.[/]")
        return

    asyncio.run(
        _run_fetch(
            todo,
            console,
            cache,
            concurrency,
            timeout,
            retries,
            flush_every,
            token,
            requests_per_second,
        )
    )

    counts = cache.counts()
    console.print(f"[green]Done.[/] {counts['found']} feedstocks cached in {cache_dir}")
    if counts["not_found"]:
        console.print(
            f"[yellow]{counts['not_found']}[/] feedstocks had no recipe/recipe.yaml or "
            "recipe/meta.yaml on GitHub"
        )
    if counts["error"]:
        console.print(f"[red]{counts['error']}[/] feedstocks failed to fetch (will retry next run)")


@fetch.command("maintainer-info")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer-info.json"),
    show_default=True,
    help="Path to write per-maintainer GitHub user info.",
)
@click.option(
    "--not-found-output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainers-not-found.json"),
    show_default=True,
    help="Path to write {username: reason} for usernames that couldn't be resolved (404s and "
    "fetch failures) -- useful for spotting feedstocks that may have been abandoned.",
)
@click.option(
    "--force/--resume",
    default=False,
    help="Re-fetch usernames already present in --output (default: resume, skipping them).",
)
@click.option(
    "--flush-every",
    type=int,
    default=20,
    show_default=True,
    help="Write --output to disk after this many newly fetched usernames.",
)
@click.option(
    "--concurrency",
    type=int,
    default=25,
    show_default=True,
    help="Number of concurrent GitHub requests.",
)
@click.option(
    "--timeout",
    type=float,
    default=15.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    help="Retries for transient errors (403/429/5xx/network) per request.",
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token. Raises the Users API limit from 60 req/hr (unauthenticated) to 5,000 "
    "req/hr, and enables proactive rate-limit pacing from real X-RateLimit-* headers. Prefer "
    "setting the GITHUB_TOKEN environment variable over this flag so the token doesn't end up "
    "in your shell history.",
)
@click.option(
    "--rate-limit",
    "requests_per_second",
    type=float,
    default=None,
    help="Steady-state cap on requests/second across all workers. Default: "
    f"{_DEFAULT_RATE_LIMIT_NO_TOKEN} without --token, {_DEFAULT_RATE_LIMIT_WITH_TOKEN} with it. "
    "Pass 0 to disable pacing entirely.",
)
def fetch_maintainer_info(
    maintainers_file: Path,
    output: Path,
    not_found_output: Path,
    force: bool,
    flush_every: int,
    concurrency: int,
    timeout: float,
    retries: int,
    token: str | None,
    requests_per_second: float | None,
) -> None:
    """Fetch each maintainer's GitHub user info and write it to --output.

    Reads the unique set of GitHub usernames out of --maintainers-file (as produced by
    `generate maintainers`), fetches each one's public profile from the GitHub Users API, and
    writes {username: <user info>} to --output. Entries containing "/" are team handles (e.g.
    "conda-forge/go"), not individual users, and are skipped. Usernames that couldn't be
    resolved (404s and fetch failures) are written as {username: reason} to
    --not-found-output.
    """
    console = Console()

    maintainers_data = json.loads(Path(maintainers_file).read_text(encoding="utf-8"))
    all_names = {name for names in maintainers_data.values() for name in names}
    team_handles = {name for name in all_names if "/" in name}
    usernames = sorted(all_names - team_handles)

    existing: dict[str, dict] = {}
    if output.exists() and not force:
        existing = json.loads(output.read_text(encoding="utf-8"))

    not_found: dict[str, str] = {}
    if not_found_output.exists() and not force:
        previously_not_found = json.loads(not_found_output.read_text(encoding="utf-8"))
        not_found = {
            name: reason for name, reason in previously_not_found.items() if name in all_names
        }

    todo = [name for name in usernames if force or name not in existing]

    console.print(
        f"Discovered {len(usernames)} unique maintainer usernames "
        f"({len(team_handles)} team handles skipped)"
    )
    console.print(f"{len(todo)} to fetch ({len(usernames) - len(todo)} already in {output})")

    if requests_per_second is None:
        requests_per_second = (
            _DEFAULT_RATE_LIMIT_WITH_TOKEN if token else _DEFAULT_RATE_LIMIT_NO_TOKEN
        )

    if not todo:
        _atomic_write(not_found_output, not_found)
        console.print("[green]Nothing to do.[/]")
        return

    results = dict(existing)
    errors: list[tuple[str, str]] = []

    asyncio.run(
        _run_fetch_maintainer_info(
            todo,
            console,
            output,
            not_found_output,
            results,
            not_found,
            errors,
            concurrency,
            timeout,
            retries,
            flush_every,
            token,
            requests_per_second,
        )
    )

    _atomic_write(output, results)
    _atomic_write(not_found_output, not_found)

    console.print(f"[green]Done.[/] {len(results)} maintainers recorded in {output}")
    if not_found:
        console.print(
            f"[yellow]{len(not_found)}[/] usernames could not be resolved, "
            f"recorded in {not_found_output}"
        )
    if errors:
        console.print(f"[red]{len(errors)}[/] usernames failed to fetch:")
        for name, message in errors[:20]:
            console.print(f"  - {name}: {message}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


@fetch.command("maintainer-history-backfill")
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default="2014-01-01",
    show_default=True,
    help="Earliest month to backfill. Months before conda-forge/feedstocks' history begins are "
    "detected automatically and skipped, so this is a conservative floor, not a precise date.",
)
@click.option(
    "--end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Latest month to backfill. Defaults to today.",
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer_history_state.json"),
    show_default=True,
    help="Checkpoint file: the last snapshot built plus the full per-feedstock maintainer state "
    "needed to build the next one. Not meant to be committed -- delete it to start over.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer-history.json"),
    show_default=True,
    help="Path to write the monthly unique-maintainer-count time series.",
)
@click.option(
    "--concurrency",
    type=int,
    default=25,
    show_default=True,
    help="Number of concurrent GitHub requests per month's recipe fetches.",
)
@click.option(
    "--timeout",
    type=float,
    default=15.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    help="Retries for transient errors (403/429/5xx/network) per request.",
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token. Strongly recommended for this command: it makes many more requests than "
    "a normal `fetch feedstocks` run. Prefer setting the GITHUB_TOKEN environment variable over "
    "this flag so the token doesn't end up in your shell history.",
)
@click.option(
    "--rate-limit",
    "requests_per_second",
    type=float,
    default=5.0,
    show_default=True,
    help="Steady-state cap on requests/second for recipe content fetches. Pass 0 to disable "
    "pacing entirely.",
)
def fetch_maintainer_history_backfill(
    start_date: datetime,
    end_date: datetime | None,
    state_file: Path,
    output: Path,
    concurrency: int,
    timeout: float,
    retries: int,
    token: str | None,
    requests_per_second: float,
) -> None:
    """Backfill a monthly time series of unique feedstock maintainer counts.

    This is a one-off, potentially long-running command meant to be run manually (never from the
    3-hour update workflow): it walks conda-forge/feedstocks' history one month at a time,
    re-fetching recipe content only for feedstocks whose submodule pointer changed since the
    previous month and carrying everything else forward, but even so can still make several
    hundred thousand requests across the full history. It's resumable -- re-run the same command
    (same --state-file) to continue after an interruption. Once built, keep --output current via
    `generate maintainer-history-append` on each `update.yml` run instead of re-running this.
    """
    console = Console()
    end = (end_date or datetime.now(timezone.utc)).date()
    start = start_date.date()

    console.print(f"Backfilling monthly snapshots from {start} through {end}")
    if state_file.exists():
        console.print(f"Resuming from checkpoint {state_file}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Backfilling...", total=None)
        run_backfill(
            start,
            end,
            state_file,
            output,
            token=token,
            concurrency=concurrency,
            requests_per_second=requests_per_second,
            retries=retries,
            timeout=timeout,
            on_step=lambda description: progress.update(task, description=description + "..."),
        )

    console.print(f"[green]Done.[/] Monthly history written to {output}")


@fetch.command("feedstock-count-history-backfill")
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default="2014-01-01",
    show_default=True,
    help="Earliest month to backfill. Months before conda-forge/feedstocks' history begins are "
    "detected automatically and skipped, so this is a conservative floor, not a precise date.",
)
@click.option(
    "--end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Latest month to backfill. Defaults to today.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("feedstock-count-history.json"),
    show_default=True,
    help="Path to the monthly feedstock-count time series to write/resume.",
)
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    help="Retries for transient errors (403/429/5xx/network) per request.",
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token. Not required -- this command makes only two requests per month -- but "
    "raises the rate limit if you're running it right after other fetches. Prefer setting the "
    "GITHUB_TOKEN environment variable over this flag so the token doesn't end up in your shell "
    "history.",
)
def fetch_feedstock_count_history_backfill(
    start_date: datetime,
    end_date: datetime | None,
    output: Path,
    retries: int,
    token: str | None,
) -> None:
    """Backfill a monthly time series of total feedstock counts.

    Much cheaper than `fetch maintainer-history-backfill`: no recipe content is ever fetched,
    just two GitHub API requests per month (resolve the commit as of that month, then count its
    submodule tree entries). Resumable -- re-run with the same --output to continue after an
    interruption or to add new months later.
    """
    console = Console()
    end = (end_date or datetime.now(timezone.utc)).date()
    start = start_date.date()

    console.print(f"Backfilling monthly feedstock counts from {start} through {end}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Backfilling...", total=None)
        fch.run_backfill(
            start,
            end,
            output,
            token=token,
            retries=retries,
            on_step=lambda description: progress.update(task, description=description + "..."),
        )

    console.print(f"[green]Done.[/] Monthly feedstock counts written to {output}")


@fetch.command("package-downloads")
@click.option(
    "--months",
    type=click.IntRange(min=1),
    default=13,
    show_default=True,
    help="Number of trailing calendar months to fetch, ending with the current (partial) month. "
    "E.g. 13 covers roughly the last year plus the current in-progress month.",
)
@click.option(
    "--data-source",
    default="conda-forge",
    show_default=True,
    help="Anaconda Package Data 'data_source' (channel) to filter to.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("package-downloads.json"),
    show_default=True,
    help='Path to write {package_name: {"YYYY-MM": total_downloads}}.',
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Directory to cache each month's raw downloaded parquet file in, to avoid re-fetching "
    "from S3 on repeated runs. Off by default.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Ignore --cache-dir entirely and re-download every month fresh from S3.",
)
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Per-month download timeout in seconds (monthly parquet files run ~10-15MB).",
)
def fetch_package_downloads(
    months: int,
    data_source: str,
    output: Path,
    cache_dir: Path | None,
    force: bool,
    timeout: float,
) -> None:
    """Fetch monthly conda-forge package download totals from Anaconda Package Data.

    Downloads the last --months pre-aggregated monthly parquet files (one per month) straight
    from the public anaconda-package-data S3 bucket
    (https://anaconda-package-data.s3.amazonaws.com/conda/monthly/...) -- no AWS credentials
    needed. Each month is filtered to --data-source and grouped by package name, summing across
    package version/platform/python build variants, then written to --output as
    {package_name: {"YYYY-MM": total_downloads}}.

    The most recent month in the window is still accumulating downloads throughout the month, so
    treat its total as provisional. A month with no parquet file yet published (e.g. one before
    the dataset's coverage begins) is silently skipped, not an error.
    """
    console = Console()
    today = datetime.now(timezone.utc).date()
    target_months = trailing_months(today, months)

    console.print(
        f"Fetching {len(target_months)} month(s) of {data_source!r} downloads "
        f"({target_months[0].isoformat()[:7]} through {target_months[-1].isoformat()[:7]})"
    )

    try:
        result = asyncio.run(
            _run_fetch_package_downloads(
                target_months, console, timeout, data_source, cache_dir, force
            )
        )
    except PackageDownloadsFetchError as exc:
        raise click.ClickException(str(exc)) from None

    _atomic_write(output, result)

    months_with_data = {month_key for per_pkg in result.values() for month_key in per_pkg}
    missing = len(target_months) - len(months_with_data)

    console.print(f"[green]Done.[/] {len(result)} packages written to {output}")
    if missing:
        console.print(f"[yellow]{missing}[/] month(s) had no data published yet (skipped)")


@main.group()
def generate() -> None:
    """Build artifacts from the local recipe cache. No network access, except `package-graph`
    and `transitive-dependencies`, which download repodata directly from conda-forge."""


@generate.command("maintainers")
@click.option(
    "--cache-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("recipe_cache"),
    show_default=True,
    help="Directory previously populated by `fetch`.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to write the maintainers JSON file.",
)
@click.option(
    "--package-names-output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("package-names.json"),
    show_default=True,
    help="Path to write each feedstock's real, installable conda package name(s).",
)
def generate_maintainers(cache_dir: Path, output: Path, package_names_output: Path) -> None:
    """Read cached recipe files and write each feedstock's maintainers and package name(s).

    Writes --output: {feedstock: [extra.recipe-maintainers, ...]}. Also writes
    --package-names-output: {feedstock: [package_name, ...]} -- the real, installable conda
    package name(s) declared by the recipe (more than one for a multi-output feedstock) -- for
    use by `generate package-maintainers`.
    """
    console = Console()
    cache = RecipeCache(cache_dir)

    maintainers: dict[str, list] = {}
    package_names: dict[str, list[str]] = {}
    errors: list[tuple[str, str]] = []
    for entry in cache.found_entries():
        text = cache.read_text(entry)
        assert entry.filename is not None
        try:
            maintainers[entry.name], package_names[entry.name] = parse_recipe(entry.filename, text)
        except ParseError as exc:
            errors.append((entry.name, str(exc)))

    _atomic_write(output, maintainers)
    _atomic_write(package_names_output, package_names)

    console.print(
        f"[green]Done.[/] {len(maintainers)} feedstocks recorded in {output} and "
        f"{package_names_output}"
    )
    not_found = cache.counts()["not_found"]
    if not_found:
        console.print(f"[yellow]{not_found}[/] cached feedstocks had no recipe file")
    if errors:
        console.print(f"[red]{len(errors)}[/] feedstocks failed to parse:")
        for name, message in errors[:20]:
            console.print(f"  - {name}: {message}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


@generate.command("maintainer-history-append")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--history-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer-history.json"),
    show_default=True,
    help="Path to the monthly time series to append/update (as produced by `fetch "
    "maintainer-history-backfill`). Created if it doesn't exist yet.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Always append a new entry, even if the current month already has one (default: "
    "replace the current month's entry in place, so runs every few hours don't pile up "
    "duplicate points).",
)
def generate_maintainer_history_append(
    maintainers_file: Path, history_file: Path, force: bool
) -> None:
    """Append (or update) today's unique-maintainer count in --history-file.

    Reads the unique GitHub logins out of --maintainers-file -- data the 3-hour update workflow
    already regenerates from a full recipe cache scan every run -- and records the count as
    today's data point, replacing any existing entry for the current calendar month unless
    --force is given.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else []

    updated = append_current_point(maintainers_data, history, date.today(), force=force)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(history_file, updated)

    console.print(
        f"[green]Done.[/] {updated[-1]['unique_maintainer_count']} unique maintainers recorded "
        f"for {updated[-1]['date']} in {history_file}"
    )


@generate.command("feedstock-count-append")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`) -- its key "
    "count is today's feedstock count, since it has one entry per feedstock.",
)
@click.option(
    "--history-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("feedstock-count-history.json"),
    show_default=True,
    help="Path to the monthly time series to append/update (as produced by `fetch "
    "feedstock-count-history-backfill`). Created if it doesn't exist yet.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Always append a new entry, even if the current month already has one (default: "
    "replace the current month's entry in place, so runs every few hours don't pile up "
    "duplicate points).",
)
def generate_feedstock_count_append(
    maintainers_file: Path, history_file: Path, force: bool
) -> None:
    """Append (or update) today's feedstock count in --history-file.

    Reads the feedstock count out of --maintainers-file -- data the 3-hour update workflow
    already regenerates from a full recipe cache scan every run -- and records it as today's data
    point, replacing any existing entry for the current calendar month unless --force is given.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else []

    updated = fch.append_current_point(len(maintainers_data), history, date.today(), force=force)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(history_file, updated)

    console.print(
        f"[green]Done.[/] {updated[-1]['feedstock_count']} feedstocks recorded for "
        f"{updated[-1]['date']} in {history_file}"
    )


@generate.command("maintainer-graph")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--maintainer-info-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainer-info.json"),
    show_default=True,
    help="Path to per-maintainer GitHub user info (as produced by `fetch maintainer-info`).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer-graph.json"),
    show_default=True,
    help="Path to write the graphology-format maintainer collaboration graph JSON.",
)
def generate_maintainer_graph_data(
    maintainers_file: Path, maintainer_info_file: Path, output: Path
) -> None:
    """Build a maintainer collaboration graph from --maintainers-file and --maintainer-info-file.

    Nodes are maintainers with profile info in --maintainer-info-file; edges connect maintainers
    who co-maintain at least one feedstock, weighted by the number of shared feedstocks. Team
    handles (e.g. "conda-forge/go") and usernames missing profile info are excluded.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    maintainer_info_data = json.loads(maintainer_info_file.read_text(encoding="utf-8"))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Building maintainer collaboration graph...", total=None)
        graph = build_graph(
            maintainers_data,
            maintainer_info_data,
            on_step=lambda description: progress.update(task, description=description + "..."),
        )

    _atomic_write(output, graph)

    console.print(
        f"[green]Done.[/] {len(graph['nodes'])} nodes, {len(graph['edges'])} "
        f"edges written to {output}"
    )


@generate.command("package-maintainers")
@click.option(
    "--package-names-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-names.json"),
    show_default=True,
    help="Path to the package names JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("package-maintainers.json"),
    show_default=True,
    help="Path to write the package -> maintainers JSON file.",
)
def generate_package_maintainers(
    package_names_file: Path, maintainers_file: Path, output: Path
) -> None:
    """Join --package-names-file with --maintainers-file into a package -> maintainers lookup.

    Answers "who maintains package X" directly: {package_name: [maintainer_login, ...]}.
    """
    console = Console()

    package_names_data = json.loads(package_names_file.read_text(encoding="utf-8"))
    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))

    result = build_package_maintainers(package_names_data, maintainers_data)
    _atomic_write(output, result)

    console.print(f"[green]Done.[/] {len(result)} packages recorded in {output}")


@generate.command("package-graph")
@click.option(
    "--platform",
    "-p",
    "platforms",
    multiple=True,
    required=True,
    help="conda-forge platform subdir to include (e.g. noarch, linux-64, linux-aarch64, "
    "osx-arm64). Repeat for multiple platforms.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("package-graph.json"),
    show_default=True,
    help="Path to write the graphology-format packages graph JSON.",
)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Per-platform repodata download timeout in seconds.",
)
def generate_package_graph_data(platforms: tuple[str, ...], output: Path, timeout: float) -> None:
    """Build a package dependency graph from conda-forge's repodata for one or more --platform.

    Downloads repodata.json.zst concurrently, straight from
    https://conda.anaconda.org/conda-forge/<platform>/repodata.json.zst, for each --platform --
    no local repodata file needed. Nodes are package names (collapsed across all
    versions/builds); an edge points from a package to each of its direct dependencies, weighted
    by how many distinct (version, build) artifacts declared that dependency. Both the legacy
    `packages` and newer `packages.conda` keys in each repodata file are read.
    """
    console = Console()

    try:
        repodata_by_platform = asyncio.run(_run_fetch_repodata(list(platforms), console, timeout))
    except RepodataFetchError as exc:
        raise click.ClickException(str(exc)) from None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Building package dependency graph...", total=None)
        graph = build_package_graph(
            list(repodata_by_platform.values()),
            on_step=lambda description: progress.update(task, description=description + "..."),
        )

    _atomic_write(output, package_graph_to_json(graph))

    console.print(
        f"[green]Done.[/] {graph.number_of_nodes()} nodes, {graph.number_of_edges()} "
        f"edges written to {output}"
    )


@generate.command("transitive-dependencies")
@click.option(
    "--platform",
    "-p",
    "platforms",
    multiple=True,
    required=True,
    help="conda-forge platform subdir to include (e.g. noarch, linux-64, linux-aarch64, "
    "osx-arm64). Repeat for multiple platforms.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("transitive-dependencies.json"),
    show_default=True,
    help="Path to write the full ranked list as JSON.",
)
@click.option(
    "--top",
    "-n",
    type=int,
    default=25,
    show_default=True,
    help="Number of rows to print for each ranking.",
)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Per-platform repodata download timeout in seconds.",
)
def generate_transitive_dependencies(
    platforms: tuple[str, ...], output: Path, top: int, timeout: float
) -> None:
    """Rank packages by how many other packages depend on them only transitively, from
    conda-forge's repodata for one or more --platform.

    Downloads repodata.json.zst concurrently, straight from
    https://conda.anaconda.org/conda-forge/<platform>/repodata.json.zst, for each --platform --
    no local repodata file needed. Builds the same package dependency graph as `generate
    package-graph`, then for every package reports how many packages depend on it only through a
    chain of dependencies and never as a direct, declared dependency -- the ecosystem's "hidden"
    load-bearing packages.
    """
    console = Console()

    try:
        repodata_by_platform = asyncio.run(_run_fetch_repodata(list(platforms), console, timeout))
    except RepodataFetchError as exc:
        raise click.ClickException(str(exc)) from None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Building package dependency graph...", total=None)
        graph = build_package_graph(
            list(repodata_by_platform.values()),
            on_step=lambda description: progress.update(task, description=description + "..."),
        )
        progress.update(task, description="Ranking transitive-only dependencies...")
        records = find_transitive_only_dependencies(graph)

    _atomic_write(output, {"packages": records})

    def _print_ranking(title: str, key: str) -> None:
        ranked = sorted(records, key=lambda record: (-record[key], record["name"]))[:top]
        table = Table(title=title)
        table.add_column("Package")
        table.add_column("Direct", justify="right")
        table.add_column("Transitive", justify="right")
        table.add_column("Transitive-only", justify="right")
        table.add_column("Ratio", justify="right")
        for record in ranked:
            table.add_row(
                record["name"],
                str(record["direct_dependents"]),
                str(record["transitive_dependents"]),
                str(record["transitive_only_dependents"]),
                f"{record['transitive_only_ratio']:.2f}",
            )
        console.print(table)

    _print_ranking(f"Top {top} by transitive-only dependent count", "transitive_only_dependents")
    _print_ranking(f"Top {top} by transitive-only ratio", "transitive_only_ratio")

    console.print(
        f"[green]Done.[/] {len(records)} packages ranked, full results written to {output}"
    )


@generate.command("maintainer-coverage")
@click.option(
    "--transitive-dependencies-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("transitive-dependencies.json"),
    show_default=True,
    help="Path to the ranking JSON file (as produced by `generate transitive-dependencies`).",
)
@click.option(
    "--package-maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-maintainers.json"),
    show_default=True,
    help="Path to the package -> maintainers JSON file (as produced by "
    "`generate package-maintainers`).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainer-coverage.json"),
    show_default=True,
    help="Path to write the full stats + ranked list as JSON.",
)
@click.option(
    "--top",
    "-n",
    type=int,
    default=25,
    show_default=True,
    help="Number of rows to print in the ranking table.",
)
def generate_maintainer_coverage(
    transitive_dependencies_file: Path,
    package_maintainers_file: Path,
    output: Path,
    top: int,
) -> None:
    """Rank packages by dependents-per-maintainer and report overall maintainer-count stats.

    Joins --transitive-dependencies-file with --package-maintainers-file by package name (no
    repodata needed) to answer questions like "what's the mean/median/stddev number of
    maintainers per package?" and "what packages are heavily depended on but have relatively few
    maintainers?".
    """
    console = Console()

    transitive_data = json.loads(transitive_dependencies_file.read_text(encoding="utf-8"))
    package_maintainers_data = json.loads(package_maintainers_file.read_text(encoding="utf-8"))

    result = compute_maintainer_coverage(transitive_data["packages"], package_maintainers_data)
    _atomic_write(output, result)

    stats = result["stats"]
    console.print(
        f"[green]Done.[/] {stats['package_count']} packages: "
        f"mean={stats['mean_maintainers']:.2f} median={stats['median_maintainers']:.2f} "
        f"stddev={stats['stddev_maintainers']:.2f}, "
        f"{stats['packages_with_zero_maintainers']} with zero maintainers"
    )

    table = Table(title=f"Top {top} by risk score (transitive dependents per maintainer)")
    table.add_column("Package")
    table.add_column("Maintainers", justify="right")
    table.add_column("Transitive dependents", justify="right")
    table.add_column("Risk score", justify="right")
    for record in result["packages"][:top]:
        table.add_row(
            record["name"],
            str(record["maintainer_count"]),
            str(record["transitive_dependents"]),
            f"{record['risk_score']:.1f}",
        )
    console.print(table)

    console.print(f"Full results written to {output}")


@generate.command("site-data")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--maintainer-info-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainer-info.json"),
    show_default=True,
    help="Path to per-maintainer GitHub user info (as produced by `fetch maintainer-info`).",
)
@click.option(
    "--maintainer-graph-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainer-graph.json"),
    show_default=True,
    help="Path to the maintainer collaboration graph (as produced by `generate maintainer-graph`).",
)
@click.option(
    "--package-names-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-names.json"),
    show_default=True,
    help="Path to the package names JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--package-maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-maintainers.json"),
    show_default=True,
    help="Path to the package -> maintainers JSON file (as produced by "
    "`generate package-maintainers`).",
)
@click.option(
    "--package-downloads-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-downloads.json"),
    show_default=True,
    help="Path to monthly package download totals (as produced by `fetch package-downloads`).",
)
@click.option(
    "--package-graph-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-graph.json"),
    show_default=True,
    help="Path to the package dependency graph (as produced by `generate package-graph`).",
)
@click.option(
    "--transitive-dependencies-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("transitive-dependencies.json"),
    show_default=True,
    help="Path to the ranking JSON file (as produced by `generate transitive-dependencies`).",
)
@click.option(
    "--maintainer-history-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("maintainer-history.json"),
    show_default=True,
    help="Path to the monthly maintainer-count time series (as produced by `generate "
    "maintainer-history-append`). Copied as-is into --output-dir; skipped with a warning "
    "(not an error) if it doesn't exist yet.",
)
@click.option(
    "--feedstock-count-history-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-count-history.json"),
    show_default=True,
    help="Path to the monthly feedstock-count time series (as produced by `generate "
    "feedstock-count-append`). Copied as-is into --output-dir; skipped with a warning "
    "(not an error) if it doesn't exist yet.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("web/static/data"),
    show_default=True,
    help="Directory to write the site-data JSON files into (mirrors what the frontend expects "
    "under web/static/data/).",
)
def generate_site_data(
    maintainers_file: Path,
    maintainer_info_file: Path,
    maintainer_graph_file: Path,
    package_names_file: Path,
    package_maintainers_file: Path,
    package_downloads_file: Path,
    package_graph_file: Path,
    transitive_dependencies_file: Path,
    maintainer_history_file: Path,
    feedstock_count_history_file: Path,
    output_dir: Path,
) -> None:
    """Build small, page-ready JSON payloads for the statistics/profile pages.

    Reads every existing root artifact (maintainers, maintainer profiles, both collaboration and
    dependency graphs, package downloads, transitive dependency rankings) and writes:
    --output-dir/maintainer-overview.json, --output-dir/package-overview.json,
    --output-dir/maintainers/index.json plus one --output-dir/maintainers/<login>.json per
    maintainer with a GitHub profile, and --output-dir/packages/index.json plus one
    --output-dir/packages/<name>.json per package. Also copies --maintainer-history-file and
    --feedstock-count-history-file into --output-dir unchanged (they're independently-maintained
    time series, not derived here -- see `generate maintainer-history-append`/`generate
    feedstock-count-append`). Meant to run only at deploy time (see
    .github/workflows/pages.yml) -- nothing it writes is committed to git.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    maintainer_info_data = json.loads(maintainer_info_file.read_text(encoding="utf-8"))
    maintainer_graph_data = json.loads(maintainer_graph_file.read_text(encoding="utf-8"))
    package_names_data = json.loads(package_names_file.read_text(encoding="utf-8"))
    package_maintainers_data = json.loads(package_maintainers_file.read_text(encoding="utf-8"))
    package_downloads_data = json.loads(package_downloads_file.read_text(encoding="utf-8"))
    package_graph_data = json.loads(package_graph_file.read_text(encoding="utf-8"))
    transitive_dependencies_data = json.loads(
        transitive_dependencies_file.read_text(encoding="utf-8")
    )["packages"]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    output_dir.mkdir(parents=True, exist_ok=True)
    maintainers_dir = output_dir / "maintainers"
    packages_dir = output_dir / "packages"
    maintainers_dir.mkdir(parents=True, exist_ok=True)
    packages_dir.mkdir(parents=True, exist_ok=True)

    console.print("Building maintainer-overview.json and package-overview.json...")
    maintainer_overview = compute_maintainer_overview(
        maintainers_data,
        maintainer_info_data,
        package_maintainers_data,
        package_downloads_data,
        generated_at,
    )
    _atomic_write(output_dir / "maintainer-overview.json", maintainer_overview)

    package_overview = compute_package_overview(
        package_maintainers_data,
        transitive_dependencies_data,
        package_downloads_data,
        generated_at,
    )
    _atomic_write(output_dir / "package-overview.json", package_overview)

    console.print(f"Writing {len(maintainer_info_data)} maintainer profile(s)...")
    maintainer_logins = []
    for login, profile in build_maintainer_profiles(
        maintainers_data, maintainer_info_data, maintainer_graph_data, package_names_data
    ):
        maintainer_logins.append(login)
        _write_json_fast(maintainers_dir / f"{login}.json", profile)
    _atomic_write(maintainers_dir / "index.json", sorted(maintainer_logins))

    console.print(f"Writing {len(package_maintainers_data)} package profile(s)...")
    package_names_list = []
    for name, profile in build_package_profiles(
        package_maintainers_data,
        maintainer_info_data,
        package_graph_data,
        transitive_dependencies_data,
        package_downloads_data,
    ):
        package_names_list.append(name)
        _write_json_fast(packages_dir / f"{name}.json", profile)
    _atomic_write(packages_dir / "index.json", sorted(package_names_list))

    _copy_if_exists(maintainer_history_file, output_dir / "maintainer-history.json", console)
    _copy_if_exists(
        feedstock_count_history_file, output_dir / "feedstock-count-history.json", console
    )

    console.print(
        f"[green]Done.[/] Site data written to {output_dir} "
        f"({len(maintainer_logins)} maintainers, {len(package_names_list)} packages)"
    )


if __name__ == "__main__":
    main()
