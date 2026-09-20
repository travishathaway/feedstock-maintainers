"""Reconstruct the total number of feedstocks over time, sampled monthly.

Simpler than `maintainer_history`: a feedstock's presence in the mono-repo's submodule tree at a
given commit is all that's needed here -- no recipe content ever has to be fetched, so there's
nothing to carry forward between months and no per-feedstock state to track. Each month is one
independent lookup: resolve the commit as of that month, fetch its root tree, and record how many
submodule entries it has.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from .maintainer_history import fetch_submodule_tree, monthly_snapshot_dates, resolve_commit_at


def _atomic_write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_backfill(
    start: date,
    end: date,
    output_path: Path,
    token: str | None = None,
    retries: int = 3,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Backfill a monthly feedstock-count time series from `start` through `end`.

    Resumable by construction: reads any existing `output_path`, skips months already recorded,
    and flushes after every month. No separate checkpoint file is needed -- unlike
    `maintainer_history`, a month's count depends only on that month's own commit, never on the
    previous one, so there's no state to carry forward between runs.

    Coverage is tracked per (year, month) rather than via a simple "after the last date" cutoff,
    because `output_path` is shared with `append_current_point`: its most recent entry is usually
    a mid-month "today" date (from the daily append job) rather than a month-end date, and that
    date is always later than every genuinely-missing historical month-end candidate. A cutoff
    comparison would treat that as "everything before it is already covered" and never backfill
    anything. New entries are inserted in chronological order and the file is re-sorted after each
    flush, since they land before that existing mid-month tail entry.
    """
    step = on_step or (lambda _description: None)

    history: list[dict] = (
        json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else []
    )
    covered_months = {
        (parsed.year, parsed.month)
        for parsed in (date.fromisoformat(entry["date"]) for entry in history)
    }

    months = monthly_snapshot_dates(start, end)
    months = [month for month in months if (month.year, month.month) not in covered_months]

    for target_date in months:
        step(f"Resolving commit as of {target_date.isoformat()}")
        commit_sha = resolve_commit_at(target_date, token=token, retries=retries)
        if commit_sha is None:
            step(f"No history yet as of {target_date.isoformat()}, skipping")
            continue

        step(f"Counting feedstocks at {commit_sha[:8]}")
        tree = fetch_submodule_tree(commit_sha, token=token, retries=retries)
        history.append({"date": target_date.isoformat(), "feedstock_count": len(tree)})
        history.sort(key=lambda entry: entry["date"])
        _atomic_write_json(output_path, history)


def append_current_point(
    feedstock_count: int,
    history: list[dict],
    today: date,
    force: bool = False,
) -> list[dict]:
    """Return `history` with today's feedstock count appended or updated in place.

    Mirrors `maintainer_history.append_current_point`: if the last entry already falls in
    `today`'s calendar month (and `force` is False), it's replaced rather than duplicated, so
    repeated runs through a month (e.g. every 3 hours) collapse to one entry per month.
    """
    entry = {"date": today.isoformat(), "feedstock_count": feedstock_count}

    if history and not force:
        last_date = date.fromisoformat(history[-1]["date"])
        if (last_date.year, last_date.month) == (today.year, today.month):
            return [*history[:-1], entry]

    return [*history, entry]
