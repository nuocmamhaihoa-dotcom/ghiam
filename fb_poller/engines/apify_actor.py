"""Rank 4 — run one Apify actor and read its dataset. Isolated from other engines."""

from __future__ import annotations

import os
from typing import Any

import httpx

from fb_poller.engines.guard import invalid_limit, public_error
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 4
NAME = "apify"


def _token() -> str:
    return os.environ.get("APIFY_TOKEN", "").strip()


def _actor() -> str:
    raw = os.environ.get("APIFY_ACTOR_ID", "apify~facebook-comments-scraper").strip()
    return raw.replace("/", "~")


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
        return EngineResult(NAME, RANK, False, error=f"http_{resp.status_code}: {resp.text[:180]}")
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
        if isinstance(nested, list):
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
    text = row.get("text") or row.get("message") or row.get("comment")
    if not isinstance(text, str) or not text.strip():
        return None
    author = row.get("author") or row.get("profileName") or row.get("userName") or row.get("name")
    if isinstance(author, dict):
        author = author.get("name")
    return PublicComment(
        text=text.strip(),
        author_name=str(author) if author else None,
        comment_id=str(row["id"]) if row.get("id") else None,
        created_time=str(row.get("date") or row.get("created_time") or "") or None,
    )
