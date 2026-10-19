"""Rank 4 — run one Apify actor and read its dataset. Isolated from other engines."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from fb_poller.engines.guard import (
    first_comment_id,
    first_person_name,
    invalid_limit,
    nested_text,
    public_error,
    redact_text,
    scalar_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

_ACTOR_ID = re.compile(r"^[A-Za-z0-9_.~\-]{1,200}$")

RANK = 4
NAME = "apify"


def _token() -> str:
    return os.environ.get("APIFY_TOKEN", "").strip()


def _actor() -> str | None:
    raw = os.environ.get("APIFY_ACTOR_ID", "apify~facebook-comments-scraper").strip()
    raw = raw.replace("/", "~")
    if not _ACTOR_ID.match(raw):
        return None
    return raw


async def scan(url: str, *, limit: int = 50, timeout: float = 180.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    token = _token()
    if not token:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_APIFY_TOKEN",
            note="Create a run at Apify and set APIFY_TOKEN plus APIFY_ACTOR_ID.",
        )
    actor = _actor()
    if actor is None:
        return EngineResult(NAME, RANK, False, error="invalid_APIFY_ACTOR_ID")
    payload = {
        "startUrls": [{"url": url}],
        "resultsLimit": limit,
        "maxComments": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(
                f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                params={"token": token},
                json=payload,
            )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    if resp.status_code >= 400:
        return EngineResult(NAME, RANK, False, error=redact_text(f"http_{resp.status_code}: {resp.text}", 220))
    try:
        payload = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments = _from_items(payload, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "empty_actor_dataset",
        note=f"Actor {actor}. Input shape varies; override the actor id if the default rejects it.",
    )


def _from_items(payload: Any, limit: int) -> list[PublicComment]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        nested_rows: list[Any] | None = None
        for key in ("items", "data", "comments", "results"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                nested_rows = value
                break
        if _one(payload) or nested_rows is None:
            rows = [payload]
        else:
            rows = nested_rows
    else:
        rows = []
    out: list[PublicComment] = []
    seen: set[str] = set()
    _collect(rows, out, seen, limit, 0)
    return out


def _collect(
    rows: list[Any],
    out: list[PublicComment],
    seen: set[str],
    limit: int,
    depth: int,
) -> None:
    """Keep a comment and replies nested under it. An empty list does not hide a later list."""
    if depth > 6 or len(out) >= limit:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _one(row)
        if item:
            key = item.comment_id or f"{item.author_name}|{item.text}"
            if key not in seen:
                seen.add(key)
                out.append(item)
        if len(out) >= limit:
            return
        for name in ("comments", "replies"):
            nested = _connection_rows(row.get(name))
            if nested:
                _collect(nested, out, seen, limit, depth + 1)
            if len(out) >= limit:
                return


def _connection_rows(value: object) -> list[Any]:
    """An empty list does not count. ``{data: [...]}`` is the list inside."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            return data
        if nested_text(value.get("text")) or nested_text(value.get("message")) or nested_text(value.get("body")):
            return [value]
    return []


def _one(row: Any) -> PublicComment | None:
    if not isinstance(row, dict):
        return None
    text = None
    for key in ("text", "message", "comment", "body"):
        text = nested_text(row.get(key))
        if text:
            break
    if not text:
        return None
    return PublicComment(
        text=text,
        author_name=first_person_name(row, ("author", "profileName", "userName", "name", "from")),
        comment_id=first_comment_id(row),
        created_time=scalar_text(row.get("date")) or scalar_text(row.get("created_time")),
    )
