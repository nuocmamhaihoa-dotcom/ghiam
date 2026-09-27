from __future__ import annotations

from collections import deque
from typing import Protocol

from tiktok_osint.config import TikTokSettings


class JobQueue(Protocol):
    def push(self, job_id: str) -> None: ...

    def pop(self, timeout: float = 1) -> str | None: ...


class MemoryJobQueue:
    def __init__(self) -> None:
        self._items: deque[str] = deque()

    def push(self, job_id: str) -> None:
        self._items.append(job_id)

    def pop(self, timeout: float = 1) -> str | None:
        del timeout
        if not self._items:
            return None
        return self._items.popleft()


class RedisJobQueue:
    def __init__(self, url: str, key: str = "tiktok_osint:jobs") -> None:
        import redis

        self._redis = redis.Redis.from_url(url)
        self.key = key

    def push(self, job_id: str) -> None:
        self._redis.lpush(self.key, job_id)

    def pop(self, timeout: float = 1) -> str | None:
        item = self._redis.brpop(self.key, timeout=max(int(timeout), 1))
        if not item:
            return None
        _key, value = item
        if isinstance(value, bytes):
            return value.decode()
        return str(value)


def build_queue(settings: TikTokSettings) -> JobQueue:
    if settings.redis_url:
        return RedisJobQueue(settings.redis_url)
    return MemoryJobQueue()
