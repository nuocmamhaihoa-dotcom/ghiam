"""Rank 3 — call one managed comments API. Provider is selected by env, not hardcoded secrets."""

from __future__ import annotations

import os
from typing import Any

import httpx

from fb_poller.engines.guard import comment_id, invalid_limit, public_error, redact_text, scalar_text
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 3
NAME = "managed-api"


def _provider() -> str:
    return os.environ.get("MANAGED_PROVIDER", "generic").strip().lower() or "generic"


async def scan(url: str, *, limit: int = 50, timeout: float = 60.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    provider = _provider()
    key = os.environ.get("MANAGED_API_KEY", "").strip()
    endpoint = os.environ.get("MANAGED_COMMENTS_URL", "").strip()
    if provider not in {"generic", "chocodata", "brightdata"}:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="unknown_provider",
            note="Set MANAGED_PROVIDER=generic|chocodata|brightdata and the matching key.",
        )
    if provider == "generic" and not endpoint:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_MANAGED_COMMENTS_URL",
            note="Set MANAGED_PROVIDER=generic|chocodata|brightdata and the matching key.",
        )
    if not key:
        return EngineResult(NAME, RANK, False, error="missing_MANAGED_API_KEY")

    if provider == "chocodata" and not endpoint:
        endpoint = "https://chocodata.com/api/v1/facebook/comments"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if provider == "brightdata":
                resp = await _bright_data(client, url, key, limit)
            else:
                resp = await client.get(
                    endpoint,
                    params={"url": url, "limit": limit, "api_key": key},
                    headers={"Authorization": f"Bearer {key}"},
                )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    if resp.status_code >= 400:
        return EngineResult(NAME, RANK, False, error=redact_text(f"http_{resp.status_code}: {resp.text}", 220))
    try:
        payload = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments = _comments_from_json(payload, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "empty_provider_payload",
        note=f"Provider={provider}. This engine does not share clients with other ranks.",
    )


async def _bright_data(
    client: httpx.AsyncClient,
    url: str,
    key: str,
    limit: int,
) -> httpx.Response:
    dataset = os.environ.get("BRIGHTDATA_DATASET_ID", "").strip()
    if not dataset:
        raise RuntimeError("missing_BRIGHTDATA_DATASET_ID")
    return await client.post(
        f"https://api.brightdata.com/datasets/v3/scrape?dataset_id={dataset}&format=json",
        headers={"Authorization": f"Bearer {key}"},
        json=[{"url": url, "limit": limit}],
    )


def _comments_from_json(payload: Any, limit: int) -> list[PublicComment]:
    rows = _rows(payload)
    out: list[PublicComment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = row.get("text") or row.get("message") or row.get("comment") or row.get("body")
        if isinstance(text, dict):
            text = text.get("text")
        text = scalar_text(text)
        if not text or len(text) > 8000:
            continue
        author = row.get("author") or row.get("author_name") or row.get("name") or row.get("user_name")
        if isinstance(author, dict):
            author = author.get("name")
        out.append(
            PublicComment(
                text=text,
                author_name=scalar_text(author),
                comment_id=comment_id(row.get("id")),
                created_time=scalar_text(row.get("created_time")) or scalar_text(row.get("date")),
            )
        )
        if len(out) >= limit:
            break
    return out


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("comments", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return value
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    if any(key in payload for key in ("text", "message", "comment", "body")):
        return [payload]
    return []
