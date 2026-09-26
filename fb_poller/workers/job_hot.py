from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

from fb_poller.config import Settings
from fb_poller.storage.db import session_scope
from fb_poller.storage.models import PollRun, Post
from fb_poller.storage.repo import CommentRepo, PollRunRepo, PostRepo, ensure_utc, utcnow
from fb_poller.workers.browser_pool import BrowserPool, WorkerBrowser
from fb_poller.workers.extractor import ExtractResult, NetworkCommentSniffer, extract_comments
from fb_poller.workers.proxy_manager import ProxyManager
from fb_poller.workers.types import PostJob


@dataclass
class JobOutcome:
    post_id: int
    ok: bool
    fetched: int
    inserted_new: int
    latency_ms: int
    error_code: str | None = None
    error_detail: str | None = None
    retries: int = 0


async def _fetch_once(
    *,
    settings: Settings,
    worker: WorkerBrowser,
    url: str,
) -> ExtractResult:
    sniffer = NetworkCommentSniffer(limit=settings.hot_max_comments_page)
    worker.page.on("response", sniffer.on_response)
    try:
        timeout_s = settings.hot_job_timeout_ms / 1000.0

        async def _work() -> ExtractResult:
            resp = await worker.page.goto(url, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                return ExtractResult(
                    ok=False,
                    error_code="nav",
                    error_detail=f"http_{resp.status}",
                    blocked=resp.status in {403, 429},
                )
            # Soft wait for comment widgets / XHR
            try:
                await worker.page.wait_for_timeout(500)
            except Exception:
                pass
            return await extract_comments(
                worker.page,
                max_comments=settings.hot_max_comments_page,
                sniffer=sniffer,
            )

        return await asyncio.wait_for(_work(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ExtractResult(ok=False, error_code="timeout", error_detail="job_timeout")
    except Exception as exc:
        return ExtractResult(ok=False, error_code="nav", error_detail=str(exc)[:500])
    finally:
        try:
            worker.page.remove_listener("response", sniffer.on_response)
        except Exception:
            pass


async def poll_post(
    *,
    settings: Settings,
    pool: BrowserPool,
    worker: WorkerBrowser,
    job: PostJob,
    proxy_manager: ProxyManager,
) -> JobOutcome:
    started = utcnow()
    async with session_scope() as session:
        run = await PollRunRepo(session).start(
            job.id,
            job.tier,
            settings.worker_id,
            worker.proxy.id if worker.proxy else None,
        )
        run_id = run.id

    result = await _fetch_once(settings=settings, worker=worker, url=job.url)
    retries = 0
    # One soft retry for transient nav/timeout (same sticky browser/proxy)
    if (not result.ok) and result.error_code in {"nav", "timeout"} and not result.blocked:
        retries = 1
        await asyncio.sleep(0.4)
        result = await _fetch_once(settings=settings, worker=worker, url=job.url)

    comments = [c.as_dict() for c in result.comments]
    inserted = 0
    async with session_scope() as session:
        post_db = await session.get(Post, job.id)
        if post_db is None:
            return JobOutcome(job.id, False, 0, 0, 0, "invalid", "post_missing", retries)

        try:
            recent = set(json.loads(post_db.recent_ids_json or "[]"))
        except Exception:
            recent = set()

        filtered: list[dict] = []
        for c in comments:
            if c["comment_id"] in recent:
                break
            filtered.append(c)
        if not filtered and comments:
            filtered = [c for c in comments if c["comment_id"] not in recent]

        new_ids: list[str] = []
        if result.ok:
            inserted, new_ids = await CommentRepo(session).insert_new(job.id, filtered)

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
        post_id=job.id,
        ok=result.ok,
        fetched=len(comments),
        inserted_new=inserted if result.ok else 0,
        latency_ms=latency,
        error_code=result.error_code,
        error_detail=result.error_detail,
        retries=retries,
    )
