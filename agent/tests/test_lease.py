"""Thuê proxy, tự gia hạn và luôn trả lại kèm kết quả, với VPS giả và đồng hồ giả (không phải chờ thật)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from commentscope_agent import lease as lease_module
from commentscope_agent.client import ControlPlaneClient, NoProxy
from commentscope_agent.lease import LeaseSession, NoProxyAvailableError
from tests.support import FakeControlPlane


class FakeTime:
    """Đồng hồ giả cho LeaseSession: sleep() ghi lại thời gian chờ rồi cho thời gian trôi ngay.

    `gated=True`: sleep() chỉ xong khi test gọi wake(), để test quyết định lúc nào lượt gia hạn tiếp theo chạy.
    """

    def __init__(self, *, gated: bool = False) -> None:
        self.now = 1_000.0
        self.delays: list[float] = []
        self._gated = gated
        self._slept = asyncio.Event()
        self._wakeups = asyncio.Semaphore(0)

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self._slept.set()
        if self._gated:
            await self._wakeups.acquire()
        else:
            await asyncio.sleep(0)
        self.now += delay

    def wake(self) -> None:
        self._wakeups.release()

    async def until_sleeps(self, count: int) -> None:
        async with asyncio.timeout(5):
            while len(self.delays) < count:
                self._slept.clear()
                await self._slept.wait()


def session_for(client: ControlPlaneClient, time: FakeTime, **options: Any) -> LeaseSession:
    return LeaseSession(client, worker_id="pc-1", sleep=time.sleep, clock=time.clock, **options)


def available(**options: Any) -> FakeControlPlane:
    fake = FakeControlPlane(**options)
    fake.use_proxy("http", 3128)
    return fake


async def lease_and_give_back(session: LeaseSession) -> None:
    async with session:
        pass


async def test_waits_for_a_free_proxy_then_gives_it_back_with_the_result() -> None:
    fake = FakeControlPlane(unavailable_times=2, retry_after_sec=5)
    fake.use_proxy("http", 3128, kind="rotating", pool="vn-4g")
    time = FakeTime()
    waits: list[tuple[str, float]] = []

    def on_wait(no_proxy: NoProxy, delay: float) -> None:
        waits.append((no_proxy.message, delay))

    async with fake.client() as client:
        session = session_for(
            client, time, pool="vn-4g", kind="rotating", ttl_sec=300, job_ref="job-1", on_wait=on_wait
        )
        async with session as lease:
            session.finish("ok", "Mở được 1 trang qua proxy")
    assert lease.lease_id == "lease-3"
    assert waits == [(fake.unavailable_message, 5.0)] * 2
    assert time.delays == [5.0, 5.0]
    assert (
        fake.leases
        == [{"worker_id": "pc-1", "pool": "vn-4g", "kind": "rotating", "ttl_sec": 300, "job_ref": "job-1"}] * 3
    )
    assert fake.releases == [
        ("lease-3", {"outcome": "ok", "detail": "Mở được 1 trang qua proxy", "request_rotation": False})
    ]
    assert session.release_result is not None
    assert session.release_result.released
    assert session.release_error is None


async def test_gives_up_when_no_proxy_frees_up_in_time() -> None:
    fake = FakeControlPlane(retry_after_sec=5)
    time = FakeTime()
    async with fake.client() as client:
        with pytest.raises(NoProxyAvailableError, match=fake.unavailable_message):
            await lease_and_give_back(session_for(client, time, wait_sec=12))
    assert time.delays == [5.0, 5.0, 2.0]
    assert len(fake.leases) == 4
    assert fake.releases == []


async def test_zero_wait_fails_at_once() -> None:
    fake = FakeControlPlane()
    time = FakeTime()
    async with fake.client() as client:
        with pytest.raises(NoProxyAvailableError):
            await lease_and_give_back(session_for(client, time, wait_sec=0))
    assert time.delays == []
    assert len(fake.leases) == 1


@pytest.mark.parametrize(("retry_after_sec", "delay"), [(0, 1.0), (100, 30.0)])
async def test_retry_delay_from_the_server_is_kept_within_bounds(retry_after_sec: int, delay: float) -> None:
    fake = available(unavailable_times=1, retry_after_sec=retry_after_sec)
    time = FakeTime()
    async with fake.client() as client:
        await lease_and_give_back(session_for(client, time))
    assert time.delays == [delay]


async def test_lease_is_renewed_a_third_of_the_way_through() -> None:
    fake = available()
    time = FakeTime(gated=True)
    async with fake.client() as client:
        session = session_for(client, time, ttl_sec=90)
        async with session as lease:
            await time.until_sleeps(1)
            time.wake()
            await time.until_sleeps(2)
            session.finish("ok")
    assert time.delays == pytest.approx([30, 30], abs=1)
    assert fake.renewals == [("lease-1", {"ttl_sec": 90})]
    assert session.expires_at is not None
    assert session.expires_at >= lease.expires_at
    assert fake.last_release["outcome"] == "ok"


@pytest.mark.parametrize(("clock_skew_sec", "first_delay"), [(3600, 200.0), (-3600, 5.0)])
async def test_renewal_does_not_trust_a_skewed_server_clock(clock_skew_sec: float, first_delay: float) -> None:
    fake = available(clock_skew_sec=clock_skew_sec)
    time = FakeTime(gated=True)
    async with fake.client() as client, session_for(client, time, ttl_sec=600):
        await time.until_sleeps(1)
    assert time.delays == [pytest.approx(first_delay, abs=1)]


async def test_failed_renewal_is_retried_sooner() -> None:
    fake = available(renew_statuses=[500])
    time = FakeTime(gated=True)
    async with fake.client() as client:
        session = session_for(client, time, ttl_sec=600)
        async with session:
            await time.until_sleeps(1)
            time.wake()
            await time.until_sleeps(2)
            time.wake()
            await time.until_sleeps(3)
    assert time.delays == pytest.approx([200, 15, 200], abs=1)
    assert len(fake.renewals) == 2
    assert not session.lost.is_set()


@pytest.mark.parametrize(
    ("status", "reason"),
    [(410, "Lượt thuê proxy đã kết thúc, hãy thuê proxy mới"), (401, "VPS từ chối token agent")],
)
async def test_lease_ended_on_the_server_stops_renewing_and_is_given_back_as_cancelled(
    status: int, reason: str
) -> None:
    fake = available(renew_statuses=[status])
    time = FakeTime(gated=True)
    async with fake.client() as client:
        session = session_for(client, time)
        async with session:
            await time.until_sleeps(1)
            time.wake()
            async with asyncio.timeout(5):
                await session.lost.wait()
    assert session.lost_reason is not None
    assert session.lost_reason.startswith(reason)
    assert len(time.delays) == 1
    assert fake.last_release["outcome"] == "cancelled"
    assert fake.last_release["detail"] == f"Lượt thuê đã kết thúc trên VPS: {session.lost_reason}"


async def test_crash_on_the_pc_gives_the_proxy_back_as_cancelled() -> None:
    fake = available()
    async with fake.client() as client:
        session = session_for(client, FakeTime(gated=True))

        async def crash() -> None:
            async with session:
                raise OSError("hỏng ổ đĩa")

        with pytest.raises(OSError, match="hỏng ổ đĩa"):
            await crash()
    assert fake.last_release == {
        "outcome": "cancelled",
        "detail": "Agent gặp lỗi trên máy PC: hỏng ổ đĩa",
        "request_rotation": False,
    }


async def test_stopping_the_agent_gives_the_proxy_back_as_cancelled() -> None:
    fake = available()
    entered = asyncio.Event()
    async with fake.client() as client:
        session = session_for(client, FakeTime(gated=True))

        async def work() -> None:
            async with session:
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(work())
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fake.last_release == {"outcome": "cancelled", "detail": "Người dùng dừng agent", "request_rotation": False}


async def test_leaving_without_a_result_is_not_counted_against_the_proxy() -> None:
    fake = available()
    async with fake.client() as client:
        await lease_and_give_back(session_for(client, FakeTime(gated=True)))
    assert fake.last_release == {
        "outcome": "cancelled",
        "detail": "Agent kết thúc mà không báo kết quả",
        "request_rotation": False,
    }


async def test_long_details_are_cut_to_the_server_limit() -> None:
    fake = available()
    async with fake.client() as client:
        session = session_for(client, FakeTime(gated=True))
        async with session:
            session.finish("blocked", "x" * 600, request_rotation=True)
    assert fake.last_release == {"outcome": "blocked", "detail": "x" * 500, "request_rotation": True}


async def test_release_does_not_hang_when_the_server_stops_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lease_module, "RELEASE_TIMEOUT_SEC", 0.05)
    fake = available(release_delay=30)
    async with fake.client() as client:
        session = session_for(client, FakeTime(gated=True))
        async with asyncio.timeout(5), session:
            session.finish("ok")
    assert session.release_result is None
    assert session.release_error == "VPS không trả lời sau 0.05 giây, VPS sẽ tự thu hồi proxy khi hết hạn thuê"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (410, "VPS không còn lượt thuê này (Lượt thuê proxy đã kết thúc, hãy thuê proxy mới)"),
        (500, "VPS trả lỗi HTTP 500: Lỗi thử; VPS sẽ tự thu hồi proxy khi hết hạn thuê"),
    ],
)
async def test_failed_release_is_explained(status: int, error: str) -> None:
    fake = available(release_status=status)
    async with fake.client() as client:
        session = session_for(client, FakeTime(gated=True))
        async with session:
            session.finish("failed", "Proxy lỗi")
    assert session.release_result is None
    assert session.release_error == error
