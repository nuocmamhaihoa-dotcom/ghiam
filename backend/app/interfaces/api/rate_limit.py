"""In-process sliding-window rate limiter (enterprise audit Phase 8/11).

Not a substitute for edge/API-gateway limits, but closes the documented gap
where no application-level rate limiting existed.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client request budget over a fixed window."""

    def __init__(self, app) -> None:  # noqa: ANN001
        super().__init__(app)
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Never throttle health probes.
        if request.url.path in {"/health", "/ready", "/live"}:
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        window = float(settings.rate_limit_window_seconds)
        limit = int(settings.rate_limit_requests)
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "type": "https://aqate.example/errors/rate-limit",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": f"Rate limit exceeded: {limit}/{int(window)}s",
                    "instance": str(request.url.path),
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={"Retry-After": str(int(window))},
            )
        bucket.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(bucket)))
        return response
