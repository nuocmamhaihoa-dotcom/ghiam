"""Health and readiness endpoints."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.infrastructure.redis.client import RedisClient, get_redis_client
from app.infrastructure.s3.client import S3Client, get_s3_client
from app.interfaces.api.schemas import HealthResponse, ReadyCheck, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        time=datetime.now(UTC),
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[RedisClient, Depends(lambda: get_redis_client())],
    s3: Annotated[S3Client, Depends(get_s3_client)],
) -> ReadyResponse:
    checks: dict[str, ReadyCheck] = {}

    t0 = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = ReadyCheck(
            ok=True, latency_ms=round((time.perf_counter() - t0) * 1000, 2)
        )
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = ReadyCheck(ok=False, detail=str(exc))

    t0 = time.perf_counter()
    try:
        ok = await redis.ping()
        checks["redis"] = ReadyCheck(
            ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000, 2)
        )
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = ReadyCheck(ok=False, detail=str(exc))

    t0 = time.perf_counter()
    try:
        ok = s3.head_ok()
        checks["s3"] = ReadyCheck(
            ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000, 2)
        )
    except Exception as exc:  # noqa: BLE001
        checks["s3"] = ReadyCheck(ok=False, detail=str(exc))

    all_ok = all(c.ok for c in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if all_ok else "not_ready", checks=checks)
