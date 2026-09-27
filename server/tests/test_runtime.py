from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.models import Proxy, ProxyLease, RotationState
from app.timeutil import utcnow
from tests.conftest import AppHarness
from tests.netsim import HOST, NetSim, free_port, session_ip


async def test_url_rotation_changes_ip_and_respects_cooldown(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u4g", password="p4g")
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}")
    detail = await harness.proxy(proxy_id)
    assert (detail["kind"], detail["rotation_mode"]) == ("rotating", "url")
    await harness.check(proxy_id)
    old_ip = sim.ip

    first = await harness.rotate(proxy_id)
    assert first["status"] == "rotated", first
    assert first["message"] == f"Đã đổi IP: {old_ip} → 203.0.113.1 · Nhà cung cấp: Đổi IP thành công"
    proxy = first["proxy"]
    assert proxy["exit_ip"] == sim.ip == "203.0.113.1"
    assert proxy["rotation_count"] == 1
    assert proxy["last_rotation_ok"] is True
    assert proxy["rotation_state"] == "idle"
    assert proxy["health"] == "alive"
    assert 0 < proxy["cooldown_remaining_sec"] <= 60

    again = await harness.rotate(proxy_id)
    assert again["status"] == "skipped"
    assert again["message"].startswith("Vừa đổi IP gần đây, hãy chờ thêm")
    assert sim.rotations == 1

    forced = await harness.rotate(proxy_id, force=True)
    assert forced["status"] == "rotated", forced
    assert forced["proxy"]["exit_ip"] == "203.0.113.2"
    assert forced["proxy"]["rotation_count"] == 2
    assert [(method, path, query["key"]) for method, path, query in netsim.api_calls] == [
        ("GET", "/change", netsim.api_key)
    ] * 2


async def test_rotation_link_can_use_post(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy()
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}|method=POST")
    result = await harness.rotate(proxy_id)
    assert result["status"] == "rotated", result
    assert [call[0] for call in netsim.api_calls] == ["POST"]


async def test_rotation_can_run_in_background(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy()
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}")
    started = await harness.rotate(proxy_id, wait=False)
    assert (started["status"], started["message"]) == ("started", "Đã bắt đầu đổi IP")
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(proxy_id)
    assert proxy["rotation_count"] == 1
    assert proxy["last_rotation_message"] == "IP mới: 203.0.113.1 · Nhà cung cấp: Đổi IP thành công"


async def test_failed_rotations_keep_the_old_ip(harness: AppHarness, netsim: NetSim) -> None:
    cases = {
        "/change?key=SAI-KEY": "Nhà cung cấp báo lỗi: Key không hợp lệ",
        "/broken": "Link đổi IP trả về HTTP 500. Internal Server Error",
        "/sticky": "Đã gọi link đổi IP nhưng IP vẫn là {ip} · Nhà cung cấp: Đã nhận yêu cầu",
    }
    for path, expected in cases.items():
        sim = await netsim.add_proxy(username="u", password="p")
        proxy_id = await harness.add(f"{sim.colon_line}|http://{HOST}:{netsim.api_port}{path}")
        await harness.check(proxy_id)
        result = await harness.rotate(proxy_id)
        assert result["status"] == "failed", result
        assert result["message"] == expected.format(ip=sim.ip)
        proxy = result["proxy"]
        assert proxy["exit_ip"] == sim.ip
        assert proxy["health"] == "alive"
        assert proxy["last_rotation_ok"] is False
        assert proxy["last_rotation_message"] == result["message"]
        assert proxy["rotation_count"] == 0
        assert proxy["rotation_state"] == "idle"


async def test_unreachable_rotation_link_is_reported(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy()
    proxy_id = await harness.add(f"{sim.colon_line}|http://{HOST}:{free_port()}/change?key=abc")
    result = await harness.rotate(proxy_id)
    assert result["status"] == "failed"
    assert result["message"].startswith("Không gọi được link đổi IP:")


async def test_session_rotation_switches_session_and_ip(harness: AppHarness, netsim: NetSim) -> None:
    prefix = "khach-session-"
    sim = await netsim.add_proxy(session_prefix=prefix, password="pw")
    proxy_id = await harness.add(sim.colon_line)
    detail = await harness.proxy(proxy_id)
    assert (detail["kind"], detail["rotation_mode"]) == ("rotating", "session")

    first = await harness.check(proxy_id)
    assert sim.last_username is not None
    first_session = sim.last_username.removeprefix(prefix)
    assert first["exit_ip"] == session_ip(first_session)

    result = await harness.rotate(proxy_id)
    assert result["status"] == "rotated", result
    new_ip = result["proxy"]["exit_ip"]
    assert new_ip != first["exit_ip"]
    assert result["message"] == f"Đã đổi session, IP: {first['exit_ip']} → {new_ip}"

    lease = (await harness.lease()).json()
    username = lease["proxy"]["username"]
    new_session = username.removeprefix(prefix)
    assert new_session != first_session
    assert session_ip(new_session) == new_ip
    assert lease["proxy"]["playwright"]["username"] == username
    assert netsim.api_calls == []


async def test_proxies_without_a_way_to_rotate_are_skipped(harness: AppHarness) -> None:
    static_id = await harness.add("10.0.0.1:8080")
    provider_id = await harness.add("10.0.0.2:8080|type=4g|interval=5m")
    bare_id = await harness.add("10.0.0.3:8080|type=4g")

    static = await harness.rotate(static_id)
    assert (static["status"], static["message"]) == ("skipped", "Proxy tĩnh không đổi IP được")
    provider = await harness.rotate(provider_id)
    assert provider["status"] == "skipped"
    assert provider["message"].startswith("Nhà cung cấp tự đổi IP mỗi 300 giây")
    bare = await harness.rotate(bare_id)
    assert bare["status"] == "skipped"
    assert bare["message"].startswith("Proxy 4G này chưa có link đổi IP hoặc {session}")

    missing = await harness.admin.post("/api/proxies/999999/rotate")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Không tìm thấy proxy #999999"


async def test_rotation_waits_until_the_proxy_is_released(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u", password="p")
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}")
    await harness.check(proxy_id)
    lease = (await harness.lease()).json()
    assert lease["proxy"]["id"] == proxy_id

    pending = await harness.rotate(proxy_id)
    assert pending["status"] == "pending"
    assert pending["message"] == "Proxy đang được 1 máy sử dụng, sẽ đổi IP ngay khi máy trả proxy"
    assert pending["proxy"]["rotation_state"] == "pending"
    assert (await harness.stats())["rotation_pending"] == 1

    busy = (await harness.lease(worker_id="pc-2")).json()
    assert busy["lease_id"] is None
    assert busy["retry_after_sec"] == 10
    assert sim.rotations == 0

    released = await harness.release(lease["lease_id"])
    assert released.json() == {"released": True, "rotation_scheduled": False, "quarantined_until": None}
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(proxy_id)
    assert sim.rotations == 1
    assert (proxy["rotation_state"], proxy["rotation_count"], proxy["exit_ip"]) == ("idle", 1, sim.ip)


async def test_blocked_release_triggers_rotation(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u", password="p")
    rotating_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}|cooldown=0")
    static_id = await harness.add("10.0.0.9:3128")
    await harness.check(rotating_id)
    await harness.mark_alive([static_id])

    lease = (await harness.lease(kind="rotating")).json()
    blocked = (await harness.release(lease["lease_id"], "blocked", detail="Bị checkpoint")).json()
    assert blocked == {"released": True, "rotation_scheduled": True, "quarantined_until": None}
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(rotating_id)
    assert (proxy["rotation_count"], proxy["failure_count"], proxy["exit_ip"]) == (1, 1, sim.ip)
    assert proxy["consecutive_failures"] == 0

    lease = (await harness.lease(kind="rotating")).json()
    requested = (await harness.release(lease["lease_id"], "ok", request_rotation=True)).json()
    assert requested["rotation_scheduled"] is True
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(rotating_id)
    assert (proxy["rotation_count"], proxy["success_count"]) == (2, 1)

    static_lease = (await harness.lease(kind="static")).json()
    static_release = (await harness.release(static_lease["lease_id"], "blocked")).json()
    assert static_release["rotation_scheduled"] is False


async def test_provider_can_hand_out_a_new_endpoint(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u", password="p")
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.api_url('/move', port=sim.port)}")
    await harness.check(proxy_id)

    result = await harness.rotate(proxy_id)
    moved = netsim.proxies[-1]
    assert result["status"] == "rotated", result
    assert result["message"] == (
        f"Đã đổi IP: {sim.ip} → {moved.ip} · Nhà cung cấp cấp endpoint mới {HOST}:{moved.port}"
    )
    assert (result["proxy"]["host"], result["proxy"]["port"]) == (HOST, moved.port)
    assert moved.hits == 1

    lease = (await harness.lease()).json()
    assert lease["proxy"]["port"] == moved.port
    assert lease["proxy"]["password"] == "p"


async def test_scheduler_tick_rotates_on_interval_and_reaps_expired_leases(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u", password="p")
    rotating_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}|interval=10m")
    static_id = await harness.add("10.0.0.5:3128")
    await harness.check(rotating_id)
    await harness.mark_alive([static_id])
    lease = (await harness.lease(kind="static", ttl_sec=60)).json()

    await harness.runtime.tick()
    await harness.runtime.wait_idle()
    assert sim.rotations == 0
    assert (await harness.stats())["active_leases"] == 1

    past = utcnow() - timedelta(minutes=11)
    await harness.execute(update(Proxy).where(Proxy.id == rotating_id).values(created_at=past))
    await harness.execute(update(ProxyLease).where(ProxyLease.id == lease["lease_id"]).values(expires_at=past))
    await harness.runtime.tick()
    await harness.runtime.wait_idle()

    proxy = await harness.proxy(rotating_id)
    assert sim.rotations == 1
    assert (proxy["rotation_count"], proxy["exit_ip"], proxy["rotation_state"]) == (1, sim.ip, "idle")
    assert (await harness.stats())["active_leases"] == 0
    assert (await harness.proxy(static_id))["active_leases"] == 0

    late = (await harness.release(lease["lease_id"], "ok")).json()
    assert late["released"] is True
    assert (await harness.proxy(static_id))["success_count"] == 1
    assert (await harness.release(lease["lease_id"], "ok")).json()["released"] is False


async def test_check_detects_provider_side_ip_changes(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy(username="u", password="p")
    proxy_id = await harness.add(f"{sim.colon_line}|type=4g|interval=10m")
    await harness.check(proxy_id)

    old_ip, sim.ip = sim.ip, netsim.next_ip()
    proxy = (await harness.check(proxy_id))["proxy"]
    assert proxy["rotation_mode"] == "provider"
    assert proxy["rotation_count"] == 1
    assert proxy["last_rotation_message"] == f"Nhà cung cấp đã đổi IP: {old_ip} → {sim.ip}"

    await harness.runtime.tick()
    await harness.runtime.wait_idle()
    assert (await harness.proxy(proxy_id))["rotation_count"] == 1

    old_ip, sim.ip = sim.ip, netsim.next_ip()
    await harness.execute(
        update(Proxy).where(Proxy.id == proxy_id).values(last_checked_at=utcnow() - timedelta(minutes=11))
    )
    await harness.runtime.tick()
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(proxy_id)
    assert (proxy["rotation_count"], proxy["exit_ip"]) == (2, sim.ip)


async def test_restart_recovers_interrupted_work(harness: AppHarness, netsim: NetSim) -> None:
    sim = await netsim.add_proxy()
    proxy_id = await harness.add(f"{sim.colon_line}|{netsim.change_url(sim)}")
    await harness.execute(update(Proxy).values(rotation_state=RotationState.ROTATING, check_in_progress=True))

    await harness.runtime.stop()
    await harness.runtime.start()
    proxy = await harness.proxy(proxy_id)
    assert (proxy["rotation_state"], proxy["check_in_progress"]) == ("pending", False)

    assert await harness.runtime.dispatch_pending_now() == 1
    await harness.runtime.wait_idle()
    proxy = await harness.proxy(proxy_id)
    assert (proxy["rotation_state"], proxy["rotation_count"]) == ("idle", 1)


async def test_bulk_rotate_only_touches_rotatable_proxies(harness: AppHarness, netsim: NetSim) -> None:
    sims = [await netsim.add_proxy(username=f"u{index}", password="p") for index in range(2)]
    lines = [f"{sim.colon_line}|{netsim.change_url(sim)}" for sim in sims]
    await harness.import_text("\n".join([*lines, "10.0.0.1:8080", "10.0.0.2:8080|type=4g|interval=300"]))

    response = await harness.admin.post("/api/proxies/bulk", json={"action": "rotate", "filter": {}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["matched"], body["affected"]) == (4, 2)
    assert body["message"] == "Đã yêu cầu đổi IP 2 proxy 4G; proxy đang được dùng sẽ đổi IP khi được trả"
    await harness.runtime.wait_idle()

    assert [sim.rotations for sim in sims] == [1, 1]
    items = await harness.proxies()
    assert [item["rotation_count"] for item in items] == [1, 1, 0, 0]
    assert {item["rotation_state"] for item in items} == {"idle"}
