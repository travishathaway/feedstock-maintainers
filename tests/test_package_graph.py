"""Tests for building the package dependency graph from repodata.json contents."""

from __future__ import annotations

import json

import networkx as nx
import pytest

from feedstock_maintainers import graph_data
from feedstock_maintainers.graph_data import (
    build_package_graph,
    find_transitive_only_dependencies,
    package_graph_to_json,
)


def _artifact(name: str, depends: list[str] | None = None) -> dict:
    return {"name": name, "depends": depends or []}


def _repodata(
    packages: dict[str, dict] | None = None,
    packages_conda: dict[str, dict] | None = None,
) -> dict:
    return {"packages": packages or {}, "packages.conda": packages_conda or {}}


def test_empty_input_produces_empty_graph():
    graph = build_package_graph([])

    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0


def test_single_dependency_creates_directed_edge():
    repodata = _repodata(packages_conda={"numpy-1.21.0-py39.conda": _artifact("numpy", ["python"])})

    graph = build_package_graph([repodata])

    assert graph.has_edge("numpy", "python")
    assert graph["numpy"]["python"]["weight"] == 1
    assert not graph.has_edge("python", "numpy")


def test_version_specifiers_are_stripped():
    repodata = _repodata(
        packages_conda={
            "numpy-1.21.0-py39.conda": _artifact("numpy", ["python >=3.9,<3.10.0a0"]),
        }
    )

    graph = build_package_graph([repodata])

    assert list(graph.successors("numpy")) == ["python"]


def test_weight_is_count_of_distinct_artifacts_declaring_dependency():
    repodata = _repodata(
        packages_conda={
            "numpy-1.20-py38.conda": _artifact("numpy", ["python"]),
            "numpy-1.21-py39.conda": _artifact("numpy", ["python"]),
            "numpy-1.21-py310.conda": _artifact("numpy", ["python", "libblas"]),
        }
    )

    graph = build_package_graph([repodata])

    assert graph["numpy"]["python"]["weight"] == 3
    assert graph["numpy"]["libblas"]["weight"] == 1


def test_both_packages_and_packages_dot_conda_keys_contribute():
    repodata = _repodata(
        packages={"a-1.0-0.tar.bz2": _artifact("a", ["b"])},
        packages_conda={"a-1.0-0.conda": _artifact("a", ["c"])},
    )

    graph = build_package_graph([repodata])

    assert graph.has_edge("a", "b")
    assert graph.has_edge("a", "c")
    assert graph["a"]["b"]["weight"] == 1
    assert graph["a"]["c"]["weight"] == 1


def test_multiple_repodata_files_are_merged():
    linux64 = _repodata(packages_conda={"foo-1.0-linux.conda": _artifact("foo", ["bar"])})
    noarch = _repodata(packages_conda={"foo-1.0-noarch.conda": _artifact("foo", ["bar"])})

    graph = build_package_graph([linux64, noarch])

    assert graph["foo"]["bar"]["weight"] == 2


def test_self_dependency_is_excluded():
    repodata = _repodata(
        packages_conda={
            "weirdpkg-1.0-0.conda": _artifact("weirdpkg", ["weirdpkg", "otherpkg"]),
        }
    )

    graph = build_package_graph([repodata])

    assert not graph.has_edge("weirdpkg", "weirdpkg")
    assert graph.has_edge("weirdpkg", "otherpkg")


def test_leaf_dependency_not_independently_packaged_still_becomes_a_node():
    repodata = _repodata(packages_conda={"foo-1.0-0.conda": _artifact("foo", ["zlib"])})

    graph = build_package_graph([repodata])

    assert "zlib" in graph.nodes
    assert list(graph.predecessors("zlib")) == ["foo"]
    assert list(graph.successors("zlib")) == []


def test_all_nodes_have_metric_keys_present():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
        }
    )

    graph = build_package_graph([repodata])

    expected_keys = {
        "in_degree_centrality",
        "out_degree_centrality",
        "betweenness_centrality",
        "pagerank",
    }
    for _, attrs in graph.nodes(data=True):
        assert expected_keys <= attrs.keys()


def test_hand_computable_metrics_on_small_star_graph():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["d"]),
            "b-1.0-0.conda": _artifact("b", ["d"]),
            "c-1.0-0.conda": _artifact("c", ["d"]),
        }
    )

    graph = build_package_graph([repodata])

    for dependent in ("a", "b", "c"):
        assert graph.nodes[dependent]["in_degree_centrality"] == 0.0
    assert graph.nodes["d"]["out_degree_centrality"] == 0.0
    assert graph.nodes["d"]["in_degree_centrality"] > graph.nodes["a"]["in_degree_centrality"]
    assert graph.nodes["d"]["pagerank"] > graph.nodes["a"]["pagerank"]


def test_bridge_node_has_higher_betweenness_than_endpoints():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
        }
    )

    graph = build_package_graph([repodata])

    assert graph.nodes["b"]["betweenness_centrality"] > graph.nodes["a"]["betweenness_centrality"]
    assert graph.nodes["b"]["betweenness_centrality"] > graph.nodes["c"]["betweenness_centrality"]


def test_isolated_node_gets_zero_valued_metrics():
    repodata = _repodata(packages_conda={"lonely-1.0-0.conda": _artifact("lonely")})

    graph = build_package_graph([repodata])

    assert graph.nodes["lonely"]["in_degree_centrality"] == 0.0
    assert graph.nodes["lonely"]["out_degree_centrality"] == 0.0
    assert graph.nodes["lonely"]["betweenness_centrality"] == 0.0
    assert graph.nodes["lonely"]["pagerank"] == 1.0


def test_betweenness_falls_back_to_sampled_computation_above_threshold(monkeypatch):
    monkeypatch.setattr(graph_data, "_BETWEENNESS_EXACT_NODE_LIMIT", 3)
    monkeypatch.setattr(graph_data, "_BETWEENNESS_SAMPLE_SIZE", 2)

    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
            "c-1.0-0.conda": _artifact("c", ["d"]),
            "d-1.0-0.conda": _artifact("d", ["e"]),
        }
    )

    graph = build_package_graph([repodata])

    assert graph.number_of_nodes() == 5
    for _, attrs in graph.nodes(data=True):
        assert isinstance(attrs["betweenness_centrality"], float)
        assert attrs["betweenness_centrality"] >= 0.0


def test_betweenness_is_exact_below_threshold():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
        }
    )

    graph = build_package_graph([repodata])
    expected = nx.betweenness_centrality(graph, weight="weight")

    for node, value in expected.items():
        assert graph.nodes[node]["betweenness_centrality"] == pytest.approx(value)


def test_package_graph_to_json_shape():
    repodata = _repodata(packages_conda={"a-1.0-0.conda": _artifact("a", ["b"])})
    graph = build_package_graph([repodata])

    data = package_graph_to_json(graph)

    assert data["options"] == {"type": "directed", "multi": False, "allowSelfLoops": False}
    assert {n["key"] for n in data["nodes"]} == {"a", "b"}
    assert data["edges"] == [
        {
            "key": "a->b",
            "source": "a",
            "target": "b",
            "undirected": False,
            "attributes": {"weight": 1},
        }
    ]


def test_package_graph_to_json_is_json_serializable():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
        }
    )
    graph = build_package_graph([repodata])

    data = package_graph_to_json(graph)

    assert json.loads(json.dumps(data)) == data


def _records_by_name(records: list[dict]) -> dict[str, dict]:
    return {record["name"]: record for record in records}


def test_transitive_only_dependents_on_a_chain():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["c"]),
        }
    )
    graph = build_package_graph([repodata])

    records = _records_by_name(find_transitive_only_dependencies(graph))

    assert records["c"]["direct_dependents"] == 1
    assert records["c"]["transitive_dependents"] == 2
    assert records["c"]["transitive_only_dependents"] == 1


def test_transitive_only_dependents_deduplicated_across_diamond_paths():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b", "c"]),
            "b-1.0-0.conda": _artifact("b", ["d"]),
            "c-1.0-0.conda": _artifact("c", ["d"]),
        }
    )
    graph = build_package_graph([repodata])

    records = _records_by_name(find_transitive_only_dependencies(graph))

    assert records["d"]["direct_dependents"] == 2
    assert records["d"]["transitive_dependents"] == 3
    assert records["d"]["transitive_only_dependents"] == 1


def test_node_with_no_dependents_has_zero_counts_and_ratio():
    repodata = _repodata(packages_conda={"lonely-1.0-0.conda": _artifact("lonely")})
    graph = build_package_graph([repodata])

    records = _records_by_name(find_transitive_only_dependencies(graph))

    assert records["lonely"]["direct_dependents"] == 0
    assert records["lonely"]["transitive_dependents"] == 0
    assert records["lonely"]["transitive_only_dependents"] == 0
    assert records["lonely"]["transitive_only_ratio"] == 0.0


def test_results_sorted_descending_by_transitive_only_dependents():
    repodata = _repodata(
        packages_conda={
            "a-1.0-0.conda": _artifact("a", ["b"]),
            "b-1.0-0.conda": _artifact("b", ["d"]),
            "c-1.0-0.conda": _artifact("c", ["d"]),
        }
    )
    graph = build_package_graph([repodata])

    records = find_transitive_only_dependencies(graph)
    counts = [record["transitive_only_dependents"] for record in records]

    assert counts == sorted(counts, reverse=True)


def test_cyclic_dependency_does_not_crash_and_counts_mutual_dependents():
    repodata = _repodata(
        packages_conda={
            "x-1.0-0.conda": _artifact("x", ["y"]),
            "y-1.0-0.conda": _artifact("y", ["x"]),
        }
    )
    graph = build_package_graph([repodata])

    records = _records_by_name(find_transitive_only_dependencies(graph))

    assert records["x"]["transitive_dependents"] == 1
    assert records["y"]["transitive_dependents"] == 1
