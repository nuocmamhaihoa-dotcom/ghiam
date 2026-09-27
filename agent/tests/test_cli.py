"""Dòng lệnh agent (ping, check, phần của open không cần Chromium) với VPS giả, proxy giả và trang đích giả."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from commentscope_agent import __version__
from commentscope_agent.cli import execute, execute_async
from tests.support import (
    EXIT_IP,
    PASSWORD,
    USER,
    FakeControlPlane,
    FakeHttpProxy,
    FakeOrigin,
    FakeSocks5Proxy,
    agent_env,
    run_cli,
    unused_port,
)


async def test_ping_shows_that_the_token_works() -> None:
    fake = FakeControlPlane(check_urls=["https://ipinfo.io/json", "https://api.ipify.org?format=json"])
    run = await run_cli(fake, "ping")
    assert run.code == 0
    assert "Kết nối tới https://vps.test thành công" in run.stdout
    assert "token hợp lệ (agent-token-1), máy PC 'pc-test'" in run.stdout
    assert f"Phiên bản server 0.1.0, agent {__version__}" in run.stdout
    assert "Trang kiểm tra IP: https://ipinfo.io/json, https://api.ipify.org?format=json" in run.stdout
    assert run.stderr == ""


async def test_ping_json_keeps_stdout_machine_readable() -> None:
    fake = FakeControlPlane(check_urls=["https://ipinfo.io/json"])
    run = await run_cli(fake, "ping", "--json")
    assert run.code == 0
    result = run.json
    assert (result["command"], result["ok"], result["server_url"]) == ("ping", True, "https://vps.test")
    assert (result["agent"], result["worker_id"], result["server_version"]) == ("agent-token-1", "pc-test", "0.1.0")
    assert result["agent_version"] == __version__
    assert result["check_urls"] == ["https://ipinfo.io/json"]
    assert abs(result["clock_skew_sec"]) < 5
    assert "thành công" in run.stderr


async def test_ping_warns_when_the_pc_clock_is_off() -> None:
    run = await run_cli(FakeControlPlane(clock_skew_sec=600), "ping")
    assert run.code == 0
    assert "Cảnh báo: đồng hồ máy PC lệch 600 giây so với VPS" in run.stderr


async def test_missing_token_is_reported_before_contacting_the_server() -> None:
    fake = FakeControlPlane()
    run = await run_cli(fake, "ping", "--json", env={"COMMENTSCOPE_SERVER_URL": "https://vps.test"})
    assert run.code == 2
    assert {key: run.json[key] for key in ("command", "ok", "exit_code")} == {
        "command": "ping",
        "ok": False,
        "exit_code": 2,
    }
    assert run.json["error"].startswith("Thiếu token agent")
    assert run.stderr.startswith("Lỗi: Thiếu token agent")
    assert fake.requests == []


async def test_wrong_token_is_a_configuration_error() -> None:
    fake = FakeControlPlane()
    run = await run_cli(fake, "ping", env=agent_env(fake, COMMENTSCOPE_AGENT_TOKEN="sai-token"))
    assert run.code == 2
    assert "VPS từ chối token agent" in run.stderr


async def test_wrong_server_address_is_explained() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    transport = httpx.MockTransport(lambda _: httpx.Response(404, text="Not Found"))
    env = agent_env(FakeControlPlane())
    code = await execute_async(["ping"], env=env, transport=transport, stdout=stdout, stderr=stderr)
    assert code == 1
    assert "Không tìm thấy API agent" in stderr.getvalue()


async def test_plain_http_to_a_remote_server_is_warned_about() -> None:
    fake = FakeControlPlane()
    run = await run_cli(fake, "ping", env=agent_env(fake, COMMENTSCOPE_SERVER_URL="http://vps.test"))
    assert run.code == 0
    assert "Cảnh báo: token agent đang được gửi qua http:// không mã hoá tới vps.test" in run.stderr


def test_config_file_in_the_working_directory_is_used() -> None:
    fake = FakeControlPlane()
    config = {"server_url": "https://vps.test", "token": fake.token, "worker_id": "may-van-phong"}
    Path("config.json").write_text(json.dumps(config), encoding="utf-8")
    stdout = io.StringIO()
    code = execute(["ping", "--json"], env={}, transport=fake.transport(), stdout=stdout, stderr=io.StringIO())
    assert code == 0
    assert json.loads(stdout.getvalue())["worker_id"] == "may-van-phong"


async def test_check_leases_a_proxy_reads_its_exit_ip_and_gives_it_back() -> None:
    async with FakeOrigin() as origin, FakeSocks5Proxy(username=USER, password=PASSWORD) as proxy:
        fake = FakeControlPlane(check_urls=[origin.url("/json")])
        fake.use_proxy("socks5", proxy.port, username=USER, password=PASSWORD, kind="rotating", pool="vn-4g")
        options = ["--pool", "vn-4g", "--kind", "rotating", "--ttl", "120", "--job-ref", "job-9"]
        run = await run_cli(fake, "check", "--json", *options)
    assert run.code == 0, run.stderr
    summary = run.json
    assert (summary["command"], summary["ok"], summary["outcome"]) == ("check", True, "ok")
    assert summary["detail"] == "Mở được 1 trang qua proxy"
    assert summary["proxy"] == {
        "id": 7,
        "label": f"#7 socks5://127.0.0.1:{proxy.port} (pool vn-4g)",
        "kind": "rotating",
        "pool": "vn-4g",
        "protocol": "socks5",
    }
    assert (summary["exit_ip"], summary["country"]) == (EXIT_IP, "VN")
    assert [(visit["url"], visit["status"]) for visit in summary["visits"]] == [(origin.url("/json"), 200)]
    assert summary["release"] == {"released": True, "rotation_scheduled": False, "quarantined_until": None}
    assert summary["release_error"] is None
    assert summary["bridge"]["upstream_failures"] == 0
    assert fake.leases == [
        {"worker_id": "pc-test", "pool": "vn-4g", "kind": "rotating", "ttl_sec": 120, "job_ref": "job-9"}
    ]
    assert fake.releases == [
        ("lease-1", {"outcome": "ok", "detail": "Mở được 1 trang qua proxy", "request_rotation": False})
    ]
    assert [request.host for request in proxy.requests] == ["origin.test"]
    assert f"IP ra của proxy: {EXIT_IP} (VN)" in run.stderr
    assert PASSWORD not in run.stdout + run.stderr


async def test_check_url_option_replaces_the_server_list() -> None:
    async with FakeOrigin() as origin, FakeHttpProxy() as proxy:
        fake = FakeControlPlane(check_urls=["https://unknown.test/json"])
        fake.use_proxy("http", proxy.port)
        run = await run_cli(fake, "check", "--json", "--check-url", origin.url("/text-ip"))
    assert run.code == 0, run.stderr
    assert (run.json["exit_ip"], run.json["country"]) == (EXIT_IP, None)
    assert [visit["url"] for visit in run.json["visits"]] == [origin.url("/text-ip")]


async def test_blocked_proxy_can_ask_for_a_new_ip() -> None:
    async with FakeOrigin() as origin, FakeHttpProxy() as proxy:
        fake = FakeControlPlane(check_urls=[origin.url("/status/403")], rotation_scheduled=True)
        fake.use_proxy("http", proxy.port, kind="rotating")
        run = await run_cli(fake, "check", "--rotate")
    assert run.code == 3
    assert fake.last_release == {
        "outcome": "blocked",
        "detail": "origin.test trả HTTP 403, IP của proxy có thể đã bị chặn",
        "request_rotation": True,
    }
    assert f"Đã thuê proxy #7 http://127.0.0.1:{proxy.port} (pool default), agent tự gia hạn tới khi trả" in run.stdout
    assert (
        "Kết quả: trang đích chặn IP của proxy (origin.test trả HTTP 403, IP của proxy có thể đã bị chặn). "
        "Đã trả proxy cho VPS, VPS sẽ đổi IP proxy"
    ) in run.stdout
    assert "Proxy không dùng được: Trang kiểm tra origin.test trả HTTP 403" in run.stderr


async def test_dead_proxy_is_reported_as_failed() -> None:
    until = datetime(2026, 9, 27, 8, 30, tzinfo=UTC)
    port = unused_port()
    fake = FakeControlPlane(check_urls=["http://origin.test/json"], quarantined_until=until)
    fake.use_proxy("http", port)
    run = await run_cli(fake, "check")
    assert run.code == 3
    label = f"#7 http://127.0.0.1:{port} (pool default)"
    assert fake.last_release["outcome"] == "failed"
    assert fake.last_release["detail"].startswith(f"Không kết nối được tới proxy {label}")
    assert f"Kết quả: proxy lỗi (Không kết nối được tới proxy {label}" in run.stdout
    local = until.astimezone().strftime("%H:%M:%S %d/%m/%Y")
    assert run.stdout.rstrip().endswith(f"Đã trả proxy cho VPS, proxy tạm ngưng tới {local}")


async def test_open_does_not_start_chromium_when_the_proxy_is_dead() -> None:
    fake = FakeControlPlane(check_urls=["http://origin.test/json"])
    fake.use_proxy("socks5", unused_port())
    run = await run_cli(fake, "open", "--headless", "--json", "origin.test/page")
    assert run.code == 3
    assert run.json["outcome"] == "failed"
    assert len(run.json["visits"]) == 1
    assert "Đang mở" not in run.stderr


async def test_no_free_proxy_has_its_own_exit_code() -> None:
    fake = FakeControlPlane(check_urls=["http://origin.test/json"])
    run = await run_cli(fake, "check", "--wait", "0", "--json")
    assert run.code == 4
    assert run.json == {
        "command": "check",
        "ok": False,
        "exit_code": 4,
        "error": f"Không thuê được proxy: {fake.unavailable_message}",
    }
    assert fake.releases == []


async def test_check_needs_a_check_page() -> None:
    fake = FakeControlPlane()
    fake.use_proxy("http", 3128)
    run = await run_cli(fake, "check")
    assert run.code == 2
    assert "Chưa có trang kiểm tra IP" in run.stderr
    assert fake.leases == []


async def test_proxy_that_could_not_be_given_back_is_reported() -> None:
    async with FakeOrigin() as origin, FakeHttpProxy() as proxy:
        fake = FakeControlPlane(check_urls=[origin.url("/json")], release_status=500)
        fake.use_proxy("http", proxy.port)
        run = await run_cli(fake, "check", "--json")
    assert run.code == 0, run.stderr
    assert run.json["release"] is None
    assert run.json["release_error"] == "VPS trả lỗi HTTP 500: Lỗi thử; VPS sẽ tự thu hồi proxy khi hết hạn thuê"
    assert "Chưa trả được proxy: VPS trả lỗi HTTP 500" in run.stderr


async def test_ctrl_c_gives_the_proxy_back_as_cancelled() -> None:
    async with FakeOrigin() as origin, FakeHttpProxy() as proxy:
        fake = FakeControlPlane(check_urls=[origin.url("/slow")])
        fake.use_proxy("http", proxy.port)
        stdout, stderr = io.StringIO(), io.StringIO()
        running = execute_async(
            ["check", "--json"], env=agent_env(fake), transport=fake.transport(), stdout=stdout, stderr=stderr
        )
        task = asyncio.create_task(running)
        async with asyncio.timeout(5):
            await origin.requested.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fake.last_release == {"outcome": "cancelled", "detail": "Người dùng dừng agent", "request_rotation": False}
    assert json.loads(stdout.getvalue())["outcome"] == "cancelled"
    assert "Kết quả: huỷ, không tính điểm proxy (Người dùng dừng agent)" in stderr.getvalue()


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        (
            ["open", "ftp://example.com/tep"],
            "Địa chỉ trang phải bắt đầu bằng http:// hoặc https://: ftp://example.com/tep",
        ),
        (["open", "https://"], "Địa chỉ trang phải bắt đầu bằng http:// hoặc https://: https://"),
        (["open", "--headless", "--keep-open", "example.com"], "--keep-open cần cửa sổ Chromium"),
        (["open", "--timeout", "0", "example.com"], "--timeout phải lớn hơn 0"),
        (["check", "--ttl", "5"], "lease_ttl_sec phải từ 30 đến 86400 giây"),
        (["check", "--wait", "-1"], "lease_wait_sec không được âm"),
    ],
)
async def test_bad_options_are_rejected_before_contacting_the_server(argv: list[str], error: str) -> None:
    fake = FakeControlPlane()
    run = await run_cli(fake, *argv)
    assert run.code == 2
    assert error in run.stderr
    assert fake.requests == []


def test_execute_runs_its_own_event_loop() -> None:
    fake = FakeControlPlane()
    stdout, stderr = io.StringIO(), io.StringIO()
    code = execute(["ping", "--json"], env=agent_env(fake), transport=fake.transport(), stdout=stdout, stderr=stderr)
    assert code == 0
    assert json.loads(stdout.getvalue())["ok"] is True


def test_version_is_printed_without_contacting_the_server(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        execute(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"commentscope-agent {__version__}"
