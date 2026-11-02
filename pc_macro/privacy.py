"""Local privacy checks so the clicker does not run on the real address."""

from __future__ import annotations

from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

_IP6_URLS = ("https://api6.ipify.org",)
_CHROME_KEY = r"HKCU:\Software\Policies\Google\Chrome"
_EDGE_KEY = r"HKCU:\Software\Policies\Microsoft\Edge"
_FIREFOX_KEY = r"HKCU:\Software\Policies\Mozilla\Firefox"
_CHROME_EXES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_EDGE_EXES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_BROWSER_FLAGS = (
    "--incognito",
    "--inprivate",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--enforce-webrtc-ip-permission-check",
)
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


def _ps(script: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def harden_commands() -> list[list[str]]:
    chrome = (
        f"New-Item -Path '{_CHROME_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{_CHROME_KEY}' -Name WebRtcIPHandling -Value disable_non_proxied_udp; "
        f"Set-ItemProperty -Path '{_CHROME_KEY}' -Name WebRtcLocalhostIpHandling -Value disable_non_proxied_udp"
    )
    edge = (
        f"New-Item -Path '{_EDGE_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{_EDGE_KEY}' -Name WebRtcIPHandling -Value disable_non_proxied_udp; "
        f"Set-ItemProperty -Path '{_EDGE_KEY}' -Name WebRtcLocalhostIpHandling -Value disable_non_proxied_udp"
    )
    firefox = (
        f"New-Item -Path '{_FIREFOX_KEY}' -Force | Out-Null; "
        r"""Set-ItemProperty -Path '{0}' -Name Preferences -Value '{{"media.peerconnection.ice.no_host":{{"Value":true,"Status":"locked"}},"media.peerconnection.ice.default_address_only":{{"Value":true,"Status":"locked"}}}}'""".format(
            _FIREFOX_KEY
        )
    )
    ipv6 = "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { Disable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }"
    dns = (
        "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex "
        "-ServerAddresses '1.1.1.1','9.9.9.9' -ErrorAction SilentlyContinue }"
    )
    return [
        ["netsh", "interface", "teredo", "set", "state", "disabled"],
        ["netsh", "interface", "6to4", "set", "state", "disabled"],
        ["netsh", "interface", "isatap", "set", "state", "disabled"],
        _ps(ipv6),
        _ps(chrome),
        _ps(edge),
        _ps(firefox),
        _ps(dns),
        ["ipconfig", "/flushdns"],
    ]


def restore_commands() -> list[list[str]]:
    ipv6 = "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { Enable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }"
    dns = (
        "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex "
        "-ResetServerAddresses -ErrorAction SilentlyContinue }"
    )
    return [
        ["netsh", "interface", "teredo", "set", "state", "default"],
        ["netsh", "interface", "6to4", "set", "state", "default"],
        ["netsh", "interface", "isatap", "set", "state", "default"],
        _ps(ipv6),
        _ps(dns),
    ]


def warp_install_commands() -> list[list[str]]:
    return [
        [
            "winget",
            "install",
            "-e",
            "--id",
            "Cloudflare.Warp",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
    ]


def private_browser_flags() -> list[str]:
    return list(_BROWSER_FLAGS)


def chrome_paths() -> tuple[str, ...]:
    return _CHROME_EXES


def edge_paths() -> tuple[str, ...]:
    return _EDGE_EXES


def browser_command(exe: str, *, edge: bool = False) -> list[str]:
    flags = list(_BROWSER_FLAGS)
    if edge:
        flags = [flag for flag in flags if flag != "--incognito"]
    else:
        flags = [flag for flag in flags if flag != "--inprivate"]
    return [exe, *flags]


def inspect_label(report: dict[str, Any]) -> str:
    ip = str(report.get("ip") or "")
    baseline = str(report.get("baseline") or "")
    ipv6 = str(report.get("ipv6") or "")
    source = str(report.get("source") or "")
    if report.get("ok") and ip and baseline and ip != baseline:
        extra = f" · {source}" if source else ""
        return f"Đã ẩn địa chỉ{extra}: {baseline} → {ip}. IPv6 tắt."
    if ipv6:
        return report.get("error") or f"IPv6 còn lộ ({ipv6})."
    if ip and baseline and ip == baseline:
        return report.get("error") or f"IP chưa đổi ({ip}) — địa chỉ thật vẫn lộ."
    return report.get("error") or "Chưa kiểm tra được địa chỉ công cộng."
