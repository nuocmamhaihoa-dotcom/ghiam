"""Rank 8 — read an Octoparse export. The desktop app stays outside this process."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fb_poller.engines.guard import invalid_limit, nested_text, public_error
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 8
NAME = "octoparse"

_TEXT_KEYS = ("text", "comment", "content", "message", "body", "nội dung", "binh luan", "bình luận")
_AUTHOR_KEYS = ("author", "author_name", "name", "user", "tác giả", "tac gia")
_TIME_KEYS = ("time", "date", "created_time", "created", "thời gian", "thoi gian")


def comments_from_export(path: Path, limit: int = 500) -> list[PublicComment]:
    if limit < 1:
        return []
    raw = path.read_text(encoding="utf-8-sig")
    stripped = raw.lstrip()
    if path.suffix.lower() == ".json" or stripped.startswith(("{", "[")):
        try:
            return _from_json(json.loads(raw), limit)
        except json.JSONDecodeError:
            return _from_json_lines(raw, limit)
    if stripped.startswith("{") is False and "\n{" in raw:
        lined = _from_json_lines(raw, limit)
        if lined:
            return lined
    return _from_csv(raw, limit)


def _from_json_lines(raw: str, limit: int) -> list[PublicComment]:
    rows: list[object] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _from_json(rows, limit)


def _from_json(payload: object, limit: int, depth: int = 0) -> list[PublicComment]:
    if depth > 6 or limit < 1:
        return []
    if isinstance(payload, list):
        out: list[PublicComment] = []
        for row in payload:
            out.extend(_from_json(row, limit - len(out), depth + 1))
            if len(out) >= limit:
                break
        return out
    if not isinstance(payload, dict):
        return []
    out = []
    item = _from_mapping({str(k): v for k, v in payload.items()})
    if item:
        out.append(item)
    if len(out) >= limit:
        return out
    for key in ("comments", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, (list, dict)) and value:
            out.extend(_from_json(value, limit - len(out), depth + 1))
            if len(out) >= limit:
                break
    return out


def _delimiter(header: str) -> str:
    """Count separators outside quotes. A comma inside a header cell is not the delimiter."""
    semicolons = 0
    commas = 0
    quoted = False
    for char in header:
        if char == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == ";":
            semicolons += 1
        elif char == ",":
            commas += 1
    return ";" if semicolons > commas else ","


def _from_csv(raw: str, limit: int) -> list[PublicComment]:
    if not raw.strip():
        return []
    header = raw.splitlines()[0]
    delimiter = _delimiter(header)
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
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
        text = nested_text(lowered.get(key))
        if text:
            return text
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
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
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
        return EngineResult(NAME, RANK, False, error=f"file_not_found: {path.name}")
    try:
        comments = comments_from_export(path, limit=limit)
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_rows",
        note="Imported a local Octoparse export. This process does not drive the desktop app.",
    )
