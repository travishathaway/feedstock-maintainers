"""CLI: `fetch` caches feedstock recipes to disk, `generate` builds artifacts from that cache."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .cache import RecipeCache
from .github import Cooldown, FetchError, RatePacer, fetch_recipe, fetch_user_info
from .gitmodules import FeedstockSource, parse_gitmodules
from .graph_data import build_graph
from .recipe import ParseError, extract_maintainers_from_text

_DEFAULT_RATE_LIMIT_NO_TOKEN = 0.5
_DEFAULT_RATE_LIMIT_WITH_TOKEN = 1.2


def _atomic_write(output: Path, data: dict) -> None:
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)


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
        info = await fetch_user_info(client, username, cooldown, pacer, retries=retries, token=token)
    except FetchError as exc:
        errors.append((username, str(exc)))
        not_found[username] = f"fetch failed after retries: {exc}"
        return

    if info is None:
        not_found[username] = "GitHub user not found (HTTP 404) -- account may have been deleted or renamed"
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
                await _fetch_user_one(username, client, retries, cooldown, pacer, token, results, not_found, errors)

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


@click.group()
def main() -> None:
    """Track conda-forge feedstock maintainers: fetch recipes, then generate artifacts from them."""


@main.group()
def fetch() -> None:
    """Fetch data from GitHub: feedstock recipes, or maintainer profile info."""


@fetch.command("feedstocks")
@click.option(
    "--feedstocks-repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Path to a local checkout of conda-forge/feedstocks (must contain .gitmodules).",
)
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
def fetch_feedstocks(
    feedstocks_repo: Path,
    cache_dir: Path,
    force: bool,
    flush_every: int,
    concurrency: int,
    timeout: float,
    retries: int,
    token: str | None,
    requests_per_second: float | None,
) -> None:
    """Download and cache each feedstock's raw recipe file from GitHub.

    Reads feedstock names and their GitHub repo/branch from
    <feedstocks-repo>/.gitmodules, then fetches only recipe/recipe.yaml (or
    recipe/meta.yaml) for each -- no submodule checkout needed -- and stores
    the raw text under --cache-dir. Run `generate maintainers` afterward to
    turn the cache into maintainers.json.
    """
    console = Console()

    gitmodules = feedstocks_repo / ".gitmodules"
    if not gitmodules.exists():
        raise click.ClickException(
            f"No .gitmodules found in {feedstocks_repo}. Pass --feedstocks-repo pointing at "
            "a local checkout of conda-forge/feedstocks."
        )

    if requests_per_second is None:
        requests_per_second = _DEFAULT_RATE_LIMIT_WITH_TOKEN if token else _DEFAULT_RATE_LIMIT_NO_TOKEN

    mode = "authenticated Contents API" if token else "anonymous raw.githubusercontent.com"
    console.print(f"Fetching via {mode}, paced at {requests_per_second:g} req/s")

    all_sources = parse_gitmodules(gitmodules)
    console.print(f"Discovered {len(all_sources)} feedstocks in {gitmodules}")

    cache = RecipeCache(cache_dir)
    todo = [source for name, source in sorted(all_sources.items()) if cache.should_fetch(name, force)]

    console.print(f"{len(todo)} to fetch ({len(all_sources) - len(todo)} already cached in {cache_dir})")

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
@click.argument("maintainers_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
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

    Reads the unique set of GitHub usernames out of MAINTAINERS_FILE (as produced by
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
        not_found = {name: reason for name, reason in previously_not_found.items() if name in all_names}

    todo = [name for name in usernames if force or name not in existing]

    console.print(
        f"Discovered {len(usernames)} unique maintainer usernames "
        f"({len(team_handles)} team handles skipped)"
    )
    console.print(f"{len(todo)} to fetch ({len(usernames) - len(todo)} already in {output})")

    if requests_per_second is None:
        requests_per_second = _DEFAULT_RATE_LIMIT_WITH_TOKEN if token else _DEFAULT_RATE_LIMIT_NO_TOKEN

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
        console.print(f"[yellow]{len(not_found)}[/] usernames could not be resolved, recorded in {not_found_output}")
    if errors:
        console.print(f"[red]{len(errors)}[/] usernames failed to fetch:")
        for name, message in errors[:20]:
            console.print(f"  - {name}: {message}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


@main.group()
def generate() -> None:
    """Build artifacts from the local recipe cache. No network access."""


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
def generate_maintainers(cache_dir: Path, output: Path) -> None:
    """Read cached recipe files and write each feedstock's extra.recipe-maintainers to --output."""
    console = Console()
    cache = RecipeCache(cache_dir)

    results: dict[str, list] = {}
    errors: list[tuple[str, str]] = []
    for entry in cache.found_entries():
        text = cache.read_text(entry)
        try:
            results[entry.name] = extract_maintainers_from_text(entry.filename, text)
        except ParseError as exc:
            errors.append((entry.name, str(exc)))

    _atomic_write(output, results)

    console.print(f"[green]Done.[/] {len(results)} feedstocks recorded in {output}")
    not_found = cache.counts()["not_found"]
    if not_found:
        console.print(f"[yellow]{not_found}[/] cached feedstocks had no recipe file")
    if errors:
        console.print(f"[red]{len(errors)}[/] feedstocks failed to parse:")
        for name, message in errors[:20]:
            console.print(f"  - {name}: {message}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


@generate.command("graph-data")
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
    default=Path("graph-data.json"),
    show_default=True,
    help="Path to write the graphology-format maintainer collaboration graph JSON.",
)
def generate_graph_data(maintainers_file: Path, maintainer_info_file: Path, output: Path) -> None:
    """Build a maintainer collaboration graph from --maintainers-file and --maintainer-info-file.

    Nodes are maintainers with profile info in --maintainer-info-file; edges connect maintainers
    who co-maintain at least one feedstock, weighted by the number of shared feedstocks. Team
    handles (e.g. "conda-forge/go") and usernames missing profile info are excluded.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    maintainer_info_data = json.loads(maintainer_info_file.read_text(encoding="utf-8"))

    graph = build_graph(maintainers_data, maintainer_info_data)
    _atomic_write(output, graph)

    console.print(f"[green]Done.[/] {len(graph['nodes'])} nodes, {len(graph['edges'])} edges written to {output}")


if __name__ == "__main__":
    main()
