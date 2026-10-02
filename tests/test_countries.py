"""Tests for resolving GitHub profile locations to countries and tallying them."""

from __future__ import annotations

import json

from feedstock_maintainers import countries as ct


def test_resolve_plain_country_name():
    assert ct.resolve("Germany", {}) == "Germany"


def test_resolve_city():
    assert ct.resolve("Berlin", {}) == "Germany"


def test_resolve_city_with_trailing_state_code():
    assert ct.resolve("Portland, OR", {}) == "United States"


def test_resolve_override_entry():
    overrides = ct.build_overrides()
    assert ct.resolve("CERN", overrides) == "Switzerland"


def test_resolve_unresolvable_joke_string_returns_none():
    overrides = ct.build_overrides()
    assert ct.resolve("Atlantis", overrides) is None


def test_resolve_uses_pretty_display_names_not_raw_pycountry_names():
    assert ct.resolve("South Korea", {}) == "South Korea"
    assert ct.resolve("Russia", {}) == "Russia"
    assert ct.resolve("Taiwan", {}) == "Taiwan"


def test_iso_numeric_covers_display_names():
    assert ct.ISO_NUMERIC["United States"] == "840"
    assert ct.ISO_NUMERIC["South Korea"] == "410"
    assert ct.ISO_NUMERIC["Russia"] == "643"
    for name in ct.PRETTY.values():
        assert ct.ISO_NUMERIC.get(name) is not None


def test_iso_alpha2_covers_display_names():
    assert ct.ISO_ALPHA2["United States"] == "US"
    assert ct.ISO_ALPHA2["South Korea"] == "KR"
    assert ct.ISO_ALPHA2["Russia"] == "RU"
    for name in ct.PRETTY.values():
        assert ct.ISO_ALPHA2.get(name) is not None


def test_tally_countries_resolves_and_counts():
    overrides = ct.build_overrides()
    locations = {
        "alice": "Berlin, Germany",
        "bob": "Portland, OR",
        "eve": "Atlantis",
    }

    tally, unresolved, resolved = ct.tally_countries(locations, overrides)

    assert tally == {"Germany": 1, "United States": 1}
    assert unresolved == {"Atlantis": 1}
    assert resolved == {"alice": "Germany", "bob": "United States"}


def test_to_rows_shape_sorted_descending():
    tally = ct.tally_countries(
        {"a": "Germany", "b": "Germany", "c": "France"}, ct.build_overrides()
    )[0]

    rows = ct.to_rows(tally)

    assert rows == [
        {"country": "Germany", "iso_numeric": "276", "iso_alpha2": "DE", "count": 2},
        {"country": "France", "iso_numeric": "250", "iso_alpha2": "FR", "count": 1},
    ]


def test_load_locations_from_dict_keyed_by_login(tmp_path):
    path = tmp_path / "maintainer-info.json"
    path.write_text(
        json.dumps(
            {
                "alice": {"login": "alice", "location": "Berlin"},
                "bob": {"login": "bob", "location": None},
            }
        )
    )

    assert ct.load_locations(path) == {"alice": "Berlin", "bob": ""}


def test_build_overrides_merges_extra_file(tmp_path):
    extra = tmp_path / "overrides.json"
    extra.write_text(json.dumps({"Nowhereville": "Germany"}))

    overrides = ct.build_overrides(extra)

    assert overrides[ct.norm("Nowhereville")] == "Germany"
    assert overrides[ct.norm("CERN")] == "Switzerland"  # built-in table still present
