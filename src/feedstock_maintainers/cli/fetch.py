"""`fetch` caches feedstock recipes and other network-sourced data to disk."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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

from .. import activity
from .. import feedstock_count_history as fch
from ..cache import RecipeCache
from ..github import (
    Cooldown,
    FetchError,
    RatePacer,
    fetch_gitmodules,
    fetch_recipe,
    fetch_updated_feedstocks,
    fetch_user_info,
)
from ..gitmodules import FeedstockSource, parse_gitmodules
from ..maintainer_history import run_backfill
from ..package_about import fetch_package_about
from ..package_downloads import (
    PackageDownloadsFetchError,
    fetch_monthly_downloads,
    trailing_months,
)
from ..repodata import RepodataFetchError
from ._shared import _atomic_write, _load_json_if_exists, _run_fetch_repodata

_DEFAULT_RATE_LIMIT_NO_TOKEN = 0.5
_DEFAULT_RATE_LIMIT_WITH_TOKEN = 1.2


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


async def _run_fetch_package_about(
    package_names: list[str],
    repodata_by_platform: dict[str, dict],
    existing: dict[str, dict],
    force: bool,
    concurrency: int,
    output: Path,
    flush_every: int,
    console: Console,
) -> dict[str, dict]:
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching package about.json metadata", total=None)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        return await fetch_package_about(
            package_names,
            repodata_by_platform,
            existing,
            force,
            concurrency,
            on_progress=on_progress,
            # Periodically write partial progress to --output (the same file this run started
            # from) so a crash or interruption partway through a large run -- e.g. one bad
            # package's about.json triggering an unexpected error -- doesn't lose everything
            # already fetched; the next run resumes from whatever was last flushed.
            on_flush=lambda partial: _atomic_write(output, partial),
            flush_every=flush_every,
        )


@click.group()
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


@fetch.command("package-about")
@click.option(
    "--package-maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-maintainers.json"),
    show_default=True,
    help="Path to the package -> maintainers JSON file (as produced by `generate "
    "package-maintainers`) -- its keys are exactly the set of packages that get a profile page "
    "(see `generate site-data`), so this enumerates every package worth fetching about.json for.",
)
@click.option(
    "--platform",
    "-p",
    "platforms",
    multiple=True,
    default=("noarch", "linux-64"),
    show_default=True,
    help="Representative platform subdirs to search for each package's latest build, in "
    "preference order (noarch preferred, first remaining platform used otherwise). Repeat for "
    "multiple platforms.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("package-about.json"),
    show_default=True,
    help="Path to write per-package about.json metadata (also doubles as the incremental-fetch "
    "manifest -- see --force).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-fetch every package regardless of manifest state (default: only never-fetched, "
    "errored, or version-changed packages -- see `package_about.should_fetch_about`).",
)
@click.option(
    "--concurrency",
    type=int,
    default=25,
    show_default=True,
    help="Number of concurrent about.json fetches.",
)
@click.option(
    "--flush-every",
    type=int,
    default=25,
    show_default=True,
    help="Write --output to disk after this many packages complete (found, not found, or "
    "errored), so an interrupted or crashed run doesn't lose progress already made -- the next "
    "run resumes from whatever was last written instead of starting over.",
)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Per-platform repodata download timeout in seconds.",
)
def fetch_package_about_cmd(
    package_maintainers_file: Path,
    platforms: tuple[str, ...],
    output: Path,
    force: bool,
    concurrency: int,
    flush_every: int,
    timeout: float,
) -> None:
    """Fetch info/about.json metadata (description, home, dev_url, doc_url, summary,
    recipe-maintainers) for the latest version of every package, streamed directly from
    conda.anaconda.org via py-rattler -- no full package download needed.

    Only the latest version of each package is fetched, using a single representative platform
    build per version (see --platform). A package whose latest version hasn't changed since the
    last run is skipped entirely; pass --force to re-fetch everything.
    """
    console = Console()

    package_names = sorted(json.loads(package_maintainers_file.read_text(encoding="utf-8")))
    existing = _load_json_if_exists(output, console, "every package will be fetched fresh")

    try:
        repodata_by_platform = asyncio.run(_run_fetch_repodata(list(platforms), console, timeout))
    except RepodataFetchError as exc:
        raise click.ClickException(str(exc)) from None

    result = asyncio.run(
        _run_fetch_package_about(
            package_names,
            repodata_by_platform,
            existing,
            force,
            concurrency,
            output,
            flush_every,
            console,
        )
    )

    # No final _atomic_write(output, result) needed here -- _run_fetch_package_about's on_flush
    # callback already wrote this exact `result` to `output` once more just before returning.
    counts = Counter(entry["status"] for entry in result.values())
    console.print(f"[green]Done.[/] {len(result)} package(s) written to {output}")
    console.print(
        f"  {counts.get('found', 0)} found, {counts.get('not_found', 0)} not found, "
        f"{counts.get('error', 0)} errored (will retry next run)"
    )


@fetch.command("feedstock-activity")
@click.option(
    "--tiers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("feedstock-tiers.json"),
    show_default=True,
    help="Path to the feedstock tiers JSON file (as produced by `generate feedstock-tiers`).",
)
@click.option(
    "--tier",
    "tiers",
    multiple=True,
    default=("top",),
    show_default=True,
    help="Which tier(s) to fetch activity for. Repeat for multiple.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("feedstock-activity-raw.json"),
    show_default=True,
    help="Path to the raw per-feedstock activity events file to update/create.",
)
@click.option(
    "--since",
    default=None,
    help="Only fetch feedstocks flagged as updated since this timestamp (via the same mono-repo "
    "commit check `fetch feedstocks --since` uses) plus any never-fetched/stale tier members. "
    "Omit for a full fetch of every eligible tier member -- expensive; prefer running that "
    "manually or from an external box, not from the 3-hourly update workflow (same rationale as "
    "the full `maintainer-info` refresh in NOTES.md).",
)
@click.option(
    "--activity-window-days",
    type=int,
    default=365,
    show_default=True,
    help="How many trailing days of merged-PR history to request per feedstock from GitHub's "
    "search API.",
)
@click.option(
    "--force/--resume",
    default=False,
    help="Re-fetch every tier member regardless of --since/staleness (default: resume).",
)
@click.option(
    "--staleness-days",
    type=int,
    default=35,
    show_default=True,
    help="Re-fetch a tier member even if not flagged as updated, once its last fetch is older "
    "than this -- a safety net for activity the --since check might miss.",
)
@click.option(
    "--batch-size",
    type=int,
    default=20,
    show_default=True,
    help="Number of feedstocks aliased into one GraphQL request.",
)
@click.option(
    "--prs-per-feedstock",
    "prs_first",
    type=int,
    default=50,
    show_default=True,
    help="Merged PRs requested per feedstock per page.",
)
@click.option(
    "--reviews-per-pr",
    "reviews_first",
    type=int,
    default=5,
    show_default=True,
    help="Approved reviews requested per PR -- the main GraphQL point-cost lever; tune this down "
    "first if --rate-limit cost reports come back too high.",
)
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    help="Retries for transient errors (403/429/5xx/network) per request.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token. Required in practice -- GraphQL's anonymous rate limit is far too tight "
    "for this command. Prefer setting the GITHUB_TOKEN environment variable over this flag so "
    "the token doesn't end up in your shell history.",
)
@click.option(
    "--rate-limit",
    "requests_per_second",
    type=float,
    default=1.0,
    show_default=True,
    help="Steady-state cap on GraphQL requests/second. Pass 0 to disable pacing entirely.",
)
def fetch_feedstock_activity(
    tiers_file: Path,
    tiers: tuple[str, ...],
    output: Path,
    since: str | None,
    activity_window_days: int,
    force: bool,
    staleness_days: int,
    batch_size: int,
    prs_first: int,
    reviews_first: int,
    retries: int,
    timeout: float,
    token: str | None,
    requests_per_second: float,
) -> None:
    """Fetch merged-PR activity (author, merger, approving reviewer -- bot-filtered) for a tier of
    feedstocks from GitHub's GraphQL search API.

    Reads --tiers-file (as produced by `generate feedstock-tiers`) and restricts to feedstocks in
    --tier. Each request batches --batch-size feedstocks into one aliased GraphQL query. Results
    are merged into --output by PR number, so incremental (--since) runs accumulate history rather
    than overwriting it. Run `generate feedstock-activity` afterward to turn the raw cache into
    feedstock-activity.json.
    """
    console = Console()

    tiers_data = json.loads(tiers_file.read_text(encoding="utf-8"))["feedstocks"]
    tier_set = set(tiers)
    tier_members = sorted(name for name, info in tiers_data.items() if info["tier"] in tier_set)
    tier_by_feedstock = {name: tiers_data[name]["tier"] for name in tier_members}
    console.print(f"{len(tier_members)} feedstocks in tier(s) {sorted(tier_set)}")

    if since is not None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Finding feedstocks updated since {since}...", total=None)
            updated_feedstocks = fetch_updated_feedstocks(
                since,
                token=token,
                on_step=lambda description: progress.update(task, description=description + "..."),
            )
        console.print(f"{len(updated_feedstocks)} feedstocks changed since {since}")
    else:
        updated_feedstocks = None

    store = activity.ActivityStore(output)
    now = datetime.now(timezone.utc)
    todo = [
        name
        for name in tier_members
        if store.should_fetch(name, updated_feedstocks, force, now, timedelta(days=staleness_days))
    ]
    console.print(
        f"{len(todo)} to fetch ({len(tier_members) - len(todo)} already fresh in {output})"
    )

    if not todo:
        console.print("[green]Nothing to do.[/]")
        return

    since_date = (date.today() - timedelta(days=activity_window_days)).isoformat()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Fetching feedstock activity...", total=None)
        asyncio.run(
            activity.run_activity_fetch(
                todo,
                since_date,
                store,
                tier_by_feedstock,
                token,
                batch_size=batch_size,
                prs_first=prs_first,
                reviews_first=reviews_first,
                requests_per_second=requests_per_second,
                retries=retries,
                timeout=timeout,
                on_step=lambda description: progress.update(task, description=description + "..."),
            )
        )

    store.prune_old_events(date.today())
    store.flush()

    console.print(f"[green]Done.[/] Activity for {len(todo)} feedstocks written to {output}")
