from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tiktok_osint.errors import (
    InvalidProfileInput,
    ProfileNotFound,
    ProfileNotPublic,
    ReverseLookupForbidden,
    TransientScrapeError,
    UnsafeUrl,
)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            ProfileNotFound,
            ProfileNotPublic,
            InvalidProfileInput,
            UnsafeUrl,
            ReverseLookupForbidden,
        ),
    ):
        return False
    return isinstance(exc, (TransientScrapeError, TimeoutError, asyncio.TimeoutError, OSError, ConnectionError))


async def call_with_retry(
    operation: Callable[[], Awaitable[object]],
    *,
    attempts: int,
    timeout_sec: float,
    base_delay_sec: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> object:
    if attempts < 1:
        raise ValueError("attempts phải >= 1")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(operation(), timeout=timeout_sec)
        except Exception as exc:
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = base_delay_sec * (2 ** (attempt - 1))
            if delay > 0:
                await sleep(delay)
    assert last is not None
    raise last
