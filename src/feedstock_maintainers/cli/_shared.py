"""Helpers shared by `cli.fetch` and `cli.generate`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from ..repodata import fetch_all_repodata


def _atomic_write(output: Path, data: dict | list) -> None:
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)


def _write_json_fast(output: Path, data: dict | list) -> None:
    """Write JSON without the indent/sort-keys/atomic-rename overhead of `_atomic_write`.

    Used for the tens of thousands of small per-maintainer/per-package files `generate
    site-data` writes -- at that scale, a tmp-file-then-rename plus pretty-printed indentation
    per file adds meaningful syscall and CPU overhead for files nobody hand-edits or diffs.
    """
    output.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path, console: Console) -> None:
    """Copy `src` to `dst` if it exists, else print a warning and continue.

    Used for `generate site-data`'s history-file passthrough: these two files are maintained
    independently (appended to every few hours by `update.yml`, persisted across runs via the
    recipe-cache artifact -- see .github/workflows/update.yml -- never committed to git), so
    they may legitimately be absent (e.g. a fresh checkout before the first `update.yml` run has
    ever produced them). Missing them shouldn't fail the whole `generate site-data` run -- the
    frontend's history chart just shows a "failed to load" state until they exist.
    """
    if not src.exists():
        console.print(
            f"[yellow]Warning:[/] {src} not found, skipping (history chart will 404 until "
            "update.yml produces it)."
        )
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    console.print(f"Copied {src} -> {dst}")


def _load_json_if_exists(path: Path, console: Console, label: str) -> dict:
    """Load `path` as JSON, or return `{}` with a warning if it doesn't exist yet.

    Used for `generate site-data` inputs derived by a `generate` command that predates them (so
    an existing recipe cache/output directory from before that command started writing this file
    legitimately won't have it yet) -- missing it shouldn't fail the whole run.
    """
    if not path.exists():
        console.print(f"[yellow]Warning:[/] {path} not found, skipping ({label})")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


REPODATA_CACHE_DIR = Path("repodata_cache")


async def _run_fetch_repodata(
    platforms: list[str],
    console: Console,
    timeout: float,
    use_cache: bool = True,
) -> dict[str, dict]:
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        tasks = {
            platform: progress.add_task(f"Fetching {platform} repodata.json.zst", total=None)
            for platform in platforms
        }

        def on_progress(platform: str, downloaded: int, total: int | None) -> None:
            progress.update(tasks[platform], completed=downloaded, total=total)

        return await fetch_all_repodata(
            platforms,
            timeout,
            on_progress=on_progress,
            cache_dir=REPODATA_CACHE_DIR if use_cache else None,
        )
