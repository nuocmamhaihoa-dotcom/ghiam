from __future__ import annotations

import asyncio
from collections import deque

from rich.console import Console

from fb_poller.config import Settings
from fb_poller.storage.db import session_scope
from fb_poller.storage.models import Tier
from fb_poller.storage.repo import PostRepo
from fb_poller.workers.types import PostJob

console = Console()


class LocalJobQueue:
    """In-process priority queue: hot > warm > cold."""

    def __init__(self) -> None:
        self._hot: deque[PostJob] = deque()
        self._warm: deque[PostJob] = deque()
        self._cold: deque[PostJob] = deque()
        self._ids: set[int] = set()
        self._cv = asyncio.Condition()

    def _bucket(self, tier: str) -> deque[PostJob]:
        if tier == Tier.hot.value:
            return self._hot
        if tier == Tier.warm.value:
            return self._warm
        return self._cold

    async def put(self, job: PostJob) -> None:
        async with self._cv:
            if job.id in self._ids:
                return
            self._bucket(job.tier).append(job)
            self._ids.add(job.id)
            self._cv.notify()

    async def put_many(self, jobs: list[PostJob]) -> int:
        n = 0
        async with self._cv:
            for job in jobs:
                if job.id in self._ids:
                    continue
                self._bucket(job.tier).append(job)
                self._ids.add(job.id)
                n += 1
            if n:
                self._cv.notify_all()
        return n

    async def get(self) -> PostJob:
        async with self._cv:
            while True:
                job = None
                if self._hot:
                    job = self._hot.popleft()
                elif self._warm:
                    job = self._warm.popleft()
                elif self._cold:
                    job = self._cold.popleft()
                if job:
                    self._ids.discard(job.id)
                    return job
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
                batch = max(settings.workers * 2, 10)
                posts = await repo.claim_due(batch)
                jobs = [PostJob(id=p.id, url=p.url, tier=p.tier) for p in posts]
            added = await queue.put_many(jobs)
            if added:
                console.log(f"[scheduler] queued={added} sizes={queue.qsize()}")
        except Exception as exc:
            console.log(f"[scheduler] error: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
    console.log("[scheduler] stopped")
