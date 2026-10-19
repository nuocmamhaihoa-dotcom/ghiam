"""Rank 6 — official Graph API for public Page posts (Page Public Content Access)."""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from fb_poller.engines.types import EngineResult, PublicComment

RANK = 6
NAME = "graph-ppca"


def graph_version() -> str:
    return os.environ.get("GRAPH_VERSION", "v21.0").strip() or "v21.0"


def access_token() -> str:
    return os.environ.get("GRAPH_ACCESS_TOKEN", "").strip()


async def scan(url: str, *, limit: int = 50, timeout: float = 30.0, **_: object) -> EngineResult:
    token = access_token()
    if not token:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_GRAPH_ACCESS_TOKEN",
            note="Needs a Page or system-user token with Page Public Content Access.",
        )
    version = graph_version()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            object_id = await _resolve_id(client, version, token, url)
            if not object_id:
                return EngineResult(NAME, RANK, False, error="unresolved_post_id")
            resp = await client.get(
                f"https://graph.facebook.com/{version}/{object_id}/comments",
                params={
                    "fields": "message,from,created_time",
                    "limit": str(min(limit, 100)),
                    "access_token": token,
                },
            )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=f"{type(exc).__name__}: {exc}")

    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        message = err.get("message") if isinstance(err, dict) else resp.text[:200]
        return EngineResult(NAME, RANK, False, error=str(message)[:300])

    comments: list[PublicComment] = []
    for row in data.get("data") or []:
        if not isinstance(row, dict):
            continue
        text = row.get("message")
        if not isinstance(text, str) or not text.strip():
            continue
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        comments.append(
            PublicComment(
                text=text.strip(),
                author_name=str(author.get("name")) if author.get("name") else None,
                comment_id=str(row["id"]) if row.get("id") else None,
                created_time=str(row.get("created_time") or "") or None,
            )
        )
        if len(comments) >= limit:
            break
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "empty_comment_edge",
        note="PPCA may omit commenter ids unless the token can moderate the Page.",
    )


async def _resolve_id(client: httpx.AsyncClient, version: str, token: str, url: str) -> str | None:
    raw = url.strip()
    if raw.isdigit() or ("_" in raw and raw.replace("_", "").isdigit()):
        return raw
    resp = await client.get(
        f"https://graph.facebook.com/{version}/",
        params={"id": raw, "access_token": token},
    )
    if resp.status_code >= 400:
        return None
    body = resp.json()
    og = body.get("og_object") if isinstance(body, dict) else None
    if isinstance(og, dict) and og.get("id"):
        return str(og["id"])
    if isinstance(body, dict) and body.get("id") and "error" not in body:
        return str(body["id"])
    return quote(raw, safe="")
