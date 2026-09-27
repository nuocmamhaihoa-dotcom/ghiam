"""Thuê một proxy từ VPS, tự gia hạn trong lúc dùng và luôn trả lại kèm kết quả."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import TracebackType

from commentscope_agent.client import (
    AgentAuthError,
    ControlPlaneClient,
    ControlPlaneError,
    Lease,
    LeaseGoneError,
    NoProxy,
    Outcome,
    ReleaseResult,
)

MIN_RETRY_SEC = 1.0
MAX_RETRY_SEC = 30.0
MIN_RENEW_SEC = 5.0
RENEW_RETRY_SEC = 15.0
RELEASE_TIMEOUT_SEC = 20.0
DETAIL_LIMIT = 500

WaitCallback = Callable[[NoProxy, float], None]


class NoProxyAvailableError(Exception):
    """Đã chờ hết thời gian mà VPS vẫn chưa có proxy rảnh."""


class LeaseSession:
    """Dùng với `async with`: thuê proxy khi vào, trả proxy kèm kết quả khi ra, kể cả khi lỗi hoặc Ctrl+C.

    Chưa gọi `finish()` thì proxy được trả với kết quả "cancelled" để VPS không chấm điểm proxy
    vì lỗi phía máy PC.
    """

    def __init__(
        self,
        client: ControlPlaneClient,
        *,
        worker_id: str,
        pool: str | None = None,
        kind: str | None = None,
        ttl_sec: int = 600,
        wait_sec: float = 120.0,
        job_ref: str | None = None,
        on_wait: WaitCallback | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._worker_id = worker_id
        self._pool = pool
        self._kind = kind
        self._ttl_sec = ttl_sec
        self._wait_sec = wait_sec
        self._job_ref = job_ref
        self._on_wait = on_wait
        self._sleep = sleep
        self._clock = clock
        self._renew_task: asyncio.Task[None] | None = None
        self.lease: Lease | None = None
        self.expires_at: datetime | None = None
        self.lost = asyncio.Event()
        self.lost_reason: str | None = None
        self.outcome: Outcome | None = None
        self.detail: str | None = None
        self.request_rotation = False
        self.release_result: ReleaseResult | None = None
        self.release_error: str | None = None

    @property
    def acquired(self) -> bool:
        return self.lease is not None

    def finish(self, outcome: Outcome, detail: str | None = None, *, request_rotation: bool = False) -> None:
        self.outcome = outcome
        self.detail = detail[:DETAIL_LIMIT] if detail else None
        self.request_rotation = request_rotation

    async def __aenter__(self) -> Lease:
        lease = await self._acquire()
        self.lease = lease
        self.expires_at = lease.expires_at
        self._renew_task = asyncio.create_task(self._keep_alive(lease))
        return lease

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._renew_task is not None:
            self._renew_task.cancel()
            await asyncio.gather(self._renew_task, return_exceptions=True)
        if self.lease is None:
            return
        if self.outcome is None:
            self.finish("cancelled", _unfinished_detail(exc_type, exc, self.lost_reason))
        await self._release(self.lease, self.outcome or "cancelled")

    async def _acquire(self) -> Lease:
        deadline = self._clock() + self._wait_sec
        while True:
            result = await self._client.lease(
                worker_id=self._worker_id,
                pool=self._pool,
                kind=self._kind,
                ttl_sec=self._ttl_sec,
                job_ref=self._job_ref,
            )
            if isinstance(result, Lease):
                return result
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise NoProxyAvailableError(result.message)
            delay = min(max(float(result.retry_after_sec), MIN_RETRY_SEC), MAX_RETRY_SEC, remaining)
            if self._on_wait is not None:
                self._on_wait(result, delay)
            await self._sleep(delay)

    async def _keep_alive(self, lease: Lease) -> None:
        delay = self._renew_delay(lease.expires_at)
        while True:
            await self._sleep(delay)
            try:
                self.expires_at = await self._client.renew(lease.lease_id, self._ttl_sec)
            except (LeaseGoneError, AgentAuthError) as exc:
                self.lost_reason = str(exc)
                self.lost.set()
                return
            except ControlPlaneError:
                delay = min(RENEW_RETRY_SEC, self._renew_delay(self.expires_at or lease.expires_at))
                continue
            delay = self._renew_delay(self.expires_at)

    def _renew_delay(self, expires_at: datetime) -> float:
        # Đồng hồ máy PC có thể lệch với VPS nên không tin hẳn vào expires_at, luôn gia hạn trước 1/3 TTL.
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        return max(MIN_RENEW_SEC, min(float(self._ttl_sec), remaining) / 3)

    async def _release(self, lease: Lease, outcome: Outcome) -> None:
        later = "VPS sẽ tự thu hồi proxy khi hết hạn thuê"
        try:
            async with asyncio.timeout(RELEASE_TIMEOUT_SEC):
                self.release_result = await self._client.release(
                    lease.lease_id, outcome, detail=self.detail, request_rotation=self.request_rotation
                )
        except LeaseGoneError as exc:
            self.release_error = f"VPS không còn lượt thuê này ({exc})"
        except TimeoutError:
            self.release_error = f"VPS không trả lời sau {RELEASE_TIMEOUT_SEC:g} giây, {later}"
        except ControlPlaneError as exc:
            self.release_error = f"{exc}; {later}"


def _unfinished_detail(exc_type: type[BaseException] | None, exc: BaseException | None, lost_reason: str | None) -> str:
    if lost_reason:
        return f"Lượt thuê đã kết thúc trên VPS: {lost_reason}"
    if exc_type is None:
        return "Agent kết thúc mà không báo kết quả"
    if issubclass(exc_type, asyncio.CancelledError | KeyboardInterrupt):
        return "Người dùng dừng agent"
    return f"Agent gặp lỗi trên máy PC: {exc or exc_type.__name__}"[:DETAIL_LIMIT]
