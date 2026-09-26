from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from fb_poller.config import Settings
from fb_poller.storage.db import session_scope
from fb_poller.storage.models import PollRun, Post
from fb_poller.storage.repo import CommentRepo, PollRunRepo, PostRepo, ensure_utc, utcnow
from fb_poller.workers.browser_pool import BrowserPool, WorkerBrowser
from fb_poller.workers.extractor import ExtractResult, NetworkCommentSniffer, extract_comments
from fb_poller.workers.proxy_manager import ProxyManager


@dataclass
class JobOutcome:
    post_id: int
    ok: bool
    fetched: int
    inserted_new: int
    latency_ms: int
    error_code: str | None = None
    error_detail: str | None = None


async def poll_post(
    *,
    settings: Settings,
    pool: BrowserPool,
    worker: WorkerBrowser,
    post: Post,
    proxy_manager: ProxyManager,
) -> JobOutcome:
    started = utcnow()
    async with session_scope() as session:
        run = await PollRunRepo(session).start(
            post.id,
            post.tier,
            settings.worker_id,
            worker.proxy.id if worker.proxy else None,
        )
        run_id = run.id

    sniffer = NetworkCommentSniffer(limit=settings.hot_max_comments_page)
    worker.page.on("response", sniffer.on_response)

    result = ExtractResult(ok=False, error_code="unknown", error_detail="not_started")
    try:
        timeout_s = settings.hot_job_timeout_ms / 1000.0

        async def _work() -> ExtractResult:
            resp = await worker.page.goto(post.url, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                return ExtractResult(
                    ok=False,
                    error_code="nav",
                    error_detail=f"http_{resp.status}",
                    blocked=resp.status in {403, 429},
                )
            return await extract_comments(
                worker.page,
                max_comments=settings.hot_max_comments_page,
                sniffer=sniffer,
            )

        result = await asyncio.wait_for(_work(), timeout=timeout_s)
    except asyncio.TimeoutError:
        result = ExtractResult(ok=False, error_code="timeout", error_detail="job_timeout")
    except Exception as exc:
        result = ExtractResult(ok=False, error_code="nav", error_detail=str(exc)[:500])
    finally:
        try:
            worker.page.remove_listener("response", sniffer.on_response)
        except Exception:
            pass

    # Watermark filter: keep only potentially new (all first page; DB dedupes)
    comments = [c.as_dict() for c in result.comments]
    # Prefer stopping early against known recent ids
    async with session_scope() as session:
        post_db = await session.get(Post, post.id)
        if post_db is None:
            return JobOutcome(post.id, False, 0, 0, 0, "invalid", "post_missing")

        recent = set()
        try:
            import json

            recent = set(json.loads(post_db.recent_ids_json or "[]"))
        except Exception:
            recent = set()

        filtered = []
        for c in comments:
            if c["comment_id"] in recent:
                # reached known territory — stop further when ordered newest
                break
            filtered.append(c)
        # If sort failed, still insert unknowns via full list dedupe
        if not filtered and comments:
            filtered = [c for c in comments if c["comment_id"] not in recent]

        inserted = 0
        new_ids: list[str] = []
        if result.ok:
            inserted, new_ids = await CommentRepo(session).insert_new(post.id, filtered)

        newest_time: datetime | None = None
        for c in filtered:
            ct = c.get("created_time")
            if isinstance(ct, datetime):
                if newest_time is None or ct > newest_time:
                    newest_time = ct

        await PostRepo(session, settings).apply_poll_result(
            post_db,
            ok=result.ok,
            inserted_new=inserted,
            fetched=len(comments),
            error_code=result.error_code,
            error_detail=result.error_detail,
            new_ids=new_ids,
            newest_time=newest_time,
            guest_visible=result.guest_visible,
        )

        run = await session.get(PollRun, run_id)
        if run:
            await PollRunRepo(session).finish(
                run,
                fetched=len(comments),
                inserted_new=inserted,
                error_code=None if result.ok else result.error_code,
                error_detail=None if result.ok else result.error_detail,
            )

    if result.blocked and worker.proxy:
        await proxy_manager.mark_sick(worker.proxy.id)

    await pool.maybe_recycle(worker)
    latency = int((utcnow() - ensure_utc(started)).total_seconds() * 1000)
    return JobOutcome(
        post_id=post.id,
        ok=result.ok,
        fetched=len(comments),
        inserted_new=inserted if result.ok else 0,
        latency_ms=latency,
        error_code=result.error_code,
        error_detail=result.error_detail,
    )
