"""Kiểm tra IP ra của proxy qua cầu nối, dùng cùng danh sách trang kiểm tra với VPS (PROXY_CHECK_URLS)."""

from __future__ import annotations

import ipaddress
import json
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from commentscope_agent.bridge import ProxyBridge

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
NO_CHECK_URLS = "Chưa có trang kiểm tra IP: đặt PROXY_CHECK_URLS trên VPS hoặc check_url cho agent"
_IP_KEYS = ("ip", "query", "origin", "ip_addr", "ipAddress", "address")
_COUNTRY_KEYS = ("country_code", "countryCode", "country")


@dataclass(frozen=True, slots=True)
class ExitIp:
    ip: str
    country: str | None
    latency_ms: int
    check_url: str


class ProbeError(Exception):
    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status = status


def parse_ip_payload(body: str) -> tuple[str | None, str | None]:
    """Đọc IP và mã quốc gia từ JSON (ipinfo.io, ip-api.com, ipify, httpbin) hoặc text thuần."""
    text = body.strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _valid_ip(text), None
    raw_ip = _first_str(data, _IP_KEYS)
    country = _first_str(data, _COUNTRY_KEYS)
    return (
        _valid_ip(raw_ip.split(",")[0].strip() if raw_ip else None),
        country.upper() if country and len(country) == 2 else None,
    )


async def probe_exit_ip(
    bridge: ProxyBridge, check_urls: Sequence[str], *, timeout_sec: float, verify: ssl.SSLContext | bool = True
) -> ExitIp:
    """Thử lần lượt các trang kiểm tra; dừng sớm nếu chính proxy hỏng vì thử trang khác cũng vô ích."""
    if not check_urls:
        raise ProbeError(NO_CHECK_URLS, url="")
    error = ProbeError(NO_CHECK_URLS, url="")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
    async with httpx.AsyncClient(
        proxy=bridge.proxy_url,
        timeout=timeout_sec,
        verify=verify,
        headers=headers,
        follow_redirects=True,
        trust_env=False,
    ) as client:
        for url in check_urls:
            host = httpx.URL(url).host
            proxy_failures = bridge.stats.upstream_failures
            failures = proxy_failures + bridge.stats.target_failures
            started = time.perf_counter()
            try:
                response = await client.get(url)
            except httpx.TimeoutException:
                error = ProbeError(f"{host} không trả lời qua proxy sau {timeout_sec:g} giây", url=url)
            except httpx.HTTPError as exc:
                bridge_error = _new_bridge_error(bridge, failures)
                error = ProbeError(bridge_error or f"Không mở được {host} qua proxy: {_short(exc)}", url=url)
            else:
                latency_ms = int((time.perf_counter() - started) * 1000)
                bridge_error = _new_bridge_error(bridge, failures)
                if bridge_error is not None:
                    error = ProbeError(bridge_error, url=url)
                elif response.status_code >= 400:
                    status = response.status_code
                    error = ProbeError(f"Trang kiểm tra {host} trả HTTP {status}", url=url, status=status)
                else:
                    ip, country = parse_ip_payload(response.text)
                    if ip is not None:
                        return ExitIp(ip=ip, country=country, latency_ms=latency_ms, check_url=url)
                    error = ProbeError(f"Không đọc được IP từ {host}", url=url, status=response.status_code)
            if bridge.stats.upstream_failures > proxy_failures:
                break
    raise error


def _new_bridge_error(bridge: ProxyBridge, failures_before: int) -> str | None:
    if bridge.stats.upstream_failures + bridge.stats.target_failures > failures_before:
        return bridge.stats.last_error
    return None


def _first_str(data: dict[str, object], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return None


def _short(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= limit else f"{text[:limit]}…"
