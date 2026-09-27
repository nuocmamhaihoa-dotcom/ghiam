from __future__ import annotations

import asyncio
import logging

from tiktok_osint.config import TikTokSettings, get_settings
from tiktok_osint.logging_config import log_event, setup_logging
from tiktok_osint.ocr.engine import PaddleOcrEngine
from tiktok_osint.queue.redis_queue import JobQueue, build_queue
from tiktok_osint.scrape.fetchers import HttpxAvatarFetcher, HttpxBioLinkFetcher, PublicRedirectResolver
from tiktok_osint.scrape.playwright_scraper import PlaywrightPublicScraper
from tiktok_osint.scrape.runner import ProfileScraper, execute_scan
from tiktok_osint.storage.db import build_repository
from tiktok_osint.storage.repo import Repository

logger = logging.getLogger(__name__)

RUNNABLE_STATUSES = {"queued", "running", "paused", "draft", "failed", "completed"}


async def run_worker(
    settings: TikTokSettings | None = None,
    *,
    once: bool = False,
    repo: Repository | None = None,
    queue: JobQueue | None = None,
    scraper: ProfileScraper | None = None,
) -> None:
    active = settings or get_settings()
    active.ensure_dirs()
    setup_logging(active.log_level)
    store = repo or build_repository(active.database_url)
    jobs = queue if queue is not None else build_queue(active)
    owns_scraper = scraper is None
    active_scraper = scraper or PlaywrightPublicScraper(active)
    resolver = PublicRedirectResolver(active.request_timeout_sec)
    bio_fetcher = HttpxBioLinkFetcher(active.request_timeout_sec, active.max_bio_link_bytes)
    avatar_fetcher = HttpxAvatarFetcher(active.request_timeout_sec, active.max_avatar_bytes)
    ocr = PaddleOcrEngine()
    log_event(logger, logging.INFO, "worker.start", once=once)
    try:
        while True:
            job_id = _next_job_id(jobs, store)
            if job_id:
                job = store.get_job(job_id)
                if job and str(job["status"]) in RUNNABLE_STATUSES:
                    try:
                        await execute_scan(
                            job_id,
                            repo=store,
                            scraper=active_scraper,
                            resolver=resolver,
                            bio_fetcher=bio_fetcher,
                            avatar_fetcher=avatar_fetcher,
                            ocr=ocr,
                            settings=active,
                        )
                    except Exception as exc:
                        store.set_job_status(job_id, "failed", error=str(exc))
                        log_event(logger, logging.ERROR, "worker.job_failed", job_id=job_id, error=exc)
            if once:
                return
            await asyncio.sleep(0.5)
    finally:
        if owns_scraper and isinstance(active_scraper, PlaywrightPublicScraper):
            await active_scraper.aclose()


def _next_job_id(queue: JobQueue, repo: Repository) -> str | None:
    try:
        job_id = queue.pop(timeout=1)
    except Exception as exc:
        log_event(logger, logging.WARNING, "worker.queue_pop_failed", error=exc)
        job_id = None
    if job_id:
        return job_id
    return repo.claim_next_queued()
