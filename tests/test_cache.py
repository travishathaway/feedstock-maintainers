"""Tests for the on-disk recipe cache: round-trip, resume semantics, atomic flush."""

from __future__ import annotations

from feedstock_maintainers.cache import RecipeCache


def test_record_found_round_trips_through_found_entries(tmp_path):
    cache = RecipeCache(tmp_path / "cache")
    cache.record_found("widget-feedstock", "recipe.yaml", "extra:\n  recipe-maintainers: [alice]\n")
    cache.flush()

    entries = list(cache.found_entries())
    assert len(entries) == 1
    assert entries[0].name == "widget-feedstock"
    assert entries[0].filename == "recipe.yaml"
    assert cache.read_text(entries[0]) == "extra:\n  recipe-maintainers: [alice]\n"


def test_manifest_persists_across_instances(tmp_path):
    cache_dir = tmp_path / "cache"

    first = RecipeCache(cache_dir)
    first.record_found("widget-feedstock", "meta.yaml", "extra:\n  maintainers: [bob]\n")
    first.record_not_found("gadget-feedstock")
    first.record_error("broken-feedstock", "HTTP 500")
    first.flush()

    second = RecipeCache(cache_dir)
    assert second.status_for("widget-feedstock") == "found"
    assert second.status_for("gadget-feedstock") == "not_found"
    assert second.status_for("broken-feedstock") == "error"
    assert second.status_for("never-seen-feedstock") is None

    entries = list(second.found_entries())
    assert len(entries) == 1
    assert second.read_text(entries[0]) == "extra:\n  maintainers: [bob]\n"


def test_should_fetch_resume_semantics(tmp_path):
    cache = RecipeCache(tmp_path / "cache")
    cache.record_found("found-feedstock", "recipe.yaml", "extra: {}\n")
    cache.record_not_found("not-found-feedstock")
    cache.record_error("errored-feedstock", "boom")

    # Unseen names always need fetching.
    assert cache.should_fetch("unseen-feedstock", force=False) is True

    # found/not_found are skipped on resume...
    assert cache.should_fetch("found-feedstock", force=False) is False
    assert cache.should_fetch("not-found-feedstock", force=False) is False

    # ...but errors are always retried, force or not.
    assert cache.should_fetch("errored-feedstock", force=False) is True
    assert cache.should_fetch("errored-feedstock", force=True) is True

    # --force re-fetches everything regardless of prior status.
    assert cache.should_fetch("found-feedstock", force=True) is True
    assert cache.should_fetch("not-found-feedstock", force=True) is True


def test_should_fetch_forces_refetch_for_names_in_recipes_to_update(tmp_path):
    cache = RecipeCache(tmp_path / "cache", recipes_to_update={"found-feedstock"})
    cache.record_found("found-feedstock", "recipe.yaml", "extra: {}\n")
    cache.record_found("other-feedstock", "recipe.yaml", "extra: {}\n")

    # In recipes_to_update -> refetched even though already found, without --force.
    assert cache.should_fetch("found-feedstock", force=False) is True

    # Already found but not in recipes_to_update -> normal resume semantics still apply.
    assert cache.should_fetch("other-feedstock", force=False) is False


def test_recipes_to_update_none_preserves_normal_resume_semantics(tmp_path):
    cache = RecipeCache(tmp_path / "cache", recipes_to_update=None)
    cache.record_found("found-feedstock", "recipe.yaml", "extra: {}\n")

    assert cache.should_fetch("found-feedstock", force=False) is False


def test_flush_is_atomic_and_leaves_no_tmp_file(tmp_path):
    cache_dir = tmp_path / "cache"
    cache = RecipeCache(cache_dir)
    cache.record_found("widget-feedstock", "recipe.yaml", "extra: {}\n")
    cache.flush()

    manifest = cache_dir / "manifest.json"
    assert manifest.exists()
    assert not (cache_dir / "manifest.json.tmp").exists()


def test_not_found_and_error_entries_write_no_recipe_file(tmp_path):
    cache_dir = tmp_path / "cache"
    cache = RecipeCache(cache_dir)
    cache.record_not_found("gadget-feedstock")
    cache.record_error("broken-feedstock", "boom")
    cache.flush()

    assert not (cache_dir / "gadget-feedstock").exists()
    assert not (cache_dir / "broken-feedstock").exists()

    counts = cache.counts()
    assert counts == {"found": 0, "not_found": 1, "error": 1}


def test_found_entries_excludes_not_found_and_error(tmp_path):
    cache = RecipeCache(tmp_path / "cache")
    cache.record_found("found-feedstock", "recipe.yaml", "extra: {}\n")
    cache.record_not_found("not-found-feedstock")
    cache.record_error("errored-feedstock", "boom")

    names = [entry.name for entry in cache.found_entries()]
    assert names == ["found-feedstock"]
