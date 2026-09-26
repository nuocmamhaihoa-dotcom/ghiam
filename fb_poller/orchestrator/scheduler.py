from __future__ import annotations

import asyncio
from collections import deque

from rich.console import Console

from fb_poller.config import Settings
from fb_poller.storage.db import session_scope
from fb_poller.storage.models import Post, Tier
from fb_poller.storage.repo import PostRepo

console = Console()


class LocalJobQueue:
    """In-process priority queue: hot > warm > cold."""

    def __init__(self) -> None:
        self._hot: deque[Post] = deque()
        self._warm: deque[Post] = deque()
        self._cold: deque[Post] = deque()
        self._ids: set[int] = set()
        self._cv = asyncio.Condition()

    def _bucket(self, tier: str) -> deque[Post]:
        if tier == Tier.hot.value:
            return self._hot
        if tier == Tier.warm.value:
            return self._warm
        return self._cold

    async def put(self, post: Post) -> None:
        async with self._cv:
            if post.id in self._ids:
                return
            self._bucket(post.tier).append(post)
            self._ids.add(post.id)
            self._cv.notify()

    async def put_many(self, posts: list[Post]) -> int:
        n = 0
        async with self._cv:
            for post in posts:
                if post.id in self._ids:
                    continue
                self._bucket(post.tier).append(post)
                self._ids.add(post.id)
                n += 1
            if n:
                self._cv.notify_all()
        return n

    async def get(self, prefer_hot: bool = True) -> Post:
        async with self._cv:
            while True:
                post = None
                if prefer_hot and self._hot:
                    post = self._hot.popleft()
                elif self._hot:
                    post = self._hot.popleft()
                elif self._warm:
                    post = self._warm.popleft()
                elif self._cold:
                    post = self._cold.popleft()
                if post:
                    self._ids.discard(post.id)
                    return post
                await self._cv.wait()

    def qsize(self) -> dict[str, int]:
        return {"hot": len(self._hot), "warm": len(self._warm), "cold": len(self._cold)}


async def scheduler_loop(settings: Settings, queue: LocalJobQueue, stop: asyncio.Event) -> None:
    console.log("[scheduler] started")
    while not stop.is_set():
        try:
            async with session_scope() as session:
                repo = PostRepo(session, settings)
                await repo.rebalance_hot()
                # Claim more than workers to keep queue warm
                batch = max(settings.workers * 2, 10)
                posts = await repo.claim_due(batch)
            added = await queue.put_many(posts)
            if added:
                console.log(f"[scheduler] queued={added} sizes={queue.qsize()}")
        except Exception as exc:
            console.log(f"[scheduler] error: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
    console.log("[scheduler] stopped")
