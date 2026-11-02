"""Local privacy checks so the clicker does not run on the real address."""

from __future__ import annotations

from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

_IP6_URLS = ("https://api6.ipify.org",)
_CHROME_KEY = r"HKCU:\Software\Policies\Google\Chrome"
_EDGE_KEY = r"HKCU:\Software\Policies\Microsoft\Edge"
Opener = Callable[..., Any]


def normalize_ip(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    token = "".join(ch for ch in text.splitlines()[0] if ch.isdigit() or ch in ".:abcdefABCDEF")
    if token.count(".") == 3 and all(part.isdigit() and int(part) <= 255 for part in token.split(".")):
        return token[:64]
    if ":" in token and any(ch.isalpha() or ch.isdigit() for ch in token):
        return token[:64]
    return ""


def is_ipv4(raw: Any) -> bool:
    return "." in normalize_ip(raw) and ":" not in normalize_ip(raw)


def is_ipv6(raw: Any) -> bool:
    return ":" in normalize_ip(raw)


def ips_equal(left: Any, right: Any) -> bool:
    one = normalize_ip(left)
    two = normalize_ip(right)
    return bool(one and two and one.lower() == two.lower())


def fetch_ipv6(opener: Opener | None = None) -> str:
    take = opener or urlopen
    for url in _IP6_URLS:
        try:
            with take(Request(url, headers={"User-Agent": "pc-macro-vpn"}), timeout=5) as reply:
                found = normalize_ip(reply.read().decode("utf-8", errors="replace"))
        except (OSError, URLError, TimeoutError, ValueError):
            continue
        if is_ipv6(found):
            return found
    return ""


def assess(
    baseline: str,
    current: str,
    ipv6: str = "",
    *,
    privacy_lock: bool,
    block_ipv6: bool,
) -> dict[str, Any]:
    if not privacy_lock:
        return {"ok": True, "error": ""}
    now = normalize_ip(current)
    before = normalize_ip(baseline)
    if not now:
        return {"ok": False, "error": "Không lấy được IP sau khi nối — chưa chắc đã ẩn địa chỉ."}
    if not before:
        return {"ok": False, "error": "Không lấy được IP trước khi nối — không đối chiếu được. Đã ngắt."}
    if ips_equal(before, now):
        return {"ok": False, "error": "IP công cộng không đổi — địa chỉ thật vẫn lộ. Đã ngắt."}
    if block_ipv6 and is_ipv6(ipv6):
        return {"ok": False, "error": "IPv6 còn lộ địa chỉ thật. Đã ngắt."}
    return {"ok": True, "error": ""}


def leak_message(baseline: str, current: str, ipv6: str = "", *, block_ipv6: bool) -> str:
    now = normalize_ip(current)
    before = normalize_ip(baseline)
    if not now:
        return "Chế độ ẩn IP: không kiểm tra được IP — dừng để khỏi lộ địa chỉ."
    if before and ips_equal(before, now):
        return "IP đã trở về địa chỉ thật — đã dừng."
    if block_ipv6 and is_ipv6(ipv6):
        return "IPv6 còn lộ địa chỉ — đã dừng."
    return ""


def harden_commands() -> list[list[str]]:
    chrome = (
        f"New-Item -Path '{_CHROME_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{_CHROME_KEY}' -Name WebRtcIPHandling -Value disable_non_proxied_udp"
    )
    edge = (
        f"New-Item -Path '{_EDGE_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{_EDGE_KEY}' -Name WebRtcIPHandling -Value disable_non_proxied_udp"
    )
    ipv6 = "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { Disable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }"
    return [
        ["netsh", "interface", "teredo", "set", "state", "disabled"],
        ["netsh", "interface", "6to4", "set", "state", "disabled"],
        ["netsh", "interface", "isatap", "set", "state", "disabled"],
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ipv6],
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", chrome],
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", edge],
    ]


def restore_commands() -> list[list[str]]:
    ipv6 = "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { Enable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }"
    return [
        ["netsh", "interface", "teredo", "set", "state", "default"],
        ["netsh", "interface", "6to4", "set", "state", "default"],
        ["netsh", "interface", "isatap", "set", "state", "default"],
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ipv6],
    ]
