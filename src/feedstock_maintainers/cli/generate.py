"""`generate` builds artifacts from the local recipe cache and other fetched inputs."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

import rich_click as click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .. import activity, countries, feedstock_health, feedstock_tiers
from .. import feedstock_count_history as fch
from ..cache import RecipeCache
from ..graph_data import (
    build_graph,
    build_package_graph,
    build_package_maintainers,
    compute_maintainer_coverage,
    find_transitive_only_dependencies,
    package_graph_to_json,
)
from ..maintainer_history import append_current_point
from ..recipe import ParseError, parse_recipe
from ..repodata import RepodataFetchError
from ..site_data import (
    build_maintainer_profiles,
    build_package_list_row,
    build_package_profiles,
    build_search_index,
    compute_maintainer_overview,
    compute_package_overview,
)
from ..teams import listed_maintainer_counts
from ._shared import (
    _atomic_write,
    _copy_if_exists,
    _load_json_if_exists,
    _run_fetch_repodata,
    _write_json_fast,
)


@click.group()
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
@click.option(
    "--license-output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("licenses.json"),
    show_default=True,
    help="Path to write each feedstock's declared license.",
)
def generate_maintainers(
    cache_dir: Path, output: Path, package_names_output: Path, license_output: Path
) -> None:
    """Read cached recipe files and write each feedstock's maintainers, package name(s), and
    license.

    Writes --output: {feedstock: [extra.recipe-maintainers, ...]}. Also writes
    --package-names-output: {feedstock: [package_name, ...]} -- the real, installable conda
    package name(s) declared by the recipe (more than one for a multi-output feedstock) -- for
    use by `generate package-maintainers`. Also writes --license-output: {feedstock: license} --
    the recipe's top-level `about.license`, omitted for feedstocks that don't declare one.
    """
    console = Console()
    cache = RecipeCache(cache_dir)

    maintainers: dict[str, list] = {}
    package_names: dict[str, list[str]] = {}
    licenses: dict[str, str] = {}
    errors: list[tuple[str, str]] = []
    for entry in cache.found_entries():
        text = cache.read_text(entry)
        assert entry.filename is not None
        try:
            names, packages, license_ = parse_recipe(entry.filename, text)
        except ParseError as exc:
            errors.append((entry.name, str(exc)))
            continue
        maintainers[entry.name] = names
        package_names[entry.name] = packages
        if license_ is not None:
            licenses[entry.name] = license_

    _atomic_write(output, maintainers)
    _atomic_write(package_names_output, package_names)
    _atomic_write(license_output, licenses)

    console.print(
        f"[green]Done.[/] {len(maintainers)} feedstocks recorded in {output}, "
        f"{package_names_output}, and {license_output}"
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
    "--feedstock-activity-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-activity.json"),
    show_default=True,
    help="Path to feedstock activity data (as produced by `generate feedstock-activity`). Skipped "
    "with a warning (not an error) if it doesn't exist yet -- every node's activeFeedstockCount "
    "is then 0.",
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
    maintainers_file: Path, maintainer_info_file: Path, feedstock_activity_file: Path, output: Path
) -> None:
    """Build a maintainer collaboration graph from --maintainers-file and --maintainer-info-file.

    Nodes are maintainers with profile info in --maintainer-info-file; edges connect maintainers
    who co-maintain at least one feedstock, weighted by the number of shared feedstocks. Team
    handles (e.g. "conda-forge/go") and usernames missing profile info are excluded.
    """
    console = Console()

    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    maintainer_info_data = json.loads(maintainer_info_file.read_text(encoding="utf-8"))
    feedstock_activity_data = _load_json_if_exists(
        feedstock_activity_file, console, "every node's activeFeedstockCount will be 0"
    ).get("feedstocks", {})

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
            activity=feedstock_activity_data,
        )

    _atomic_write(output, graph)

    console.print(
        f"[green]Done.[/] {len(graph['nodes'])} nodes, {len(graph['edges'])} "
        f"edges written to {output}"
    )


@generate.command("maintainer-countries")
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
    default=Path("maintainer-countries.json"),
    show_default=True,
    help="Path to write the country tally JSON file.",
)
@click.option(
    "--overrides",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help='Extra JSON {"location string": "Country" or null} overrides merged on top of the '
    "built-in table.",
)
@click.option(
    "--report",
    is_flag=True,
    default=False,
    help="Print which raw locations resolved to each country, plus the unresolved ones, and "
    "exit without writing --output.",
)
def generate_maintainer_countries(
    maintainer_info_file: Path, output: Path, overrides: Path | None, report: bool
) -> None:
    """Tally which countries maintainers come from, resolved offline from each GitHub user's
    free-text `location` field (see `countries.py` for the resolution rules).

    Writes --output: [{"country": ..., "iso_numeric": ..., "iso_alpha2": ..., "count": ...}, ...]
    sorted by count descending. Pass --report to instead print which raw locations resolved to
    each country plus the unresolved ones, for auditing -- check this before trusting the
    numbers, since free text is messy and new data may need new entries in countries.OVERRIDES.
    """
    console = Console()

    locations = countries.load_locations(maintainer_info_file, "location")
    resolved_overrides = countries.build_overrides(overrides)
    tally, unresolved, resolved = countries.tally_countries(locations, resolved_overrides)

    if report:
        by_country: dict[str, dict[str, int]] = {}
        for user, country in resolved.items():
            by_country.setdefault(country, {})
            loc = locations[user]
            by_country[country][loc] = by_country[country].get(loc, 0) + 1
        for country, n in tally.most_common():
            examples = "; ".join(
                loc for loc, _ in sorted(by_country[country].items(), key=lambda kv: -kv[1])[:25]
            )
            console.print(f"== {country} ({n}): {examples}")
        console.print(f"\nUNRESOLVED ({sum(unresolved.values())}):")
        for loc, n in unresolved.most_common():
            console.print(f"  {n:3d}  {loc!r}")
        return

    _atomic_write(output, countries.to_rows(tally))

    with_location = sum(1 for loc in locations.values() if loc)
    console.print(
        f"[green]Done.[/] {len(locations)} users, {with_location} with a location, "
        f"{len(resolved)} resolved to {len(tally)} countries "
        f"({sum(unresolved.values())} unresolved), written to {output}"
    )


@generate.command("listed-maintainer-counts")
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--team-members-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("team-members.json"),
    show_default=True,
    help="Transient team handle -> members file (from `fetch team-members`). Never publish or "
    "archive it. Skipped with a warning if it doesn't exist.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("listed-maintainer-counts.json"),
    show_default=True,
    help="Path to write the {feedstock: number of distinct people} JSON file.",
)
def generate_listed_maintainer_counts(
    maintainers_file: Path, team_members_file: Path, output: Path
) -> None:
    """Count the distinct people behind each feedstock, with team members included.

    Writes only numbers -- never who is in a team. Run this right after `fetch team-members` and
    delete --team-members-file afterwards.
    """
    console = Console()
    result = listed_maintainer_counts(
        json.loads(maintainers_file.read_text(encoding="utf-8")),
        _load_json_if_exists(team_members_file, console, "team handles will count no members"),
    )
    _atomic_write(output, result)
    console.print(f"[green]Done.[/] Counts for {len(result)} feedstocks written to {output}")


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

    Answers "who maintains package X" directly: {package_name: [maintainer_login, ...]}. Team
    handles (e.g. conda-forge/r) are kept as-is and never expanded: team membership is private
    (see `generate listed-maintainer-counts` for the head count).
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
@click.option(
    "--no-repodata-cache",
    is_flag=True,
    default=False,
    help="Always re-download repodata instead of reusing ./repodata_cache (entries younger than "
    "an hour are reused as-is; older ones are revalidated by ETag).",
)
def generate_package_graph_data(
    platforms: tuple[str, ...], output: Path, timeout: float, no_repodata_cache: bool
) -> None:
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
        repodata_by_platform = asyncio.run(
            _run_fetch_repodata(list(platforms), console, timeout, not no_repodata_cache)
        )
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
            repodata_by_platform,
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
@click.option(
    "--no-repodata-cache",
    is_flag=True,
    default=False,
    help="Always re-download repodata instead of reusing ./repodata_cache (entries younger than "
    "an hour are reused as-is; older ones are revalidated by ETag).",
)
def generate_transitive_dependencies(
    platforms: tuple[str, ...], output: Path, top: int, timeout: float, no_repodata_cache: bool
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
        repodata_by_platform = asyncio.run(
            _run_fetch_repodata(list(platforms), console, timeout, not no_repodata_cache)
        )
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
            repodata_by_platform,
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


@generate.command("feedstock-tiers")
@click.option(
    "--package-names-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-names.json"),
    show_default=True,
    help="Path to the package names JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--package-downloads-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("package-downloads.json"),
    show_default=True,
    help="Path to monthly package download totals (as produced by `fetch package-downloads`).",
)
@click.option(
    "--transitive-dependencies-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("transitive-dependencies.json"),
    show_default=True,
    help="Path to the ranking JSON file (as produced by `generate transitive-dependencies`).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("feedstock-tiers.json"),
    show_default=True,
    help="Path to write the feedstock tiers JSON file.",
)
@click.option(
    "--top-downloads-fraction",
    type=float,
    default=0.10,
    show_default=True,
    help="Fraction of feedstocks, ranked by last month's downloads, to put in the top tier.",
)
@click.option(
    "--top-transitive-fraction",
    type=float,
    default=0.05,
    show_default=True,
    help="Fraction of feedstocks, ranked by transitive dependents, unioned into the top tier -- "
    "catches low-download-but-structurally-critical packages (compilers, toolchains) that "
    "download ranking alone would miss.",
)
def generate_feedstock_tiers(
    package_names_file: Path,
    package_downloads_file: Path,
    transitive_dependencies_file: Path,
    output: Path,
    top_downloads_fraction: float,
    top_transitive_fraction: float,
) -> None:
    """Rank feedstocks by popularity/importance and split them into a "top" tier plus everyone
    else.

    Feeds `fetch feedstock-activity`: only the top tier gets the expensive per-feedstock GitHub
    activity fetch. A feedstock's score is the max across its output package name(s) (see
    --package-names-file).
    """
    console = Console()

    package_names_data = json.loads(package_names_file.read_text(encoding="utf-8"))
    package_downloads_data = json.loads(package_downloads_file.read_text(encoding="utf-8"))
    transitive_data = json.loads(transitive_dependencies_file.read_text(encoding="utf-8"))[
        "packages"
    ]

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = feedstock_tiers.compute_feedstock_tiers(
        package_names_data,
        package_downloads_data,
        transitive_data,
        generated_at,
        top_downloads_fraction=top_downloads_fraction,
        top_transitive_fraction=top_transitive_fraction,
    )
    _atomic_write(output, result)

    top_count = sum(1 for info in result["feedstocks"].values() if info["tier"] == "top")
    console.print(
        f"[green]Done.[/] {top_count} of {len(result['feedstocks'])} feedstocks in the top tier, "
        f"written to {output}"
    )


@generate.command("feedstock-activity")
@click.option(
    "--raw-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-activity-raw.json"),
    show_default=True,
    help="Path to the raw per-feedstock activity events file (as produced by `fetch "
    "feedstock-activity`). Skipped with a warning (not an error) if it doesn't exist yet.",
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
    default=Path("feedstock-activity.json"),
    show_default=True,
    help="Path to write the final, window-pruned feedstock activity JSON file.",
)
@click.option(
    "--window-months",
    type=int,
    default=12,
    show_default=True,
    help="Trailing months of activity to keep in --output.",
)
def generate_feedstock_activity(
    raw_file: Path,
    maintainers_file: Path,
    output: Path,
    window_months: int,
) -> None:
    """Prune raw per-feedstock activity to the trailing window and join it against declared
    maintainers.

    Pure and offline -- no network access. Reads --raw-file (as produced by `fetch
    feedstock-activity`) and --maintainers-file, and writes, per feedstock covered by --raw-file,
    which logins were active in the trailing --window-months (author/merger/approving reviewer,
    bot-filtered, with monthly counts) alongside the declared-vs-active set differences
    (declared_and_active / declared_not_active / active_not_declared).
    """
    console = Console()

    raw_data = _load_json_if_exists(
        raw_file, console, "feedstock-activity.json will have no covered feedstocks"
    )
    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))

    raw_entries = {
        name: activity.ActivityEntry(
            tier=fields.get("tier", "long_tail"),
            fetched_at=fields["fetched_at"],
            events=fields.get("events", []),
            bot_merged_count=fields.get("bot_merged_count", 0),
        )
        for name, fields in raw_data.items()
    }

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = activity.build_feedstock_activity(
        raw_entries, maintainers_data, generated_at, window_months=window_months
    )
    _atomic_write(output, result)

    console.print(
        f"[green]Done.[/] Activity computed for {len(result['feedstocks'])} feedstocks, "
        f"written to {output}"
    )


@generate.command("feedstock-health")
@click.option(
    "--signals-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-health-signals-raw.json"),
    show_default=True,
    help="Raw signals file from `fetch feedstock-health-signals`. Skipped with a warning if it "
    "doesn't exist yet.",
)
@click.option(
    "--maintainers-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("maintainers.json"),
    show_default=True,
    help="Path to the maintainers JSON file (as produced by `generate maintainers`).",
)
@click.option(
    "--activity-raw-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-activity-raw.json"),
    show_default=True,
    help="Raw merged-PR activity (for the last human merge). Optional.",
)
@click.option(
    "--activity-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-activity.json"),
    show_default=True,
    help="Processed activity (for active maintainer counts). Optional.",
)
@click.option(
    "--package-names-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("package-names.json"),
    show_default=True,
    help="Feedstock -> package names, used to join dependency exposure. Optional.",
)
@click.option(
    "--transitive-dependencies-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("transitive-dependencies.json"),
    show_default=True,
    help="Dependency ranking from `generate transitive-dependencies`. Optional.",
)
@click.option(
    "--listed-counts-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("listed-maintainer-counts.json"),
    show_default=True,
    help="Feedstock -> head count including team members (from `generate "
    "listed-maintainer-counts`). Optional: without it team handles contribute no people.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("feedstock-health.json"),
    show_default=True,
    help="Path to write the feedstock health JSON file.",
)
@click.option(
    "--top",
    "-n",
    type=int,
    default=25,
    show_default=True,
    help="Number of rows to print in the lowest-scoring ranking.",
)
def generate_feedstock_health(
    listed_counts_file: Path,
    signals_file: Path,
    maintainers_file: Path,
    activity_raw_file: Path,
    activity_file: Path,
    package_names_file: Path,
    transitive_dependencies_file: Path,
    output: Path,
    top: int,
) -> None:
    """Compute a relative, explainable health score for every feedstock with collected signals.

    Pure and offline. Combines recency of human activity, maintainer coverage, open PR backlog,
    and open issues into a 0-100 score (with per-component breakdown), and uses dependency
    exposure to decide when a clearly dormant feedstock is worth surfacing. Prints the
    lowest-scoring feedstocks as a demo ranking.
    """
    console = Console()

    signals = _load_json_if_exists(
        signals_file, console, "feedstock-health.json will have no covered feedstocks"
    )
    maintainers_data = json.loads(maintainers_file.read_text(encoding="utf-8"))
    activity_data = _load_json_if_exists(activity_file, console, "active counts unavailable")
    raw_activity = _load_json_if_exists(activity_raw_file, console, "last merged PR unavailable")
    package_names = _load_json_if_exists(package_names_file, console, "exposure unavailable")
    transitive = _load_json_if_exists(transitive_dependencies_file, console, "exposure unavailable")

    last_merged: dict[str, str] = {}
    for name, fields in raw_activity.items():
        merged = [
            event["merged_at"] for event in fields.get("events", []) if event.get("merged_at")
        ]
        if merged:
            last_merged[name] = max(merged)

    records_by_package = {r["name"]: r for r in transitive.get("packages", [])}
    feedstock_exposure: dict[str, dict] = {}
    for feedstock, packages in package_names.items():
        found = [
            records_by_package[p] for p in (packages or [feedstock]) if p in records_by_package
        ]
        if found:
            feedstock_exposure[feedstock] = {
                "transitive_dependents": max(r["transitive_dependents"] for r in found),
                "transitive_only_ratio": max(r["transitive_only_ratio"] for r in found),
            }

    listed_counts = _load_json_if_exists(
        listed_counts_file, console, "team members will not be counted"
    )

    result = feedstock_health.build_feedstock_health(
        signals,
        maintainers_data,
        listed_counts,
        activity_data.get("feedstocks", {}),
        last_merged,
        feedstock_exposure,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _atomic_write(output, result)

    ranked = sorted(result["feedstocks"].items(), key=lambda item: (item[1]["score"], item[0]))[
        :top
    ]
    table = Table(title=f"{len(ranked)} lowest relative health scores")
    table.add_column("Feedstock")
    table.add_column("Score", justify="right")
    table.add_column("Tier")
    table.add_column("Last human activity")
    table.add_column("Dependents", justify="right")
    for name, record in ranked:
        table.add_row(
            name,
            f"{record['score']:.1f}",
            record["tier"],
            (record["last_activity_at"] or "none found")[:10],
            str(record["exposure"]["transitive_dependents"] or 0),
        )
    console.print(table)
    console.print(
        f"[green]Done.[/] Health computed for {len(result['feedstocks'])} feedstocks, "
        f"written to {output}"
    )


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
    "--license-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("licenses.json"),
    show_default=True,
    help="Path to each feedstock's declared license (as produced by `generate maintainers "
    "--license-output`). Skipped with a warning (not an error) if it doesn't exist yet.",
)
@click.option(
    "--feedstock-activity-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-activity.json"),
    show_default=True,
    help="Path to feedstock activity data (as produced by `generate feedstock-activity`). Skipped "
    "with a warning (not an error) if it doesn't exist yet -- package profiles then have "
    "active_maintainer_count: null for every package (no activity data collected, not zero).",
)
@click.option(
    "--listed-counts-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("listed-maintainer-counts.json"),
    show_default=True,
    help="Feedstock -> head count including team members (from `generate "
    "listed-maintainer-counts`). Skipped with a warning if it doesn't exist.",
)
@click.option(
    "--feedstock-health-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("feedstock-health.json"),
    show_default=True,
    help="Path to feedstock health data (as produced by `generate feedstock-health`). Skipped "
    "with a warning (not an error) if it doesn't exist yet -- package profiles then have "
    "health: null and package-list.json is empty.",
)
@click.option(
    "--package-about-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("package-about.json"),
    show_default=True,
    help="Path to per-package about.json metadata (as produced by `fetch package-about`). Skipped "
    "with a warning (not an error) if it doesn't exist yet -- package profiles then have "
    "about: null for every package.",
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
    "--maintainer-countries-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("maintainer-countries.json"),
    show_default=True,
    help="Path to the maintainer country tally (as produced by `generate "
    "maintainer-countries`). Copied as-is into --output-dir; skipped with a warning (not an "
    "error) if it doesn't exist yet.",
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
    license_file: Path,
    feedstock_activity_file: Path,
    feedstock_health_file: Path,
    listed_counts_file: Path,
    package_about_file: Path,
    maintainer_history_file: Path,
    feedstock_count_history_file: Path,
    maintainer_countries_file: Path,
    output_dir: Path,
) -> None:
    """Build small, page-ready JSON payloads for the statistics/profile pages.

    Reads every existing root artifact (maintainers, maintainer profiles, both collaboration and
    dependency graphs, package downloads, transitive dependency rankings, feedstock activity) and
    writes:
    --output-dir/maintainer-overview.json, --output-dir/package-overview.json,
    --output-dir/maintainers/index.json plus one --output-dir/maintainers/<login>.json per
    maintainer with a GitHub profile, and --output-dir/packages/index.json plus one
    --output-dir/packages/<name>.json per package (each carrying a link to its feedstock(s) on
    GitHub, plus --license-file's declared license when known). Also copies
    --maintainer-history-file, --feedstock-count-history-file, and --maintainer-countries-file
    into --output-dir unchanged (they're independently-maintained artifacts, not derived here --
    see `generate maintainer-history-append`/`generate feedstock-count-append`/`generate
    maintainer-countries`). Meant to run only at deploy time (see .github/workflows/pages.yml) --
    nothing it writes is committed to git.
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
    licenses_data = _load_json_if_exists(
        license_file, console, "package profiles will have no license shown"
    )
    feedstock_activity_data = _load_json_if_exists(
        feedstock_activity_file, console, "package profiles will have active_maintainer_count: null"
    ).get("feedstocks", {})
    feedstock_health_data = _load_json_if_exists(
        feedstock_health_file, console, "package profiles will have health: null"
    )
    package_about_data = _load_json_if_exists(
        package_about_file, console, "package profiles will have about: null"
    )
    listed_counts_data = _load_json_if_exists(
        listed_counts_file, console, "team members will not be counted"
    )

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        package_names_data,
        listed_counts_data,
    )
    _atomic_write(output_dir / "package-overview.json", package_overview)

    console.print(f"Writing {len(maintainer_info_data)} maintainer profile(s)...")
    maintainer_logins = []
    feedstock_counts: dict[str, int] = {}
    for login, profile in build_maintainer_profiles(
        maintainers_data, maintainer_info_data, maintainer_graph_data, package_names_data
    ):
        maintainer_logins.append(login)
        feedstock_counts[login] = profile.get("feedstock_count", 0)
        _write_json_fast(maintainers_dir / f"{login}.json", profile)
    _atomic_write(maintainers_dir / "index.json", sorted(maintainer_logins))

    console.print(f"Writing {len(package_maintainers_data)} package profile(s)...")
    package_names_list = []
    package_list_rows = []
    transitive_by_name = {record["name"]: record for record in transitive_dependencies_data}
    for name, profile in build_package_profiles(
        package_maintainers_data,
        maintainer_info_data,
        package_graph_data,
        transitive_dependencies_data,
        package_downloads_data,
        package_names_data,
        licenses_data,
        feedstock_activity_data,
        package_about_data,
        feedstock_health_data.get("feedstocks", {}),
        listed_counts_data,
    ):
        package_names_list.append(name)
        _write_json_fast(packages_dir / f"{name}.json", profile)
        row = build_package_list_row(profile, transitive_by_name.get(name))
        if row is not None:
            package_list_rows.append(row)
    _atomic_write(packages_dir / "index.json", sorted(package_names_list))
    _write_json_fast(
        output_dir / "package-list.json",
        {
            "generated_at": generated_at,
            "config": feedstock_health_data.get("config"),
            "packages": package_list_rows,
        },
    )

    console.print("Building search-index.json...")
    _write_json_fast(
        output_dir / "search-index.json",
        build_search_index(
            {login: maintainer_info_data[login] for login in maintainer_logins},
            feedstock_counts,
            package_names_list,
            package_downloads_data,
        ),
    )

    _copy_if_exists(maintainer_history_file, output_dir / "maintainer-history.json", console)
    _copy_if_exists(
        feedstock_count_history_file, output_dir / "feedstock-count-history.json", console
    )
    _copy_if_exists(maintainer_countries_file, output_dir / "maintainer-countries.json", console)

    console.print(
        f"[green]Done.[/] Site data written to {output_dir} "
        f"({len(maintainer_logins)} maintainers, {len(package_names_list)} packages)"
    )
