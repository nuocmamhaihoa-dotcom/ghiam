"""Push local comments to the LAN control-plane server."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from fb_poller.config import Settings, get_settings
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.models import Comment, Post


async def collect_comments(settings: Settings, limit: int = 2000) -> list[dict[str, Any]]:
    await init_db(settings)
    out: list[dict[str, Any]] = []
    async with session_scope() as session:
        rows = (
            await session.execute(select(Comment).order_by(Comment.id.desc()).limit(limit))
        ).scalars().all()
        post_ids = {r.post_id for r in rows}
        posts: dict[Any, str] = {}
        if post_ids:
            for p in (
                await session.execute(select(Post).where(Post.id.in_(list(post_ids))))
            ).scalars().all():
                posts[p.id] = p.url
        for r in rows:
            out.append(
                {
                    "comment_id": r.comment_id,
                    "post_id": r.post_id,
                    "post_url": posts.get(r.post_id),
                    "text": r.text,
                    "author_name": r.author_name,
                    "author_id": r.author_id,
                    "created_time": r.created_time.isoformat() if r.created_time else None,
                    "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                }
            )
    return out


async def push_comments(
    *,
    settings: Settings | None = None,
    control_url: str,
    machine_id: str,
    token: str = "",
    limit: int = 2000,
) -> dict[str, Any]:
    settings = settings or get_settings()
    comments = await collect_comments(settings, limit=limit)
    headers = {"X-Machine-Id": machine_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = control_url.rstrip("/") + "/v1/sync/comments"
    # Large batches over LAN — generous timeout, no proxy
    async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
        resp = await client.post(
            url,
            headers=headers,
            json={"machine_id": machine_id, "comments": comments},
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "pushed": len(comments),
        "server": data,
        "at": datetime.now(timezone.utc).isoformat(),
    }
