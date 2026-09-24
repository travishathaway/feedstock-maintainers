"""Pre-aggregate small, page-ready JSON payloads for the statistics/profile pages.

Mirrors the style of `graph_data.py`: every function here is pure (no file I/O -- the `generate
site-data` CLI command in `cli.py` handles reading inputs and writing outputs) and operates on
the already-parsed contents of the root `*.json` artifacts:

  - `maintainers.json`: {feedstock: [maintainer_login, ...]}
  - `maintainer-info.json`: {login: <GitHub user info>} -- the authoritative "has a profile" set.
    A login absent from this dict never gets a profile page or a profile link, whether it's a
    team handle (contains "/") or a real user whose GitHub fetch 404'd.
  - `maintainer-graph.json`: graphology-format collaboration graph (see `graph_data.build_graph`).
  - `package-names.json`: {feedstock: [package_name, ...]}
  - `package-maintainers.json`: {package_name: [maintainer_login, ...]}
  - `package-downloads.json`: {package_name: {"YYYY-MM": total_downloads}}
  - `package-graph.json`: graphology-format dependency graph (see `graph_data.build_package_graph`
    / `package_graph_to_json`) -- edge direction is package -> dependency.
  - `transitive-dependencies.json`: {"packages": [{name, direct_dependents, transitive_dependents,
    transitive_only_dependents, transitive_only_ratio}, ...]}, on-disk order sorted by
    `transitive_only_dependents` (NOT `transitive_dependents` -- always re-sort before using it
    for a "most depended-on" ranking).
  - `licenses.json`: {feedstock: license} (as produced by `generate maintainers
    --license-output`), omitted for a feedstock whose recipe declares none. Optional -- may not
    exist for an older recipe cache, in which case every package profile's `license` is `None`.
  - `feedstock-activity.json`'s `"feedstocks"` sub-object (as produced by
    `activity.build_feedstock_activity`): {feedstock: {tier, active_maintainers, ...}} for the
    popularity-thresholded tier of feedstocks `activity.py` covers. Optional and partial by
    design -- a feedstock absent from it means "no activity data collected", not "zero active
    maintainers"; every function here distinguishes that `None` from an explicit `0` (see
    `compute_status`, `active_maintainer_count_for_feedstocks`).

Design decisions worth calling out (kept consistent across every output so the same number never
looks different on two pages):

  - A "team handle" is any maintainer login containing "/" (e.g. "conda-forge/go"). It is always
    excluded from any *count* of maintainers, but *is* still included in per-entity maintainer
    lists (e.g. a package's `maintainers` array) so the UI can still show it -- just as
    unlinkable plain text, since it has no `maintainer-info.json` entry.
  - Any login missing from `maintainer-info.json` (team handle or unresolved/404'd real user) is
    treated the same way for linking purposes: in every `{"login", "name"}`-shaped entry produced
    here, `name` is `None` exactly when that login has no profile page, and a real display name
    (falling back to the login itself) otherwise. Frontend contract: link to
    `/maintainers/<login>` only when `name` is not `None`.
  - conda's synthetic virtual packages (names starting with "__", e.g. "__glibc", "__unix",
    "__win", "__osx", "__cuda", "__archspec") are solver-injected platform markers, not real
    feedstock-built packages -- they never appear in `package-maintainers.json` and have no
    profile page. They're excluded from every package ranking/list here (`most_depended_on`,
    `transitive_dependencies`, and a package's own `direct_dependencies`/`notable_dependents`)
    since otherwise they'd dominate rankings (e.g. "__glibc" outranks every real package by
    transitive dependents) or show up as an unlinkable, meaningless dependency chip.
"""

from __future__ import annotations

import statistics
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

STATUS_AT_RISK = "at_risk"
STATUS_WATCH = "watch"
STATUS_HEALTHY = "healthy"

_TOP_MAINTAINERS_LIMIT = 10
_POPULAR_PACKAGES_LIMIT = 10
_RISK_PACKAGES_LIMIT = 6
_TRANSITIVE_DEPENDENCIES_LIMIT = 6
_CO_MAINTAINERS_LIMIT = 12
_NOTABLE_DEPENDENTS_LIMIT = 8

_FEEDSTOCK_URL_TEMPLATE = "https://github.com/conda-forge/{feedstock}-feedstock"


def is_team_handle(login: str) -> bool:
    """A maintainer login is a team handle (e.g. "conda-forge/go"), not an individual user, iff
    it contains "/"."""
    return "/" in login


def is_virtual_package(name: str) -> bool:
    """conda's synthetic virtual packages (solver-injected platform markers such as "__glibc",
    "__unix", "__win") are conventionally named with a leading "__" and never correspond to a
    real, feedstock-built package."""
    return name.startswith("__")


def team_handles(logins: Iterable[str]) -> set[str]:
    return {login for login in logins if is_team_handle(login)}


def compute_status(maintainer_count: int, active_maintainer_count: int | None = None) -> str:
    """Map a (team-handle-excluded) maintainer count to a health status.

    Base thresholds (decision #5 in the plan): <=2 -> at_risk, 3-5 -> watch, 6+ -> healthy.

    `active_maintainer_count` (from `feedstock-activity.json`, see `activity.py`) is the number of
    distinct logins who actually authored/merged/reviewed a merged PR in the trailing 12 months --
    a second, activity-based signal alongside the purely recipe-declared `maintainer_count`. When
    it's known and zero, the status is demoted to at_risk even if the declared count looks
    healthy: a maintainer list that reads fine on paper but nobody has touched in a year is exactly
    the case this signal exists to catch. `None` (activity data not collected for this feedstock,
    e.g. it's outside the popularity-thresholded tier `activity.py` covers) leaves the
    declared-count-only behavior unchanged -- callers must not treat `None` the same as `0`.
    """
    if maintainer_count <= 2:
        return STATUS_AT_RISK
    if active_maintainer_count == 0:
        return STATUS_AT_RISK
    if maintainer_count <= 5:
        return STATUS_WATCH
    return STATUS_HEALTHY


def compute_risk_score(downloads_last_month: int, maintainer_count: int) -> float:
    """ "High usage, thin bench" ranking score (decision #6): downloads relative to maintainer
    count, Laplace-smoothed (+1 in the denominator) so zero-maintainer packages still rank highest
    without dividing by zero -- same shape as `graph_data.compute_maintainer_coverage`'s
    `risk_score`, applied to downloads instead of transitive dependents.
    """
    return downloads_last_month / (maintainer_count + 1)


def latest_month_downloads(monthly_downloads: dict[str, int]) -> tuple[str | None, int]:
    """Return the (month_key, total) for the most recent "YYYY-MM" key present.

    Months are added incrementally as `fetch package-downloads` runs, so the most recent key
    can't be assumed fixed -- but "YYYY-MM" strings sort correctly as plain strings, so the max
    key is always the most recent month.
    """
    if not monthly_downloads:
        return None, 0
    month = max(monthly_downloads)
    return month, monthly_downloads[month]


def normalize_package_downloads(
    package_downloads: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    """Decode oddly-encoded keys in `package-downloads.json` into a plain-name lookup.

    Most keys are plain package names, but some are URL-encoded (e.g. "%5flibgcc%5fmutex" for
    "_libgcc_mutex") -- observed *alongside*, not instead of, the plain-string key. Decoding every
    key and keeping the first one seen wins ties; keys are visited already-plain-first (a key
    equal to its own decoded form) so a real plain key always takes precedence over an encoded
    duplicate that decodes to the same name.

    Call once per `generate site-data` run and pass the result to `lookup_package_downloads`,
    rather than decoding on every per-package lookup -- this dataset has ~35k keys, and decoding
    is O(1) work done once here instead of repeated for every one of the ~32k packages looked up.
    """
    normalized: dict[str, dict[str, int]] = {}
    for raw_key in sorted(package_downloads, key=lambda k: (k != urllib.parse.unquote(k), k)):
        name = urllib.parse.unquote(raw_key)
        if name not in normalized:
            normalized[name] = package_downloads[raw_key]
    return normalized


def lookup_package_downloads(
    normalized_package_downloads: dict[str, dict[str, int]], name: str
) -> dict[str, int]:
    """Look up a package's monthly downloads dict by name in an already-normalized dict (see
    `normalize_package_downloads`), defensively falling back to an empty dict (no download data
    yet) rather than raising.
    """
    return normalized_package_downloads.get(name, {})


def _display_name(login: str, maintainer_info: dict[str, dict]) -> str | None:
    """`None` when `login` has no profile page (team handle or unresolved/404'd user); otherwise
    a display name, falling back to the login itself when the GitHub profile has no `name` set.
    """
    info = maintainer_info.get(login)
    if info is None:
        return None
    return info.get("name") or login


def compute_maintainer_overview(
    maintainers: dict[str, list[str]],
    maintainer_info: dict[str, dict],
    package_maintainers: dict[str, list[str]],
    package_downloads: dict[str, dict[str, int]],
    generated_at: str,
) -> dict[str, Any]:
    """Build the home page's `maintainer-overview.json` payload."""
    package_downloads = normalize_package_downloads(package_downloads)
    all_logins = {login for names in maintainers.values() for login in names}
    handles = team_handles(all_logins)

    feedstock_count = len(maintainers)
    raw_counts = [len(names) for names in maintainers.values()]
    avg = statistics.mean(raw_counts) if raw_counts else 0.0
    median = statistics.median(raw_counts) if raw_counts else 0
    single_maintainer_count = sum(1 for count in raw_counts if count == 1)
    single_maintainer_pct = (
        (single_maintainer_count / feedstock_count * 100) if feedstock_count else 0.0
    )

    # Per-login feedstock counts, excluding team handles -- used for "top maintainers by
    # feedstocks managed" and as the (defined-but-arbitrary) tiebreak for a package's
    # "top maintainer".
    login_feedstock_counts: Counter[str] = Counter()
    for names in maintainers.values():
        for login in names:
            if login not in handles:
                login_feedstock_counts[login] += 1

    top_maintainer_logins = sorted(
        (login for login in login_feedstock_counts if login in maintainer_info),
        key=lambda login: (-login_feedstock_counts[login], login),
    )[:_TOP_MAINTAINERS_LIMIT]
    top_maintainers = [
        {
            "login": login,
            "name": _display_name(login, maintainer_info),
            "avatar_url": maintainer_info[login].get("avatar_url"),
            "feedstock_count": login_feedstock_counts[login],
        }
        for login in top_maintainer_logins
    ]

    downloads_last_month_by_package = {
        name: latest_month_downloads(lookup_package_downloads(package_downloads, name))[1]
        for name in package_maintainers
    }
    popular_package_names = sorted(
        package_maintainers,
        key=lambda name: (-downloads_last_month_by_package[name], name),
    )[:_POPULAR_PACKAGES_LIMIT]

    popular_packages = []
    for name in popular_package_names:
        package_login_list = package_maintainers[name]
        real_maintainers = [
            m for m in package_login_list if m not in team_handles(package_login_list)
        ]
        profiled_candidates = [m for m in real_maintainers if m in maintainer_info]
        top_maintainer_login = None
        if profiled_candidates:
            top_maintainer_login = min(
                profiled_candidates,
                key=lambda login: (-login_feedstock_counts.get(login, 0), login),
            )
        popular_packages.append(
            {
                "name": name,
                "downloads_last_month": downloads_last_month_by_package[name],
                "maintainer_count": len(real_maintainers),
                "top_maintainer_login": top_maintainer_login,
            }
        )

    return {
        "generated_at": generated_at,
        "stats": {
            "maintainer_count": len(all_logins - handles),
            "feedstock_count": feedstock_count,
            "avg_maintainers_per_feedstock": round(avg, 2),
            "median_maintainers_per_feedstock": median,
            "single_maintainer_feedstock_count": single_maintainer_count,
            "single_maintainer_feedstock_pct": round(single_maintainer_pct, 1),
        },
        "top_maintainers": top_maintainers,
        "popular_packages": popular_packages,
    }


def compute_package_overview(
    package_maintainers: dict[str, list[str]],
    transitive_dependency_records: list[dict],
    package_downloads: dict[str, dict[str, int]],
    generated_at: str,
) -> dict[str, Any]:
    """Build the packages page's `package-overview.json` payload."""
    package_downloads = normalize_package_downloads(package_downloads)
    package_count = len(package_maintainers)

    maintainer_counts: dict[str, int] = {}
    for name, logins in package_maintainers.items():
        handles = team_handles(logins)
        maintainer_counts[name] = len([login for login in logins if login not in handles])

    le2_count = sum(1 for count in maintainer_counts.values() if count <= 2)
    le2_pct = (le2_count / package_count * 100) if package_count else 0.0

    # The on-disk order in transitive-dependencies.json is sorted by transitive_only_dependents,
    # a different metric -- always re-sort by transitive_dependents before using it for a
    # "most depended-on" / "most depended-on transitive dependencies" ranking. Also restrict to
    # names present in package_maintainers, which excludes conda's virtual packages (see module
    # docstring) -- they have no maintainers and no profile page to link to, and otherwise
    # dominate the top of this ranking.
    ranked_by_transitive_dependents = sorted(
        (
            record
            for record in transitive_dependency_records
            if record["name"] in package_maintainers
        ),
        key=lambda record: (-record["transitive_dependents"], record["name"]),
    )

    most_depended_on = None
    if ranked_by_transitive_dependents:
        top = ranked_by_transitive_dependents[0]
        most_depended_on = {
            "name": top["name"],
            "transitive_dependents": top["transitive_dependents"],
            "maintainer_count": maintainer_counts.get(top["name"], 0),
        }

    downloads_last_month_by_package = {
        name: latest_month_downloads(lookup_package_downloads(package_downloads, name))[1]
        for name in package_maintainers
    }
    risk_ranked_names = sorted(
        package_maintainers,
        key=lambda name: (
            -compute_risk_score(downloads_last_month_by_package[name], maintainer_counts[name]),
            name,
        ),
    )[:_RISK_PACKAGES_LIMIT]
    risk_packages = [
        {
            "name": name,
            "downloads_last_month": downloads_last_month_by_package[name],
            "maintainer_count": maintainer_counts[name],
            "status": compute_status(maintainer_counts[name]),
        }
        for name in risk_ranked_names
    ]

    transitive_dependencies = [
        {
            "name": record["name"],
            "dependent_feedstocks": record["transitive_dependents"],
            "maintainer_count": maintainer_counts.get(record["name"], 0),
        }
        for record in ranked_by_transitive_dependents[:_TRANSITIVE_DEPENDENCIES_LIMIT]
    ]

    return {
        "generated_at": generated_at,
        "stats": {
            "package_count": package_count,
            "packages_le2_maintainers_count": le2_count,
            "packages_le2_maintainers_pct": round(le2_pct, 1),
            "most_depended_on": most_depended_on,
        },
        "risk_packages": risk_packages,
        "transitive_dependencies": transitive_dependencies,
    }


def _maintainer_graph_adjacency(maintainer_graph: dict) -> dict[str, list[tuple[str, int]]]:
    """Adjacency list keyed by login: [(other_login, shared_feedstock_count), ...], derived from
    `maintainer-graph.json`'s undirected edges (each edge touches both endpoints)."""
    adjacency: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for edge in maintainer_graph.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        weight = edge["attributes"]["weight"]
        adjacency[source].append((target, weight))
        adjacency[target].append((source, weight))
    return adjacency


def build_maintainer_ego_network(
    login: str,
    maintainer_info: dict[str, dict],
    maintainer_graph_adjacency: dict[str, list[tuple[str, int]]],
) -> dict[str, Any]:
    """The center's induced ego subgraph, capped at `_CO_MAINTAINERS_LIMIT` people (the same cap
    and ranking as the "Co-maintainers" list above it) so the rendered graph stays readable --
    an earlier uncapped version was cluttered and unreadable for well-connected maintainers.
    Direct co-maintainers fill the cap first, ranked by shared-feedstock weight (identical
    ranking to `co_maintainers`); any remaining slots are filled with second-degree
    collaborators, ranked by their strongest edge into the direct co-maintainer set. Embedded
    directly into the maintainer's profile payload so the frontend can render (and toggle
    between 1 and 2 degrees of) an ego network without fetching the full
    `maintainer-graph.json`."""
    direct = sorted(
        maintainer_graph_adjacency.get(login, []),
        key=lambda pair: (-pair[1], pair[0]),
    )
    one_hop = [other_login for other_login, _weight in direct[:_CO_MAINTAINERS_LIMIT]]

    visited: dict[str, int] = {login: 0}
    for other_login in one_hop:
        visited[other_login] = 1

    remaining_budget = _CO_MAINTAINERS_LIMIT - len(one_hop)
    if remaining_budget > 0:
        best_weight_by_candidate: dict[str, int] = {}
        for node in one_hop:
            for neighbor, weight in maintainer_graph_adjacency.get(node, []):
                if neighbor in visited:
                    continue
                if weight > best_weight_by_candidate.get(neighbor, -1):
                    best_weight_by_candidate[neighbor] = weight
        two_hop = sorted(
            best_weight_by_candidate,
            key=lambda candidate: (-best_weight_by_candidate[candidate], candidate),
        )[:remaining_budget]
        for other_login in two_hop:
            visited[other_login] = 2

    nodes = [
        {
            "key": node_login,
            "label": _display_name(node_login, maintainer_info) or node_login,
            "distance": distance,
        }
        for node_login, distance in sorted(visited.items(), key=lambda pair: (pair[1], pair[0]))
    ]

    edges_seen: dict[tuple[str, ...], int] = {}
    for node in visited:
        for neighbor, weight in maintainer_graph_adjacency.get(node, []):
            if neighbor in visited:
                lo, hi = sorted((node, neighbor))
                edges_seen[(lo, hi)] = weight
    edges = [
        {"source": source, "target": target, "weight": weight}
        for (source, target), weight in sorted(edges_seen.items())
    ]

    return {"nodes": nodes, "edges": edges}


def build_maintainer_feedstocks_index(
    maintainers: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Invert maintainers.json into {login: [feedstock, ...]} (sorted), for every login that
    appears anywhere, including team handles (callers filter as needed)."""
    by_login: dict[str, list[str]] = defaultdict(list)
    for feedstock, logins in maintainers.items():
        for login in logins:
            by_login[login].append(feedstock)
    return {login: sorted(feedstocks) for login, feedstocks in by_login.items()}


def build_maintainer_profile(
    login: str,
    maintainer_info: dict[str, dict],
    maintained_feedstocks: dict[str, list[str]],
    package_names: dict[str, list[str]],
    maintainer_graph_adjacency: dict[str, list[tuple[str, int]]],
) -> dict[str, Any]:
    """Build one `maintainers/<login>.json` payload. `login` must be a key of `maintainer_info`."""
    info = maintainer_info[login]
    feedstocks = maintained_feedstocks.get(login, [])

    packages: set[str] = set()
    for feedstock in feedstocks:
        effective_names = package_names.get(feedstock) or [feedstock]
        packages.update(effective_names)

    edges = sorted(
        maintainer_graph_adjacency.get(login, []),
        key=lambda pair: (-pair[1], pair[0]),
    )
    co_maintainers = [
        {
            "login": other_login,
            "name": _display_name(other_login, maintainer_info),
            "shared_feedstocks": weight,
        }
        for other_login, weight in edges[:_CO_MAINTAINERS_LIMIT]
    ]
    most_shared_with = None
    if edges:
        top_login, top_weight = edges[0]
        most_shared_with = {"login": top_login, "shared_feedstocks": top_weight}

    ego_network = build_maintainer_ego_network(login, maintainer_info, maintainer_graph_adjacency)

    return {
        "login": login,
        "name": info.get("name") or login,
        "avatar_url": info.get("avatar_url"),
        "html_url": info.get("html_url"),
        "company": info.get("company"),
        "location": info.get("location"),
        "feedstock_count": len(feedstocks),
        "packages": sorted(packages),
        "co_maintainer_count": len(edges),
        "co_maintainers": co_maintainers,
        "most_shared_with": most_shared_with,
        "ego_network": ego_network,
    }


def build_maintainer_profiles(
    maintainers: dict[str, list[str]],
    maintainer_info: dict[str, dict],
    maintainer_graph: dict,
    package_names: dict[str, list[str]],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield `(login, profile)` for every login in `maintainer_info` (sorted), the authoritative
    "has a profile" set -- i.e. exactly the logins `maintainers/index.json` should list.
    """
    maintained_feedstocks = build_maintainer_feedstocks_index(maintainers)
    adjacency = _maintainer_graph_adjacency(maintainer_graph)
    for login in sorted(maintainer_info):
        yield (
            login,
            build_maintainer_profile(
                login, maintainer_info, maintained_feedstocks, package_names, adjacency
            ),
        )


def _package_graph_indices(
    package_graph: dict,
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, float]]:
    """Return (successors, predecessors, pagerank_by_name) from a `package-graph.json`-shaped
    graphology graph. An edge {source, target} means source depends on target, so
    `successors[source]` are source's direct dependencies and `predecessors[target]` are
    target's direct dependents.
    """
    pagerank_by_name = {
        node["key"]: node.get("attributes", {}).get("pagerank", 0.0)
        for node in package_graph.get("nodes", [])
    }
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    for edge in package_graph.get("edges", []):
        successors[edge["source"]].append(edge["target"])
        predecessors[edge["target"]].append(edge["source"])
    return successors, predecessors, pagerank_by_name


def feedstocks_by_package_name(package_names: dict[str, list[str]]) -> dict[str, list[str]]:
    """Invert {feedstock: [package_name, ...]} into {package_name: [feedstock, ...]} (sorted).

    Mirrors `graph_data.build_package_maintainers`'s join exactly: a feedstock missing from
    `package_names` (or mapped to an empty list, e.g. its recipe failed to parse) falls back to
    using the feedstock name itself as the package name, so every feedstock is still reachable
    under some name. A package name produced by more than one feedstock (rare -- e.g. "blas" and
    "lapack" both produce "libblas") lists all of them, sorted, matching how `package-maintainers
    .json` already unions maintainers across such feedstocks rather than picking just one.
    """
    by_package: dict[str, set[str]] = defaultdict(set)
    for feedstock, names in package_names.items():
        for package in names or [feedstock]:
            by_package[package].add(feedstock)
    return {package: sorted(feedstocks) for package, feedstocks in by_package.items()}


def active_maintainer_count_for_feedstocks(
    feedstocks: list[str], feedstock_activity: dict[str, dict] | None
) -> int | None:
    """Number of distinct logins active (declared or not) across `feedstocks` in the trailing
    activity window, or `None` if none of `feedstocks` is covered by `feedstock_activity` (as
    produced by `activity.build_feedstock_activity`) -- distinct from `0`, which means activity
    data exists and says nobody was active. Callers must preserve that distinction (see
    `compute_status`) rather than defaulting a missing feedstock to zero."""
    if not feedstock_activity:
        return None
    covered = [feedstock_activity[fs] for fs in feedstocks if fs in feedstock_activity]
    if not covered:
        return None
    active: set[str] = set()
    for record in covered:
        active.update(record.get("active_maintainers", {}))
    return len(active)


def build_package_profile(
    name: str,
    maintainer_logins: list[str],
    maintainer_info: dict[str, dict],
    successors: dict[str, list[str]],
    predecessors: dict[str, list[str]],
    pagerank_by_name: dict[str, float],
    transitive_by_name: dict[str, dict],
    normalized_package_downloads: dict[str, dict[str, int]],
    feedstocks: list[str],
    license: str | None,
    feedstock_activity: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build one `packages/<name>.json` payload. `normalized_package_downloads` must already be
    the output of `normalize_package_downloads`. `feedstocks` is the (sorted, non-empty) list of
    feedstock(s) that produce this package name (see `feedstocks_by_package_name`); `license` is
    the declared license of the first of them that has one, or `None`. `feedstock_activity` is the
    optional `"feedstocks"` sub-object of `feedstock-activity.json` (see `activity.py`); omitting
    it (or a package whose feedstock(s) aren't covered) just means `active_maintainer_count` is
    `None`, not an error."""
    handles = team_handles(maintainer_logins)
    maintainer_count = len([login for login in maintainer_logins if login not in handles])
    maintainers = [
        {"login": login, "name": _display_name(login, maintainer_info)}
        for login in maintainer_logins
    ]

    transitive_record = transitive_by_name.get(name)
    dependent_feedstock_count = (
        transitive_record["transitive_dependents"] if transitive_record else None
    )

    # Virtual packages (see module docstring) are excluded from both lists -- they have no
    # profile page to link to and aren't meaningful "dependencies"/"dependents" to a reader.
    direct_dependencies = sorted(
        dep for dep in successors.get(name, []) if not is_virtual_package(dep)
    )
    notable_dependents = [
        dependent
        for dependent, _pagerank in sorted(
            (
                (dep, pagerank_by_name.get(dep, 0.0))
                for dep in predecessors.get(name, [])
                if not is_virtual_package(dep)
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )[:_NOTABLE_DEPENDENTS_LIMIT]
    ]

    monthly = lookup_package_downloads(normalized_package_downloads, name)
    downloads_monthly = [
        {"month": month, "downloads": total} for month, total in sorted(monthly.items())
    ]
    downloads_last_month = downloads_monthly[-1]["downloads"] if downloads_monthly else 0

    feedstock_links = [
        {"name": feedstock, "url": _FEEDSTOCK_URL_TEMPLATE.format(feedstock=feedstock)}
        for feedstock in feedstocks
    ]

    active_maintainer_count = active_maintainer_count_for_feedstocks(feedstocks, feedstock_activity)

    return {
        "name": name,
        "maintainers": maintainers,
        "maintainer_count": maintainer_count,
        "active_maintainer_count": active_maintainer_count,
        "dependent_feedstock_count": dependent_feedstock_count,
        "direct_dependencies": direct_dependencies,
        "notable_dependents": notable_dependents,
        "downloads_monthly": downloads_monthly,
        "downloads_last_month": downloads_last_month,
        "status": compute_status(maintainer_count, active_maintainer_count),
        "feedstocks": feedstock_links,
        "license": license,
    }


def build_package_profiles(
    package_maintainers: dict[str, list[str]],
    maintainer_info: dict[str, dict],
    package_graph: dict,
    transitive_dependency_records: list[dict],
    package_downloads: dict[str, dict[str, int]],
    package_names: dict[str, list[str]] | None = None,
    licenses: dict[str, str] | None = None,
    feedstock_activity: dict[str, dict] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield `(name, profile)` for every package in `package_maintainers` (sorted) -- exactly the
    names `packages/index.json` should list.

    `package_names` (as produced by `generate maintainers --package-names-output`), `licenses` (as
    produced by `generate maintainers --license-output`), and `feedstock_activity` (the
    `"feedstocks"` sub-object of `feedstock-activity.json`, see `activity.py`) are all optional --
    omitting them (or passing feedstocks/names they don't cover) just means the resulting
    profile(s) have no known feedstock link, license, or `active_maintainer_count`, rather than
    raising.
    """
    successors, predecessors, pagerank_by_name = _package_graph_indices(package_graph)
    transitive_by_name = {record["name"]: record for record in transitive_dependency_records}
    normalized_package_downloads = normalize_package_downloads(package_downloads)
    feedstocks_by_package = feedstocks_by_package_name(package_names or {})
    licenses = licenses or {}
    for name in sorted(package_maintainers):
        feedstocks = feedstocks_by_package.get(name) or [name]
        license = next((licenses[fs] for fs in feedstocks if fs in licenses), None)
        yield (
            name,
            build_package_profile(
                name,
                package_maintainers[name],
                maintainer_info,
                successors,
                predecessors,
                pagerank_by_name,
                transitive_by_name,
                normalized_package_downloads,
                feedstocks,
                license,
                feedstock_activity,
            ),
        )
