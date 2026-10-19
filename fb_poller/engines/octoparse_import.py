"""Rank 8 — read an Octoparse export. The desktop app stays outside this process."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fb_poller.engines.types import EngineResult, PublicComment

RANK = 8
NAME = "octoparse"

_TEXT_KEYS = ("text", "comment", "content", "message", "body", "nội dung", "binh luan", "bình luận")
_AUTHOR_KEYS = ("author", "author_name", "name", "user", "tác giả", "tac gia")
_TIME_KEYS = ("time", "date", "created_time", "created", "thời gian", "thoi gian")


def comments_from_export(path: Path, limit: int = 500) -> list[PublicComment]:
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or raw.lstrip().startswith(("{", "[")):
        return _from_json(json.loads(raw), limit)
    return _from_csv(raw, limit)


def _from_json(payload: object, limit: int) -> list[PublicComment]:
    rows: list[object]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("comments") or payload.get("data") or [payload]  # type: ignore[assignment]
    else:
        return []
    out: list[PublicComment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _from_mapping({str(k): v for k, v in row.items()})
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _from_csv(raw: str, limit: int) -> list[PublicComment]:
    reader = csv.DictReader(raw.splitlines())
    out: list[PublicComment] = []
    for row in reader:
        item = _from_mapping(row)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _pick(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _from_mapping(row: dict[str, object]) -> PublicComment | None:
    text = _pick(row, _TEXT_KEYS)
    if not text:
        return None
    return PublicComment(
        text=text,
        author_name=_pick(row, _AUTHOR_KEYS),
        created_time=_pick(row, _TIME_KEYS),
    )


async def scan(url: str = "", *, limit: int = 500, export_path: str = "", **_: object) -> EngineResult:
    del url  # Octoparse already fetched; this engine only imports the file.
    if not export_path:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_export_file",
            note="Export CSV or JSON from Octoparse, then pass --file.",
        )
    path = Path(export_path)
    if not path.is_file():
        return EngineResult(NAME, RANK, False, error=f"file_not_found: {path}")
    try:
        comments = comments_from_export(path, limit=limit)
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=f"{type(exc).__name__}: {exc}")
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_rows",
        note="Imported a local Octoparse export. This process does not drive the desktop app.",
    )
