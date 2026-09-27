"""Gọi API agent trên VPS (ping, thuê, gia hạn, trả proxy) với VPS giả trả dữ liệu đúng khuôn server thật."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from commentscope_agent import __version__
from commentscope_agent.client import (
    AgentAuthError,
    ControlPlaneClient,
    ControlPlaneError,
    Lease,
    LeaseGoneError,
    NoProxy,
)
from tests.support import FakeControlPlane


def client_for(handler: httpx.MockTransport, *, retries: int = 0) -> ControlPlaneClient:
    return ControlPlaneClient("https://vps.test", "t", transport=handler, retry_delays=(0.0,) * retries)


async def call_ping(client: ControlPlaneClient) -> object:
    return await client.ping()


async def call_lease(client: ControlPlaneClient) -> object:
    return await client.lease(worker_id="pc-1")


async def test_ping_reads_server_info_and_sends_identity_headers() -> None:
    fake = FakeControlPlane(check_urls=["https://ipinfo.io/json", "https://api.ipify.org?format=json"])
    async with fake.client() as client:
        info = await client.ping()
    assert info.agent == "agent-token-1"
    assert info.version == "0.1.0"
    assert info.check_urls == ("https://ipinfo.io/json", "https://api.ipify.org?format=json")
    assert info.server_time.tzinfo is not None
    assert abs((info.server_time - datetime.now(UTC)).total_seconds()) < 5
    headers = fake.requests[0].headers
    assert headers["authorization"] == f"Bearer {fake.token}"
    assert headers["user-agent"] == f"commentscope-agent/{__version__}"


async def test_time_without_timezone_is_read_as_utc() -> None:
    naive = "2026-09-27T08:00:00"
    payload = {"ok": True, "agent": "agent-token-1", "server_time": naive, "version": "0.1.0", "check_urls": []}
    async with client_for(httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        info = await client.ping()
    assert info.server_time == datetime(2026, 9, 27, 8, 0, tzinfo=UTC)


async def test_wrong_token_says_where_to_fix_it() -> None:
    fake = FakeControlPlane()
    async with ControlPlaneClient("https://vps.test", "sai-token", transport=fake.transport()) as client:
        with pytest.raises(AgentAuthError, match="COMMENTSCOPE_AGENT_TOKEN") as caught:
            await client.ping()
    assert caught.value.status == 401
    assert len(fake.requests) == 1


async def test_server_without_agent_tokens_is_an_auth_problem() -> None:
    fake = FakeControlPlane(agent_api_enabled=False)
    async with fake.client() as client:
        with pytest.raises(AgentAuthError, match=r"VPS chưa bật API cho agent: .*AGENT_TOKENS"):
            await client.ping()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, text="<html>Trang chủ</html>"), "không trả dữ liệu của CommentScope"),
        (httpx.Response(404, text="Not Found"), "Không tìm thấy API agent"),
        (httpx.Response(500, text="Internal Server Error"), "VPS trả lỗi HTTP 500 Internal Server Error"),
        (httpx.Response(200, json={"ok": True}), "thiếu hoặc sai trường 'agent'"),
    ],
)
async def test_wrong_server_url_or_unexpected_answer(response: httpx.Response, message: str) -> None:
    async with client_for(httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(ControlPlaneError, match=message):
            await client.ping()


async def test_gateway_errors_are_retried_for_calls_that_are_safe_to_repeat() -> None:
    fake = FakeControlPlane(fail_next=[502, 504])
    async with fake.client(retry_delays=(0, 0)) as client:
        info = await client.ping()
    assert info.agent == "agent-token-1"
    assert len(fake.requests) == 3


async def test_lease_is_not_repeated_when_the_server_may_have_handled_it() -> None:
    fake = FakeControlPlane(fail_next=[504])
    fake.use_proxy("http", 3128)
    async with fake.client(retry_delays=(0, 0)) as client:
        with pytest.raises(ControlPlaneError, match="HTTP 504"):
            await client.lease(worker_id="pc-1")
    assert len(fake.requests) == 1


async def test_lease_is_retried_when_the_request_never_reached_the_app() -> None:
    fake = FakeControlPlane(fail_next=[502])
    fake.use_proxy("http", 3128)
    async with fake.client(retry_delays=(0,)) as client:
        lease = await client.lease(worker_id="pc-1")
    assert isinstance(lease, Lease)
    assert len(fake.leases) == 1


async def test_unreachable_server_is_retried_then_reported() -> None:
    calls: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("Connection refused", request=request)

    message = re.escape("Không kết nối được tới VPS https://vps.test: Connection refused")
    async with client_for(httpx.MockTransport(refuse), retries=2) as client:
        with pytest.raises(ControlPlaneError, match=message):
            await client.ping()
    assert len(calls) == 3


@pytest.mark.parametrize(("call", "attempts"), [(call_ping, 2), (call_lease, 1)], ids=["ping", "lease"])
async def test_read_timeouts_are_retried_only_for_safe_calls(
    call: Callable[[ControlPlaneClient], Awaitable[object]], attempts: int
) -> None:
    calls: list[str] = []

    def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("timed out", request=request)

    async with client_for(httpx.MockTransport(slow), retries=1) as client:
        with pytest.raises(ControlPlaneError, match="không trả lời kịp"):
            await call(client)
    assert len(calls) == attempts


async def test_lease_returns_the_proxy_or_when_to_try_again() -> None:
    fake = FakeControlPlane(unavailable_times=1, retry_after_sec=7)
    fake.use_proxy("socks5", 1080, username="khach", password="bi-mat", kind="rotating", pool="vn-4g")
    async with fake.client() as client:
        waiting = await client.lease(worker_id="pc-1", pool="vn-4g", kind="rotating", ttl_sec=120, job_ref="job-9")
        lease = await client.lease(worker_id="pc-1", exclude_ids=[3, 4])
    assert waiting == NoProxy(retry_after_sec=7, message=fake.unavailable_message)
    assert isinstance(lease, Lease)
    assert lease.lease_id == "lease-2"
    assert lease.expires_at > datetime.now(UTC) + timedelta(seconds=500)
    assert (lease.proxy.protocol, lease.proxy.username, lease.proxy.password) == ("socks5", "khach", "bi-mat")
    assert lease.proxy.label == "#7 socks5://127.0.0.1:1080 (pool vn-4g)"
    assert "bi-mat" not in repr(lease)
    assert fake.leases == [
        {"worker_id": "pc-1", "pool": "vn-4g", "kind": "rotating", "ttl_sec": 120, "job_ref": "job-9"},
        {"worker_id": "pc-1", "exclude_ids": [3, 4]},
    ]


async def test_renew_extends_the_lease() -> None:
    fake = FakeControlPlane()
    async with fake.client() as client:
        expires_at = await client.renew("lease-1", 90)
    assert fake.renewals == [("lease-1", {"ttl_sec": 90})]
    assert timedelta(seconds=80) < expires_at - datetime.now(UTC) <= timedelta(seconds=90)


async def test_finished_lease_is_reported_as_gone() -> None:
    fake = FakeControlPlane(renew_statuses=[410], release_status=404)
    async with fake.client() as client:
        with pytest.raises(LeaseGoneError, match="đã kết thúc"):
            await client.renew("lease-1")
        with pytest.raises(LeaseGoneError, match="Không tìm thấy lượt thuê proxy"):
            await client.release("lease-1", "ok")


async def test_release_sends_the_outcome_and_reads_the_result() -> None:
    until = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    fake = FakeControlPlane(rotation_scheduled=True, quarantined_until=until)
    async with fake.client() as client:
        result = await client.release("lease-1", "blocked", detail="origin.test trả HTTP 403", request_rotation=True)
    assert fake.releases == [
        ("lease-1", {"outcome": "blocked", "detail": "origin.test trả HTTP 403", "request_rotation": True})
    ]
    assert result.released
    assert result.rotation_scheduled
    assert result.quarantined_until == until
