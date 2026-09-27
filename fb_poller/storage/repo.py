from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fb_poller.config import Settings
from fb_poller.storage.models import Comment, PollRun, Post, PostStatus, Proxy, Tier


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes (SQLite often returns naive) to aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class PostRepo:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def upsert_urls(self, urls: list[str]) -> tuple[int, int]:
        inserted = 0
        skipped = 0
        now = utcnow()
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            existing = await self.session.scalar(select(Post).where(Post.url == url))
            if existing:
                skipped += 1
                continue
            self.session.add(
                Post(
                    url=url,
                    tier=Tier.cold.value,
                    status=PostStatus.active.value,
                    next_poll_at=now,
                    recent_ids_json="[]",
                )
            )
            inserted += 1
        await self.session.flush()
        return inserted, skipped

    async def count_by_tier(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(Post.tier, func.count()).where(Post.status == PostStatus.active.value).group_by(Post.tier)
        )
        return {tier: count for tier, count in rows.all()}

    async def list_due(self, limit: int = 50) -> list[Post]:
        from sqlalchemy import case

        now = utcnow()
        stmt = (
            select(Post)
            .where(
                Post.status == PostStatus.active.value,
                Post.next_poll_at.is_not(None),
                Post.next_poll_at <= now,
            )
            .order_by(
                case(
                    (Post.tier == Tier.hot.value, 0),
                    (Post.tier == Tier.warm.value, 1),
                    else_=2,
                ),
                Post.next_poll_at.asc(),
            )
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def claim_due(self, limit: int) -> list[Post]:
        """Fetch due posts and push next_poll_at forward to avoid double-claim."""
        posts = await self.list_due(limit)
        now = utcnow()
        for post in posts:
            interval = self._interval_for(post)
            post.next_poll_at = now + timedelta(seconds=interval)
        await self.session.flush()
        return posts

    def _interval_for(self, post: Post) -> int:
        if post.tier == Tier.hot.value:
            return self.settings.hot_interval_sec
        if post.tier == Tier.warm.value:
            return self.settings.warm_interval_sec
        return self.settings.cold_interval_sec

    async def rebalance_hot(self) -> dict[str, int]:
        """Ensure exactly hot_size posts in hot tier (prefer boosted / recent activity)."""
        now = utcnow()
        # Demote expired hot boosts if over capacity handled below
        hot_posts = list(
            (
                await self.session.scalars(
                    select(Post)
                    .where(Post.status == PostStatus.active.value, Post.tier == Tier.hot.value)
                    .order_by(Post.last_new_at.desc().nullslast(), Post.id.asc())
                )
            ).all()
        )

        # Keep boosted posts preferred
        boosted = [p for p in hot_posts if p.hot_until and ensure_utc(p.hot_until) and ensure_utc(p.hot_until) > now]
        others = [p for p in hot_posts if p not in boosted]

        keep: list[Post] = []
        keep.extend(boosted[: self.settings.hot_size])
        remaining_slots = self.settings.hot_size - len(keep)
        if remaining_slots > 0:
            keep.extend(others[:remaining_slots])

        keep_ids = {p.id for p in keep}
        demoted = 0
        for p in hot_posts:
            if p.id not in keep_ids:
                p.tier = Tier.warm.value
                demoted += 1

        # Promote from warm/cold if under capacity
        need = self.settings.hot_size - len(keep_ids)
        promoted = 0
        if need > 0:
            candidates = list(
                (
                    await self.session.scalars(
                        select(Post)
                        .where(
                            Post.status == PostStatus.active.value,
                            Post.tier.in_([Tier.warm.value, Tier.cold.value]),
                            Post.guest_visible.is_not(False),
                        )
                        .order_by(Post.last_new_at.desc().nullslast(), Post.id.asc())
                        .limit(need)
                    )
                ).all()
            )
            for p in candidates:
                p.tier = Tier.hot.value
                promoted += 1

        await self.session.flush()
        return {"demoted": demoted, "promoted": promoted, "hot": len(keep_ids) + promoted}

    async def apply_poll_result(
        self,
        post: Post,
        *,
        ok: bool,
        inserted_new: int,
        fetched: int,
        error_code: str | None,
        error_detail: str | None,
        new_ids: list[str],
        newest_time: datetime | None,
        guest_visible: bool | None = None,
    ) -> None:
        now = utcnow()
        if guest_visible is not None:
            post.guest_visible = guest_visible
            if guest_visible is False:
                post.status = PostStatus.guest_hidden.value

        if ok:
            post.fail_streak = 0
            post.last_success_at = now
            post.last_error = None
            recent = _parse_ids(post.recent_ids_json)
            for cid in reversed(new_ids):
                if cid not in recent:
                    recent.insert(0, cid)
            post.recent_ids_json = json.dumps(recent[: self.settings.recent_ids_keep])
            wm = ensure_utc(post.watermark_time)
            if newest_time and (wm is None or newest_time > wm):
                post.watermark_time = newest_time
            if inserted_new > 0:
                post.last_new_at = now
                post.tier = Tier.hot.value
                post.hot_until = now + timedelta(seconds=self.settings.boost_on_new_sec)
        else:
            post.fail_streak += 1
            post.last_error = f"{error_code}: {error_detail}" if error_code else error_detail
            if post.fail_streak >= self.settings.fail_streak_cooldown:
                # cooldown: push next poll further
                post.next_poll_at = now + timedelta(seconds=max(self.settings.cold_interval_sec, 300))

        # Demote hot if boost expired and no longer needed — handled in rebalance
        hot_until = ensure_utc(post.hot_until)
        if hot_until and hot_until <= now and post.tier == Tier.hot.value and inserted_new == 0:
            # leave demotion to rebalance_hot to respect capacity
            pass

        await self.session.flush()

    async def set_tier(self, post_id: int, tier: str) -> None:
        await self.session.execute(update(Post).where(Post.id == post_id).values(tier=tier))

    async def mark_guest_hidden(self, post_id: int) -> None:
        await self.session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(guest_visible=False, status=PostStatus.guest_hidden.value)
        )


class CommentRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_new(self, post_id: int, comments: list[dict[str, Any]]) -> tuple[int, list[str]]:
        inserted = 0
        new_ids: list[str] = []
        for c in comments:
            cid = str(c["comment_id"])
            exists = await self.session.scalar(select(Comment.id).where(Comment.comment_id == cid))
            if exists:
                continue
            self.session.add(
                Comment(
                    comment_id=cid,
                    post_id=post_id,
                    parent_id=c.get("parent_id"),
                    text=c.get("text"),
                    author_name=c.get("author_name"),
                    author_id=c.get("author_id"),
                    created_time=c.get("created_time"),
                    raw_json=json.dumps(c.get("raw") or c, ensure_ascii=False),
                )
            )
            inserted += 1
            new_ids.append(cid)
        await self.session.flush()
        return inserted, new_ids


class PollRunRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def start(self, post_id: int, tier: str, worker_id: str, proxy_id: int | None) -> PollRun:
        run = PollRun(
            post_id=post_id,
            tier=tier,
            started_at=utcnow(),
            worker_id=worker_id,
            proxy_id=proxy_id,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish(
        self,
        run: PollRun,
        *,
        fetched: int,
        inserted_new: int,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        finished = utcnow()
        run.finished_at = finished
        started = ensure_utc(run.started_at) or finished
        run.latency_ms = int((finished - started).total_seconds() * 1000)
        run.fetched = fetched
        run.inserted_new = inserted_new
        run.error_code = error_code
        run.error_detail = error_detail
        await self.session.flush()


class ProxyRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_endpoints(self, endpoints: list[str], proxy_type: str) -> tuple[int, int]:
        inserted = skipped = 0
        for endpoint in endpoints:
            endpoint = endpoint.strip()
            if not endpoint or endpoint.startswith("#"):
                continue
            existing = await self.session.scalar(select(Proxy).where(Proxy.endpoint == endpoint))
            if existing:
                skipped += 1
                continue
            self.session.add(Proxy(endpoint=endpoint, proxy_type=proxy_type))
            inserted += 1
        await self.session.flush()
        return inserted, skipped

    async def assign_static(self, worker_id: str, count: int) -> list[Proxy]:
        # Reuse already assigned
        mine = list(
            (
                await self.session.scalars(
                    select(Proxy).where(
                        Proxy.assigned_worker == worker_id,
                        Proxy.proxy_type == "static",
                        Proxy.health == "ok",
                    )
                )
            ).all()
        )
        if len(mine) >= count:
            return mine[:count]

        need = count - len(mine)
        free = list(
            (
                await self.session.scalars(
                    select(Proxy)
                    .where(
                        Proxy.assigned_worker.is_(None),
                        Proxy.proxy_type == "static",
                        Proxy.health == "ok",
                    )
                    .limit(need)
                )
            ).all()
        )
        for p in free:
            p.assigned_worker = worker_id
        await self.session.flush()
        return mine + free

    async def mark_sick(self, proxy_id: int) -> None:
        proxy = await self.session.get(Proxy, proxy_id)
        if not proxy:
            return
        proxy.health = "sick"
        proxy.last_ban_at = utcnow()
        proxy.assigned_worker = None
        await self.session.flush()
