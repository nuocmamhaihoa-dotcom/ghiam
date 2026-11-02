"""Local privacy layers so the clicker does not run on the real address."""

from __future__ import annotations

from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

_IP6_URLS = ("https://api6.ipify.org",)
_CHROME_KEY = r"HKCU:\Software\Policies\Google\Chrome"
_EDGE_KEY = r"HKCU:\Software\Policies\Microsoft\Edge"
_BRAVE_KEY = r"HKCU:\Software\Policies\BraveSoftware\Brave"
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
    "--disable-geolocation",
    "--disable-notifications",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-client-side-phishing-detection",
    "--no-first-run",
    "--disable-features=WebRtcHideLocalIpsWithMdns,MediaRouter",
)
LAYERS = (
    "DoH 1.1.1.1 / 9.9.9.9",
    "Tắt IPv6 + Teredo",
    "Khóa WebRTC / STUN",
    "Tắt LLMNR / NetBIOS",
    "Kill switch tường lửa",
    "WARP / VPN Gate",
    "Ẩn IP (cấm gõ nếu lộ)",
)
LIMITS = (
    "Không phần mềm nào giấu tuyệt đối IP và mọi thông tin. "
    "Nhà VPN vẫn thấy máy bạn; tài khoản web, thanh toán, "
    "vân tay trình duyệt và thời điểm truy cập vẫn nhận diện được."
)
Opener = Callable[..., Any]


def layer_names() -> list[str]:
    return list(LAYERS)


def limits_text() -> str:
    return LIMITS


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
    extra: str = "",
    webrtc: str = "",
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
    extra_ip = normalize_ip(extra)
    if extra_ip and is_ipv4(extra_ip) and not ips_equal(extra_ip, now):
        return {"ok": False, "error": "Hai nguồn IP khác nhau — có đường phụ lộ địa chỉ. Đã ngắt."}
    webrtc_ip = normalize_ip(webrtc)
    if webrtc_ip and before and ips_equal(webrtc_ip, before):
        return {"ok": False, "error": "WebRTC còn lộ địa chỉ thật. Đã ngắt."}
    if webrtc_ip and is_ipv4(webrtc_ip) and now and not ips_equal(webrtc_ip, now):
        return {"ok": False, "error": "WebRTC lệch tunnel — địa chỉ phụ còn lộ. Đã ngắt."}
    return {"ok": True, "error": ""}


def leak_message(
    baseline: str,
    current: str,
    ipv6: str = "",
    *,
    block_ipv6: bool,
    extra: str = "",
    webrtc: str = "",
) -> str:
    now = normalize_ip(current)
    before = normalize_ip(baseline)
    if not now:
        return "Chế độ ẩn IP: không kiểm tra được IP — dừng để khỏi lộ địa chỉ."
    if before and ips_equal(before, now):
        return "IP đã trở về địa chỉ thật — đã dừng."
    if block_ipv6 and is_ipv6(ipv6):
        return "IPv6 còn lộ địa chỉ — đã dừng."
    extra_ip = normalize_ip(extra)
    if extra_ip and is_ipv4(extra_ip) and now and not ips_equal(extra_ip, now):
        return "Hai nguồn IP khác nhau — đã dừng."
    webrtc_ip = normalize_ip(webrtc)
    if webrtc_ip and before and ips_equal(webrtc_ip, before):
        return "WebRTC còn lộ địa chỉ thật — đã dừng."
    if webrtc_ip and is_ipv4(webrtc_ip) and now and not ips_equal(webrtc_ip, now):
        return "WebRTC lệch tunnel — đã dừng."
    return ""


def _ps(script: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def _browser_policy(key: str) -> str:
    return (
        f"New-Item -Path '{key}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{key}' -Name WebRtcIPHandling -Value disable_non_proxied_udp; "
        f"Set-ItemProperty -Path '{key}' -Name WebRtcLocalhostIpHandling -Value disable_non_proxied_udp; "
        f"Set-ItemProperty -Path '{key}' -Name DnsOverHttpsMode -Value secure; "
        f"Set-ItemProperty -Path '{key}' -Name DnsOverHttpsTemplates "
        r"-Value 'https://cloudflare-dns.com/dns-query https://dns.quad9.net/dns-query'; "
        f"Set-ItemProperty -Path '{key}' -Name DefaultGeolocationSetting -Value 2; "
        f"Set-ItemProperty -Path '{key}' -Name SyncDisabled -Value 1; "
        f"Set-ItemProperty -Path '{key}' -Name BrowserSignin -Value 0; "
        f"Set-ItemProperty -Path '{key}' -Name MetricsReportingEnabled -Value 0; "
        f"Set-ItemProperty -Path '{key}' -Name UrlKeyedAnonymizedDataCollectionEnabled -Value 0; "
        f"Set-ItemProperty -Path '{key}' -Name NetworkPredictionOptions -Value 2; "
        f"Set-ItemProperty -Path '{key}' -Name TranslateEnabled -Value 0; "
        f"Set-ItemProperty -Path '{key}' -Name WebRtcEventLogCollection -Value 0"
    )


def harden_commands() -> list[list[str]]:
    firefox_prefs = (
        r'{"media.peerconnection.enabled":{"Value":false,"Status":"locked"},'
        r'"media.peerconnection.ice.no_host":{"Value":true,"Status":"locked"},'
        r'"media.peerconnection.ice.default_address_only":{"Value":true,"Status":"locked"},'
        r'"network.dns.disableIPv6":{"Value":true,"Status":"locked"},'
        r'"geo.enabled":{"Value":false,"Status":"locked"},'
        r'"privacy.resistFingerprinting":{"Value":true,"Status":"locked"}}'
    )
    firefox = (
        f"New-Item -Path '{_FIREFOX_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{_FIREFOX_KEY}' -Name Preferences -Value '{firefox_prefs}'"
    )
    ipv6 = (
        "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { "
        "Disable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }; "
        "New-Item -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters' -Force | Out-Null; "
        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters' "
        "-Name DisabledComponents -Value 255 -Type DWord -ErrorAction SilentlyContinue"
    )
    dns = (
        "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex "
        "-ServerAddresses '1.1.1.1','9.9.9.9' -ErrorAction SilentlyContinue }; "
        "New-Item -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Dnscache\\Parameters' -Force | Out-Null; "
        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Dnscache\\Parameters' "
        "-Name EnableAutoDoh -Value 2 -Type DWord -ErrorAction SilentlyContinue"
    )
    llmnr = (
        "New-Item -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' -Force | Out-Null; "
        "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' "
        "-Name EnableMulticast -Value 0 -Type DWord -ErrorAction SilentlyContinue; "
        "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\NetBT\\Parameters\\Interfaces' "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "Set-ItemProperty -Path $_.PSPath -Name NetbiosOptions -Value 2 -ErrorAction SilentlyContinue }; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        "-Name AutoDetect -Value 0 -ErrorAction SilentlyContinue"
    )
    identity = (
        "New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager"
        "\\ConsentStore\\location' -Force | Out-Null; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager"
        "\\ConsentStore\\location' -Name Value -Value Deny -ErrorAction SilentlyContinue; "
        "New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\AdvertisingInfo' -Force | Out-Null; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\AdvertisingInfo' "
        "-Name Enabled -Value 0 -ErrorAction SilentlyContinue"
    )
    stun = (
        "Get-NetFirewallRule -DisplayName 'PcMacroKS-Stun' -ErrorAction SilentlyContinue | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue; "
        "New-NetFirewallRule -DisplayName 'PcMacroKS-Stun' -Direction Outbound -Action Block "
        "-Protocol UDP -RemotePort 3478,19302,19305,5349 -Enabled True -Profile Any "
        "-ErrorAction SilentlyContinue | Out-Null"
    )
    return [
        ["netsh", "interface", "teredo", "set", "state", "disabled"],
        ["netsh", "interface", "6to4", "set", "state", "disabled"],
        ["netsh", "interface", "isatap", "set", "state", "disabled"],
        ["netsh", "wlan", "set", "randomization", "enabled=yes"],
        _ps(ipv6),
        _ps(_browser_policy(_CHROME_KEY)),
        _ps(_browser_policy(_EDGE_KEY)),
        _ps(_browser_policy(_BRAVE_KEY)),
        _ps(firefox),
        _ps(dns),
        _ps(llmnr),
        _ps(identity),
        _ps(stun),
        ["ipconfig", "/flushdns"],
    ]


def kill_switch_on_commands() -> list[list[str]]:
    """Block every outbound path except the live VPN / WARP adapter."""
    script = (
        "Get-NetFirewallRule -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName -like 'PcMacroKS-Tunnel*' -or $_.DisplayName -eq 'PcMacroKS-Block' "
        "-or $_.DisplayName -eq 'PcMacroKS-Loop' } | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue; "
        "New-NetFirewallRule -DisplayName 'PcMacroKS-Loop' -Direction Outbound -Action Allow "
        "-RemoteAddress 127.0.0.0/8 -Enabled True -Profile Any -ErrorAction SilentlyContinue | Out-Null; "
        "$ifaces = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { "
        "$_.Status -eq 'Up' -and ($_.InterfaceDescription -match "
        "'WARP|Cloudflare|Proton|Windscribe|WireGuard|TAP-Windows|OpenVPN|VPN|tun|wg' "
        "-or $_.Name -match 'WARP|Proton|Windscribe|WireGuard|VPN') }; "
        "foreach ($i in $ifaces) { "
        "New-NetFirewallRule -DisplayName ('PcMacroKS-Tunnel-' + $i.ifIndex) -Direction Outbound "
        "-Action Allow -InterfaceAlias $i.Name -Enabled True -Profile Any "
        "-ErrorAction SilentlyContinue | Out-Null }; "
        "Set-NetFirewallProfile -Profile Domain,Public,Private -DefaultOutboundAction Block "
        "-ErrorAction SilentlyContinue"
    )
    return [_ps(script)]


def kill_switch_off_commands() -> list[list[str]]:
    script = (
        "Set-NetFirewallProfile -Profile Domain,Public,Private -DefaultOutboundAction Allow "
        "-ErrorAction SilentlyContinue; "
        "Get-NetFirewallRule -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName -like 'PcMacroKS-*' } | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue"
    )
    return [_ps(script)]


def restore_commands() -> list[list[str]]:
    ipv6 = (
        "Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object { "
        "Enable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue }; "
        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters' "
        "-Name DisabledComponents -Value 0 -Type DWord -ErrorAction SilentlyContinue"
    )
    dns = (
        "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex "
        "-ResetServerAddresses -ErrorAction SilentlyContinue }; "
        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Dnscache\\Parameters' "
        "-Name EnableAutoDoh -Value 0 -Type DWord -ErrorAction SilentlyContinue"
    )
    llmnr = (
        "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' "
        "-Name EnableMulticast -Value 1 -Type DWord -ErrorAction SilentlyContinue; "
        "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\NetBT\\Parameters\\Interfaces' "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "Set-ItemProperty -Path $_.PSPath -Name NetbiosOptions -Value 0 -ErrorAction SilentlyContinue }; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        "-Name AutoDetect -Value 1 -ErrorAction SilentlyContinue"
    )
    identity = (
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager"
        "\\ConsentStore\\location' -Name Value -Value Allow -ErrorAction SilentlyContinue; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\AdvertisingInfo' "
        "-Name Enabled -Value 1 -ErrorAction SilentlyContinue"
    )
    commands = list(kill_switch_off_commands())
    commands.extend(
        [
            ["netsh", "interface", "teredo", "set", "state", "default"],
            ["netsh", "interface", "6to4", "set", "state", "default"],
            ["netsh", "interface", "isatap", "set", "state", "default"],
            _ps(ipv6),
            _ps(dns),
            _ps(llmnr),
            _ps(identity),
        ]
    )
    return commands


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
        return f"Đã ẩn địa chỉ{extra}: {baseline} → {ip}. IPv6 tắt. Kill switch bật."
    if ipv6:
        return report.get("error") or f"IPv6 còn lộ ({ipv6})."
    if ip and baseline and ip == baseline:
        return report.get("error") or f"IP chưa đổi ({ip}) — địa chỉ thật vẫn lộ."
    return report.get("error") or "Chưa kiểm tra được địa chỉ công cộng."
