from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.models import Proxy
from tests.conftest import BOTH_BACKENDS, AppHarness
from tests.netsim import NetSim, free_port

ROTATION_URL = "https://api.proxy4g.vn/change?key=ABCDEFGH12345678&port=30001"


def by_host(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{item['host']}:{item['port']}": item for item in items}


async def test_preview_classifies_every_line(harness: AppHarness) -> None:
    text = "\n".join(
        [
            "# proxy mua ngày 27/9",
            "1.2.3.4:8080:user:pass",
            f"10.0.0.1:30001:u4g:p4g|{ROTATION_URL}",
            "1.2.3.4:8080:user:pass",
            "khong-phai-proxy",
            "https://1.2.3.5:443",
        ]
    )
    response = await harness.admin.post("/api/proxies/import/preview", json={"text": text})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total": 5,
        "new": 3,
        "update": 0,
        "duplicate": 1,
        "invalid": 1,
        "with_warnings": 1,
        "static": 2,
        "rotating": 1,
    }
    lines = {line["line_no"]: line for line in body["lines"]}
    assert lines[2]["display"] == "http://user:•••@1.2.3.4:8080"
    assert lines[3]["kind"] == "rotating"
    assert lines[3]["rotation_mode"] == "url"
    assert lines[3]["rotation_url"] == "https://api.proxy4g.vn/change?key=•••&port=•••"
    assert lines[4]["status"] == "duplicate"
    assert lines[4]["duplicate_of_line"] == 2
    assert lines[5]["status"] == "invalid"
    assert "Thiếu cổng" in lines[5]["error"]
    assert lines[6]["protocol"] == "https"
    assert lines[6]["warnings"]
    assert not body["truncated"]
    assert (await harness.proxies()) == []


async def test_import_then_reimport_skips_or_updates_duplicates(harness: AppHarness) -> None:
    first = await harness.import_text("1.2.3.4:8080:user:pass\n5.6.7.8:3128")
    assert (first["created"], first["updated"], first["skipped"]) == (2, 0, 0)

    again = await harness.import_text("1.2.3.4:8080:user:pass\n5.6.7.8:3128")
    assert (again["created"], again["updated"], again["skipped"]) == (0, 0, 2)

    await harness.mark_alive()
    preview = await harness.admin.post(
        "/api/proxies/import/preview",
        json={"text": "1.2.3.4:8080:user:newpass pool=vip\n5.6.7.8:3128", "on_duplicate": "update"},
    )
    statuses = [line["status"] for line in preview.json()["lines"]]
    assert statuses == ["update", "update"]
    assert all(line["existing_id"] for line in preview.json()["lines"])

    updated = await harness.import_text("1.2.3.4:8080:user:newpass pool=vip\n5.6.7.8:3128", on_duplicate="update")
    assert (updated["created"], updated["updated"], updated["skipped"]) == (0, 2, 0)
    items = by_host(await harness.proxies())
    assert items["1.2.3.4:8080"]["pool"] == "vip"
    assert items["1.2.3.4:8080"]["health"] == "unchecked"
    assert items["5.6.7.8:3128"]["health"] == "alive"


async def test_import_reports_invalid_lines(harness: AppHarness) -> None:
    result = await harness.import_text("1.2.3.4:8080\n1.2.3.4:99999\nsocks4://1.1.1.1:1080")
    assert (result["created"], result["invalid"], result["total"]) == (1, 2, 3)
    assert [error["line_no"] for error in result["errors"]] == [2, 3]
    assert "SOCKS4" in result["errors"][1]["error"]


async def test_secrets_are_encrypted_at_rest_and_masked_in_lists(harness: AppHarness) -> None:
    await harness.import_text(f"10.0.0.1:30001:user:SuperSecret99|{ROTATION_URL}")
    [item] = await harness.proxies()
    assert "password" not in item
    assert item["has_password"] is True
    assert item["display"] == "http://user:•••@10.0.0.1:30001"
    assert item["rotation_url"] == "https://api.proxy4g.vn/change?key=•••&port=•••"
    assert "rotation_url_full" not in item

    detail = await harness.proxy(item["id"])
    assert detail["rotation_url_full"] == ROTATION_URL

    async with harness.container.db.sessionmaker() as session:
        stored = (await session.scalars(select(Proxy))).one()
    assert stored.password_enc is not None
    assert stored.rotation_url_enc is not None
    assert "SuperSecret99" not in stored.password_enc
    assert "ABCDEFGH12345678" not in stored.rotation_url_enc
    assert harness.container.box.decrypt(stored.password_enc) == "SuperSecret99"


@BOTH_BACKENDS
async def test_list_filters_search_pagination_and_stats(harness: AppHarness) -> None:
    await harness.import_text("1.2.3.4:8080\n1.2.3.5:8080:u:p\n9.9.9.9:3128", defaults={"pool": "tinh"})
    await harness.import_text(
        "10.0.0.1:30001:a:b|https://x.vn/change?port=30001\n10.0.0.1:30002:a:b|https://x.vn/change?port=30002",
        defaults={"kind": "rotating", "pool": "viettel"},
    )
    items = await harness.proxies()
    assert len(items) == 5

    assert len(await harness.proxies(kind="rotating")) == 2
    assert len(await harness.proxies(pool="tinh")) == 3
    assert [item["port"] for item in await harness.proxies(q="10.0.0.1:30002")] == [30002]
    assert len(await harness.proxies(q="1.2.3.")) == 2
    assert len(await harness.proxies(q="30001")) == 1

    await harness.mark_alive([items[0]["id"], items[3]["id"]])
    assert {item["id"] for item in await harness.proxies(health="alive")} == {items[0]["id"], items[3]["id"]}

    page = await harness.admin.get("/api/proxies", params={"page": 2, "page_size": 2, "sort": "id"})
    body = page.json()
    assert (body["total"], body["page"], [item["id"] for item in body["items"]]) == (
        5,
        2,
        [items[2]["id"], items[3]["id"]],
    )

    stats = (await harness.admin.get("/api/proxies/stats")).json()
    assert (stats["total"], stats["static"], stats["rotating"], stats["alive"], stats["unchecked"]) == (5, 3, 2, 2, 3)
    assert stats["pools"] == [{"pool": "tinh", "total": 3}, {"pool": "viettel", "total": 2}]


async def test_update_proxy_and_validation_errors(harness: AppHarness) -> None:
    await harness.import_text("1.2.3.4:8080:alice:pw1\n1.2.3.4:8080:bob:pw2\n5.6.7.8:3128")
    alice, bob, open_proxy = await harness.proxies()
    await harness.mark_alive()

    response = await harness.admin.patch(
        f"/api/proxies/{alice['id']}",
        json={"pool": "vip", "note": "  máy chủ HN  ", "max_concurrency": 5, "password": "pw-moi"},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["pool"], body["note"], body["max_concurrency"], body["health"]) == (
        "vip",
        "máy chủ HN",
        5,
        "unchecked",
    )

    rotating = await harness.admin.patch(
        f"/api/proxies/{open_proxy['id']}",
        json={"kind": "rotating", "rotation_url": "https://x.vn/reset?key=1234", "rotation_cooldown_sec": 30},
    )
    assert rotating.json()["rotation_mode"] == "url"
    assert rotating.json()["rotation_url_full"] == "https://x.vn/reset?key=1234"

    invalid_link = await harness.admin.patch(f"/api/proxies/{alice['id']}", json={"rotation_url": "ftp://x.vn/a"})
    assert invalid_link.status_code == 400
    assert invalid_link.json()["detail"] == "Link đổi IP không hợp lệ"

    conflict = await harness.admin.patch(f"/api/proxies/{bob['id']}", json={"username": "alice"})
    assert conflict.status_code == 409
    assert f"#{alice['id']}" in conflict.json()["detail"]

    no_user = await harness.admin.patch(f"/api/proxies/{open_proxy['id']}", json={"password": "x"})
    assert no_user.status_code == 400
    assert no_user.json()["detail"] == "Có mật khẩu nhưng thiếu tên đăng nhập"

    bad_value = await harness.admin.patch(f"/api/proxies/{alice['id']}", json={"max_concurrency": 0})
    assert bad_value.status_code == 422
    assert bad_value.json()["detail"] == "Dữ liệu không hợp lệ: max_concurrency: phải lớn hơn hoặc bằng 1"

    missing = await harness.admin.patch("/api/proxies/999999", json={"pool": "x"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Không tìm thấy proxy #999999"


async def test_bulk_actions_by_ids_and_by_filter(harness: AppHarness) -> None:
    await harness.import_text("1.1.1.1:80\n1.1.1.2:80\n2.2.2.1:80\n2.2.2.2:80")
    ids = [item["id"] for item in await harness.proxies()]

    async def bulk(**payload: Any) -> dict[str, Any]:
        response = await harness.admin.post("/api/proxies/bulk", json=payload)
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    disabled = await bulk(action="disable", ids=ids[:2])
    assert (disabled["matched"], disabled["affected"], disabled["message"]) == (2, 2, "Đã tắt 2 proxy")
    assert len(await harness.proxies(enabled=False)) == 2

    enabled = await bulk(action="enable", filter={"enabled": False})
    assert enabled["affected"] == 2

    moved = await bulk(action="set_pool", filter={"q": "2.2.2."}, pool="nhom-2")
    assert moved["affected"] == 2
    assert {item["host"] for item in await harness.proxies(pool="nhom-2")} == {"2.2.2.1", "2.2.2.2"}

    no_pool = await harness.admin.post("/api/proxies/bulk", json={"action": "set_pool", "ids": ids})
    assert no_pool.status_code == 400
    assert no_pool.json()["detail"] == "Hãy nhập tên pool mới"

    no_target = await harness.admin.post("/api/proxies/bulk", json={"action": "delete"})
    assert no_target.status_code == 400
    assert no_target.json()["detail"] == "Hãy chọn proxy hoặc dùng bộ lọc"

    assert (await bulk(action="reset_stats", ids=ids))["affected"] == 4

    deleted = await bulk(action="delete", ids=[*ids[:3], 999999])
    assert (deleted["matched"], deleted["affected"]) == (3, 3)
    assert [item["id"] for item in await harness.proxies()] == [ids[3]]


async def test_export_round_trip_preserves_every_setting(harness: AppHarness) -> None:
    source = "\n".join(
        [
            "1.2.3.4:8080",
            "http://alice:p%40ss%3Aword@5.6.7.8:3128 pool=vip concurrency=4",
            "socks5://bob:secret@9.9.9.9:1080",
            "10.0.0.1:30001:u:p|https://api.vn/change?key=K1&port=30001 interval=10m cooldown=90s pool=viettel",
            "gate.provider.com:7000:user-session-{session}:pw method=post",
            "[2001:db8::1]:8080:carol:pw",
        ]
    )
    imported = await harness.import_text(source)
    assert imported["created"] == 6

    colon = await harness.admin.get("/api/proxies/export", params={"format": "colon", "with_options": True})
    assert colon.status_code == 200
    assert colon.headers["content-disposition"].startswith("attachment;")
    exported = colon.text.splitlines()
    assert "1.2.3.4:8080|type=static" in exported
    assert "http://alice:p%40ss%3Aword@5.6.7.8:3128|type=static|pool=vip|concurrency=4" in exported
    assert "9.9.9.9:1080:bob:secret|type=static|protocol=socks5" in exported
    assert (
        "10.0.0.1:30001:u:p|https://api.vn/change?key=K1&port=30001|type=4g|pool=viettel|interval=600|cooldown=90"
        in exported
    )
    assert "gate.provider.com:7000:user-session-{session}:pw|type=4g|method=POST" in exported
    assert "[2001:db8::1]:8080:carol:pw|type=static" in exported

    url_export = await harness.admin.get("/api/proxies/export", params={"format": "url"})
    assert "socks5://bob:secret@9.9.9.9:1080" in url_export.text.splitlines()

    delete_all = await harness.admin.post("/api/proxies/bulk", json={"action": "delete", "filter": {}})
    assert delete_all.json()["affected"] == 6
    reimported = await harness.import_text(colon.text)
    assert (reimported["created"], reimported["invalid"]) == (6, 0)
    again = await harness.admin.get("/api/proxies/export", params={"format": "colon", "with_options": True})
    assert sorted(again.text.splitlines()) == sorted(exported)


@pytest.mark.parametrize("settings_overrides", [{"proxy_import_max_lines": 3}])
async def test_import_rejects_too_many_lines(harness: AppHarness) -> None:
    response = await harness.admin.post(
        "/api/proxies/import", json={"text": "# ghi chú\n1.1.1.1:80\n1.1.1.2:80\n1.1.1.3:80\n1.1.1.4:80"}
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Mỗi lần chỉ nhập tối đa 3 dòng, hãy chia nhỏ danh sách"


async def test_check_reports_alive_dead_and_wrong_credentials(harness: AppHarness, netsim: NetSim) -> None:
    http_proxy = await netsim.add_proxy(username="user", password="p@ss:w0rd")
    socks_proxy = await netsim.add_proxy("socks5", username="sock", password="s3cret")
    open_proxy = await netsim.add_proxy()
    dead_port = free_port()
    await harness.import_text(
        "\n".join(
            [
                f"http://user:p%40ss%3Aw0rd@127.0.0.1:{http_proxy.port}",
                f"socks5://sock:s3cret@127.0.0.1:{socks_proxy.port}",
                f"127.0.0.1:{open_proxy.port}",
                f"127.0.0.1:{dead_port}",
                f"127.0.0.1:{http_proxy.port}:khach:sai-mat-khau",
                f"socks5://nguoila:sai@127.0.0.1:{socks_proxy.port}",
            ]
        )
    )
    items = await harness.proxies()
    results = []
    for item in items:
        response = await harness.admin.post(f"/api/proxies/{item['id']}/check")
        assert response.status_code == 200, response.text
        results.append(response.json())

    for result, sim in zip(results[:3], (http_proxy, socks_proxy, open_proxy), strict=True):
        assert result["ok"] is True, result
        assert (result["exit_ip"], result["country"], result["isp"]) == (sim.ip, "VN", "Viettel Group")
        assert result["proxy"]["health"] == "alive"
        assert result["proxy"]["latency_ms"] is not None
    assert results[3]["ok"] is False
    assert results[3]["error"].startswith("Không kết nối được qua proxy")
    assert results[3]["proxy"]["health"] == "dead"
    assert results[4]["error"] == "Sai tên đăng nhập/mật khẩu proxy (HTTP 407)"
    assert results[5]["ok"] is False
    assert results[5]["proxy"]["last_check_error"].startswith("Proxy từ chối kết nối")


async def test_import_schedules_background_checks(harness: AppHarness, netsim: NetSim) -> None:
    sims = [await netsim.add_proxy(username=f"u{index}", password="pw") for index in range(3)]
    response = await harness.admin.post(
        "/api/proxies/import", json={"text": "\n".join(sim.colon_line for sim in sims), "check_after_import": True}
    )
    assert response.json()["check_scheduled"] == 3
    await harness.runtime.wait_idle()
    items = await harness.proxies()
    assert [item["health"] for item in items] == ["alive"] * 3
    assert [item["exit_ip"] for item in items] == [sim.ip for sim in sims]


async def test_delete_proxy(harness: AppHarness) -> None:
    await harness.import_text("1.2.3.4:8080")
    [item] = await harness.proxies()
    assert (await harness.admin.delete(f"/api/proxies/{item['id']}")).status_code == 204
    missing = await harness.admin.get(f"/api/proxies/{item['id']}")
    assert missing.status_code == 404
    assert (await harness.admin.delete(f"/api/proxies/{item['id']}")).status_code == 404
