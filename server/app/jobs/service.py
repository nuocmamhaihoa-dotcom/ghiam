"""Tạo job, theo dõi bài viết và lưu comment đã đọc."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import unicodedata
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.errors import AppError, NotFoundError, PayloadTooLargeError
from app.jobs.permalinks import PermalinkLine, parse_permalinks
from app.jobs.schemas import (
    CommentIn,
    CommentListOut,
    CommentOut,
    CreateJobIn,
    JobListOut,
    JobOut,
    PermalinkOut,
    PostListOut,
    PostOut,
    PreviewOut,
)
from app.models import AuthorMode, Comment, JobStatus, PostStatus, ScrapeAttempt, ScrapeJob, ScrapePost
from app.timeutil import utcnow

PERMALINK_LIMIT = 20_000
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_OPEN_POSTS = (PostStatus.PENDING, PostStatus.RUNNING)
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def preview_permalinks(text: str) -> PreviewOut:
    lines = _parse(text)
    return PreviewOut(
        total=len(lines),
        new=sum(item.status == "new" for item in lines),
        duplicate=sum(item.status == "duplicate" for item in lines),
        invalid=sum(item.status == "invalid" for item in lines),
        unsupported=sum(item.status == "unsupported" for item in lines),
        lines=[PermalinkOut(**asdict(item)) for item in lines],
    )


async def create_job(session: AsyncSession, data: CreateJobIn) -> JobOut:
    lines = [item for item in _parse(data.text) if item.status == "new" and item.url is not None]
    if not lines:
        raise AppError("Không có permalink Facebook hợp lệ để tạo job")
    now = utcnow()
    name = (data.name or "").strip() or f"Facebook {now.strftime('%Y-%m-%d %H:%M')}"
    job = ScrapeJob(
        name=name[:120],
        status=JobStatus.RUNNING,
        priority=data.priority,
        max_comments=data.max_comments,
        include_replies=data.include_replies,
        max_replies_per_comment=data.max_replies_per_comment,
        sort=data.sort,
        proxy_pool=data.proxy_pool or None,
        proxy_kind=data.proxy_kind,
        max_attempts=data.max_attempts,
        time_budget_sec=data.time_budget_sec,
        max_parallel=data.max_parallel,
        author_mode=data.author_mode,
        created_at=now,
        started_at=now,
    )
    session.add(job)
    await session.flush()
    session.add_all(
        [
            ScrapePost(
                job_id=job.id,
                url=item.url or "",
                platform=item.platform or "facebook",
                status=PostStatus.PENDING,
            )
            for item in lines
        ]
    )
    await session.commit()
    return await get_job(session, job.id)


async def list_jobs(session: AsyncSession, *, limit: int, offset: int) -> JobListOut:
    total = int(await session.scalar(select(func.count(ScrapeJob.id))) or 0)
    jobs = list(await session.scalars(select(ScrapeJob).order_by(ScrapeJob.id.desc()).offset(offset).limit(limit)))
    items = [await _job_out(session, job) for job in jobs]
    return JobListOut(items=items, total=total)


async def get_job(session: AsyncSession, job_id: int) -> JobOut:
    job = await session.get(ScrapeJob, job_id)
    if job is None:
        raise NotFoundError("Không tìm thấy job")
    return await _job_out(session, job)


async def list_posts(session: AsyncSession, job_id: int, *, status: str | None, limit: int, offset: int) -> PostListOut:
    await _require_job(session, job_id)
    conditions = [ScrapePost.job_id == job_id]
    if status:
        conditions.append(ScrapePost.status == status)
    total = int(await session.scalar(select(func.count(ScrapePost.id)).where(*conditions)) or 0)
    posts = list(
        await session.scalars(
            select(ScrapePost).where(*conditions).order_by(ScrapePost.id.asc()).offset(offset).limit(limit)
        )
    )
    return PostListOut(items=[await _post_out(session, post) for post in posts], total=total)


async def pause_job(session: AsyncSession, job_id: int) -> JobOut:
    job = await _require_job(session, job_id)
    if job.status != JobStatus.RUNNING:
        raise AppError("Chỉ tạm dừng được job đang chạy")
    job.status = JobStatus.PAUSED
    await session.commit()
    return await _job_out(session, job)


async def resume_job(session: AsyncSession, job_id: int) -> JobOut:
    job = await _require_job(session, job_id)
    if job.status != JobStatus.PAUSED:
        raise AppError("Chỉ tiếp tục được job đang tạm dừng")
    job.status = JobStatus.RUNNING
    job.finished_at = None
    await session.commit()
    return await _job_out(session, job)


async def cancel_job(session: AsyncSession, job_id: int) -> JobOut:
    job = await _require_job(session, job_id)
    if job.status in {JobStatus.CANCELLED, JobStatus.COMPLETED}:
        raise AppError("Job này đã kết thúc")
    now = utcnow()
    job.status = JobStatus.CANCELLED
    job.finished_at = now
    posts = list(
        await session.scalars(select(ScrapePost).where(ScrapePost.job_id == job_id, ScrapePost.status.in_(_OPEN_POSTS)))
    )
    for post in posts:
        post.status = PostStatus.CANCELLED
        post.finished_at = now
    await session.commit()
    return await _job_out(session, job)


async def retry_failed(session: AsyncSession, job_id: int) -> JobOut:
    job = await _require_job(session, job_id)
    if job.status == JobStatus.CANCELLED:
        raise AppError("Job đã huỷ, không chạy lại")
    posts = list(
        await session.scalars(
            select(ScrapePost).where(ScrapePost.job_id == job_id, ScrapePost.status == PostStatus.FAILED)
        )
    )
    if not posts:
        raise AppError("Không có bài lỗi để chạy lại")
    for post in posts:
        post.status = PostStatus.PENDING
        post.attempts = 0
        post.not_before = None
        post.last_error = None
        post.finished_at = None
        post.complete = False
        post.stop_reason = None
    job.status = JobStatus.RUNNING
    job.finished_at = None
    if job.started_at is None:
        job.started_at = utcnow()
    await session.commit()
    return await _job_out(session, job)


async def list_comments(
    session: AsyncSession,
    job_id: int,
    *,
    post_id: int | None,
    query: str | None,
    min_likes: int | None,
    after_id: int | None,
    limit: int,
) -> CommentListOut:
    await _require_job(session, job_id)
    conditions = [Comment.job_id == job_id]
    if post_id is not None:
        conditions.append(Comment.post_id == post_id)
    if after_id is not None:
        conditions.append(Comment.id > after_id)
    if min_likes is not None:
        conditions.append(Comment.likes >= min_likes)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like = f"%{escaped}%"
        conditions.append(or_(Comment.text.ilike(like, escape="\\"), Comment.author.ilike(like, escape="\\")))
    rows = list(
        await session.execute(
            select(Comment, ScrapePost.url)
            .join(ScrapePost, ScrapePost.id == Comment.post_id)
            .where(*conditions)
            .order_by(Comment.id.asc())
            .limit(limit)
        )
    )
    items = [_comment_out(comment, url) for comment, url in rows]
    next_after = items[-1].id if len(items) == limit else None
    return CommentListOut(items=items, next_after_id=next_after)


async def iter_export(db: Database, job_id: int, *, fmt: str, post_id: int | None) -> AsyncIterator[bytes]:
    async with db.sessionmaker() as session:
        if await session.get(ScrapeJob, job_id) is None:
            raise NotFoundError("Không tìm thấy job")
        conditions = [Comment.job_id == job_id]
        if post_id is not None:
            conditions.append(Comment.post_id == post_id)
        result = await session.stream(
            select(Comment, ScrapePost.url)
            .join(ScrapePost, ScrapePost.id == Comment.post_id)
            .where(*conditions)
            .order_by(Comment.id.asc())
        )
        if fmt == "csv":
            yield "\ufeff".encode()
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "post_url",
                    "id",
                    "parent_id",
                    "author",
                    "author_id",
                    "author_url",
                    "time",
                    "time_raw",
                    "likes",
                    "likes_raw",
                    "reply_count",
                    "text",
                ]
            )
            yield buffer.getvalue().encode()
            buffer.seek(0)
            buffer.truncate(0)
            async for comment, url in result:
                writer.writerow(
                    [
                        url,
                        comment.external_id,
                        comment.parent_external_id or "",
                        _excel(comment.author),
                        comment.author_id or "",
                        comment.author_url or "",
                        "" if comment.time is None else comment.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        _excel(comment.time_raw or ""),
                        "" if comment.likes is None else comment.likes,
                        _excel(comment.likes_raw or ""),
                        "" if comment.reply_count is None else comment.reply_count,
                        _excel(comment.text),
                    ]
                )
                yield buffer.getvalue().encode()
                buffer.seek(0)
                buffer.truncate(0)
            return
        if fmt == "ndjson":
            async for comment, url in result:
                payload = _comment_out(comment, url).model_dump(mode="json")
                yield (json.dumps(payload, ensure_ascii=False) + "\n").encode()
            return
        yield b"["
        first = True
        async for comment, url in result:
            payload = _comment_out(comment, url).model_dump(mode="json")
            prefix = b"" if first else b","
            first = False
            yield prefix + json.dumps(payload, ensure_ascii=False).encode()
        yield b"]"


async def store_comments(
    session: AsyncSession,
    db: Database,
    settings: Settings,
    job: ScrapeJob,
    post: ScrapePost,
    attempt_id: str,
    incoming: Sequence[CommentIn],
) -> int:
    if not incoming:
        return 0
    now = utcnow()
    rows = []
    for item in incoming:
        author, author_id, author_url = _authors(settings, job.author_mode, item)
        rows.append(
            {
                "job_id": job.id,
                "post_id": post.id,
                "platform": post.platform,
                "external_id": item.external_id,
                "parent_external_id": item.parent_external_id,
                "author": author,
                "author_id": author_id,
                "author_url": author_url,
                "text": _clean(item.text),
                "time": item.time,
                "time_raw": item.time_raw,
                "likes": item.likes,
                "likes_raw": item.likes_raw,
                "reply_count": item.reply_count,
                "first_seen_at": now,
                "last_seen_at": now,
                "attempt_id": attempt_id,
            }
        )
    insert = sqlite_insert if db.is_sqlite else pg_insert
    statement = insert(Comment).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["post_id", "external_id"],
        set_={
            "text": statement.excluded.text,
            "likes": statement.excluded.likes,
            "likes_raw": statement.excluded.likes_raw,
            "reply_count": statement.excluded.reply_count,
            "time": func.coalesce(statement.excluded.time, Comment.time),
            "time_raw": func.coalesce(statement.excluded.time_raw, Comment.time_raw),
            "last_seen_at": statement.excluded.last_seen_at,
            "attempt_id": statement.excluded.attempt_id,
        },
    )
    await session.execute(statement)
    count = int(await session.scalar(select(func.count(Comment.id)).where(Comment.post_id == post.id)) or 0)
    post.comments_count = count
    return len(rows)


async def refresh_job(session: AsyncSession, job: ScrapeJob, now: datetime) -> None:
    if job.status != JobStatus.RUNNING:
        return
    open_count = int(
        await session.scalar(
            select(func.count(ScrapePost.id)).where(ScrapePost.job_id == job.id, ScrapePost.status.in_(_OPEN_POSTS))
        )
        or 0
    )
    if open_count == 0:
        job.status = JobStatus.COMPLETED
        job.finished_at = now


async def purge_old_comments(db: Database, retention_days: int, now: datetime) -> int:
    if retention_days <= 0:
        return 0
    from sqlalchemy import delete

    cutoff = now - timedelta(days=retention_days)
    async with db.sessionmaker() as session:
        result = await session.execute(delete(Comment).where(Comment.first_seen_at < cutoff))
        await session.commit()
    return int(cast("CursorResult[Any]", result).rowcount or 0)


def _parse(text: str) -> list[PermalinkLine]:
    try:
        return parse_permalinks(text, limit=PERMALINK_LIMIT)
    except ValueError as exc:
        raise PayloadTooLargeError(str(exc)) from None


async def _require_job(session: AsyncSession, job_id: int) -> ScrapeJob:
    job = await session.get(ScrapeJob, job_id)
    if job is None:
        raise NotFoundError("Không tìm thấy job")
    return job


async def _job_out(session: AsyncSession, job: ScrapeJob) -> JobOut:
    rows = list(
        await session.execute(
            select(ScrapePost.status, func.count(ScrapePost.id), func.coalesce(func.sum(ScrapePost.comments_count), 0))
            .where(ScrapePost.job_id == job.id)
            .group_by(ScrapePost.status)
        )
    )
    counts = {status: int(count) for status, count, _ in rows}
    comments = sum(int(total) for _, _, total in rows)
    return JobOut(
        id=job.id,
        name=job.name,
        status=job.status,
        priority=job.priority,
        max_comments=job.max_comments,
        include_replies=job.include_replies,
        max_replies_per_comment=job.max_replies_per_comment,
        sort=job.sort,
        proxy_pool=job.proxy_pool,
        proxy_kind=job.proxy_kind,
        max_attempts=job.max_attempts,
        time_budget_sec=job.time_budget_sec,
        max_parallel=job.max_parallel,
        author_mode=job.author_mode,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        posts=sum(counts.values()),
        pending=counts.get(PostStatus.PENDING, 0),
        running=counts.get(PostStatus.RUNNING, 0),
        done=counts.get(PostStatus.DONE, 0),
        not_available=counts.get(PostStatus.NOT_AVAILABLE, 0),
        failed=counts.get(PostStatus.FAILED, 0),
        cancelled=counts.get(PostStatus.CANCELLED, 0),
        comments=comments,
    )


async def _post_out(session: AsyncSession, post: ScrapePost) -> PostOut:
    attempt = await session.scalar(
        select(ScrapeAttempt).where(ScrapeAttempt.post_id == post.id).order_by(ScrapeAttempt.started_at.desc()).limit(1)
    )
    return PostOut(
        id=post.id,
        url=post.url,
        platform=post.platform,
        status=post.status,
        attempts=post.attempts,
        not_before=post.not_before,
        last_error=post.last_error,
        comments_count=post.comments_count,
        comments_reported=post.comments_reported,
        complete=post.complete,
        stop_reason=post.stop_reason,
        worker_id=None if attempt is None else attempt.worker_id,
        proxy_id=None if attempt is None else attempt.proxy_id,
        started_at=post.started_at,
        finished_at=post.finished_at,
    )


def _comment_out(comment: Comment, url: str) -> CommentOut:
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        post_url=url,
        external_id=comment.external_id,
        parent_external_id=comment.parent_external_id,
        text=comment.text,
        author=comment.author,
        author_id=comment.author_id,
        author_url=comment.author_url,
        time=comment.time,
        time_raw=comment.time_raw,
        likes=comment.likes,
        likes_raw=comment.likes_raw,
        reply_count=comment.reply_count,
        first_seen_at=comment.first_seen_at,
    )


def _authors(settings: Settings, mode: str, item: CommentIn) -> tuple[str, str | None, str | None]:
    if mode == AuthorMode.NAME:
        return _clean(item.author)[:300], None, None
    if mode == AuthorMode.ANON:
        material = item.author_id or item.author
        digest = hmac.new(settings.secret_key.encode(), material.encode(), hashlib.sha256).hexdigest()[:20]
        return "Ẩn danh", digest, None
    return _clean(item.author)[:300], item.author_id, item.author_url


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).translate(_INVISIBLE).split())


def _excel(value: str) -> str:
    return f"'{value}" if value.startswith(_FORMULA) else value
