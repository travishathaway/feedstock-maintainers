"""Tests for pre-aggregating page-ready JSON payloads from maintainers/packages/graph data."""

from __future__ import annotations

from feedstock_maintainers.site_data import (
    IGNORED_PACKAGES,
    STATUS_AT_RISK,
    STATUS_HEALTHY,
    STATUS_WATCH,
    _maintainer_graph_adjacency,
    active_maintainer_count_for_feedstocks,
    build_maintainer_ego_network,
    build_maintainer_profiles,
    build_package_profiles,
    compute_maintainer_overview,
    compute_package_overview,
    compute_risk_score,
    compute_status,
    feedstocks_by_package_name,
    is_ignored_package,
    is_team_handle,
    latest_month_downloads,
    lookup_package_downloads,
    normalize_package_downloads,
)


def _info(login: str, name: str | None = None) -> dict:
    return {
        "login": login,
        "name": name,
        "avatar_url": f"https://avatars/{login}",
        "html_url": f"https://github.com/{login}",
        "company": None,
        "location": None,
    }


# ── compute_status / compute_risk_score ─────────────────────────────────────────────────────


def test_status_thresholds():
    assert compute_status(0) == STATUS_AT_RISK
    assert compute_status(2) == STATUS_AT_RISK
    assert compute_status(3) == STATUS_WATCH
    assert compute_status(5) == STATUS_WATCH
    assert compute_status(6) == STATUS_HEALTHY
    assert compute_status(100) == STATUS_HEALTHY


def test_status_active_maintainer_count_zero_demotes_healthy_to_at_risk():
    assert compute_status(6, active_maintainer_count=0) == STATUS_AT_RISK


def test_status_active_maintainer_count_zero_demotes_watch_to_at_risk():
    assert compute_status(4, active_maintainer_count=0) == STATUS_AT_RISK


def test_status_active_maintainer_count_none_preserves_declared_only_behavior():
    assert compute_status(6, active_maintainer_count=None) == STATUS_HEALTHY


def test_status_active_maintainer_count_nonzero_does_not_change_declared_status():
    assert compute_status(6, active_maintainer_count=3) == STATUS_HEALTHY


def test_active_maintainer_count_for_feedstocks_none_when_no_activity_data():
    assert active_maintainer_count_for_feedstocks(["widget-feedstock"], None) is None
    assert active_maintainer_count_for_feedstocks(["widget-feedstock"], {}) is None


def test_active_maintainer_count_for_feedstocks_none_when_feedstock_not_covered():
    activity: dict[str, dict] = {"other-feedstock": {"active_maintainers": {"alice": {}}}}
    assert active_maintainer_count_for_feedstocks(["widget-feedstock"], activity) is None


def test_active_maintainer_count_for_feedstocks_unions_across_multiple_feedstocks():
    activity: dict[str, dict] = {
        "widget-feedstock": {"active_maintainers": {"alice": {}, "bob": {}}},
        "gadget-feedstock": {"active_maintainers": {"bob": {}, "carol": {}}},
    }
    assert (
        active_maintainer_count_for_feedstocks(["widget-feedstock", "gadget-feedstock"], activity)
        == 3
    )


def test_active_maintainer_count_for_feedstocks_zero_when_covered_but_nobody_active():
    activity: dict[str, dict] = {"widget-feedstock": {"active_maintainers": {}}}
    assert active_maintainer_count_for_feedstocks(["widget-feedstock"], activity) == 0


def test_risk_score_laplace_smoothed():
    assert compute_risk_score(100, 0) == 100.0
    assert compute_risk_score(100, 1) == 50.0
    assert compute_risk_score(0, 0) == 0.0


def test_is_team_handle():
    assert is_team_handle("conda-forge/go")
    assert not is_team_handle("alice")


# ── latest_month_downloads / lookup_package_downloads ───────────────────────────────────────


def test_latest_month_downloads_picks_max_key():
    month, total = latest_month_downloads({"2025-09": 10, "2026-01": 20, "2025-12": 15})
    assert month == "2026-01"
    assert total == 20


def test_latest_month_downloads_empty():
    assert latest_month_downloads({}) == (None, 0)


def test_lookup_package_downloads_exact_match():
    downloads = normalize_package_downloads({"numpy": {"2026-01": 5}})
    assert lookup_package_downloads(downloads, "numpy") == {"2026-01": 5}


def test_lookup_package_downloads_missing_returns_empty():
    assert lookup_package_downloads({}, "missing") == {}


def test_normalize_package_downloads_decodes_percent_encoded_keys():
    normalized = normalize_package_downloads({"%5flibgcc%5fmutex": {"2026-01": 3}})
    assert lookup_package_downloads(normalized, "_libgcc_mutex") == {"2026-01": 3}


def test_normalize_package_downloads_prefers_plain_key_over_encoded_duplicate():
    normalized = normalize_package_downloads(
        {
            "%5flibgcc%5fmutex": {"2026-01": 999},
            "_libgcc_mutex": {"2026-01": 3},
        }
    )
    assert lookup_package_downloads(normalized, "_libgcc_mutex") == {"2026-01": 3}


# ── compute_maintainer_overview ──────────────────────────────────────────────────────────────


def test_maintainer_overview_excludes_team_handles_from_maintainer_count():
    maintainers = {
        "widget-feedstock": ["alice", "bob", "conda-forge/go"],
        "gadget-feedstock": ["alice"],
    }
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, {}, {}, "2026-01-01T00:00:00Z"
    )

    assert overview["stats"]["maintainer_count"] == 2  # alice, bob -- not conda-forge/go
    assert overview["stats"]["feedstock_count"] == 2


def test_maintainer_overview_avg_median_single_maintainer_stats():
    maintainers = {
        "a-feedstock": ["alice"],
        "b-feedstock": ["alice", "bob"],
        "c-feedstock": ["alice", "bob", "carol"],
    }
    maintainer_info = {name: _info(name) for name in ("alice", "bob", "carol")}
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, {}, {}, "2026-01-01T00:00:00Z"
    )
    stats = overview["stats"]

    assert stats["avg_maintainers_per_feedstock"] == 2.0
    assert stats["median_maintainers_per_feedstock"] == 2
    assert stats["single_maintainer_feedstock_count"] == 1
    assert stats["single_maintainer_feedstock_pct"] == round(1 / 3 * 100, 1)


def test_maintainer_overview_top_maintainers_ranked_by_feedstock_count_desc():
    maintainers = {
        "a-feedstock": ["alice"],
        "b-feedstock": ["alice"],
        "c-feedstock": ["alice"],
        "d-feedstock": ["bob"],
        "e-feedstock": ["bob"],
        "f-feedstock": ["carol"],
    }
    maintainer_info = {name: _info(name) for name in ("alice", "bob", "carol")}
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, {}, {}, "2026-01-01T00:00:00Z"
    )

    logins = [m["login"] for m in overview["top_maintainers"]]
    assert logins == ["alice", "bob", "carol"]
    assert overview["top_maintainers"][0]["feedstock_count"] == 3


def test_maintainer_overview_top_maintainers_includes_avatar_url():
    maintainers = {"a-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, {}, {}, "2026-01-01T00:00:00Z"
    )

    assert overview["top_maintainers"][0]["avatar_url"] == "https://avatars/alice"


def test_maintainer_overview_top_maintainers_excludes_unprofiled_logins():
    maintainers = {"a-feedstock": ["ghost", "alice"]}
    maintainer_info = {"alice": _info("alice")}
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, {}, {}, "2026-01-01T00:00:00Z"
    )

    logins = [m["login"] for m in overview["top_maintainers"]]
    assert "ghost" not in logins
    assert logins == ["alice"]


def test_maintainer_overview_popular_packages_ranked_by_downloads_desc():
    maintainers = {"numpy": ["alice"], "pandas": ["alice", "bob"]}
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}
    package_maintainers = {"numpy": ["alice"], "pandas": ["alice", "bob"]}
    package_downloads = {
        "numpy": {"2026-01": 100},
        "pandas": {"2026-01": 500},
    }
    overview = compute_maintainer_overview(
        maintainers, maintainer_info, package_maintainers, package_downloads, "2026-01-01T00:00:00Z"
    )

    names = [p["name"] for p in overview["popular_packages"]]
    assert names == ["pandas", "numpy"]
    assert overview["popular_packages"][0]["downloads_last_month"] == 500


def test_maintainer_overview_popular_package_top_maintainer_login_null_when_no_profile():
    maintainer_info: dict = {}
    package_maintainers = {"widget": ["conda-forge/go"]}
    overview = compute_maintainer_overview(
        {}, maintainer_info, package_maintainers, {}, "2026-01-01T00:00:00Z"
    )

    assert overview["popular_packages"][0]["top_maintainer_login"] is None
    assert overview["popular_packages"][0]["maintainer_count"] == 0


def test_maintainer_overview_excludes_ignored_packages_from_popular_packages():
    # Unlike conda's virtual packages, an ignored package (e.g. "pypy3.8") is a real,
    # feedstock-built package with real maintainers -- it must be dropped explicitly rather than
    # relying on it being absent from package_maintainers.
    maintainer_info = {"alice": _info("alice")}
    package_maintainers = {"numpy": ["alice"], "pypy3.8": ["alice"]}
    package_downloads = {"numpy": {"2026-01": 100}, "pypy3.8": {"2026-01": 999999}}
    overview = compute_maintainer_overview(
        {}, maintainer_info, package_maintainers, package_downloads, "2026-01-01T00:00:00Z"
    )

    names = [p["name"] for p in overview["popular_packages"]]
    assert "pypy3.8" not in names
    assert names == ["numpy"]


# ── compute_package_overview ─────────────────────────────────────────────────────────────────


def _dep(name, direct=0, transitive=0):
    return {
        "name": name,
        "direct_dependents": direct,
        "transitive_dependents": transitive,
        "transitive_only_dependents": transitive - direct,
        "transitive_only_ratio": 0.0,
    }


def test_package_overview_le2_maintainers_stats():
    package_maintainers = {
        "a": ["alice"],
        "b": ["alice", "bob"],
        "c": ["alice", "bob", "carol"],
    }
    overview = compute_package_overview(package_maintainers, [], {}, "2026-01-01T00:00:00Z")
    stats = overview["stats"]

    assert stats["package_count"] == 3
    assert stats["packages_le2_maintainers_count"] == 2  # a (1), b (2)
    assert stats["packages_le2_maintainers_pct"] == round(2 / 3 * 100, 1)


def test_package_overview_le2_maintainers_excludes_team_handles():
    package_maintainers = {"a": ["alice", "conda-forge/go", "conda-forge/help"]}
    overview = compute_package_overview(package_maintainers, [], {}, "2026-01-01T00:00:00Z")

    assert overview["stats"]["packages_le2_maintainers_count"] == 1  # only alice counts


def test_package_overview_resorts_transitive_dependencies_by_transitive_dependents():
    # On-disk order (by transitive_only_dependents, per the plan) deliberately differs from the
    # transitive_dependents order we must re-sort by.
    records = [
        _dep("low-transitive-high-only", direct=0, transitive=5),
        _dep("high-transitive", direct=0, transitive=100),
    ]
    package_maintainers = {"low-transitive-high-only": ["alice"], "high-transitive": ["alice"]}
    overview = compute_package_overview(package_maintainers, records, {}, "2026-01-01T00:00:00Z")

    names = [d["name"] for d in overview["transitive_dependencies"]]
    assert names == ["high-transitive", "low-transitive-high-only"]
    assert overview["stats"]["most_depended_on"]["name"] == "high-transitive"


def test_package_overview_most_depended_on_includes_maintainer_count():
    records = [_dep("ca-certificates", direct=2, transitive=1000)]
    package_maintainers = {"ca-certificates": ["alice", "bob"]}
    overview = compute_package_overview(package_maintainers, records, {}, "2026-01-01T00:00:00Z")

    assert overview["stats"]["most_depended_on"] == {
        "name": "ca-certificates",
        "transitive_dependents": 1000,
        "maintainer_count": 2,
    }


def test_package_overview_risk_packages_ranked_by_risk_score_desc():
    package_maintainers = {
        "thin-bench": ["alice"],
        "well-staffed": ["alice", "bob", "carol", "dave", "erin", "frank"],
    }
    package_downloads = {
        "thin-bench": {"2026-01": 1000},
        "well-staffed": {"2026-01": 1000},
    }
    overview = compute_package_overview(
        package_maintainers, [], package_downloads, "2026-01-01T00:00:00Z"
    )

    names = [p["name"] for p in overview["risk_packages"]]
    assert names[0] == "thin-bench"
    assert overview["risk_packages"][0]["status"] == STATUS_AT_RISK
    assert overview["risk_packages"][1]["status"] == STATUS_HEALTHY


def test_package_overview_empty_transitive_dependencies_gives_null_most_depended_on():
    overview = compute_package_overview({}, [], {}, "2026-01-01T00:00:00Z")
    assert overview["stats"]["most_depended_on"] is None


def test_package_overview_excludes_virtual_packages_absent_from_package_maintainers():
    # e.g. conda's synthetic "__glibc"/"__unix"/"__win" virtual packages -- present in repodata
    # dependency specs (and therefore transitive-dependencies.json) but produced by no feedstock,
    # so they have no maintainers and no profile page to link to.
    records = [
        _dep("__glibc", direct=0, transitive=100000),
        _dep("ca-certificates", direct=2, transitive=1000),
    ]
    package_maintainers = {"ca-certificates": ["alice", "bob"]}
    overview = compute_package_overview(package_maintainers, records, {}, "2026-01-01T00:00:00Z")

    assert overview["stats"]["most_depended_on"]["name"] == "ca-certificates"
    names = [d["name"] for d in overview["transitive_dependencies"]]
    assert "__glibc" not in names


def test_package_overview_excludes_ignored_packages_even_with_real_maintainers():
    # Unlike "__glibc", "pypy3.8" is a real, feedstock-built package present in
    # package-maintainers.json with real maintainers -- it must still be dropped.
    records = [
        _dep("pypy3.8", direct=5, transitive=100000),
        _dep("ca-certificates", direct=2, transitive=1000),
    ]
    package_maintainers = {"ca-certificates": ["alice", "bob"], "pypy3.8": ["carol"]}
    overview = compute_package_overview(package_maintainers, records, {}, "2026-01-01T00:00:00Z")

    assert overview["stats"]["package_count"] == 1
    assert overview["stats"]["most_depended_on"]["name"] == "ca-certificates"
    names = [d["name"] for d in overview["transitive_dependencies"]]
    assert "pypy3.8" not in names
    assert names == ["ca-certificates"]


# ── build_maintainer_profiles ────────────────────────────────────────────────────────────────


def _maintainer_graph(edges):
    return {
        "attributes": {},
        "options": {"type": "undirected", "multi": False, "allowSelfLoops": False},
        "nodes": [],
        "edges": [
            {
                "key": f"{s}--{t}",
                "source": s,
                "target": t,
                "undirected": True,
                "attributes": {"weight": w},
            }
            for s, t, w in edges
        ],
    }


def test_build_maintainer_profiles_yields_one_per_maintainer_info_entry():
    maintainers = {"widget-feedstock": ["alice", "bob"]}
    maintainer_info = {"alice": _info("alice"), "bob": _info("bob")}
    graph = _maintainer_graph([("alice", "bob", 1)])

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, {}))

    assert set(profiles.keys()) == {"alice", "bob"}


def test_maintainer_profile_feedstock_count_and_packages():
    maintainers = {
        "widget-feedstock": ["alice"],
        "gadget-feedstock": ["alice"],
    }
    maintainer_info = {"alice": _info("alice", "Alice Example")}
    package_names = {"widget-feedstock": ["widget", "widget-devel"], "gadget-feedstock": []}
    graph = _maintainer_graph([])

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, package_names))
    alice = profiles["alice"]

    assert alice["name"] == "Alice Example"
    assert alice["feedstock_count"] == 2
    # "gadget-feedstock" has no package_names entry -> falls back to its own feedstock name.
    assert alice["packages"] == ["gadget-feedstock", "widget", "widget-devel"]


def test_maintainer_profile_excludes_ignored_packages_from_packages_list():
    maintainers = {"pypy-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice", "Alice Example")}
    package_names = {"pypy-feedstock": ["pypy3.8", "pypy3.9", "widget"]}
    graph = _maintainer_graph([])

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, package_names))

    assert profiles["alice"]["packages"] == ["widget"]


def test_maintainer_profile_co_maintainers_ranked_by_shared_feedstocks_desc_capped_at_12():
    maintainers = {"solo-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice"), **{f"m{i}": _info(f"m{i}") for i in range(15)}}
    edges = [("alice", f"m{i}", 15 - i) for i in range(15)]
    graph = _maintainer_graph(edges)

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, {}))
    alice = profiles["alice"]

    assert alice["co_maintainer_count"] == 15
    assert len(alice["co_maintainers"]) == 12
    assert alice["co_maintainers"][0]["login"] == "m0"  # weight 15, highest
    assert alice["most_shared_with"] == {"login": "m0", "shared_feedstocks": 15}


def test_maintainer_profile_with_no_edges_has_null_most_shared_with():
    maintainers = {"solo-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice")}
    graph = _maintainer_graph([])

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, {}))

    assert profiles["alice"]["co_maintainer_count"] == 0
    assert profiles["alice"]["co_maintainers"] == []
    assert profiles["alice"]["most_shared_with"] is None


def test_maintainer_profile_missing_from_graph_entirely_still_produced():
    # e.g. a maintainer-info.json entry with 0 current feedstocks -- has no graph node at all.
    maintainer_info = {"ghost-profile": _info("ghost-profile")}
    graph = _maintainer_graph([])

    profiles = dict(build_maintainer_profiles({}, maintainer_info, graph, {}))

    assert profiles["ghost-profile"]["feedstock_count"] == 0
    assert profiles["ghost-profile"]["packages"] == []
    assert profiles["ghost-profile"]["ego_network"] == {
        "nodes": [{"key": "ghost-profile", "label": "ghost-profile", "distance": 0}],
        "edges": [],
    }


# ── build_maintainer_ego_network ─────────────────────────────────────────────────────────────


def _adjacency(edges: list[tuple[str, str, int]]) -> dict[str, list[tuple[str, int]]]:
    return _maintainer_graph_adjacency(_maintainer_graph(edges))


def test_ego_network_includes_center_at_distance_zero():
    maintainer_info = {"alice": _info("alice", "Alice Example")}
    adjacency = _adjacency([])

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    assert network == {
        "nodes": [{"key": "alice", "label": "Alice Example", "distance": 0}],
        "edges": [],
    }


def test_ego_network_bfs_hop_distances():
    maintainer_info = {login: _info(login) for login in ["alice", "bob", "carol", "dave"]}
    adjacency = _adjacency([("alice", "bob", 1), ("bob", "carol", 1), ("carol", "dave", 1)])

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    distances = {node["key"]: node["distance"] for node in network["nodes"]}
    assert distances == {"alice": 0, "bob": 1, "carol": 2}
    assert "dave" not in distances


def test_ego_network_induced_subgraph_includes_cross_hop_edges():
    # alice--bob and alice--carol put bob/carol both at distance 1; bob--carol is not on any BFS
    # tree path from alice, but must still appear since it's an edge between two visited nodes.
    maintainer_info = {login: _info(login) for login in ["alice", "bob", "carol", "dave"]}
    adjacency = _adjacency(
        [("alice", "bob", 1), ("alice", "carol", 1), ("bob", "carol", 3), ("bob", "dave", 1)]
    )

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    edge_pairs = {(edge["source"], edge["target"]) for edge in network["edges"]}
    assert ("bob", "carol") in edge_pairs
    matching = [e for e in network["edges"] if {e["source"], e["target"]} == {"bob", "carol"}]
    assert matching == [{"source": "bob", "target": "carol", "weight": 3}]


def test_ego_network_dedups_undirected_edges():
    maintainer_info = {login: _info(login) for login in ["alice", "bob"]}
    adjacency = _adjacency([("alice", "bob", 2)])

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    assert len(network["edges"]) == 1
    assert network["edges"][0] == {"source": "alice", "target": "bob", "weight": 2}


def test_ego_network_missing_from_graph_entirely():
    maintainer_info = {"ghost-profile": _info("ghost-profile")}
    adjacency = _adjacency([])

    network = build_maintainer_ego_network("ghost-profile", maintainer_info, adjacency)

    assert network == {
        "nodes": [{"key": "ghost-profile", "label": "ghost-profile", "distance": 0}],
        "edges": [],
    }


def test_ego_network_label_uses_display_name_and_falls_back_to_login():
    maintainer_info = {"alice": _info("alice", "Alice Example"), "bob": _info("bob")}
    adjacency = _adjacency([("alice", "bob", 1)])

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    labels = {node["key"]: node["label"] for node in network["nodes"]}
    assert labels == {"alice": "Alice Example", "bob": "bob"}


def test_ego_network_capped_at_co_maintainers_limit_and_matches_co_maintainers_list():
    maintainers = {"solo-feedstock": ["alice"]}
    maintainer_info = {"alice": _info("alice"), **{f"m{i}": _info(f"m{i}") for i in range(15)}}
    edges = [("alice", f"m{i}", 15 - i) for i in range(15)]  # distinct weights, no ties
    graph = _maintainer_graph(edges)

    profiles = dict(build_maintainer_profiles(maintainers, maintainer_info, graph, {}))
    alice = profiles["alice"]

    # Capped to the same top 12 (by weight) already shown in the "Co-maintainers" box.
    assert len(alice["ego_network"]["nodes"]) == 13  # center + top 12 by weight
    ego_logins = {node["key"] for node in alice["ego_network"]["nodes"]} - {"alice"}
    co_maintainer_logins = {co["login"] for co in alice["co_maintainers"]}
    assert ego_logins == co_maintainer_logins


def test_ego_network_fills_remaining_budget_with_ranked_two_hop_neighbors():
    # alice has only 2 direct co-maintainers (bob, carol), leaving a budget of 10 second-degree
    # slots -- filled from bob/carol's own co-maintainers, ranked by edge weight, highest first.
    maintainer_info = {
        login: _info(login) for login in ["alice", "bob", "carol"] + [f"x{i}" for i in range(15)]
    }
    edges = [("alice", "bob", 5), ("alice", "carol", 5)]
    edges += [("bob", f"x{i}", 15 - i) for i in range(8)]  # x0..x7, weights 15..8
    edges += [("carol", f"x{i}", 15 - i) for i in range(8, 15)]  # x8..x14, weights 7..1
    adjacency = _adjacency(edges)

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    assert len(network["nodes"]) == 13  # center + 2 direct + 10 second-degree
    two_hop = {node["key"] for node in network["nodes"] if node["distance"] == 2}
    assert two_hop == {f"x{i}" for i in range(10)}  # the 10 highest-weight candidates, x0..x9


def test_ego_network_no_budget_left_for_two_hop_when_direct_neighbors_fill_the_cap():
    maintainer_info = {"alice": _info("alice"), **{f"m{i}": _info(f"m{i}") for i in range(12)}}
    edges = [("alice", f"m{i}", 12 - i) for i in range(12)]
    edges.append(("m0", "extra-neighbor", 99))
    maintainer_info["extra-neighbor"] = _info("extra-neighbor")
    adjacency = _adjacency(edges)

    network = build_maintainer_ego_network("alice", maintainer_info, adjacency)

    assert len(network["nodes"]) == 13  # center + all 12 direct neighbors, no room for hop 2
    assert all(node["distance"] <= 1 for node in network["nodes"])


# ── build_package_profiles ───────────────────────────────────────────────────────────────────


def _package_graph(node_pageranks, edges):
    return {
        "attributes": {},
        "options": {"type": "directed", "multi": False, "allowSelfLoops": False},
        "nodes": [{"key": k, "attributes": {"pagerank": v}} for k, v in node_pageranks.items()],
        "edges": [
            {
                "key": f"{s}->{t}",
                "source": s,
                "target": t,
                "undirected": False,
                "attributes": {"weight": 1},
            }
            for s, t in edges
        ],
    }


def test_build_package_profiles_yields_one_per_package():
    package_maintainers = {"numpy": ["alice"], "pandas": ["alice"]}
    profiles = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))
    assert set(profiles.keys()) == {"numpy", "pandas"}


def test_package_profile_maintainer_count_excludes_team_handles_but_lists_them():
    package_maintainers = {"widget": ["alice", "conda-forge/go"]}
    maintainer_info = {"alice": _info("alice")}
    profile = dict(
        build_package_profiles(package_maintainers, maintainer_info, _package_graph({}, []), [], {})
    )["widget"]

    assert profile["maintainer_count"] == 1
    logins = [m["login"] for m in profile["maintainers"]]
    assert logins == ["alice", "conda-forge/go"]
    names = {m["login"]: m["name"] for m in profile["maintainers"]}
    assert names["alice"] == "alice"  # info has no "name" set -> falls back to login
    assert names["conda-forge/go"] is None  # no profile -> plain-text sentinel


def test_package_profile_status_derived_from_maintainer_count():
    package_maintainers = {
        "at-risk": ["alice"],
        "watch": ["alice", "bob", "carol"],
        "healthy": ["alice", "bob", "carol", "dave", "erin", "frank"],
    }
    profiles = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))

    assert profiles["at-risk"]["status"] == STATUS_AT_RISK
    assert profiles["watch"]["status"] == STATUS_WATCH
    assert profiles["healthy"]["status"] == STATUS_HEALTHY


def test_package_profile_active_maintainer_count_null_without_activity_data():
    package_maintainers = {"widget": ["alice"]}
    profile = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))[
        "widget"
    ]
    assert profile["active_maintainer_count"] is None


def test_package_profile_active_maintainer_count_from_feedstock_activity():
    package_maintainers = {"widget": ["alice"]}
    package_names = {"widget-feedstock": ["widget"]}
    feedstock_activity: dict[str, dict] = {
        "widget-feedstock": {"active_maintainers": {"alice": {}, "bob": {}}},
    }
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names,
            None,
            feedstock_activity,
        )
    )["widget"]
    assert profile["active_maintainer_count"] == 2


def test_package_profile_status_demoted_to_at_risk_when_active_count_zero():
    package_maintainers = {"healthy": ["alice", "bob", "carol", "dave", "erin", "frank"]}
    package_names = {"healthy-feedstock": ["healthy"]}
    feedstock_activity: dict[str, dict] = {"healthy-feedstock": {"active_maintainers": {}}}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names,
            None,
            feedstock_activity,
        )
    )["healthy"]
    assert profile["active_maintainer_count"] == 0
    assert profile["status"] == STATUS_AT_RISK


def test_package_profile_direct_dependencies_and_notable_dependents():
    package_maintainers = {"numpy": ["alice"], "pandas": ["alice"], "python": ["alice"]}
    # numpy -> python (numpy depends on python); pandas -> numpy (pandas depends on numpy)
    graph = _package_graph(
        {"numpy": 0.5, "pandas": 0.1, "python": 0.9},
        [("numpy", "python"), ("pandas", "numpy")],
    )
    profiles = dict(build_package_profiles(package_maintainers, {}, graph, [], {}))

    assert profiles["numpy"]["direct_dependencies"] == ["python"]
    assert profiles["numpy"]["notable_dependents"] == ["pandas"]
    assert profiles["python"]["direct_dependencies"] == []
    assert profiles["python"]["notable_dependents"] == ["numpy"]


def test_package_profile_notable_dependents_ranked_by_pagerank_desc_capped_at_8():
    dependents = [f"dep{i}" for i in range(10)]
    package_maintainers = {"hub": ["alice"], **{d: ["alice"] for d in dependents}}
    pageranks = {"hub": 0.01, **{d: float(i) for i, d in enumerate(dependents)}}
    edges = [(d, "hub") for d in dependents]
    graph = _package_graph(pageranks, edges)

    profiles = dict(build_package_profiles(package_maintainers, {}, graph, [], {}))
    notable = profiles["hub"]["notable_dependents"]

    assert len(notable) == 8
    assert notable[0] == "dep9"  # highest pagerank first
    assert notable == sorted(notable, key=lambda d: -pageranks[d])


def test_package_profile_excludes_virtual_packages_from_dependency_lists():
    package_maintainers = {"numpy": ["alice"]}
    graph = _package_graph(
        {"numpy": 0.5, "__glibc": 0.9, "python": 0.5},
        [("numpy", "__glibc"), ("numpy", "python"), ("__glibc", "numpy")],
    )
    profile = dict(build_package_profiles(package_maintainers, {}, graph, [], {}))["numpy"]

    assert "__glibc" not in profile["direct_dependencies"]
    assert profile["direct_dependencies"] == ["python"]
    assert "__glibc" not in profile["notable_dependents"]


def test_package_profile_excludes_ignored_packages_from_dependency_lists():
    package_maintainers = {"numpy": ["alice"], "pypy3.8": ["bob"]}
    graph = _package_graph(
        {"numpy": 0.5, "pypy3.8": 0.9, "python": 0.5},
        [("numpy", "pypy3.8"), ("numpy", "python"), ("pypy3.8", "numpy")],
    )
    profile = dict(build_package_profiles(package_maintainers, {}, graph, [], {}))["numpy"]

    assert "pypy3.8" not in profile["direct_dependencies"]
    assert profile["direct_dependencies"] == ["python"]
    assert "pypy3.8" not in profile["notable_dependents"]


def test_build_package_profiles_generates_no_profile_for_ignored_packages():
    for name in IGNORED_PACKAGES:
        package_maintainers = {name: ["alice"], "numpy": ["alice"]}
        profiles = dict(build_package_profiles(package_maintainers, {}, {}, [], {}))
        assert name not in profiles
        assert list(profiles) == ["numpy"]


def test_is_ignored_package():
    for name in IGNORED_PACKAGES:
        assert is_ignored_package(name)
    assert not is_ignored_package("numpy")


def test_package_profile_absent_from_graph_gets_empty_dependency_lists():
    package_maintainers = {"orphan": ["alice"]}
    profiles = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))

    assert profiles["orphan"]["direct_dependencies"] == []
    assert profiles["orphan"]["notable_dependents"] == []


def test_package_profile_dependent_feedstock_count_from_transitive_dependencies():
    package_maintainers = {"numpy": ["alice"], "obscure": ["alice"]}
    records = [_dep("numpy", direct=5, transitive=200)]
    profiles = dict(
        build_package_profiles(package_maintainers, {}, _package_graph({}, []), records, {})
    )

    assert profiles["numpy"]["dependent_feedstock_count"] == 200
    assert profiles["obscure"]["dependent_feedstock_count"] is None


def test_package_profile_downloads_monthly_chronological_and_last_month():
    package_maintainers = {"numpy": ["alice"]}
    downloads = {"numpy": {"2026-01": 10, "2025-11": 5, "2025-12": 8}}
    profile = dict(
        build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], downloads)
    )["numpy"]

    assert profile["downloads_monthly"] == [
        {"month": "2025-11", "downloads": 5},
        {"month": "2025-12", "downloads": 8},
        {"month": "2026-01", "downloads": 10},
    ]
    assert profile["downloads_last_month"] == 10


def test_package_profile_no_downloads_data_gives_empty_list_and_zero():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))[
        "numpy"
    ]

    assert profile["downloads_monthly"] == []
    assert profile["downloads_last_month"] == 0


def test_package_profile_about_null_without_package_about_data():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))[
        "numpy"
    ]
    assert profile["about"] is None


def test_package_profile_about_null_when_status_not_found():
    package_maintainers = {"numpy": ["alice"]}
    package_about = {"numpy": {"status": "not_found", "fetched_at": "2026-09-26T00:00:00Z"}}
    profile = dict(
        build_package_profiles(
            package_maintainers, {}, _package_graph({}, []), [], {}, package_about=package_about
        )
    )["numpy"]
    assert profile["about"] is None


def test_package_profile_about_populated_from_package_about_data():
    package_maintainers = {"numpy": ["alice"]}
    package_about = {
        "numpy": {
            "status": "found",
            "version": "2.1.3",
            "subdir": "noarch",
            "fetched_at": "2026-09-26T00:00:00Z",
            "description": "Fundamental package for array computing.",
            "summary": "NumPy array library",
            "home": "https://numpy.org",
            "dev_url": "https://github.com/numpy/numpy",
            "doc_url": "https://numpy.org/doc/",
            "recipe_maintainers": ["mattip", "rgommers"],
        }
    }
    profile = dict(
        build_package_profiles(
            package_maintainers, {}, _package_graph({}, []), [], {}, package_about=package_about
        )
    )["numpy"]
    assert profile["about"] == {
        "description": "Fundamental package for array computing.",
        "summary": "NumPy array library",
        "home": "https://numpy.org",
        "dev_url": "https://github.com/numpy/numpy",
        "doc_url": "https://numpy.org/doc/",
        "recipe_maintainers": ["mattip", "rgommers"],
        "version": "2.1.3",
    }


def test_package_profile_about_unaffected_fields_unchanged_when_present():
    package_maintainers = {"numpy": ["alice", "bob"]}
    package_about = {"numpy": {"status": "found", "version": "2.1.3"}}
    profile = dict(
        build_package_profiles(
            package_maintainers, {}, _package_graph({}, []), [], {}, package_about=package_about
        )
    )["numpy"]
    assert profile["maintainer_count"] == 2
    assert profile["direct_dependencies"] == []


# ── feedstocks_by_package_name ───────────────────────────────────────────────────────────────


def test_feedstocks_by_package_name_single_output_feedstock():
    assert feedstocks_by_package_name({"numpy": ["numpy"]}) == {"numpy": ["numpy"]}


def test_feedstocks_by_package_name_falls_back_to_feedstock_name_when_empty():
    assert feedstocks_by_package_name({"widget": []}) == {"widget": ["widget"]}


def test_feedstocks_by_package_name_multi_output_feedstock():
    result = feedstocks_by_package_name({"boost": ["libboost", "boost-cpp"]})
    assert result == {"libboost": ["boost"], "boost-cpp": ["boost"]}


def test_feedstocks_by_package_name_unions_and_sorts_multiple_feedstocks():
    result = feedstocks_by_package_name({"blas": ["libblas"], "lapack": ["libblas"]})
    assert result == {"libblas": ["blas", "lapack"]}


# ── build_package_profiles: feedstocks + license ─────────────────────────────────────────────


def test_package_profile_feedstock_link_derived_from_package_names():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"numpy": ["numpy"]},
        )
    )["numpy"]

    assert profile["feedstocks"] == [
        {"name": "numpy", "url": "https://github.com/conda-forge/numpy-feedstock"}
    ]


def test_package_profile_feedstock_link_falls_back_to_package_name_without_package_names():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(build_package_profiles(package_maintainers, {}, _package_graph({}, []), [], {}))[
        "numpy"
    ]

    assert profile["feedstocks"] == [
        {"name": "numpy", "url": "https://github.com/conda-forge/numpy-feedstock"}
    ]


def test_package_profile_multi_output_feedstock_produces_one_feedstock_link():
    package_maintainers = {"libboost": ["alice"], "boost-cpp": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"boost": ["libboost", "boost-cpp"]},
        )
    )["libboost"]

    assert profile["feedstocks"] == [
        {"name": "boost", "url": "https://github.com/conda-forge/boost-feedstock"}
    ]


def test_package_profile_package_from_multiple_feedstocks_lists_all_sorted():
    package_maintainers = {"libblas": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"blas": ["libblas"], "lapack": ["libblas"]},
        )
    )["libblas"]

    assert [f["name"] for f in profile["feedstocks"]] == ["blas", "lapack"]


def test_package_profile_license_from_owning_feedstock():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"numpy": ["numpy"]},
            licenses={"numpy": "BSD-3-Clause"},
        )
    )["numpy"]

    assert profile["license"] == "BSD-3-Clause"


def test_package_profile_license_none_when_not_declared():
    package_maintainers = {"numpy": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"numpy": ["numpy"]},
        )
    )["numpy"]

    assert profile["license"] is None


def test_package_profile_license_picks_first_feedstock_that_declares_one():
    package_maintainers = {"libblas": ["alice"]}
    profile = dict(
        build_package_profiles(
            package_maintainers,
            {},
            _package_graph({}, []),
            [],
            {},
            package_names={"blas": ["libblas"], "lapack": ["libblas"]},
            licenses={"lapack": "BSD-3-Clause"},
        )
    )["libblas"]

    assert profile["license"] == "BSD-3-Clause"
