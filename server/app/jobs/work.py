"""Hàng đợi việc: máy PC nhận bài, gửi comment, trả kết quả."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.config import Settings
from app.crypto import SecretBox
from app.db import Database
from app.errors import ConflictError, GoneError, NotFoundError
from app.jobs import service
from app.jobs.schemas import ClaimOut, CompleteIn, CompleteOut, ProgressIn, ProgressOut
from app.models import (
    AttemptOutcome,
    JobStatus,
    PostStatus,
    ProxyLease,
    ScrapeAttempt,
    ScrapeJob,
    ScrapePost,
)
from app.proxies import leasing
from app.proxies.schemas import LeaseIn, ReleaseIn
from app.timeutil import utcnow

RETRY_AFTER_SEC = 15
_RETRY_OUTCOMES = {AttemptOutcome.BLOCKED, AttemptOutcome.FAILED, AttemptOutcome.EXPIRED}
_ALLOWED_PROXY = {
    "done": {"ok"},
    "not_available": {"ok"},
    "blocked": {"blocked"},
    "failed": {"failed", "ok"},
    "cancelled": {"cancelled"},
}


async def claim_work(
    db: Database, box: SecretBox, settings: Settings, *, worker_id: str, ttl_sec: int | None
) -> ClaimOut:
    now = utcnow()
    async with leasing.serialized(db), db.sessionmaker() as session:
        running_post = aliased(ScrapePost)
        running = (
            select(func.count(running_post.id))
            .where(running_post.job_id == ScrapeJob.id, running_post.status == PostStatus.RUNNING)
            .correlate(ScrapeJob)
            .scalar_subquery()
        )
        statement = (
            select(ScrapePost)
            .join(ScrapeJob, ScrapePost.job_id == ScrapeJob.id)
            .where(
                ScrapePost.status == PostStatus.PENDING,
                or_(ScrapePost.not_before.is_(None), ScrapePost.not_before <= now),
                ScrapeJob.status == JobStatus.RUNNING,
                running < ScrapeJob.max_parallel,
            )
            .order_by(ScrapeJob.priority.desc(), ScrapeJob.last_claimed_at.asc().nulls_first(), ScrapePost.id.asc())
            .limit(1)
        )
        if not db.is_sqlite:
            statement = statement.with_for_update(skip_locked=True, of=ScrapePost)
        post = await session.scalar(statement)
        if post is None or post.status != PostStatus.PENDING:
            return ClaimOut(attempt_id=None, retry_after_sec=RETRY_AFTER_SEC, message="Không có bài đang chờ")
        job = await session.get(ScrapeJob, post.job_id)
        if job is None or job.status != JobStatus.RUNNING:
            return ClaimOut(attempt_id=None, retry_after_sec=RETRY_AFTER_SEC, message="Không có bài đang chờ")
        reserved = await leasing.reserve_proxy(
            session,
            db,
            box,
            settings,
            LeaseIn(
                worker_id=worker_id,
                pool=job.proxy_pool,
                kind=job.proxy_kind,
                ttl_sec=ttl_sec,
                job_ref=None,
                exclude_ids=_blocked_ids(post)[:200],
            ),
            now,
        )
        if reserved is None:
            await session.rollback()
            return ClaimOut(
                attempt_id=None,
                retry_after_sec=10,
                message="Chưa có proxy rảnh cho job này",
            )
        lease, proxy_out = reserved
        attempt = ScrapeAttempt(
            id=lease.id,
            post_id=post.id,
            worker_id=worker_id,
            proxy_lease_id=lease.id,
            proxy_id=proxy_out.id,
            expires_at=now + timedelta(seconds=settings.attempt_ttl_sec),
            started_at=now,
        )
        # job_ref trỏ về lượt xử lý để tra trên dashboard.
        lease.job_ref = attempt.id
        session.add(attempt)
        post.status = PostStatus.RUNNING
        post.attempts += 1
        post.started_at = now
        post.finished_at = None
        post.last_error = None
        job.last_claimed_at = now
        await session.commit()
    return ClaimOut(
        attempt_id=attempt.id,
        expires_at=attempt.expires_at,
        post_id=post.id,
        url=post.url,
        platform=post.platform,
        max_comments=job.max_comments,
        include_replies=job.include_replies,
        max_replies_per_comment=job.max_replies_per_comment,
        time_budget_sec=job.time_budget_sec,
        author_mode=job.author_mode,
        lease_id=lease.id,
        proxy=proxy_out,
    )


async def report_progress(db: Database, settings: Settings, attempt_id: str, data: ProgressIn) -> ProgressOut:
    now = utcnow()
    async with leasing.serialized(db), db.sessionmaker() as session:
        attempt = await session.get(ScrapeAttempt, attempt_id)
        if attempt is None:
            raise NotFoundError("Không tìm thấy lượt xử lý")
        if attempt.outcome is not None:
            raise GoneError("Lượt xử lý đã kết thúc")
        if data.seq <= attempt.last_seq:
            return ProgressOut(stored=0, duplicate=True, expires_at=attempt.expires_at)
        if data.seq != attempt.last_seq + 1:
            raise ConflictError(f"Thiếu lô số {attempt.last_seq + 1}, hãy gửi lại đúng thứ tự")
        post = await session.get(ScrapePost, attempt.post_id)
        job = None if post is None else await session.get(ScrapeJob, post.job_id)
        if post is None or job is None:
            raise NotFoundError("Không tìm thấy bài viết của lượt xử lý")
        stored = await service.store_comments(session, db, settings, job, post, attempt.id, data.comments)
        attempt.last_seq = data.seq
        attempt.comments_count = post.comments_count
        attempt.expires_at = now + timedelta(seconds=settings.attempt_ttl_sec)
        if attempt.proxy_lease_id:
            lease = await session.get(ProxyLease, attempt.proxy_lease_id)
            if lease is not None and lease.released_at is None:
                ttl = min(settings.proxy_lease_default_ttl_sec, settings.proxy_lease_max_ttl_sec)
                lease.expires_at = now + timedelta(seconds=ttl)
        await session.commit()
        return ProgressOut(stored=stored, duplicate=False, expires_at=attempt.expires_at)


async def complete_attempt(db: Database, settings: Settings, attempt_id: str, data: CompleteIn) -> CompleteOut:
    now = utcnow()
    async with leasing.serialized(db), db.sessionmaker() as session:
        attempt = await session.get(ScrapeAttempt, attempt_id)
        if attempt is None:
            raise NotFoundError("Không tìm thấy lượt xử lý")
        if attempt.outcome is not None and attempt.outcome != AttemptOutcome.EXPIRED:
            return CompleteOut(post_status=PostStatus.RUNNING, released=False)
        post = await session.get(ScrapePost, attempt.post_id)
        job = None if post is None else await session.get(ScrapeJob, post.job_id)
        if post is None or job is None:
            raise NotFoundError("Không tìm thấy bài viết của lượt xử lý")
        proxy_outcome = data.proxy_outcome if data.proxy_outcome in _ALLOWED_PROXY[data.outcome] else "ok"
        if data.outcome == "blocked":
            proxy_outcome = "blocked"
        attempt.outcome = data.outcome
        attempt.detail = data.detail
        attempt.pages = data.pages
        attempt.bytes_transferred = data.bytes_transferred
        attempt.finished_at = now
        attempt.comments_count = post.comments_count
        if data.comments_reported is not None:
            post.comments_reported = data.comments_reported
        post.complete = data.complete
        post.stop_reason = data.stop_reason
        post.finished_at = now
        post.last_error = None if data.outcome in {"done", "not_available"} else data.detail
        if data.outcome == "blocked" and attempt.proxy_id is not None:
            blocked = _blocked_ids(post)
            if attempt.proxy_id not in blocked:
                blocked.append(attempt.proxy_id)
            post.blocked_proxy_ids = json.dumps(blocked[-200:])
        _apply_post_status(post, job, data.outcome, now)
        if job.status == JobStatus.RUNNING:
            await service.refresh_job(session, job, now)
        lease_id = attempt.proxy_lease_id
        await session.commit()
        status = post.status
    released = False
    if lease_id:
        result = await leasing.release_lease(
            db,
            settings,
            lease_id,
            ReleaseIn(outcome=proxy_outcome, detail=data.detail),
        )
        released = result.released
    return CompleteOut(post_status=status, released=released)


async def reap_expired_attempts(db: Database, now: datetime) -> int:
    async with leasing.serialized(db), db.sessionmaker() as session:
        attempts = list(
            await session.scalars(
                select(ScrapeAttempt).where(ScrapeAttempt.outcome.is_(None), ScrapeAttempt.expires_at <= now)
            )
        )
        for attempt in attempts:
            attempt.outcome = AttemptOutcome.EXPIRED
            attempt.finished_at = now
            attempt.detail = "Máy PC không gửi kết quả trước khi hết hạn"
            post = await session.get(ScrapePost, attempt.post_id)
            job = None if post is None else await session.get(ScrapeJob, post.job_id)
            if post is None or job is None or post.status != PostStatus.RUNNING:
                continue
            _apply_post_status(post, job, AttemptOutcome.EXPIRED, now)
            post.last_error = attempt.detail
            if job.status == JobStatus.RUNNING:
                await service.refresh_job(session, job, now)
        await session.commit()
    return len(attempts)


def _apply_post_status(post: ScrapePost, job: ScrapeJob, outcome: str, now: datetime) -> None:
    if post.status == PostStatus.CANCELLED or job.status == JobStatus.CANCELLED:
        post.status = PostStatus.CANCELLED
        return
    if outcome == AttemptOutcome.DONE:
        post.status = PostStatus.DONE
        return
    if outcome == AttemptOutcome.NOT_AVAILABLE:
        post.status = PostStatus.NOT_AVAILABLE
        return
    if outcome == AttemptOutcome.CANCELLED:
        post.status = PostStatus.PENDING
        post.attempts = max(post.attempts - 1, 0)
        post.not_before = None
        post.finished_at = None
        return
    if post.attempts < job.max_attempts:
        post.status = PostStatus.PENDING
        post.not_before = now + _backoff(post.attempts)
        post.finished_at = None
        return
    post.status = PostStatus.FAILED


def _backoff(attempts: int) -> timedelta:
    seconds = min(30 * 2 ** min(max(attempts - 1, 0), 8), 900)
    return timedelta(seconds=seconds)


def _blocked_ids(post: ScrapePost) -> list[int]:
    try:
        data = json.loads(post.blocked_proxy_ids or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [int(item) for item in data if isinstance(item, int)]
