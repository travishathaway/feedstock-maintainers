"""Rank feedstocks by popularity/importance and split them into a small "top" tier plus everyone
else, so the expensive per-feedstock GitHub activity fetch (see `activity.py`) only has to cover a
few thousand feedstocks instead of all ~29k.

Reuses the same download-lookup helpers `site_data.py` already uses for its own popularity
rankings (`normalize_package_downloads`/`latest_month_downloads`), applied per feedstock instead
of per package, so a feedstock's score is the max across its output package name(s) (a feedstock
can produce more than one installable package -- see `package-names.json`).
"""

from __future__ import annotations

import math
from typing import Any

from .site_data import latest_month_downloads, lookup_package_downloads, normalize_package_downloads

TIER_TOP = "top"
TIER_LONG_TAIL = "long_tail"


def _feedstock_score(
    feedstock: str,
    package_names: dict[str, list[str]],
    normalized_downloads: dict[str, dict[str, int]],
    transitive_by_name: dict[str, int],
) -> tuple[int, int]:
    """Return (downloads_last_month, transitive_dependents) for `feedstock`, each the max across
    its effective output package name(s) -- mirrors the "fall back to the feedstock name itself"
    join convention used throughout `site_data.py`/`graph_data.py`."""
    effective_names = package_names.get(feedstock) or [feedstock]
    downloads = max(
        (
            latest_month_downloads(lookup_package_downloads(normalized_downloads, name))[1]
            for name in effective_names
        ),
        default=0,
    )
    transitive = max((transitive_by_name.get(name, 0) for name in effective_names), default=0)
    return downloads, transitive


def compute_feedstock_tiers(
    package_names: dict[str, list[str]],
    package_downloads: dict[str, dict[str, int]],
    transitive_dependency_records: list[dict],
    generated_at: str,
    top_downloads_fraction: float = 0.10,
    top_transitive_fraction: float = 0.05,
) -> dict[str, Any]:
    """Return {"generated_at", "feedstocks": {name: {tier, rank, downloads_last_month,
    transitive_dependents}}} for every feedstock in `package_names`.

    `tier` is `"top"` for the union of the top `top_downloads_fraction` of feedstocks by last
    month's downloads and the top `top_transitive_fraction` by transitive dependents (catching
    low-download-but-structurally-critical packages, e.g. compilers/toolchains, that pure download
    ranking would miss), `"long_tail"` otherwise. `rank` is the 1-indexed downloads ranking
    (ties broken by name), kept even for long-tail feedstocks so callers can see how close a
    feedstock came to the cutoff.
    """
    normalized_downloads = normalize_package_downloads(package_downloads)
    transitive_by_name = {
        record["name"]: record["transitive_dependents"] for record in transitive_dependency_records
    }

    scores = {
        feedstock: _feedstock_score(
            feedstock, package_names, normalized_downloads, transitive_by_name
        )
        for feedstock in package_names
    }

    by_downloads = sorted(scores, key=lambda name: (-scores[name][0], name))
    by_transitive = sorted(scores, key=lambda name: (-scores[name][1], name))

    top_downloads_count = math.ceil(len(by_downloads) * top_downloads_fraction)
    top_transitive_count = math.ceil(len(by_transitive) * top_transitive_fraction)
    top_tier = set(by_downloads[:top_downloads_count]) | set(by_transitive[:top_transitive_count])

    rank_by_name = {name: rank for rank, name in enumerate(by_downloads, start=1)}

    feedstocks = {
        name: {
            "tier": TIER_TOP if name in top_tier else TIER_LONG_TAIL,
            "rank": rank_by_name[name],
            "downloads_last_month": downloads,
            "transitive_dependents": transitive,
        }
        for name, (downloads, transitive) in scores.items()
    }

    return {"generated_at": generated_at, "feedstocks": feedstocks}
