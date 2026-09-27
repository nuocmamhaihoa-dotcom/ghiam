"""Dòng lệnh agent CommentScope.

    commentscope-agent ping                   kiểm tra kết nối và token tới VPS
    commentscope-agent check                  thuê một proxy, kiểm tra IP ra qua proxy rồi trả lại
    commentscope-agent open URL [URL ...]     thuê một proxy và mở Chromium qua proxy đó

Mã thoát: 0 thành công, 1 lỗi kết nối VPS, 2 sai cấu hình hoặc token, 3 proxy không dùng được,
4 không có proxy rảnh, 130 người dùng dừng (Ctrl+C).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

import httpx

from commentscope_agent import __version__
from commentscope_agent.bridge import ProxyBridge, Upstream
from commentscope_agent.browser import BrowserUnavailableError, ProxiedBrowser
from commentscope_agent.client import AgentAuthError, ControlPlaneClient, ControlPlaneError, NoProxy
from commentscope_agent.config import KINDS, AgentConfig, ConfigError, insecure_transport_warning, load_config
from commentscope_agent.lease import LeaseSession, NoProxyAvailableError
from commentscope_agent.probe import NO_CHECK_URLS, ExitIp, ProbeError, probe_exit_ip
from commentscope_agent.verdict import PageVisit, judge

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_PROXY_FAILED = 3
EXIT_NO_PROXY = 4
EXIT_INTERRUPTED = 130
CLOCK_SKEW_WARN_SEC = 120
OUTCOME_LABELS = {
    "ok": "proxy dùng tốt",
    "blocked": "trang đích chặn IP của proxy",
    "failed": "proxy lỗi",
    "cancelled": "huỷ, không tính điểm proxy",
}


class Console:
    """Thông báo cho người dùng; ở chế độ --json chỉ kết quả JSON được in ra stdout."""

    def __init__(self, stdout: TextIO, stderr: TextIO, *, json_mode: bool, command: str) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._json_mode = json_mode
        self._command = command

    def info(self, message: str) -> None:
        print(message, file=self._stderr if self._json_mode else self._stdout, flush=True)

    def warn(self, message: str) -> None:
        print(message, file=self._stderr, flush=True)

    def result(self, data: Mapping[str, Any]) -> None:
        if self._json_mode:
            payload = {"command": self._command, **data}
            print(json.dumps(payload, ensure_ascii=False, default=str), file=self._stdout, flush=True)

    def fail(self, code: int, message: str) -> int:
        print(f"Lỗi: {message}", file=self._stderr, flush=True)
        self.result({"ok": False, "exit_code": code, "error": message})
        return code


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="file cấu hình JSON (mặc định: config.json ở thư mục hiện tại)")
    common.add_argument("--server", help="địa chỉ VPS, ví dụ https://scope.example.com")
    common.add_argument("--worker-id", help="tên máy PC hiện trên dashboard (mặc định: tên máy)")
    common.add_argument("--json", action="store_true", help="in kết quả dạng JSON ra stdout, thông báo ra stderr")

    leasing = argparse.ArgumentParser(add_help=False)
    leasing.add_argument("--pool", help="chỉ thuê proxy trong pool này")
    leasing.add_argument("--kind", choices=KINDS, help="static: proxy tĩnh, rotating: proxy 4G xoay")
    leasing.add_argument("--wait", type=float, metavar="GIÂY", help="chờ tối đa khi chưa có proxy rảnh (mặc định 120)")
    leasing.add_argument(
        "--ttl", type=int, metavar="GIÂY", help="thời hạn mỗi lần thuê, agent tự gia hạn (mặc định 600)"
    )
    leasing.add_argument("--check-url", help="trang kiểm tra IP (mặc định dùng PROXY_CHECK_URLS của VPS)")
    leasing.add_argument("--job-ref", help="mã việc ghi kèm lượt thuê để tra cứu trên VPS")
    leasing.add_argument("--rotate", action="store_true", help="xin VPS đổi IP proxy 4G ngay sau khi trả")

    parser = argparse.ArgumentParser(
        prog="commentscope-agent",
        description="Agent CommentScope trên máy PC: thuê proxy từ VPS, kiểm tra và mở Chromium qua proxy.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="LỆNH")
    commands.add_parser("ping", parents=[common], help="kiểm tra kết nối và token tới VPS")
    commands.add_parser("check", parents=[common, leasing], help="thuê một proxy, kiểm tra IP ra rồi trả lại")
    browse = commands.add_parser("open", parents=[common, leasing], help="thuê một proxy và mở Chromium qua proxy đó")
    browse.add_argument("urls", nargs="+", metavar="URL", help="các trang cần mở")
    browse.add_argument("--headless", action="store_true", help="chạy Chromium không hiện cửa sổ")
    browse.add_argument("--keep-open", action="store_true", help="giữ Chromium mở tới khi bạn đóng cửa sổ")
    browse.add_argument("--screenshot-dir", type=Path, help="lưu ảnh chụp từng trang vào thư mục này")
    browse.add_argument("--timeout", type=float, default=45.0, metavar="GIÂY", help="thời gian chờ mỗi trang")
    browse.add_argument("--skip-check", action="store_true", help="không kiểm tra IP ra trước khi mở Chromium")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_output()
    return execute(sys.argv[1:] if argv is None else argv)


def execute(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(list(argv))
    try:
        return asyncio.run(_run(args, env=env, transport=transport, stdout=stdout, stderr=stderr))
    except KeyboardInterrupt:
        print("Đã dừng agent.", file=stderr or sys.stderr, flush=True)
        return EXIT_INTERRUPTED


async def execute_async(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(list(argv))
    return await _run(args, env=env, transport=transport, stdout=stdout, stderr=stderr)


async def _run(
    args: argparse.Namespace,
    *,
    env: Mapping[str, str] | None,
    transport: httpx.AsyncBaseTransport | None,
    stdout: TextIO | None,
    stderr: TextIO | None,
) -> int:
    console = Console(stdout or sys.stdout, stderr or sys.stderr, json_mode=args.json, command=args.command)
    try:
        config = load_config(args.config, os.environ if env is None else env, _overrides(args))
        urls = _open_urls(args) if args.command == "open" else []
    except ConfigError as exc:
        return console.fail(EXIT_CONFIG, str(exc))
    if warning := insecure_transport_warning(config):
        console.warn(warning)
    async with ControlPlaneClient(
        config.server_url, config.token, timeout=config.request_timeout_sec, transport=transport
    ) as client:
        try:
            if args.command == "ping":
                return await _ping(client, config, console)
            return await _use_proxy(args, urls, client, config, console)
        except AgentAuthError as exc:
            return console.fail(EXIT_CONFIG, str(exc))
        except NoProxyAvailableError as exc:
            return console.fail(EXIT_NO_PROXY, f"Không thuê được proxy: {exc}")
        except BrowserUnavailableError as exc:
            return console.fail(EXIT_ERROR, str(exc))
        except ControlPlaneError as exc:
            return console.fail(EXIT_ERROR, str(exc))


async def _ping(client: ControlPlaneClient, config: AgentConfig, console: Console) -> int:
    started = time.perf_counter()
    info = await client.ping()
    latency_ms = int((time.perf_counter() - started) * 1000)
    skew_sec = (info.server_time - datetime.now(UTC)).total_seconds()
    console.info(
        f"Kết nối tới {config.server_url} thành công sau {latency_ms} ms: token hợp lệ ({info.agent}), "
        f"máy PC '{config.worker_id}'"
    )
    console.info(f"Phiên bản server {info.version}, agent {__version__}")
    console.info("Trang kiểm tra IP: " + (", ".join(info.check_urls) or "chưa cấu hình PROXY_CHECK_URLS trên VPS"))
    if abs(skew_sec) >= CLOCK_SKEW_WARN_SEC:
        console.warn(f"Cảnh báo: đồng hồ máy PC lệch {abs(skew_sec):.0f} giây so với VPS, nên bật đồng bộ giờ tự động")
    console.result(
        {
            "ok": True,
            "server_url": config.server_url,
            "agent": info.agent,
            "worker_id": config.worker_id,
            "server_version": info.version,
            "agent_version": __version__,
            "latency_ms": latency_ms,
            "clock_skew_sec": round(skew_sec, 1),
            "check_urls": list(info.check_urls),
        }
    )
    return EXIT_OK


async def _use_proxy(
    args: argparse.Namespace, urls: list[str], client: ControlPlaneClient, config: AgentConfig, console: Console
) -> int:
    info = await client.ping()
    check_urls = [config.check_url] if config.check_url else list(info.check_urls)
    if args.command == "check" and not check_urls:
        return console.fail(EXIT_CONFIG, NO_CHECK_URLS)
    probe_first = bool(check_urls) and not (args.command == "open" and args.skip_check)

    def waiting(no_proxy: NoProxy, delay: float) -> None:
        console.info(f"{no_proxy.message}. Thử lại sau {delay:g} giây...")

    session = LeaseSession(
        client,
        worker_id=config.worker_id,
        pool=config.pool,
        kind=config.kind,
        ttl_sec=config.lease_ttl_sec,
        wait_sec=config.lease_wait_sec,
        job_ref=args.job_ref,
        on_wait=waiting,
    )
    visits: list[PageVisit] = []
    exit_ip: ExitIp | None = None
    bridge: ProxyBridge | None = None
    try:
        async with session as lease:
            proxy = lease.proxy
            console.info(f"Đã thuê proxy {proxy.label}, agent tự gia hạn tới khi trả")
            upstream = Upstream(
                protocol=proxy.protocol,
                host=proxy.host,
                port=proxy.port,
                username=proxy.username,
                password=proxy.password,
                label=proxy.label,
            )
            async with ProxyBridge(upstream, connect_timeout=config.request_timeout_sec) as bridge:
                active = bridge

                def settle() -> None:
                    outcome, detail = judge(
                        visits,
                        proxy_failures=active.stats.upstream_failures,
                        last_proxy_error=active.stats.last_proxy_error,
                    )
                    session.finish(outcome, detail, request_rotation=args.rotate)

                if probe_first:
                    visit, exit_ip = await _probe(bridge, check_urls, config, console)
                    visits.append(visit)
                if args.command == "open" and not any(visit.broken for visit in visits):
                    await _browse(args, urls, bridge, session, console, visits, settle)
                settle()
    finally:
        if session.acquired:
            _report_release(session, console)
            console.result(_summary(session, exit_ip, visits, bridge))
    return EXIT_OK if session.outcome == "ok" else EXIT_PROXY_FAILED


async def _probe(
    bridge: ProxyBridge, check_urls: Sequence[str], config: AgentConfig, console: Console
) -> tuple[PageVisit, ExitIp | None]:
    console.info("Đang kiểm tra IP ra của proxy...")
    try:
        exit_ip = await probe_exit_ip(bridge, check_urls, timeout_sec=config.request_timeout_sec)
    except ProbeError as exc:
        console.warn(f"Proxy không dùng được: {exc}")
        return PageVisit(url=exc.url, status=exc.status, error=str(exc)), None
    country = f" ({exit_ip.country})" if exit_ip.country else ""
    host = urlsplit(exit_ip.check_url).hostname
    console.info(f"IP ra của proxy: {exit_ip.ip}{country}, {host} trả lời sau {exit_ip.latency_ms} ms")
    return PageVisit(url=exit_ip.check_url, status=200, elapsed_ms=exit_ip.latency_ms), exit_ip


async def _browse(
    args: argparse.Namespace,
    urls: list[str],
    bridge: ProxyBridge,
    session: LeaseSession,
    console: Console,
    visits: list[PageVisit],
    settle: Callable[[], None],
) -> None:
    if args.screenshot_dir is not None:
        args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    async with ProxiedBrowser(
        bridge.server_url,
        username=bridge.credentials.username,
        password=bridge.credentials.password,
        headless=args.headless,
        navigation_timeout_sec=args.timeout,
    ) as browser:
        for index, url in enumerate(urls, start=1):
            if session.lost.is_set():
                break
            console.info(f"Đang mở {url} ...")
            screenshot = _screenshot_path(args.screenshot_dir, index, url) if args.screenshot_dir else None
            visit = await browser.visit(url, screenshot=screenshot, keep_page=args.keep_open)
            visits.append(visit)
            console.info(_describe_visit(visit))
        if args.keep_open and not session.lost.is_set():
            settle()
            console.info("Chromium vẫn mở qua proxy. Đóng cửa sổ Chromium hoặc nhấn Ctrl+C để trả proxy.")
            await browser.wait_until_closed(session.lost)
    if session.lost.is_set():
        console.warn(f"VPS đã kết thúc lượt thuê: {session.lost_reason}")


def _describe_visit(visit: PageVisit) -> str:
    if visit.error is not None:
        return f"  Lỗi: {visit.error} ({visit.elapsed_ms} ms)"
    parts = [f"HTTP {visit.status}" if visit.status is not None else "đã mở", f"{visit.elapsed_ms} ms"]
    if visit.title:
        parts.append(f"tiêu đề: {visit.title[:80]}")
    if visit.screenshot is not None:
        parts.append(f"ảnh chụp: {visit.screenshot}")
    return "  " + ", ".join(parts)


def _report_release(session: LeaseSession, console: Console) -> None:
    outcome = session.outcome or "cancelled"
    text = f"Kết quả: {OUTCOME_LABELS[outcome]}" + (f" ({session.detail})" if session.detail else "")
    result = session.release_result
    if result is None:
        console.info(text)
        console.warn(f"Chưa trả được proxy: {session.release_error}")
        return
    text += ". Đã trả proxy cho VPS" if result.released else ". VPS đã ghi nhận việc trả proxy này từ trước"
    if result.rotation_scheduled:
        text += ", VPS sẽ đổi IP proxy"
    if result.quarantined_until is not None:
        text += f", proxy tạm ngưng tới {_local_time(result.quarantined_until)}"
    console.info(text)


def _summary(
    session: LeaseSession, exit_ip: ExitIp | None, visits: Sequence[PageVisit], bridge: ProxyBridge | None
) -> dict[str, Any]:
    proxy = session.lease.proxy if session.lease is not None else None
    release = session.release_result
    return {
        "ok": session.outcome == "ok",
        "outcome": session.outcome,
        "detail": session.detail,
        "proxy": None
        if proxy is None
        else {"id": proxy.id, "label": proxy.label, "kind": proxy.kind, "pool": proxy.pool, "protocol": proxy.protocol},
        "exit_ip": exit_ip.ip if exit_ip else None,
        "country": exit_ip.country if exit_ip else None,
        "visits": [
            {
                "url": visit.url,
                "status": visit.status,
                "final_url": visit.final_url,
                "title": visit.title,
                "error": visit.error,
                "elapsed_ms": visit.elapsed_ms,
                "screenshot": str(visit.screenshot) if visit.screenshot else None,
            }
            for visit in visits
        ],
        "release": None
        if release is None
        else {
            "released": release.released,
            "rotation_scheduled": release.rotation_scheduled,
            "quarantined_until": release.quarantined_until.isoformat() if release.quarantined_until else None,
        },
        "release_error": session.release_error,
        "bridge": None
        if bridge is None
        else {
            "requests": bridge.stats.requests,
            "upstream_failures": bridge.stats.upstream_failures,
            "target_failures": bridge.stats.target_failures,
            "bytes_sent": bridge.stats.bytes_sent,
            "bytes_received": bridge.stats.bytes_received,
        },
    }


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "server_url": args.server,
        "worker_id": args.worker_id,
        "pool": getattr(args, "pool", None),
        "kind": getattr(args, "kind", None),
        "lease_wait_sec": getattr(args, "wait", None),
        "lease_ttl_sec": getattr(args, "ttl", None),
        "check_url": getattr(args, "check_url", None),
    }


def _open_urls(args: argparse.Namespace) -> list[str]:
    if args.keep_open and args.headless:
        raise ConfigError("--keep-open cần cửa sổ Chromium, không dùng cùng --headless")
    if args.timeout <= 0:
        raise ConfigError("--timeout phải lớn hơn 0")
    urls: list[str] = []
    for raw in args.urls:
        url = raw.strip()
        if "://" not in url:
            url = f"https://{url}"
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"Địa chỉ trang phải bắt đầu bằng http:// hoặc https://: {raw}")
        urls.append(url)
    return urls


def _screenshot_path(directory: Path, index: int, url: str) -> Path:
    host = re.sub(r"[^A-Za-z0-9.-]+", "-", urlsplit(url).hostname or "trang").strip("-.") or "trang"
    return directory / f"{index:02d}-{host}.png"


def _local_time(value: datetime) -> str:
    return value.astimezone().strftime("%H:%M:%S %d/%m/%Y")


def _use_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if isinstance(stream, io.TextIOWrapper) and encoding != "utf8":
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")
