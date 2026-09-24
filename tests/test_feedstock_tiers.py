"""Tests for ranking feedstocks by popularity/importance into a top tier plus everyone else."""

from __future__ import annotations

from feedstock_maintainers.feedstock_tiers import (
    TIER_LONG_TAIL,
    TIER_TOP,
    compute_feedstock_tiers,
)


def _downloads(name: str, count: int) -> dict[str, dict[str, int]]:
    return {name: {"2026-08": count}}


def _transitive(name: str, count: int) -> dict:
    return {
        "name": name,
        "direct_dependents": 0,
        "transitive_dependents": count,
        "transitive_only_dependents": 0,
        "transitive_only_ratio": 0.0,
    }


def test_top_tier_picks_highest_downloads_within_fraction():
    package_names = {f"pkg{i}-feedstock": [f"pkg{i}"] for i in range(10)}
    package_downloads: dict[str, dict[str, int]] = {}
    for i in range(10):
        package_downloads.update(_downloads(f"pkg{i}", i))

    result = compute_feedstock_tiers(
        package_names,
        package_downloads,
        [],
        "2026-09-23T00:00:00Z",
        top_downloads_fraction=0.10,
        top_transitive_fraction=0.0,
    )

    # 10 feedstocks * 10% = 1 -> only the single highest-downloads feedstock is "top".
    tiers = {name: info["tier"] for name, info in result["feedstocks"].items()}
    assert tiers["pkg9-feedstock"] == TIER_TOP
    assert tiers["pkg8-feedstock"] == TIER_LONG_TAIL


def test_top_tier_unions_transitive_dependents_even_with_low_downloads():
    package_names = {"compiler-feedstock": ["compiler"], "leaf-feedstock": ["leaf"]}
    package_downloads = {"compiler": {"2026-08": 1}, "leaf": {"2026-08": 1000}}
    transitive = [_transitive("compiler", 5000), _transitive("leaf", 0)]

    result = compute_feedstock_tiers(
        package_names,
        package_downloads,
        transitive,
        "2026-09-23T00:00:00Z",
        # By downloads alone only leaf-feedstock would make the top tier (compiler-feedstock
        # has almost none) -- the transitive-dependents union is what pulls compiler-feedstock in.
        top_downloads_fraction=0.5,
        top_transitive_fraction=0.5,
    )

    assert result["feedstocks"]["compiler-feedstock"]["tier"] == TIER_TOP
    assert result["feedstocks"]["compiler-feedstock"]["transitive_dependents"] == 5000


def test_feedstock_score_falls_back_to_feedstock_name_when_no_package_names():
    package_names: dict[str, list[str]] = {"widget-feedstock": []}
    package_downloads = {"widget-feedstock": {"2026-08": 42}}

    result = compute_feedstock_tiers(package_names, package_downloads, [], "2026-09-23T00:00:00Z")

    assert result["feedstocks"]["widget-feedstock"]["downloads_last_month"] == 42


def test_feedstock_score_is_max_across_multi_output_feedstock():
    package_names = {"widget-feedstock": ["widget-core", "widget-extra"]}
    package_downloads = {"widget-core": {"2026-08": 10}, "widget-extra": {"2026-08": 500}}

    result = compute_feedstock_tiers(package_names, package_downloads, [], "2026-09-23T00:00:00Z")

    assert result["feedstocks"]["widget-feedstock"]["downloads_last_month"] == 500


def test_rank_is_one_indexed_by_downloads_descending():
    package_names = {"a-feedstock": ["a"], "b-feedstock": ["b"]}
    package_downloads = {"a": {"2026-08": 100}, "b": {"2026-08": 10}}

    result = compute_feedstock_tiers(package_names, package_downloads, [], "2026-09-23T00:00:00Z")

    assert result["feedstocks"]["a-feedstock"]["rank"] == 1
    assert result["feedstocks"]["b-feedstock"]["rank"] == 2


def test_every_feedstock_in_package_names_is_covered():
    package_names = {f"pkg{i}-feedstock": [f"pkg{i}"] for i in range(5)}
    result = compute_feedstock_tiers(package_names, {}, [], "2026-09-23T00:00:00Z")
    assert set(result["feedstocks"]) == set(package_names)


def test_no_downloads_data_gives_zero_score_and_long_tail():
    package_names = {"widget-feedstock": ["widget"]}
    result = compute_feedstock_tiers(package_names, {}, [], "2026-09-23T00:00:00Z")
    assert result["feedstocks"]["widget-feedstock"]["downloads_last_month"] == 0
    assert result["feedstocks"]["widget-feedstock"]["tier"] == TIER_TOP  # sole feedstock -> top
