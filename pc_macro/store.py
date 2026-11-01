"""Save named desktop macros as local JSON files."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pc_macro.events import default_title, sanitize_events
from pc_macro.numbers import NumberQueue

_SLUG = re.compile(r"[^\w]+", re.UNICODE)


def default_dir() -> Path:
    env = os.environ.get("PC_MACRO_DIR", "").strip()
    if env:
        return Path(env)
    return Path.cwd() / "data" / "macros"


class MacroStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_dir()

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            item = self._read(path)
            if item is not None:
                items.append(item)
        items.sort(key=lambda row: str(row.get("created") or ""), reverse=True)
        return items

    def get(self, slug: str) -> dict[str, Any] | None:
        path = self.root / f"{_safe_slug(slug)}.json"
        if not path.is_file():
            return None
        return self._read(path)

    def save(
        self,
        name: str,
        events: list[dict[str, Any]],
        *,
        slug: str | None = None,
        numbers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned = sanitize_events(events)
        if not cleaned:
            raise ValueError("no replayable events")
        title = " ".join(name.split()) or default_title(cleaned)
        self.root.mkdir(parents=True, exist_ok=True)
        chosen = _safe_slug(slug) if slug else _unique_slug(self.root, title)
        path = self.root / f"{chosen}.json"
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if path.is_file():
            previous = self._read(path)
            if previous and previous.get("created"):
                created = str(previous["created"])
        payload = {
            "name": title,
            "slug": chosen,
            "created": created,
            "event_count": len(cleaned),
            "events": cleaned,
        }
        if numbers:
            payload["numbers"] = NumberQueue.from_dict(numbers).to_dict()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def delete(self, slug: str) -> bool:
        path = self.root / f"{_safe_slug(slug)}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        events = sanitize_events(data.get("events") or [])
        if not events:
            return None
        slug = path.stem
        item = {
            "name": str(data.get("name") or default_title(events)),
            "slug": slug,
            "created": str(data.get("created") or ""),
            "event_count": len(events),
            "events": events,
        }
        if data.get("numbers") is not None:
            item["numbers"] = NumberQueue.from_dict(data.get("numbers")).to_dict()
        return item


def _safe_slug(value: str) -> str:
    text = _SLUG.sub("-", value.strip().lower()).strip("-")
    return (text[:60] or "ban-ghi")


def _unique_slug(root: Path, name: str) -> str:
    base = _safe_slug(name)
    slug = base
    n = 2
    while (root / f"{slug}.json").exists():
        slug = f"{base}-{n}"
        n += 1
    return slug
