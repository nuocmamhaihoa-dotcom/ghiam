from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import datetime

import pytest

from tests.conftest import BOTH_BACKENDS, AppHarness


async def test_lease_returns_credentials_ready_for_playwright(harness: AppHarness) -> None:
    proxy_id = await harness.add("http://alice:p%40ss@5.6.7.8:3128|pool=vip")
    await harness.mark_alive()

    response = await harness.lease(pool="vip", job_ref="job-42")
    assert response.status_code == 200, response.text
    body = response.json()
    assert re.fullmatch(r"[0-9a-f]{32}", body["lease_id"])
    assert body["retry_after_sec"] is None
    assert body["message"] is None
    assert body["proxy"] == {
        "id": proxy_id,
        "kind": "static",
        "protocol": "http",
        "host": "5.6.7.8",
        "port": 3128,
        "username": "alice",
        "password": "p@ss",
        "url": "http://alice:p%40ss@5.6.7.8:3128",
        "pool": "vip",
        "exit_ip": "192.0.2.1",
        "country": None,
        "playwright": {"server": "http://5.6.7.8:3128", "username": "alice", "password": "p@ss"},
    }
    proxy = await harness.proxy(proxy_id)
    ttl = datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(proxy["last_used_at"])
    assert ttl.total_seconds() == pytest.approx(600, abs=1)
    assert proxy["active_leases"] == 1
    stats = await harness.stats()
    assert (stats["leased_proxies"], stats["active_leases"]) == (1, 1)
    leased = await harness.proxies(leased=True)
    assert [item["id"] for item in leased] == [proxy_id]


async def test_only_healthy_enabled_idle_proxies_are_leased(harness: AppHarness) -> None:
    await harness.import_text("1.1.1.1:80\n2.2.2.2:80")
    first, second = (item["id"] for item in await harness.proxies())

    nothing = (await harness.lease()).json()
    assert nothing["lease_id"] is None
    assert nothing["message"] == "Chưa có proxy nào đang sống"
    assert nothing["retry_after_sec"] == 10

    await harness.mark_alive([second])
    await harness.admin.post("/api/proxies/bulk", json={"action": "disable", "ids": [second]})
    assert (await harness.lease()).json()["message"] == "Chưa có proxy nào đang sống"

    await harness.mark_alive([first])
    lease = (await harness.lease()).json()
    assert lease["proxy"]["id"] == first


async def test_lease_filters_by_pool_kind_and_exclusions(harness: AppHarness) -> None:
    await harness.import_text("1.1.1.1:80|pool=a\n2.2.2.2:80|pool=b\n3.3.3.3:80|pool=a|type=4g")
    static_a, static_b, rotating_a = (item["id"] for item in await harness.proxies())
    await harness.mark_alive()

    assert (await harness.lease(pool="b")).json()["proxy"]["id"] == static_b
    assert (await harness.lease(kind="rotating")).json()["proxy"]["id"] == rotating_a

    excluded = (await harness.lease(pool="a", exclude_ids=[static_a])).json()
    assert excluded["lease_id"] is None
    assert excluded["message"] == "Tất cả 2 proxy sống trong pool 'a' đang bận, đang đổi IP hoặc đang bị cách ly"

    unknown = (await harness.lease(pool="khong-co")).json()
    assert unknown["message"] == "Chưa có proxy nào đang sống trong pool 'khong-co'"

    assert (await harness.lease(pool="a")).json()["proxy"]["id"] == static_a


async def test_max_concurrency_and_least_recently_used_order(harness: AppHarness) -> None:
    await harness.import_text("1.1.1.1:80|concurrency=2\n2.2.2.2:80|concurrency=2")
    first, second = (item["id"] for item in await harness.proxies())
    await harness.mark_alive()

    leases = [(await harness.lease(worker_id=f"pc-{index}")).json() for index in range(4)]
    assert [lease["proxy"]["id"] for lease in leases] == [first, second, first, second]

    busy = (await harness.lease()).json()
    assert busy["lease_id"] is None
    assert busy["retry_after_sec"] == 10
    assert busy["message"] == "Tất cả 2 proxy sống đang bận, đang đổi IP hoặc đang bị cách ly"

    await harness.release(leases[2]["lease_id"])
    assert (await harness.lease()).json()["proxy"]["id"] == first


async def test_renew_and_release_lifecycle(harness: AppHarness) -> None:
    await harness.add("1.1.1.1:80")
    await harness.mark_alive()
    lease = (await harness.lease(ttl_sec=60)).json()
    lease_id = lease["lease_id"]

    renewed = await harness.agent.post(f"/api/agent/leases/{lease_id}/renew", json={"ttl_sec": 900})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["lease_id"] == lease_id
    extended = datetime.fromisoformat(renewed.json()["expires_at"]) - datetime.fromisoformat(lease["expires_at"])
    assert extended.total_seconds() > 800

    capped = await harness.agent.post(f"/api/agent/leases/{lease_id}/renew", json={"ttl_sec": 86_400})
    capped_ttl = datetime.fromisoformat(capped.json()["expires_at"]) - datetime.fromisoformat(lease["expires_at"])
    assert capped_ttl.total_seconds() < 3600 + 5

    too_short = await harness.agent.post(f"/api/agent/leases/{lease_id}/renew", json={"ttl_sec": 5})
    assert too_short.status_code == 422
    assert too_short.json()["detail"] == "Dữ liệu không hợp lệ: ttl_sec: phải lớn hơn hoặc bằng 30"

    first = await harness.release(lease_id, "ok", detail="Quét xong 120 bình luận")
    assert first.json() == {"released": True, "rotation_scheduled": False, "quarantined_until": None}
    again = await harness.release(lease_id, "failed")
    assert again.json()["released"] is False
    proxy = (await harness.proxies())[0]
    assert (proxy["success_count"], proxy["failure_count"], proxy["active_leases"]) == (1, 0, 0)

    gone = await harness.agent.post(f"/api/agent/leases/{lease_id}/renew", json={})
    assert gone.status_code == 410
    assert gone.json()["detail"] == "Lượt thuê proxy đã kết thúc, hãy thuê proxy mới"

    for path in ("renew", "release"):
        missing = await harness.agent.post(f"/api/agent/leases/khong-ton-tai/{path}", json={"outcome": "ok"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Không tìm thấy lượt thuê proxy"


async def test_repeated_failures_quarantine_the_proxy(harness: AppHarness) -> None:
    await harness.add("1.1.1.1:80")
    await harness.mark_alive()

    responses = []
    for _ in range(3):
        lease = (await harness.lease()).json()
        responses.append((await harness.release(lease["lease_id"], "failed", detail="Timeout")).json())
    assert [response["quarantined_until"] is None for response in responses] == [True, True, False]

    proxy = (await harness.proxies())[0]
    assert (proxy["failure_count"], proxy["consecutive_failures"]) == (3, 3)
    assert proxy["quarantined_until"] is not None
    assert (await harness.stats())["quarantined"] == 1
    assert len(await harness.proxies(quarantined=True)) == 1
    blocked = (await harness.lease()).json()
    assert blocked["message"] == "Tất cả 1 proxy sống đang bận, đang đổi IP hoặc đang bị cách ly"

    reset = await harness.admin.post("/api/proxies/bulk", json={"action": "reset_stats", "filter": {}})
    assert reset.json()["message"] == "Đã đặt lại thống kê của 1 proxy"
    lease = (await harness.lease()).json()
    assert lease["lease_id"] is not None
    await harness.release(lease["lease_id"], "ok")
    proxy = (await harness.proxies())[0]
    assert (proxy["success_count"], proxy["consecutive_failures"], proxy["quarantined_until"]) == (1, 0, None)


async def test_success_resets_the_failure_streak(harness: AppHarness) -> None:
    await harness.add("1.1.1.1:80")
    await harness.mark_alive()
    for outcome in ("failed", "failed", "ok", "failed", "failed"):
        lease = (await harness.lease()).json()
        await harness.release(lease["lease_id"], outcome)
    proxy = (await harness.proxies())[0]
    assert (proxy["consecutive_failures"], proxy["quarantined_until"]) == (2, None)


async def test_session_proxy_gets_a_sticky_session(harness: AppHarness) -> None:
    await harness.add("gate.example.com:7000:user-session-{session}:pw")
    await harness.mark_alive()

    first = (await harness.lease()).json()
    username = first["proxy"]["username"]
    assert re.fullmatch(r"user-session-[0-9a-f]{12}", username)
    assert first["proxy"]["url"] == f"http://{username}:pw@gate.example.com:7000"
    assert first["proxy"]["playwright"] == {
        "server": "http://gate.example.com:7000",
        "username": username,
        "password": "pw",
    }
    await harness.release(first["lease_id"])
    second = (await harness.lease()).json()
    assert second["proxy"]["username"] == username


async def test_agent_input_is_validated(harness: AppHarness) -> None:
    missing = await harness.agent.post("/api/agent/proxies/lease", json={})
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Dữ liệu không hợp lệ: worker_id: bắt buộc phải có"

    await harness.add("1.1.1.1:80")
    await harness.mark_alive()
    lease = (await harness.lease()).json()
    wrong = await harness.release(lease["lease_id"], "khong-ro")
    assert wrong.status_code == 422
    assert wrong.json()["detail"].startswith("Dữ liệu không hợp lệ: outcome:")


@BOTH_BACKENDS
async def test_concurrent_leases_never_exceed_max_concurrency(harness: AppHarness) -> None:
    await harness.import_text("\n".join(f"10.1.0.{index}:8000|concurrency=2" for index in range(1, 6)))
    await harness.mark_alive()

    responses = await asyncio.gather(*(harness.lease(worker_id=f"pc-{index}") for index in range(20)))
    assert all(response.status_code == 200 for response in responses)
    granted = [response.json() for response in responses if response.json()["lease_id"]]
    per_proxy = Counter(lease["proxy"]["id"] for lease in granted)
    assert granted
    assert max(per_proxy.values()) <= 2

    for _ in range(20):
        if not (await harness.lease(worker_id="pc-late")).json()["lease_id"]:
            break
    stats = await harness.stats()
    assert (stats["active_leases"], stats["leased_proxies"]) == (10, 5)
    assert {item["active_leases"] for item in await harness.proxies()} == {2}
