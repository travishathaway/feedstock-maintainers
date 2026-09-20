"""Tests for the monthly feedstock-count backfill/append logic.

Network access (the commits/trees REST endpoints, via maintainer_history's helpers) is
monkeypatched throughout -- no real requests, no token needed.
"""

from __future__ import annotations

import json
from datetime import date

from feedstock_maintainers import feedstock_count_history as fch


def _tree(count: int) -> dict[str, str]:
    return {f"feedstock-{i}": f"sha{i}" for i in range(count)}


# --- run_backfill ---------------------------------------------------------------------------


def test_run_backfill_records_one_entry_per_month(tmp_path, monkeypatch):
    commits_by_month = {
        date(2024, 1, 31): "commit-jan",
        date(2024, 2, 29): "commit-feb",
    }
    trees_by_commit = {
        "commit-jan": _tree(3),
        "commit-feb": _tree(5),
    }

    monkeypatch.setattr(fch, "resolve_commit_at", lambda d, **k: commits_by_month.get(d))
    monkeypatch.setattr(fch, "fetch_submodule_tree", lambda sha, **k: trees_by_commit[sha])

    output = tmp_path / "feedstock-count-history.json"
    fch.run_backfill(date(2024, 1, 1), date(2024, 2, 29), output)

    history = json.loads(output.read_text())
    assert history == [
        {"date": "2024-01-31", "feedstock_count": 3},
        {"date": "2024-02-29", "feedstock_count": 5},
    ]


def test_run_backfill_skips_months_before_history_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(fch, "resolve_commit_at", lambda d, **k: None)
    monkeypatch.setattr(
        fch,
        "fetch_submodule_tree",
        lambda sha, **k: (_ for _ in ()).throw(AssertionError("should never be called")),
    )

    output = tmp_path / "feedstock-count-history.json"
    fch.run_backfill(date(2010, 1, 1), date(2010, 3, 31), output)

    assert not output.exists()


def test_run_backfill_resumes_from_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "feedstock-count-history.json"
    output.write_text(json.dumps([{"date": "2024-01-31", "feedstock_count": 3}]))

    seen_dates = []

    def fake_resolve(target_date, **kwargs):
        seen_dates.append(target_date)
        return "commit-feb"

    monkeypatch.setattr(fch, "resolve_commit_at", fake_resolve)
    monkeypatch.setattr(fch, "fetch_submodule_tree", lambda sha, **k: _tree(5))

    fch.run_backfill(date(2024, 1, 1), date(2024, 2, 29), output)

    assert seen_dates == [date(2024, 2, 29)]  # January already recorded, not re-fetched
    history = json.loads(output.read_text())
    assert history == [
        {"date": "2024-01-31", "feedstock_count": 3},
        {"date": "2024-02-29", "feedstock_count": 5},
    ]


def test_run_backfill_resumes_past_a_mid_month_append_entry(tmp_path, monkeypatch):
    """Regression test: a mid-month entry from `append_current_point` must not be mistaken for
    a high-water mark that makes every earlier, still-missing month look already covered."""
    output = tmp_path / "feedstock-count-history.json"
    output.write_text(json.dumps([{"date": "2024-02-15", "feedstock_count": 9}]))

    commits_by_month = {date(2024, 1, 31): "commit-jan"}
    monkeypatch.setattr(fch, "resolve_commit_at", lambda d, **k: commits_by_month.get(d))
    monkeypatch.setattr(fch, "fetch_submodule_tree", lambda sha, **k: _tree(3))

    fch.run_backfill(date(2024, 1, 1), date(2024, 2, 29), output)

    history = json.loads(output.read_text())
    assert history == [
        {"date": "2024-01-31", "feedstock_count": 3},
        {"date": "2024-02-15", "feedstock_count": 9},
    ]


def test_run_backfill_flushes_after_every_month(tmp_path, monkeypatch):
    """An interrupted run should leave whatever months completed so far on disk."""
    commits_by_month = {date(2024, 1, 31): "commit-jan", date(2024, 2, 29): "commit-feb"}
    monkeypatch.setattr(fch, "resolve_commit_at", lambda d, **k: commits_by_month[d])

    call_count = 0

    def fake_fetch_tree(sha, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated crash mid-backfill")
        return _tree(3)

    monkeypatch.setattr(fch, "fetch_submodule_tree", fake_fetch_tree)

    output = tmp_path / "feedstock-count-history.json"
    try:
        fch.run_backfill(date(2024, 1, 1), date(2024, 2, 29), output)
    except RuntimeError:
        pass

    history = json.loads(output.read_text())
    assert history == [{"date": "2024-01-31", "feedstock_count": 3}]


# --- append_current_point -------------------------------------------------------------------


def test_append_current_point_adds_new_entry_for_new_month():
    history = [{"date": "2024-01-31", "feedstock_count": 100}]
    result = fch.append_current_point(105, history, date(2024, 2, 15))

    assert result == [
        {"date": "2024-01-31", "feedstock_count": 100},
        {"date": "2024-02-15", "feedstock_count": 105},
    ]


def test_append_current_point_replaces_entry_in_same_month():
    history = [
        {"date": "2024-01-31", "feedstock_count": 100},
        {"date": "2024-02-15", "feedstock_count": 105},
    ]
    result = fch.append_current_point(110, history, date(2024, 2, 20))

    assert result == [
        {"date": "2024-01-31", "feedstock_count": 100},
        {"date": "2024-02-20", "feedstock_count": 110},
    ]


def test_append_current_point_force_always_appends():
    history = [{"date": "2024-02-15", "feedstock_count": 105}]
    result = fch.append_current_point(110, history, date(2024, 2, 20), force=True)

    assert len(result) == 2
    assert result[-1] == {"date": "2024-02-20", "feedstock_count": 110}


def test_append_current_point_empty_history_appends_first_entry():
    result = fch.append_current_point(42, [], date(2024, 1, 1))
    assert result == [{"date": "2024-01-01", "feedstock_count": 42}]
