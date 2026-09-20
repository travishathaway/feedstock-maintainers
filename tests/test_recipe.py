"""Tests for parsing maintainers and package names out of meta.yaml/recipe.yaml text."""

from __future__ import annotations

import pytest

from feedstock_maintainers.recipe import (
    ParseError,
    extract_license_from_text,
    extract_maintainers_from_text,
    extract_package_names_from_text,
    parse_recipe,
)


def test_extract_maintainers_from_meta_yaml():
    text = "extra:\n  recipe-maintainers: [alice, bob]\n"
    assert extract_maintainers_from_text("meta.yaml", text) == ["alice", "bob"]


def test_extract_maintainers_from_recipe_yaml():
    text = "extra:\n  recipe-maintainers: [alice, bob]\n"
    assert extract_maintainers_from_text("recipe.yaml", text) == ["alice", "bob"]


def test_extract_maintainers_renders_meta_yaml_jinja_set():
    text = '{% set name = "widget" %}\nextra:\n  recipe-maintainers: [alice]\n'
    assert extract_maintainers_from_text("meta.yaml", text) == ["alice"]


def test_extract_maintainers_missing_extra_returns_empty_list():
    assert extract_maintainers_from_text("meta.yaml", "package:\n  name: widget\n") == []


def test_extract_maintainers_raises_parse_error_on_bad_yaml():
    with pytest.raises(ParseError):
        extract_maintainers_from_text("meta.yaml", "extra: [unterminated\n")


def test_package_name_single_output_meta_yaml():
    text = 'package:\n  name: widget\n  version: "1.0"\n'
    assert extract_package_names_from_text("meta.yaml", text) == ["widget"]


def test_package_name_single_output_recipe_yaml_with_context():
    text = "context:\n  name: marimo\npackage:\n  name: ${{ name|lower }}\n  version: 1.0.0\n"
    assert extract_package_names_from_text("recipe.yaml", text) == ["marimo"]


def test_package_name_recipe_yaml_context_chain_of_references():
    text = (
        "context:\n"
        "  repo_name: gz-common\n"
        "  name: ${{ repo_name }}\n"
        '  cxx_name: ${{ "lib" ~ name }}\n'
        "package:\n"
        "  name: ${{ cxx_name }}\n"
    )
    assert extract_package_names_from_text("recipe.yaml", text) == ["libgz-common"]


def test_package_name_multi_output_meta_yaml_uses_flat_output_names():
    text = "package:\n  name: boost-split\noutputs:\n  - name: libboost\n  - name: boost-cpp\n"
    names = extract_package_names_from_text("meta.yaml", text)
    assert names == ["libboost", "boost-cpp"]
    assert "boost-split" not in names


def test_package_name_multi_output_recipe_yaml_uses_nested_package_names():
    text = (
        "recipe:\n"
        "  name: mamba-split\n"
        "  version: 1.0.0\n"
        "outputs:\n"
        "  - package:\n"
        "      name: libmamba\n"
        "  - package:\n"
        "      name: mamba\n"
    )
    names = extract_package_names_from_text("recipe.yaml", text)
    assert names == ["libmamba", "mamba"]
    assert "mamba-split" not in names


def test_package_name_missing_outputs_and_package_returns_empty_list():
    assert extract_package_names_from_text("meta.yaml", "extra:\n  recipe-maintainers: []\n") == []


def test_package_name_malformed_output_entries_are_skipped_not_fatal():
    text = "outputs:\n  - name: real-package\n  - just-a-string\n  - {}\n"
    assert extract_package_names_from_text("meta.yaml", text) == ["real-package"]


def test_parse_recipe_returns_both_lists_from_one_parse():
    text = "package:\n  name: widget\nextra:\n  recipe-maintainers: [alice, bob]\n"
    maintainers, package_names, license_ = parse_recipe("meta.yaml", text)
    assert maintainers == ["alice", "bob"]
    assert package_names == ["widget"]
    assert license_ is None


def test_parse_recipe_raises_parse_error_on_unparsable_yaml():
    with pytest.raises(ParseError):
        parse_recipe("recipe.yaml", "package: [unterminated\n")


def test_extract_license_from_meta_yaml():
    text = "about:\n  license: BSD-3-Clause\n"
    assert extract_license_from_text("meta.yaml", text) == "BSD-3-Clause"


def test_extract_license_from_recipe_yaml():
    text = "about:\n  license: Apache-2.0\n"
    assert extract_license_from_text("recipe.yaml", text) == "Apache-2.0"


def test_extract_license_ignores_per_output_about_uses_top_level():
    text = (
        "about:\n"
        "  license: BSD-3-Clause\n"
        "outputs:\n"
        "  - name: libwidget\n"
        "    about:\n"
        "      license: MIT\n"
    )
    assert extract_license_from_text("meta.yaml", text) == "BSD-3-Clause"


def test_extract_license_missing_about_returns_none():
    assert extract_license_from_text("meta.yaml", "package:\n  name: widget\n") is None


def test_extract_license_missing_license_key_returns_none():
    assert extract_license_from_text("meta.yaml", "about:\n  summary: a widget\n") is None


def test_extract_license_renders_meta_yaml_jinja():
    text = '{% set lic = "MIT" %}\nabout:\n  license: {{ lic }}\n'
    assert extract_license_from_text("meta.yaml", text) == "MIT"


def test_parse_recipe_returns_license_for_recipe_yaml():
    text = "package:\n  name: widget\nabout:\n  license: MIT\n"
    _maintainers, _package_names, license_ = parse_recipe("recipe.yaml", text)
    assert license_ == "MIT"
