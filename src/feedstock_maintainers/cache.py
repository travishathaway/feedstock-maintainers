"""On-disk cache of raw feedstock recipe files.

Stores each feedstock's fetched recipe.yaml/meta.yaml text under
<cache_dir>/<feedstock-name>/<filename>, tracking per-feedstock fetch status
(found / not_found / error) in <cache_dir>/manifest.json. This lets `fetch`
resume without re-hitting GitHub for feedstocks it already has, and lets
`generate` commands build artifacts entirely offline from whatever is cached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, Optional

_MANIFEST_FILENAME = "manifest.json"

Status = Literal["found", "not_found", "error"]


@dataclass(frozen=True)
class CacheEntry:
    name: str
    status: Status
    filename: Optional[str] = None
    message: Optional[str] = None
    fetched_at: Optional[str] = None


class RecipeCache:
    """Reads/writes <cache_dir>/<name>/<filename> plus <cache_dir>/manifest.json."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._manifest_path = cache_dir / _MANIFEST_FILENAME
        self._entries: dict[str, CacheEntry] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if not self._manifest_path.exists():
            return
        try:
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(raw, dict):
            return
        for name, fields in raw.items():
            if isinstance(fields, dict) and fields.get("status") in ("found", "not_found", "error"):
                self._entries[name] = CacheEntry(name=name, **fields)

    def _entry_dir(self, name: str) -> Path:
        return self.cache_dir / name

    def status_for(self, name: str) -> Optional[Status]:
        entry = self._entries.get(name)
        return entry.status if entry else None

    def should_fetch(self, name: str, force: bool) -> bool:
        """True if `name` needs a network fetch: unseen, forced, or last attempt errored."""
        if force:
            return True
        status = self.status_for(name)
        return status is None or status == "error"

    def record_found(self, name: str, filename: str, text: str) -> None:
        entry_dir = self._entry_dir(name)
        entry_dir.mkdir(parents=True, exist_ok=True)
        (entry_dir / filename).write_text(text, encoding="utf-8")
        self._entries[name] = CacheEntry(
            name=name, status="found", filename=filename, fetched_at=_now()
        )

    def record_not_found(self, name: str) -> None:
        self._entries[name] = CacheEntry(name=name, status="not_found", fetched_at=_now())

    def record_error(self, name: str, message: str) -> None:
        self._entries[name] = CacheEntry(name=name, status="error", message=message, fetched_at=_now())

    def flush(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            name: {k: v for k, v in vars(entry).items() if k != "name" and v is not None}
            for name, entry in self._entries.items()
        }
        tmp = self._manifest_path.with_suffix(self._manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._manifest_path)

    def found_entries(self) -> Iterator[CacheEntry]:
        for name in sorted(self._entries):
            entry = self._entries[name]
            if entry.status == "found":
                yield entry

    def read_text(self, entry: CacheEntry) -> str:
        return (self._entry_dir(entry.name) / entry.filename).read_text(encoding="utf-8")

    def __len__(self) -> int:
        return len(self._entries)

    def counts(self) -> dict[str, int]:
        counts = {"found": 0, "not_found": 0, "error": 0}
        for entry in self._entries.values():
            counts[entry.status] += 1
        return counts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
