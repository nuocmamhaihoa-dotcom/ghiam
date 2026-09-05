"""Redis client wrapper."""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis

from app.core.config import get_settings


class RedisClient:
    def __init__(self, url: str) -> None:
        self._client = redis.from_url(url, decode_responses=True)

    @property
    def raw(self) -> redis.Redis:
        return self._client

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._client.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache
def get_redis_client() -> RedisClient:
    return RedisClient(get_settings().redis_url)
