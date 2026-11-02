"""Connect a local VPN when the PC clicker starts."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

VPN_KINDS = ("windows", "wireguard", "openvpn", "command")
DEFAULT_KIND = "windows"
KIND_LABELS = {
    "windows": "VPN Windows",
    "wireguard": "WireGuard",
    "openvpn": "OpenVPN",
    "command": "Lệnh riêng",
}
_CREATE_NO_WINDOW = 0x08000000
_IP_URLS = ("https://api.ipify.org", "https://ifconfig.me/ip")
_NAME_MAX = 80
_PATH_MAX = 260
_CMD_MAX = 400
Runner = Callable[..., Any]


def vpn_path() -> Path:
    env = os.environ.get("PC_MACRO_VPN", "").strip()
    if env:
        return Path(env)
    store = os.environ.get("PC_MACRO_DIR", "").strip()
    if store:
        return Path(store).parent / "vpn.json"
    return Path.cwd() / "data" / "vpn.json"


def normalize_kind(raw: Any) -> str:
    text = str(raw or DEFAULT_KIND).strip().lower()
    if text in {"rasdial", "win", "system"}:
        return "windows"
    if text in {"wg", "wire-guard"}:
        return "wireguard"
    if text in {"ovpn", "open-vpn"}:
        return "openvpn"
    if text in {"cmd", "custom", "shell"}:
        return "command"
    return text if text in VPN_KINDS else DEFAULT_KIND


def sanitize_profile(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    kind = normalize_kind(data.get("kind"))
    name = " ".join(str(data.get("name") or "").split())[:_NAME_MAX]
    config = str(data.get("config") or data.get("file") or "").strip()[:_PATH_MAX]
    username = str(data.get("username") or data.get("user") or "").strip()[:_NAME_MAX]
    password = str(data.get("password") or data.get("pass") or "")[:_NAME_MAX]
    connect_cmd = str(data.get("connect_cmd") or data.get("connect") or "").strip()[:_CMD_MAX]
    disconnect_cmd = str(data.get("disconnect_cmd") or data.get("disconnect") or "").strip()[:_CMD_MAX]
    if kind == "command" and not connect_cmd:
        connect_cmd = config or name
    if kind in {"wireguard", "openvpn"} and not name and config:
        name = _base_name(config)
    return {
        "enabled": bool(data.get("enabled")),
        "disconnect_on_exit": bool(data.get("disconnect_on_exit")),
        "kind": kind,
        "name": name,
        "config": config,
        "username": username,
        "password": password,
        "connect_cmd": connect_cmd,
        "disconnect_cmd": disconnect_cmd,
        "last_ip": str(data.get("last_ip") or "").strip()[:64],
        "connected": bool(data.get("connected")),
    }


def public_profile(profile: Any) -> dict[str, Any]:
    """Same settings without the password, safe to show or sync."""
    item = sanitize_profile(profile)
    item["password"] = ""
    item["has_password"] = bool(sanitize_profile(profile)["password"])
    return item


def profile_ready(profile: Any) -> bool:
    item = sanitize_profile(profile)
    kind = item["kind"]
    if kind == "windows":
        return bool(item["name"])
    if kind == "wireguard":
        return bool(item["config"] or item["name"])
    if kind == "openvpn":
        return bool(item["config"])
    if kind == "command":
        return bool(item["connect_cmd"])
    _unknown(kind)
    return False


def connect_command(profile: Any) -> list[str]:
    item = sanitize_profile(profile)
    kind = item["kind"]
    if kind == "windows":
        cmd = ["rasdial", item["name"]]
        if item["username"]:
            cmd.append(item["username"])
        if item["password"]:
            cmd.append(item["password"])
        return cmd
    if kind == "wireguard":
        exe = _find_exe(("wireguard",), _WIREGUARD)
        config = item["config"]
        if exe and config:
            return [exe, "/installtunnelservice", config]
        quick = _find_exe(("wg-quick",), ())
        if quick and config:
            return [quick, "up", config]
        return []
    if kind == "openvpn":
        exe = _find_exe(("openvpn",), _OPENVPN)
        if exe and item["config"]:
            return [exe, "--config", item["config"]]
        return []
    if kind == "command":
        return _split_command(item["connect_cmd"])
    _unknown(kind)
    return []


def disconnect_command(profile: Any) -> list[str]:
    item = sanitize_profile(profile)
    kind = item["kind"]
    if kind == "windows":
        return ["rasdial", item["name"], "/disconnect"] if item["name"] else []
    if kind == "wireguard":
        exe = _find_exe(("wireguard",), _WIREGUARD)
        tunnel = _base_name(item["config"]) if item["config"] else item["name"]
        if exe and tunnel:
            return [exe, "/uninstalltunnelservice", tunnel]
        quick = _find_exe(("wg-quick",), ())
        if quick and item["config"]:
            return [quick, "down", item["config"]]
        return []
    if kind == "openvpn":
        return []
    if kind == "command":
        return _split_command(item["disconnect_cmd"])
    _unknown(kind)
    return []


def fetch_public_ip(opener: Callable[..., Any] | None = None) -> str:
    take = opener or urlopen
    for url in _IP_URLS:
        try:
            with take(Request(url, headers={"User-Agent": "pc-macro-vpn"}), timeout=8) as reply:
                text = reply.read().decode("utf-8", errors="replace").strip()
        except (OSError, URLError, TimeoutError, ValueError):
            continue
        ip = "".join(ch for ch in text.splitlines()[0] if ch.isdigit() or ch in ".:abcdefABCDEF")
        if ip:
            return ip[:64]
    return ""


def status_label(profile: Any, ip: str = "") -> str:
    item = sanitize_profile(profile)
    shown = ip or item["last_ip"]
    kind = KIND_LABELS[item["kind"]]
    if item["connected"] and shown:
        return f"VPN đã nối ({kind}) · IP {shown}"
    if item["connected"]:
        return f"VPN đã nối ({kind})."
    if item["enabled"] and profile_ready(item):
        return f"VPN sẵn sàng ({kind}) — sẽ nối khi mở phần mềm."
    if item["enabled"]:
        return "VPN đang bật nhưng chưa đủ tên / file cấu hình."
    return "VPN tắt — đánh dấu Bật VPN khi mở phần mềm để nối tự động."


class VpnClient:
    """Load, connect, and drop a local VPN for this PC session."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        runner: Runner | None = None,
        ip_fetch: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else vpn_path()
        self.runner = runner or _run
        self.ip_fetch = ip_fetch or fetch_public_ip
        self.profile = sanitize_profile(None)
        self._child: Any = None
        self.load()

    def load(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            self.profile = sanitize_profile(raw)
        return self.profile

    def save(self, raw: Any = None) -> dict[str, Any]:
        if raw is not None:
            keep_password = self.profile.get("password") or ""
            incoming = sanitize_profile(raw)
            if not incoming["password"] and keep_password:
                incoming["password"] = keep_password
            self.profile = incoming
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.profile

    def connect(self) -> dict[str, Any]:
        if not profile_ready(self.profile):
            return {"ok": False, "error": "Chưa đủ tên VPN hoặc file cấu hình.", "ip": ""}
        cmd = connect_command(self.profile)
        if not cmd:
            return {"ok": False, "error": "Không tìm thấy chương trình VPN trên máy.", "ip": ""}
        if self.profile["kind"] == "openvpn":
            result = self._start_child(cmd)
        else:
            result = self._run_cmd(cmd)
        if not result["ok"]:
            return result
        self.profile["connected"] = True
        ip = ""
        try:
            ip = self.ip_fetch() or ""
        except Exception:
            ip = ""
        if ip:
            self.profile["last_ip"] = ip
        self.save()
        return {"ok": True, "error": "", "ip": ip}

    def disconnect(self) -> dict[str, Any]:
        cmd = disconnect_command(self.profile)
        error = ""
        if cmd:
            result = self._run_cmd(cmd)
            if not result["ok"]:
                error = result["error"]
        self._stop_child()
        self.profile["connected"] = False
        self.save()
        if error:
            return {"ok": False, "error": error, "ip": self.profile.get("last_ip") or ""}
        return {"ok": True, "error": "", "ip": self.profile.get("last_ip") or ""}

    def autostart(self) -> dict[str, Any] | None:
        if not self.profile["enabled"]:
            return None
        return self.connect()

    def shutdown(self) -> dict[str, Any] | None:
        if not self.profile["disconnect_on_exit"] or not self.profile["connected"]:
            return None
        return self.disconnect()

    def refresh_ip(self) -> str:
        ip = ""
        try:
            ip = self.ip_fetch() or ""
        except Exception:
            ip = ""
        if ip:
            self.profile["last_ip"] = ip
            self.save()
        return ip

    def _run_cmd(self, cmd: list[str]) -> dict[str, Any]:
        try:
            done = self.runner(cmd)
        except FileNotFoundError:
            return {"ok": False, "error": f"Không có lệnh {cmd[0]}.", "ip": ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "ip": ""}
        code = getattr(done, "returncode", 0)
        if code:
            text = str(getattr(done, "stderr", "") or getattr(done, "stdout", "") or f"mã {code}")
            return {"ok": False, "error": text.strip()[:200], "ip": ""}
        return {"ok": True, "error": "", "ip": ""}

    def _start_child(self, cmd: list[str]) -> dict[str, Any]:
        try:
            self._child = self.runner(cmd, background=True)
        except FileNotFoundError:
            return {"ok": False, "error": f"Không có lệnh {cmd[0]}.", "ip": ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "ip": ""}
        return {"ok": True, "error": "", "ip": ""}

    def _stop_child(self) -> None:
        child = self._child
        self._child = None
        if child is None:
            return
        kill = getattr(child, "kill", None)
        if callable(kill):
            try:
                kill()
            except Exception:
                return


def _run(cmd: list[str], background: bool = False) -> Any:
    flags = 0
    if sys.platform == "win32":
        flags = _CREATE_NO_WINDOW
    if background:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=40,
        creationflags=flags,
    )


def _base_name(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    return Path(text).stem[:_NAME_MAX]


def _split_command(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    posix = sys.platform != "win32"
    try:
        parts = shlex.split(text, posix=posix)
    except ValueError:
        parts = text.split()
    return [part for part in parts if part][:20]


def _find_exe(names: tuple[str, ...], extra: tuple[str, ...]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in extra:
        if Path(path).is_file():
            return path
    return ""


def _unknown(kind: str) -> None:
    checked: str = kind
    raise ValueError(f"unsupported vpn kind: {checked}")


_WIREGUARD = (
    r"C:\Program Files\WireGuard\wireguard.exe",
    r"C:\Program Files\WireGuard\wg.exe",
)
_OPENVPN = (
    r"C:\Program Files\OpenVPN\bin\openvpn.exe",
    r"C:\Program Files\OpenVPN Connect\OpenVPNConnect.exe",
)
