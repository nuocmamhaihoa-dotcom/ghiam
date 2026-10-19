"""Rank 4 — run one Apify actor and read its dataset. Isolated from other engines."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from fb_poller.engines.guard import comment_id, invalid_limit, public_error, redact_text, scalar_text
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
        rows = []
        for key in ("items", "data", "comments", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
    else:
        rows = []
    out: list[PublicComment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("comments")
        if isinstance(nested, list) and nested:
            for child in nested:
                item = _one(child)
                if item:
                    out.append(item)
                if len(out) >= limit:
                    return out
            continue
        item = _one(row)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _one(row: Any) -> PublicComment | None:
    if not isinstance(row, dict):
        return None
    text = scalar_text(row.get("text") or row.get("message") or row.get("comment"))
    if not text:
        return None
    author = row.get("author") or row.get("profileName") or row.get("userName") or row.get("name")
    if isinstance(author, dict):
        author = author.get("name")
    return PublicComment(
        text=text,
        author_name=scalar_text(author),
        comment_id=comment_id(row.get("id")),
        created_time=scalar_text(row.get("date")) or scalar_text(row.get("created_time")),
    )
