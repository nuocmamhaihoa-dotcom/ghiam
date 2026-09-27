from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from tiktok_osint.config import TikTokSettings
from tiktok_osint.domain.dedupe import dedupe_contacts
from tiktok_osint.domain.extract import extract_contacts
from tiktok_osint.domain.models import ContactCandidate, PublicSnapshot
from tiktok_osint.domain.normalize import parse_profile_input
from tiktok_osint.errors import (
    InvalidProfileInput,
    JobNotFound,
    ProfileNotFound,
    ProfileNotPublic,
    UnsafeUrl,
)
from tiktok_osint.logging_config import log_event
from tiktok_osint.ocr.pipeline import read_avatar_text
from tiktok_osint.scrape.retry import call_with_retry
from tiktok_osint.storage.repo import Repository

logger = logging.getLogger(__name__)


class ProfileScraper(Protocol):
    async def fetch(self, username: str) -> PublicSnapshot: ...


class RedirectResolver(Protocol):
    async def resolve(self, url: str) -> str: ...


class TextFetcher(Protocol):
    async def fetch_text(self, url: str) -> str: ...


class BytesFetcher(Protocol):
    async def fetch_bytes(self, url: str) -> bytes: ...


class TextRecognizer(Protocol):
    def recognize(self, image_bytes: bytes) -> str: ...


async def execute_scan(
    job_id: str,
    *,
    repo: Repository,
    scraper: ProfileScraper,
    resolver: RedirectResolver,
    bio_fetcher: TextFetcher,
    avatar_fetcher: BytesFetcher,
    ocr: TextRecognizer,
    settings: TikTokSettings,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_after_target: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, object]:
    job = repo.get_job(job_id)
    if job is None:
        raise JobNotFound(job_id)
    repo.set_job_status(job_id, "running", error=None)
    repo.reclaim_running(job_id, settings.max_attempts)
    targets = repo.runnable_targets(job_id, settings.max_attempts)
    log_event(logger, logging.INFO, "scan.job.start", job_id=job_id, targets=len(targets))
    if not targets:
        repo.finish_job_if_idle(job_id)
        finished = repo.get_job(job_id)
        assert finished is not None
        return finished

    pauser = sleep
    for target in targets:
        current = repo.get_job(job_id)
        if current and current["status"] == "paused":
            repo.save_checkpoint(job_id, _checkpoint(repo, job_id, target))
            log_event(logger, logging.INFO, "scan.job.paused", job_id=job_id)
            paused = repo.get_job(job_id)
            assert paused is not None
            return paused
        await _process_target(
            job_id,
            target,
            repo=repo,
            scraper=scraper,
            resolver=resolver,
            bio_fetcher=bio_fetcher,
            avatar_fetcher=avatar_fetcher,
            ocr=ocr,
            settings=settings,
        )
        repo.save_checkpoint(job_id, _checkpoint(repo, job_id, target))
        if settings.min_delay_sec > 0 and pauser is not None:
            await pauser(settings.min_delay_sec)
        if on_after_target is not None:
            await on_after_target(str(target["id"]))
        current = repo.get_job(job_id)
        if current and current["status"] == "paused":
            repo.save_checkpoint(job_id, _checkpoint(repo, job_id, target))
            log_event(logger, logging.INFO, "scan.job.paused", job_id=job_id)
            return current

    repo.finish_job_if_idle(job_id)
    finished = repo.get_job(job_id)
    assert finished is not None
    log_event(
        logger,
        logging.INFO,
        "scan.job.finish",
        job_id=job_id,
        status=finished["status"],
    )
    return finished


async def _process_target(
    job_id: str,
    target: dict[str, object],
    *,
    repo: Repository,
    scraper: ProfileScraper,
    resolver: RedirectResolver,
    bio_fetcher: TextFetcher,
    avatar_fetcher: BytesFetcher,
    ocr: TextRecognizer,
    settings: TikTokSettings,
) -> None:
    target_id = str(target["id"])
    previous_attempts = int(target["attempts"])
    remaining = settings.max_attempts - previous_attempts
    if remaining < 1:
        repo.mark_target(target_id, status="failed", attempts=previous_attempts, last_error="Hết số lần thử")
        return
    tries = {"n": previous_attempts}
    username_box = {"value": target.get("username") if isinstance(target.get("username"), str) else None}

    async def _load() -> PublicSnapshot:
        tries["n"] += 1
        repo.mark_target(target_id, status="running", attempts=tries["n"])
        username = username_box["value"]
        profile_url = target.get("profile_url")
        if not username:
            if not isinstance(profile_url, str):
                raise InvalidProfileInput("Thiếu URL hồ sơ")
            resolved = await resolver.resolve(profile_url)
            parsed = parse_profile_input(str(resolved))
            if parsed.reject_reason or not parsed.username or not parsed.profile_url:
                raise InvalidProfileInput(parsed.reject_reason or "Short link không hợp lệ")
            username = parsed.username
            username_box["value"] = username
            repo.mark_target(target_id, status="running", username=username, profile_url=parsed.profile_url)
        fetched = await scraper.fetch(username)
        if not isinstance(fetched, PublicSnapshot):
            raise ProfileNotPublic(username, "Scraper trả về dữ liệu không phải hồ sơ công khai")
        return fetched

    try:
        loaded = await call_with_retry(
            _load,
            attempts=remaining,
            timeout_sec=settings.request_timeout_sec,
            base_delay_sec=settings.retry_base_delay_sec,
        )
        if not isinstance(loaded, PublicSnapshot):
            raise ProfileNotPublic(str(username_box["value"] or ""))
        snapshot = loaded
        contacts = await _collect_contacts(
            snapshot,
            bio_fetcher=bio_fetcher,
            avatar_fetcher=avatar_fetcher,
            ocr=ocr,
        )
        profile_id = repo.upsert_profile(snapshot, job_id)
        repo.replace_contacts(profile_id, contacts)
        repo.mark_target(
            target_id,
            status="done",
            attempts=tries["n"],
            last_error=None,
            username=snapshot.username,
        )
        log_event(
            logger,
            logging.INFO,
            "scan.target.done",
            job_id=job_id,
            username=snapshot.username,
            contacts=len(contacts),
            private_account=snapshot.private_account,
        )
    except (ProfileNotFound, ProfileNotPublic, InvalidProfileInput) as exc:
        repo.mark_target(target_id, status="skipped", attempts=max(tries["n"], 1), last_error=str(exc))
        log_event(logger, logging.INFO, "scan.target.skipped", job_id=job_id, target_id=target_id, error=exc)
    except Exception as exc:
        repo.mark_target(target_id, status="failed", attempts=max(tries["n"], 1), last_error=str(exc))
        log_event(logger, logging.WARNING, "scan.target.failed", job_id=job_id, target_id=target_id, error=exc)


async def _collect_contacts(
    snapshot: PublicSnapshot,
    *,
    bio_fetcher: TextFetcher,
    avatar_fetcher: BytesFetcher,
    ocr: TextRecognizer,
) -> list[ContactCandidate]:
    found: list[ContactCandidate] = []
    found.extend(extract_contacts(snapshot.bio, source="bio"))
    if snapshot.bio_link:
        found.extend(extract_contacts(snapshot.bio_link, source="bio_link"))
        if snapshot.bio_link.startswith(("http://", "https://")):
            try:
                page_text = await bio_fetcher.fetch_text(snapshot.bio_link)
            except (UnsafeUrl, Exception) as exc:
                log_event(logger, logging.INFO, "scan.biolink.skip", url=snapshot.bio_link, error=exc)
            else:
                found.extend(extract_contacts(page_text, source="bio_link"))
    if snapshot.avatar_url:
        text = await read_avatar_text(snapshot.avatar_url, fetcher=avatar_fetcher, engine=ocr)
        if text:
            found.extend(extract_contacts(text, source="ocr_avatar"))
    return dedupe_contacts(found)


def _checkpoint(repo: Repository, job_id: str, target: dict[str, object]) -> dict[str, object]:
    counts = repo.target_counts(job_id)
    counts["last_target_id"] = target.get("id")
    counts["last_username"] = target.get("username")
    return counts

