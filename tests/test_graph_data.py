"""Tests for building the maintainer collaboration graph from maintainers + maintainer-info."""

from __future__ import annotations

import pytest

from feedstock_maintainers.graph_data import (
    build_graph,
    build_package_maintainers,
    compute_maintainer_coverage,
)


def _info(login: str) -> dict:
    return {
        "login": login,
        "name": None,
        "html_url": f"https://github.com/{login}",
        "avatar_url": None,
    }


def test_two_users_sharing_two_feedstocks_get_weight_two_edge():
    maintainers = {
        "widget-feedstock": ["alice", "bob"],
        "gadget-feedstock": ["alice", "bob"],
    }
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["alice", "bob"]
    assert graph["edges"] == [
        {
            "key": "alice--bob",
            "source": "alice",
            "target": "bob",
            "undirected": True,
            "attributes": {"weight": 2},
        }
    ]


def test_third_user_sharing_one_feedstock_with_each_gets_weight_one_edges():
    maintainers = {
        "widget-feedstock": ["alice", "bob"],
        "gadget-feedstock": ["alice", "bob"],
        "gizmo-feedstock": ["alice", "carol"],
        "thingamajig-feedstock": ["bob", "carol"],
    }
    maintainer_info = {name: _info(name) for name in ("alice", "bob", "carol")}

    graph = build_graph(maintainers, maintainer_info)

    weights = {(e["source"], e["target"]): e["attributes"]["weight"] for e in graph["edges"]}
    assert weights == {("alice", "bob"): 2, ("alice", "carol"): 1, ("bob", "carol"): 1}


def test_active_feedstock_count_defaults_to_zero_without_activity_data():
    maintainers = {"widget-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}

    graph = build_graph(maintainers, maintainer_info)

    assert graph["nodes"][0]["attributes"]["activeFeedstockCount"] == 0


def test_active_feedstock_count_sums_across_covered_feedstocks():
    maintainers = {"widget-feedstock": ["alice"], "gadget-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}
    activity = {
        "widget-feedstock": {"active_maintainers": {"alice": {"total_events": 3}}},
        "gadget-feedstock": {"active_maintainers": {"alice": {"total_events": 1}}},
    }

    graph = build_graph(maintainers, maintainer_info, activity=activity)

    assert graph["nodes"][0]["attributes"]["activeFeedstockCount"] == 2


def test_active_feedstock_count_ignores_logins_outside_maintainer_info():
    maintainers = {"widget-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}
    activity: dict[str, dict] = {"widget-feedstock": {"active_maintainers": {"ghost": {}}}}

    graph = build_graph(maintainers, maintainer_info, activity=activity)

    assert [n["key"] for n in graph["nodes"]] == ["alice"]
    assert graph["nodes"][0]["attributes"]["activeFeedstockCount"] == 0


def test_team_handle_excluded_but_real_comaintainers_still_get_credit():
    maintainers = {"widget-feedstock": ["alice", "bob", "conda-forge/go"]}
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["alice", "bob"]
    assert graph["nodes"][0]["attributes"]["feedstockCount"] == 1
    assert graph["nodes"][1]["attributes"]["feedstockCount"] == 1
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["attributes"]["weight"] == 1


def test_user_missing_from_maintainer_info_excluded_without_breaking_others():
    maintainers = {"widget-feedstock": ["alice", "ghost", "bob"]}
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["alice", "bob"]
    assert graph["edges"] == [
        {
            "key": "alice--bob",
            "source": "alice",
            "target": "bob",
            "undirected": True,
            "attributes": {"weight": 1},
        }
    ]


def test_empty_input_produces_empty_skeleton():
    graph = build_graph({}, {})

    assert graph == {
        "attributes": {},
        "options": {"type": "undirected", "multi": False, "allowSelfLoops": False},
        "nodes": [],
        "edges": [],
    }


def test_feedstock_with_single_real_maintainer_produces_no_edge():
    maintainers = {"widget-feedstock": ["alice", "conda-forge/go"]}
    maintainer_info = {"alice": _info("alice")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["alice"]
    assert graph["edges"] == []


def test_isolated_node_with_no_comaintainers_is_still_included():
    maintainers = {"solo-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["alice"]
    assert graph["nodes"][0]["attributes"]["feedstockCount"] == 1
    assert graph["edges"] == []


def test_nodes_and_edges_are_sorted_deterministically():
    maintainers = {
        "z-feedstock": ["zoe", "amy"],
        "a-feedstock": ["bob", "amy"],
    }
    maintainer_info = {name: _info(name) for name in ("zoe", "amy", "bob")}

    graph = build_graph(maintainers, maintainer_info)

    assert [n["key"] for n in graph["nodes"]] == ["amy", "bob", "zoe"]
    assert [(e["source"], e["target"]) for e in graph["edges"]] == [("amy", "bob"), ("amy", "zoe")]


def test_node_label_falls_back_to_login_when_name_missing():
    maintainers = {"widget-feedstock": ["alice"]}
    maintainer_info = {"alice": {**_info("alice"), "name": "Alice Example"}}

    graph = build_graph(maintainers, maintainer_info)

    assert graph["nodes"][0]["attributes"]["label"] == "Alice Example"

    maintainer_info["alice"]["name"] = None
    graph = build_graph(maintainers, maintainer_info)
    assert graph["nodes"][0]["attributes"]["label"] == "alice"


def test_isolated_node_gets_zero_valued_metrics():
    maintainers = {"solo-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}

    graph = build_graph(maintainers, maintainer_info)
    attrs = graph["nodes"][0]["attributes"]

    assert attrs["degreeCentrality"] == 0.0
    assert attrs["weightedDegree"] == 0
    assert attrs["betweennessCentrality"] == 0.0
    assert attrs["pagerank"] == 1.0


def test_bridge_node_has_higher_betweenness_than_endpoints():
    # A path graph alice-bob-carol: bob sits on every shortest path between alice and carol.
    maintainers = {
        "widget-feedstock": ["alice", "bob"],
        "gadget-feedstock": ["bob", "carol"],
    }
    maintainer_info = {name: _info(name) for name in ("alice", "bob", "carol")}

    graph = build_graph(maintainers, maintainer_info)
    metrics = {n["key"]: n["attributes"] for n in graph["nodes"]}

    assert metrics["bob"]["betweennessCentrality"] > metrics["alice"]["betweennessCentrality"]
    assert metrics["bob"]["betweennessCentrality"] > metrics["carol"]["betweennessCentrality"]
    assert metrics["bob"]["weightedDegree"] == 2
    assert metrics["alice"]["weightedDegree"] == 1


def test_all_nodes_have_metric_keys_present():
    maintainers = {
        "widget-feedstock": ["alice", "bob"],
        "gadget-feedstock": ["alice", "bob"],
        "gizmo-feedstock": ["alice", "carol"],
    }
    maintainer_info = {name: _info(name) for name in ("alice", "bob", "carol")}

    graph = build_graph(maintainers, maintainer_info)

    expected_keys = {"degreeCentrality", "weightedDegree", "betweennessCentrality", "pagerank"}
    for node in graph["nodes"]:
        assert expected_keys.issubset(node["attributes"].keys())


def test_build_package_maintainers_simple_one_to_one_mapping():
    package_names = {"widget-feedstock": ["widget"]}
    maintainers = {"widget-feedstock": ["alice", "bob"]}

    result = build_package_maintainers(package_names, maintainers)

    assert result == {"widget": ["alice", "bob"]}


def test_build_package_maintainers_multi_output_feedstock_fans_out():
    package_names = {"boost-feedstock": ["libboost", "boost-cpp"]}
    maintainers = {"boost-feedstock": ["alice"]}

    result = build_package_maintainers(package_names, maintainers)

    assert result == {"libboost": ["alice"], "boost-cpp": ["alice"]}


def test_build_package_maintainers_falls_back_to_feedstock_name_when_unresolved():
    package_names: dict[str, list[str]] = {"widget-feedstock": []}
    maintainers = {"widget-feedstock": ["alice"]}

    result = build_package_maintainers(package_names, maintainers)

    assert result == {"widget-feedstock": ["alice"]}


def test_build_package_maintainers_unions_maintainers_on_name_collision():
    package_names = {
        "widget-feedstock": ["widget"],
        "widget2-feedstock": ["widget"],
    }
    maintainers = {
        "widget-feedstock": ["alice"],
        "widget2-feedstock": ["bob", "alice"],
    }

    result = build_package_maintainers(package_names, maintainers)

    assert result == {"widget": ["alice", "bob"]}


def test_build_package_maintainers_feedstock_absent_from_maintainers_is_empty():
    package_names = {"widget-feedstock": ["widget"]}
    maintainers: dict[str, list[str]] = {}

    result = build_package_maintainers(package_names, maintainers)

    assert result == {"widget": []}


def _dep_record(name, direct=0, transitive=0):
    return {"name": name, "direct_dependents": direct, "transitive_dependents": transitive}


def test_compute_maintainer_coverage_stats_on_hand_computed_set():
    transitive_dependencies = [
        _dep_record("a", transitive=10),
        _dep_record("b", transitive=10),
        _dep_record("c", transitive=10),
    ]
    package_maintainers = {"a": ["alice"], "b": ["alice", "bob"], "c": []}

    result = compute_maintainer_coverage(transitive_dependencies, package_maintainers)
    stats = result["stats"]

    assert stats["package_count"] == 3
    assert stats["packages_with_zero_maintainers"] == 1
    assert stats["mean_maintainers"] == 1.0
    assert stats["median_maintainers"] == 1.0
    assert stats["stddev_maintainers"] == pytest.approx((2 / 3) ** 0.5)


def test_compute_maintainer_coverage_ranks_zero_maintainer_package_first():
    transitive_dependencies = [
        _dep_record("well-maintained", transitive=100),
        _dep_record("unmaintained", transitive=100),
    ]
    package_maintainers = {"well-maintained": ["alice", "bob", "carol"], "unmaintained": []}

    result = compute_maintainer_coverage(transitive_dependencies, package_maintainers)

    assert [p["name"] for p in result["packages"]] == ["unmaintained", "well-maintained"]


def test_compute_maintainer_coverage_ties_break_by_name():
    transitive_dependencies = [
        _dep_record("zeta", transitive=5),
        _dep_record("alpha", transitive=5),
    ]

    result = compute_maintainer_coverage(transitive_dependencies, {})

    assert [p["name"] for p in result["packages"]] == ["alpha", "zeta"]
