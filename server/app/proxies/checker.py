"""Kiểm tra proxy sống/chết: gửi request qua proxy tới trang trả về IP."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_ASN_PREFIX_RE = re.compile(r"^AS\d+\s+", re.IGNORECASE)
_IP_KEYS = ("ip", "query", "origin", "ip_addr", "ipAddress", "address")
_COUNTRY_KEYS = ("country_code", "countryCode", "country")
_ISP_KEYS = ("isp", "org", "asn_org", "organization")


@dataclass(frozen=True, slots=True)
class IpInfo:
    ip: str | None
    country: str | None
    isp: str | None


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    latency_ms: int | None = None
    exit_ip: str | None = None
    country: str | None = None
    isp: str | None = None
    error: str | None = None


def short_error(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= limit else f"{text[:limit]}…"


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


def parse_ip_payload(body: str) -> IpInfo:
    """Đọc IP/quốc gia/nhà mạng từ JSON (ipinfo.io, ip-api.com, ipify, httpbin) hoặc text thuần."""
    text = body.strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return IpInfo(ip=_valid_ip(text), country=None, isp=None)
    raw_ip = _first_str(data, _IP_KEYS)
    country = _first_str(data, _COUNTRY_KEYS)
    isp = _first_str(data, _ISP_KEYS)
    return IpInfo(
        ip=_valid_ip(raw_ip.split(",")[0].strip() if raw_ip else None),
        country=country.upper() if country and len(country) == 2 else None,
        isp=_ASN_PREFIX_RE.sub("", isp)[:255] if isp else None,
    )


def _describe_proxy_error(exc: httpx.ProxyError) -> str:
    message = short_error(exc)
    if "407" in message:
        return "Sai tên đăng nhập/mật khẩu proxy (HTTP 407)"
    return f"Proxy từ chối kết nối: {message}"


async def check_proxy(proxy_url: str, *, check_urls: Sequence[str], timeout_sec: float) -> CheckResult:
    if not check_urls:
        return CheckResult(ok=False, error="Chưa cấu hình PROXY_CHECK_URLS")
    last_error = "Không kiểm tra được proxy"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
    for check_url in check_urls:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=httpx.Timeout(timeout_sec),
                headers=headers,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = await client.get(check_url)
        except httpx.ProxyError as exc:
            return CheckResult(ok=False, error=_describe_proxy_error(exc))
        except httpx.ConnectTimeout:
            return CheckResult(ok=False, error="Hết thời gian kết nối tới proxy")
        except httpx.ConnectError as exc:
            return CheckResult(ok=False, error=f"Không kết nối được qua proxy: {short_error(exc)}")
        except httpx.TimeoutException:
            last_error = "Proxy phản hồi quá chậm (quá thời gian chờ)"
            continue
        except httpx.HTTPError as exc:
            last_error = f"Lỗi khi đi qua proxy: {short_error(exc)}"
            continue
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 407:
            return CheckResult(ok=False, error="Sai tên đăng nhập/mật khẩu proxy (HTTP 407)")
        if response.status_code >= 400:
            last_error = f"Trang kiểm tra {httpx.URL(check_url).host} trả về HTTP {response.status_code}"
            continue
        info = parse_ip_payload(response.text)
        if info.ip is None:
            last_error = f"Không đọc được IP từ {httpx.URL(check_url).host}"
            continue
        return CheckResult(ok=True, latency_ms=latency_ms, exit_ip=info.ip, country=info.country, isp=info.isp)
    return CheckResult(ok=False, error=last_error)
