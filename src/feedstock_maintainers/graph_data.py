"""Build a graphology-format collaboration graph from maintainers.json + maintainer-info.json.

Nodes are maintainers with profile info; edges connect maintainers who co-maintain at least one
feedstock together, weighted by the number of shared feedstocks. Team handles (containing "/")
and usernames missing profile info in maintainer_info are excluded entirely from the graph.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import combinations

import networkx as nx

REPODATA_PACKAGE_KEYS = ("packages", "packages.conda")

_BETWEENNESS_EXACT_NODE_LIMIT = 2000
_BETWEENNESS_SAMPLE_SIZE = 500
_BETWEENNESS_SAMPLE_SEED = 0


def build_graph(
    maintainers: dict[str, list[str]],
    maintainer_info: dict[str, dict],
    on_step: Callable[[str], None] | None = None,
) -> dict:
    """Return a graphology-native serialized graph (nodes = maintainers, edges = co-maintenance).

    If given, `on_step` is called with a short description before each major phase begins --
    intended for driving a progress display around a call that can take a while on large graphs.
    """
    step = on_step or (lambda _description: None)

    step("Counting feedstock co-maintenance")
    feedstock_counts: Counter[str] = Counter()
    pair_weights: Counter[tuple[str, str]] = Counter()

    for names in maintainers.values():
        present = sorted({name for name in names if name in maintainer_info})
        feedstock_counts.update(present)
        pair_weights.update(combinations(present, 2))

    step("Building collaboration graph")
    metrics_graph = nx.Graph()
    metrics_graph.add_nodes_from(feedstock_counts)
    metrics_graph.add_weighted_edges_from((s, t, w) for (s, t), w in pair_weights.items())

    if metrics_graph.number_of_nodes() > 1:
        degree_centrality = nx.degree_centrality(metrics_graph)
    else:
        degree_centrality = dict.fromkeys(metrics_graph, 0.0)

    step("Computing betweenness centrality")
    if metrics_graph.number_of_nodes() > 1:
        betweenness_centrality = nx.betweenness_centrality(metrics_graph, weight="weight")
    else:
        betweenness_centrality = dict.fromkeys(metrics_graph, 0.0)

    step("Computing weighted degree")
    weighted_degree = dict(metrics_graph.degree(weight="weight"))

    step("Computing PageRank")
    pagerank = (
        nx.pagerank(metrics_graph, weight="weight") if metrics_graph.number_of_nodes() else {}
    )

    step("Serializing nodes and edges")
    nodes = [
        {
            "key": login,
            "attributes": {
                "label": maintainer_info[login].get("name") or login,
                "login": login,
                "githubUrl": maintainer_info[login].get("html_url"),
                "avatarUrl": maintainer_info[login].get("avatar_url"),
                "company": maintainer_info[login].get("company"),
                "location": maintainer_info[login].get("location"),
                "followers": maintainer_info[login].get("followers"),
                "publicRepos": maintainer_info[login].get("public_repos"),
                "feedstockCount": count,
                "degreeCentrality": round(degree_centrality[login], 6),
                "weightedDegree": weighted_degree[login],
                "betweennessCentrality": round(betweenness_centrality[login], 6),
                "pagerank": round(pagerank[login], 6),
            },
        }
        for login, count in sorted(feedstock_counts.items())
    ]

    edges = [
        {
            "key": f"{source}--{target}",
            "source": source,
            "target": target,
            "undirected": True,
            "attributes": {"weight": weight},
        }
        for (source, target), weight in sorted(pair_weights.items())
    ]

    return {
        "attributes": {},
        "options": {"type": "undirected", "multi": False, "allowSelfLoops": False},
        "nodes": nodes,
        "edges": edges,
    }


def _accumulate_dependency_weights(
    packages: dict[str, dict],
    weights: dict[str, Counter[str]],
    previous_versions: int | None = None,
) -> None:
    """Accumulate dependency counts from one repodata `packages`/`packages.conda` mapping.

    `packages` is keyed by artifact filename (e.g. "numpy-1.21.0-py39h....conda"); each value has
    `name` and `depends` (a list of specs like "numpy >=1.20"). For every artifact, and for every
    dependency it declares, increment `weights[package_name][dependency_name]` by one -- so the
    resulting weight is the number of distinct (version, build) artifacts of `package_name` across
    all supplied repodata files that declared a dependency on `dependency_name`. Self-dependencies
    are dropped and never turn into a graph edge.

    Mutates `weights` in place so callers can fold multiple repodata files (and both the legacy
    `packages` and `packages.conda` keys within each) into one accumulator before building the
    graph.

    `previous_versions` lets you select how many previous versions of a package should be included
    when determining its dependencies.
    """
    version_counter: Counter[str] = Counter()
    for info in packages.values():
        name = info["name"]
        version_counter.update((name,))
        counter = weights[name]

        if previous_versions is not None and version_counter[name] > previous_versions:
            continue

        for dep_spec in info.get("depends", []):
            dep_name = dep_spec.split()[0]
            if dep_name != name:
                counter[dep_name] += 1


def build_package_graph(
    repodata_jsons: list[dict[str, dict]],
    on_step: Callable[[str], None] | None = None,
    previous_versions: int | None = None,
) -> nx.DiGraph:
    """Build a directed package dependency graph from one or more repodata.json contents.

    Each element of `repodata_jsons` is a parsed repodata.json (as produced by conda-forge /
    anaconda.org channel indexes), read from both the legacy `packages` key (.tar.bz2 artifacts)
    and the newer `packages.conda` key (.conda artifacts) -- both are merged, since both represent
    real installable artifacts and omitting either silently drops packages that only ship in one
    format.

    Nodes are package *names* (not name+version+build) -- all versions/builds of a package are
    collapsed onto a single node. Every name that appears as either a dependency source or a
    dependency target becomes a node, so external/leaf dependencies that are never themselves
    indexed in the given repodata subset (e.g. a package only present in a different subdir) still
    appear as nodes with in-edges only.

    An edge (package -> dependency) is added for every declared dependency, i.e.
    `graph.successors(pkg)` gives pkg's direct dependencies and `graph.predecessors(pkg)` gives
    pkg's direct dependents ("who depends on me"). Edge weight is the number of distinct
    (version, build) artifacts of `package` that declared a dependency on `dependency`.
    Self-dependencies are dropped (never emitted as edges).

    The following metrics are computed and attached as node attributes:
      - `in_degree_centrality`: fraction of other nodes that directly depend on this package
        ("upstream importance" -- how many things break if this package breaks).
      - `out_degree_centrality`: fraction of other nodes this package directly depends on.
      - `pagerank`: computed on the graph as-is (edges point package -> dependency), so a
        dependency that accumulates many dependents naturally accumulates pagerank.
      - `betweenness_centrality`: exact (`nx.betweenness_centrality`) below
        `_BETWEENNESS_EXACT_NODE_LIMIT` nodes; above that, approximated via k-sample
        (`k=_BETWEENNESS_SAMPLE_SIZE`, fixed `seed=_BETWEENNESS_SAMPLE_SEED` for reproducibility)
        because exact betweenness is O(V*E) and real conda-forge repodata commonly collapses to
        tens of thousands of unique package names -- exact computation is impractical at that
        scale.

    If given, `on_step` is called with a short description before each major phase begins --
    intended for driving a progress display around a call that can take a while on large graphs.

    :raises KeyError: when an artifact entry is missing "name".
    """
    step = on_step or (lambda _description: None)

    step("Parsing repodata dependency specs")
    weights: dict[str, Counter[str]] = defaultdict(Counter)
    for repo in repodata_jsons:
        for key in REPODATA_PACKAGE_KEYS:
            _accumulate_dependency_weights(
                repo.get(key, {}), weights, previous_versions=previous_versions
            )

    step("Building package dependency graph")
    graph = nx.DiGraph()
    all_names = set(weights)
    for deps in weights.values():
        all_names.update(deps)
    graph.add_nodes_from(sorted(all_names))
    graph.add_weighted_edges_from(
        (pkg, dep, weight) for pkg, deps in weights.items() for dep, weight in deps.items()
    )

    node_count = graph.number_of_nodes()

    step("Computing PageRank")
    pagerank = nx.pagerank(graph, weight="weight") if node_count else {}

    step("Attaching metrics")
    nx.set_node_attributes(graph, pagerank, "pagerank")

    return graph


def _transitive_dependent_counts(graph: nx.DiGraph) -> dict[str, int]:
    """Return, for every node, the total number of packages that depend on it transitively
    (directly or indirectly) -- i.e. `len(nx.ancestors(graph, node))` for every node, computed
    without a per-node BFS.

    A per-node `nx.ancestors` call is O(V+E) each, so calling it once per node risks O(V*(V+E))
    total work when a handful of hub packages (python, openssl, libgcc-ng, ...) have ancestor sets
    touching a large fraction of the graph -- infeasible at real conda-forge scale (tens of
    thousands of nodes). Instead this computes every node's ancestor count in one linear pass:

    1. Reverse the graph (`R`): `descendants(X)` in `R` is exactly `ancestors(X)` in `graph`.
    2. Condense `R` into its strongly-connected components, guaranteeing a DAG even if the
       dependency data contains cycles.
    3. Walk the condensation in reverse topological order, computing each supernode's descendant
       set as a bitmask (a Python int) via a single OR per edge -- cheap, C-level big-int ops --
       instead of copying/union-ing Python sets.
    4. Expand supernode bit counts back to per-node totals, adding any other members of a node's
       own cycle (mutual transitive dependents), which is 0 for the common case of a singleton
       strongly-connected component.
    """
    reverse = graph.reverse(copy=False)
    condensation = nx.condensation(reverse)
    mapping: dict[str, int] = condensation.graph["mapping"]
    member_counts = {sid: len(condensation.nodes[sid]["members"]) for sid in condensation.nodes}

    desc_bits: dict[int, int] = {}
    for sid in reversed(list(nx.topological_sort(condensation))):
        bits = 0
        for child in condensation.successors(sid):
            bits |= (1 << child) | desc_bits[child]
        desc_bits[sid] = bits

    supernode_total: dict[int, int] = {}
    for sid, bits in desc_bits.items():
        total = member_counts[sid] - 1
        remaining = bits
        while remaining:
            low = remaining & (-remaining)
            child = low.bit_length() - 1
            total += member_counts[child]
            remaining ^= low
        supernode_total[sid] = total

    return {node: supernode_total[mapping[node]] for node in graph.nodes}


def find_transitive_only_dependencies(graph: nx.DiGraph) -> list[dict]:
    """Rank packages by how many other packages depend on them only transitively (through a
    chain of dependencies) and never as a direct, declared dependency.

    For every node `X`:
      - `direct_dependents`: number of packages with a direct edge to `X` (in-degree).
      - `transitive_dependents`: total number of packages that depend on `X` directly or
        indirectly (see `_transitive_dependent_counts`).
      - `transitive_only_dependents`: `transitive_dependents - direct_dependents` -- packages that
        reach `X` only through a chain, never directly. This is the "hidden dependency" count.
      - `transitive_only_ratio`: `transitive_only_dependents / transitive_dependents` (0.0 when
        `X` has no dependents at all), i.e. what fraction of `X`'s dependents rely on it
        invisibly.

    Returns all nodes as a list of dicts, sorted descending by `transitive_only_dependents` (ties
    broken ascending by name for a deterministic order).
    """
    transitive_counts = _transitive_dependent_counts(graph)

    records = []
    for name in graph.nodes:
        direct = graph.in_degree(name)
        transitive = transitive_counts[name]
        transitive_only = transitive - direct
        records.append(
            {
                "name": name,
                "direct_dependents": direct,
                "transitive_dependents": transitive,
                "transitive_only_dependents": transitive_only,
                "transitive_only_ratio": (transitive_only / transitive) if transitive else 0.0,
            }
        )

    records.sort(key=lambda record: (-record["transitive_only_dependents"], record["name"]))
    return records


def package_graph_to_json(graph: nx.DiGraph) -> dict:
    """Serialize a package dependency graph (as returned by `build_package_graph`) to the same
    graphology-native JSON shape used by `build_graph`, so both graph JSON files share one
    consumer contract. Unlike `build_graph`, this is a thin post-hoc serializer -- callers that
    only need the `nx.DiGraph` (e.g. for further analysis) should call `build_package_graph`
    directly and skip this step.
    """
    nodes = [
        {"key": name, "attributes": dict(attrs)} for name, attrs in sorted(graph.nodes(data=True))
    ]
    edges = [
        {
            "key": f"{source}->{target}",
            "source": source,
            "target": target,
            "undirected": False,
            "attributes": {"weight": attrs["weight"]},
        }
        for source, target, attrs in sorted(graph.edges(data=True))
    ]
    return {
        "attributes": {},
        "options": {"type": "directed", "multi": False, "allowSelfLoops": False},
        "nodes": nodes,
        "edges": edges,
    }
