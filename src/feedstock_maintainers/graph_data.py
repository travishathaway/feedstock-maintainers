"""Build a graphology-format collaboration graph from maintainers.json + maintainer-info.json.

Nodes are maintainers with profile info; edges connect maintainers who co-maintain at least one
feedstock together, weighted by the number of shared feedstocks. Team handles (containing "/")
and usernames missing profile info in maintainer_info are excluded entirely from the graph.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from itertools import combinations

import networkx as nx


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

    step("Computing degree centrality")
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
