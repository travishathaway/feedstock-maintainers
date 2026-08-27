"""Build a graphology-format collaboration graph from maintainers.json + maintainer-info.json.

Nodes are maintainers with profile info; edges connect maintainers who co-maintain at least one
feedstock together, weighted by the number of shared feedstocks. Team handles (containing "/")
and usernames missing profile info in maintainer_info are excluded entirely from the graph.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations


def build_graph(maintainers: dict[str, list[str]], maintainer_info: dict[str, dict]) -> dict:
    """Return a graphology-native serialized graph (nodes = maintainers, edges = co-maintenance)."""
    feedstock_counts: Counter[str] = Counter()
    pair_weights: Counter[tuple[str, str]] = Counter()

    for names in maintainers.values():
        present = sorted({name for name in names if name in maintainer_info})
        feedstock_counts.update(present)
        pair_weights.update(combinations(present, 2))

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
