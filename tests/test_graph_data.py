"""Tests for building the maintainer collaboration graph from maintainers + maintainer-info."""

from __future__ import annotations

from feedstock_maintainers.graph_data import build_graph


def _info(login: str) -> dict:
    return {"login": login, "name": None, "html_url": f"https://github.com/{login}", "avatar_url": None}


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
