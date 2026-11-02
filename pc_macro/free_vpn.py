"""Official free VPN sources the PC clicker can try on its own."""

from __future__ import annotations

import base64
import csv
import io
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

VPNGATE_URL = "https://www.vpngate.net/api/iphone/"
L2TP_NAME = "PC-Macro-Free"
L2TP_USER = "vpn"
L2TP_PASS = "vpn"
L2TP_PSK = "vpn"
_MAX_SERVERS = 6
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_BLOCKED_OVPN = frozenset(
    {
        "up",
        "down",
        "up-restart",
        "down-pre",
        "plugin",
        "script-security",
        "client-connect",
        "learn-address",
        "ipchange",
        "route-up",
        "route-pre-down",
        "auth-user-pass-verify",
        "tls-verify",
        "management",
        "tmp-dir",
        "cd",
        "chroot",
        "user",
        "group",
        "writepid",
    }
)
_ALLOWED_OVPN = frozenset(
    {
        "client",
        "dev",
        "proto",
        "remote",
        "resolv-retry",
        "nobind",
        "persist-key",
        "persist-tun",
        "ca",
        "cert",
        "key",
        "remote-cert-tls",
        "cipher",
        "data-ciphers",
        "auth",
        "verb",
        "mute",
        "ignore-unknown-option",
        "auth-user-pass",
        "redirect-gateway",
        "dhcp-option",
        "tun-mtu",
        "mssfix",
        "sndbuf",
        "rcvbuf",
        "reneg-sec",
        "ping",
        "ping-restart",
        "compress",
        "comp-lzo",
        "tls-client",
        "key-direction",
        "explicit-exit-notify",
        "float",
        "remote-random",
        "connect-retry",
        "keepalive",
        "tls-cipher",
        "tls-version-min",
        "ns-cert-type",
        "verify-x509-name",
        "setenv",
        "route-method",
        "route-delay",
        "route",
        "ifconfig",
        "fast-io",
        "mute-replay-warnings",
        "auth-nocache",
        "pull",
        "lport",
        "rport",
        "bind",
        "replay-window",
        "engine",
        "secret",
        "remote-cert-eku",
        "tls-crypt",
        "tls-auth",
    }
)
_BLOCK_TAGS = frozenset({"<up>", "<down>", "<plugin>"})
Fetcher = Callable[[str], str]


def normalize_source(raw: Any) -> str:
    text = str(raw or "auto").strip().lower()
    if text in {"auto", "free", "mienphi", "all"}:
        return "auto"
    if text in {"warp", "cloudflare", "1111", "1.1.1.1"}:
        return "warp"
    if text in {"vpngate", "gate", "tsukuba"}:
        return "vpngate"
    if text in {"proxy", "static", "proxies"}:
        return "proxy"
    if text in {"proton", "protonvpn"}:
        return "proton"
    if text in {"windscribe"}:
        return "windscribe"
    return "auto"


def country_code(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if _COUNTRY_RE.match(text):
        return text
    return ""


def source_order(kind: str, source: str = "") -> tuple[str, ...]:
    picked = normalize_source(source or kind)
    if picked == "warp":
        return ("warp",)
    if picked == "vpngate":
        return ("vpngate",)
    if picked == "proxy":
        return ("proxy",)
    if picked == "proton":
        return ("proton",)
    if picked == "windscribe":
        return ("windscribe",)
    return ("warp", "proton", "windscribe", "vpngate", "proxy")


def parse_proxy_line(line: str) -> dict[str, str] | None:
    text = str(line or "").strip()
    if not text or text.startswith("#"):
        return None
    if "://" in text:
        parsed = urlparse(text)
        if not parsed.hostname or not parsed.port:
            return None
        return {
            "host": parsed.hostname,
            "port": str(parsed.port),
            "username": parsed.username or "",
            "password": parsed.password or "",
            "server": f"{parsed.hostname}:{parsed.port}",
        }
    parts = text.split(":")
    if len(parts) == 2 and parts[1].isdigit():
        return {"host": parts[0], "port": parts[1], "username": "", "password": "", "server": f"{parts[0]}:{parts[1]}"}
    if len(parts) == 4 and parts[1].isdigit():
        return {
            "host": parts[0],
            "port": parts[1],
            "username": parts[2],
            "password": parts[3],
            "server": f"{parts[0]}:{parts[1]}",
        }
    return None


def load_proxy_lines(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    found: list[dict[str, str]] = []
    for line in text.splitlines():
        item = parse_proxy_line(line)
        if item:
            found.append(item)
    return found


def proxies_path() -> Path:
    env = os.environ.get("PC_MACRO_PROXIES", "").strip()
    if env:
        return Path(env)
    store = os.environ.get("PC_MACRO_DIR", "").strip()
    if store:
        return Path(store).parent / "proxies_static.txt"
    return Path.cwd() / "data" / "proxies_static.txt"


def fetch_text(url: str, opener: Fetcher | None = None) -> str:
    if opener is not None:
        return opener(url)
    request = Request(url, headers={"User-Agent": "pc-macro-vpn", "Accept": "text/plain,*/*"})
    with urlopen(request, timeout=20) as reply:
        return reply.read().decode("utf-8", errors="replace")


def parse_vpngate(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*") or line.startswith("#"):
            continue
        if "," not in line:
            continue
        head, blob = line.rsplit(",", 1)
        fields = next(csv.reader(io.StringIO(head)))
        if len(fields) < 7 or not blob.strip():
            continue
        try:
            score = int(fields[2] or "0")
            ping = int(fields[3] or "9999")
            speed = int(fields[4] or "0")
        except ValueError:
            continue
        rows.append(
            {
                "host": fields[0],
                "ip": fields[1],
                "score": score,
                "ping": ping,
                "speed": speed,
                "country": str(fields[6] or "").upper(),
                "config": blob.strip(),
            }
        )
    rows.sort(key=lambda item: (-item["score"], item["ping"], -item["speed"]))
    return rows


def pick_servers(rows: list[dict[str, Any]], country: str = "", limit: int = _MAX_SERVERS) -> list[dict[str, Any]]:
    wanted = country_code(country)
    chosen = [row for row in rows if not wanted or row["country"] == wanted]
    return chosen[: max(1, limit)]


def decode_ovpn(blob: str) -> str:
    compact = "".join(str(blob or "").split())
    if not compact:
        return ""
    try:
        return base64.b64decode(compact, validate=False).decode("utf-8", errors="replace")
    except (ValueError, OSError):
        return ""


def sanitize_ovpn(raw: str) -> str:
    """Keep connection settings; drop script/plugin lines from a public config."""
    keep: list[str] = []
    block = ""
    for original in str(raw or "").splitlines():
        line = original.strip()
        if not line:
            continue
        lower = line.lower()
        if block:
            keep.append(original)
            if lower.startswith("</") and lower[2:].rstrip(">") == block:
                block = ""
            continue
        if lower.startswith("<") and not lower.startswith("</"):
            tag = lower[1:].rstrip(">")
            if f"<{tag}>" in _BLOCK_TAGS:
                block = tag
                keep = keep  # skip opening by not appending; consume until close
                continue
            if tag in {"ca", "cert", "key", "tls-auth", "tls-crypt"}:
                block = tag
                keep.append(original)
                continue
            continue
        if lower.startswith("#") or lower.startswith(";"):
            continue
        key = lower.split(None, 1)[0]
        if key in _BLOCKED_OVPN:
            continue
        if key in _ALLOWED_OVPN:
            keep.append(original)
    if not any(item.strip().lower().startswith("remote ") for item in keep):
        return ""
    return "\n".join(keep) + "\n"


def warp_connect_commands(exe: str) -> list[list[str]]:
    return [[exe, "registration", "new"], [exe, "connect"]]


def warp_disconnect_command(exe: str) -> list[str]:
    return [exe, "disconnect"]


def proton_connect_command(exe: str, country: str = "") -> list[str]:
    if country_code(country):
        return [exe, "connect", "--cc", country_code(country)]
    return [exe, "connect", "--fastest"]


def proton_disconnect_command(exe: str) -> list[str]:
    return [exe, "disconnect"]


def windscribe_connect_command(exe: str, country: str = "") -> list[str]:
    if country_code(country):
        return [exe, "connect", country_code(country)]
    return [exe, "connect", "best"]


def windscribe_disconnect_command(exe: str) -> list[str]:
    return [exe, "disconnect"]


def l2tp_setup_command(ip: str, name: str = L2TP_NAME) -> list[str]:
    script = (
        f"Add-VpnConnection -Name '{name}' -ServerAddress '{ip}' -TunnelType L2tp "
        f"-L2tpPsk '{L2TP_PSK}' -AuthenticationMethod Pap,Chap,MSChapv2 "
        f"-EncryptionLevel Optional -Force -RememberCredential -ErrorAction Stop"
    )
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def l2tp_dial_command(name: str = L2TP_NAME) -> list[str]:
    return ["rasdial", name, L2TP_USER, L2TP_PASS]


def l2tp_disconnect_command(name: str = L2TP_NAME) -> list[str]:
    return ["rasdial", name, "/disconnect"]


def l2tp_remove_command(name: str = L2TP_NAME) -> list[str]:
    script = f"Remove-VpnConnection -Name '{name}' -Force -ErrorAction SilentlyContinue"
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def proxy_enable_commands(server: str) -> list[list[str]]:
    key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    script = (
        f"Set-ItemProperty -Path '{key}' ProxyEnable 1; "
        f"Set-ItemProperty -Path '{key}' ProxyServer '{server}'"
    )
    return [
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        ["netsh", "winhttp", "set", "proxy", server],
    ]


def proxy_disable_commands() -> list[list[str]]:
    key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    script = f"Set-ItemProperty -Path '{key}' ProxyEnable 0"
    return [
        ["rasdial", L2TP_NAME, "/disconnect"],
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        ["netsh", "winhttp", "reset", "proxy"],
    ]


SOURCE_LABELS = {
    "warp": "Cloudflare WARP",
    "proton": "Proton VPN",
    "windscribe": "Windscribe",
    "vpngate": "VPN Gate",
    "proxy": "proxy tĩnh",
}


def persist_source(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in SOURCE_LABELS:
        return text
    return ""


def source_label(raw: Any) -> str:
    return SOURCE_LABELS.get(persist_source(raw), "")


def build_attempts(
    *,
    kind: str,
    source: str = "",
    country: str = "",
    tools: dict[str, str] | None = None,
    servers: list[dict[str, Any]] | None = None,
    proxies: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    found = tools or {}
    steps: list[dict[str, Any]] = []
    for name in source_order(kind, source):
        if name == "warp" and found.get("warp"):
            steps.append({"source": "warp", "commands": warp_connect_commands(found["warp"])})
            continue
        if name == "proton" and found.get("proton"):
            steps.append({"source": "proton", "commands": [proton_connect_command(found["proton"], country)]})
            continue
        if name == "windscribe" and found.get("windscribe"):
            steps.append({"source": "windscribe", "commands": [windscribe_connect_command(found["windscribe"], country)]})
            continue
        if name == "vpngate":
            for server in servers or []:
                if found.get("rasdial") or found.get("powershell"):
                    steps.append(
                        {
                            "source": "vpngate",
                            "mode": "l2tp",
                            "ip": server["ip"],
                            "country": server.get("country") or "",
                            "host": server.get("host") or "",
                            "commands": [
                                l2tp_remove_command(),
                                l2tp_setup_command(server["ip"]),
                                l2tp_dial_command(),
                            ],
                        }
                    )
                config = sanitize_ovpn(decode_ovpn(str(server.get("config") or "")))
                if found.get("openvpn") and config:
                    steps.append(
                        {
                            "source": "vpngate",
                            "mode": "openvpn",
                            "ip": server["ip"],
                            "country": server.get("country") or "",
                            "host": server.get("host") or "",
                            "config": config,
                            "commands": [],
                        }
                    )
            continue
        if name == "proxy":
            for item in proxies or []:
                steps.append(
                    {
                        "source": "proxy",
                        "server": item["server"],
                        "commands": proxy_enable_commands(item["server"]),
                    }
                )
    return steps
