"""CLI: fetch each feedstock's recipe straight from GitHub, write maintainers JSON incrementally."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .github import Cooldown, FetchError, RatePacer, fetch_recipe
from .gitmodules import FeedstockSource, parse_gitmodules
from .recipe import ParseError, extract_maintainers_from_text

_DEFAULT_RATE_LIMIT_NO_TOKEN = 0.5
_DEFAULT_RATE_LIMIT_WITH_TOKEN = 1.2

# src/feedstock_maintainers/cli.py -> feedstock_maintainers -> src -> feedstock-maintainers -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_GITMODULES = _REPO_ROOT / ".gitmodules"


def _load_existing(output: Path, console: Console) -> dict:
    if not output.exists():
        return {}
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        console.print(f"[yellow]Warning:[/] could not parse existing {output}, starting fresh")
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(output: Path, data: dict) -> None:
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)


async def _process_one(
    source: FeedstockSource,
    client: httpx.AsyncClient,
    retries: int,
    cooldown: Cooldown,
    pacer: RatePacer,
    token: str | None,
) -> tuple[str, list | None, str | None]:
    """Returns (name, maintainers, error). maintainers is None when no recipe file exists."""
    try:
        fetched = await fetch_recipe(client, source, cooldown, pacer, retries=retries, token=token)
    except FetchError as exc:
        return source.name, None, str(exc)

    if fetched is None:
        return source.name, None, None

    try:
        maintainers = extract_maintainers_from_text(fetched.filename, fetched.text)
    except ParseError as exc:
        return source.name, None, str(exc)

    return source.name, maintainers, None


async def _run(
    sources: list[FeedstockSource],
    console: Console,
    output: Path,
    results: dict,
    concurrency: int,
    timeout: float,
    retries: int,
    flush_every: int,
    token: str | None,
    requests_per_second: float,
) -> tuple[int, list[tuple[str, str]]]:
    skipped = 0
    errors: list[tuple[str, str]] = []
    pending_flush = 0

    def flush() -> None:
        _atomic_write(output, results)

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    cooldown = Cooldown()
    pacer = RatePacer(requests_per_second)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:

        async def bound(source: FeedstockSource):
            async with semaphore:
                return await _process_one(source, client, retries, cooldown, pacer, token)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching feedstock recipes", total=len(sources))
            for coro in asyncio.as_completed([bound(source) for source in sources]):
                name, maintainers, error = await coro
                if error is not None:
                    errors.append((name, error))
                elif maintainers is None:
                    skipped += 1
                else:
                    results[name] = maintainers
                    pending_flush += 1
                    if pending_flush >= flush_every:
                        flush()
                        pending_flush = 0
                progress.advance(task)

    if pending_flush:
        flush()

    return skipped, errors


@click.command()
@click.option(
    "--gitmodules",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_DEFAULT_GITMODULES,
    show_default=True,
    help="Path to the .gitmodules file listing feedstocks (name -> GitHub repo/branch).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the incremental JSON output file.",
)
@click.option(
    "--force/--resume",
    default=False,
    help="Reprocess feedstocks already present in the output file (default: resume/skip them).",
)
@click.option(
    "--flush-every",
    type=int,
    default=20,
    show_default=True,
    help="Write the output file to disk after this many newly fetched feedstocks.",
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
def main(
    gitmodules: Path,
    output: Path,
    force: bool,
    flush_every: int,
    concurrency: int,
    timeout: float,
    retries: int,
    token: str | None,
    requests_per_second: float | None,
) -> None:
    """Fetch every feedstock's recipe from GitHub and record its maintainers.

    Reads the feedstock names and their GitHub repo/branch straight from
    .gitmodules, then fetches only recipe/recipe.yaml (or recipe/meta.yaml)
    for each -- no submodule checkout needed.
    """
    console = Console()

    if requests_per_second is None:
        requests_per_second = _DEFAULT_RATE_LIMIT_WITH_TOKEN if token else _DEFAULT_RATE_LIMIT_NO_TOKEN

    mode = "authenticated Contents API" if token else "anonymous raw.githubusercontent.com"
    console.print(f"Fetching via {mode}, paced at {requests_per_second:g} req/s")

    all_sources = parse_gitmodules(gitmodules)
    console.print(f"Discovered {len(all_sources)} feedstocks in {gitmodules}")

    results = {} if force else _load_existing(output, console)
    todo = [source for name, source in sorted(all_sources.items()) if force or name not in results]

    console.print(f"{len(todo)} to fetch ({len(all_sources) - len(todo)} already in {output})")

    if not todo:
        console.print("[green]Nothing to do.[/]")
        return

    skipped, errors = asyncio.run(
        _run(
            todo,
            console,
            output,
            results,
            concurrency,
            timeout,
            retries,
            flush_every,
            token,
            requests_per_second,
        )
    )

    console.print(f"[green]Done.[/] {len(results)} feedstocks recorded in {output}")
    if skipped:
        console.print(
            f"[yellow]{skipped}[/] feedstocks had no recipe/recipe.yaml or recipe/meta.yaml on GitHub"
        )
    if errors:
        console.print(f"[red]{len(errors)}[/] feedstocks failed:")
        for name, message in errors[:20]:
            console.print(f"  - {name}: {message}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


if __name__ == "__main__":
    main()
