"""Connect a local VPN when the PC clicker starts."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from pc_macro import free_vpn, privacy

FREE_KINDS = ("free", "warp", "vpngate", "proxy")
VPN_KINDS = ("windows", "wireguard", "openvpn", "command") + FREE_KINDS
DEFAULT_KIND = "windows"
KIND_LABELS = {
    "free": "Tự chọn miễn phí",
    "warp": "Cloudflare WARP",
    "vpngate": "VPN Gate",
    "proxy": "Proxy tĩnh",
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
    if text in {"free", "auto", "mienphi", "all"}:
        return "free"
    if text in {"warp", "cloudflare", "1111", "1.1.1.1"}:
        return "warp"
    if text in {"vpngate", "gate", "tsukuba"}:
        return "vpngate"
    if text in {"proxy", "static", "proxies"}:
        return "proxy"
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
        "baseline_ip": privacy.normalize_ip(data.get("baseline_ip")),
        "last_source": free_vpn.persist_source(data.get("last_source")),
        "privacy_lock": _flag(data, "privacy_lock", kind in FREE_KINDS),
        "block_ipv6": _flag(data, "block_ipv6", kind in FREE_KINDS),
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
    if kind == "free":
        return True
    if kind == "warp":
        return True
    if kind == "vpngate":
        return True
    if kind == "proxy":
        return True
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
    if kind == "free":
        return []
    if kind == "warp":
        return []
    if kind == "vpngate":
        return []
    if kind == "proxy":
        return []
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
    if kind == "free":
        return []
    if kind == "warp":
        return []
    if kind == "vpngate":
        return []
    if kind == "proxy":
        return []
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
    source = free_vpn.source_label(item.get("last_source"))
    if source and item["kind"] in FREE_KINDS:
        kind = source
    before = item.get("baseline_ip") or ""
    if item["connected"] and shown and before and shown != before:
        return f"VPN đã nối ({kind}) · ẩn {before} → {shown}"
    if item["connected"] and shown:
        return f"VPN đã nối ({kind}) · IP {shown}"
    if item["connected"]:
        return f"VPN đã nối ({kind})."
    if item["privacy_lock"] and item["enabled"] and profile_ready(item):
        return f"Ẩn IP bật ({kind}) — chưa nối thì không chạy gõ số."
    if item["enabled"] and profile_ready(item):
        return f"VPN sẵn sàng ({kind}) — sẽ nối khi mở phần mềm."
    if item["enabled"]:
        return "VPN đang bật nhưng chưa đủ tên / file cấu hình."
    if item["privacy_lock"]:
        return "Ẩn IP bật — phải bật VPN rồi nối, không chạy trên địa chỉ thật."
    return "VPN tắt — đánh dấu Bật VPN khi mở phần mềm để nối tự động."


class VpnClient:
    """Load, connect, and drop a local VPN for this PC session."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        runner: Runner | None = None,
        ip_fetch: Callable[[], str] | None = None,
        fetcher: Callable[[str], str] | None = None,
        tools: dict[str, str] | None = None,
        servers: list[dict[str, Any]] | None = None,
        proxies: list[dict[str, str]] | None = None,
        ip6_fetch: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else vpn_path()
        self.runner = runner or _run
        self.ip_fetch = ip_fetch or fetch_public_ip
        self.ip6_fetch = ip6_fetch or privacy.fetch_ipv6
        self.fetcher = fetcher
        self.tools = tools
        self.servers = servers
        self.proxies = proxies
        self.profile = sanitize_profile(None)
        self._child: Any = None
        self._privacy_applied = False
        self._leak_at = 0.0
        self._leak_text = ""
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
            keep_source = self.profile.get("last_source") or ""
            keep_baseline = self.profile.get("baseline_ip") or ""
            incoming = sanitize_profile(raw)
            if not incoming["password"] and keep_password:
                incoming["password"] = keep_password
            if not incoming["last_source"] and keep_source:
                incoming["last_source"] = keep_source
            if not incoming["baseline_ip"] and keep_baseline:
                incoming["baseline_ip"] = keep_baseline
            self.profile = incoming
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.profile

    def connect(self) -> dict[str, Any]:
        self._begin_privacy()
        if self.profile["kind"] in FREE_KINDS:
            return self._connect_free()
        if not profile_ready(self.profile):
            self._restore_privacy()
            return {"ok": False, "error": "Chưa đủ tên VPN hoặc file cấu hình.", "ip": ""}
        cmd = connect_command(self.profile)
        if not cmd:
            self._restore_privacy()
            return {"ok": False, "error": "Không tìm thấy chương trình VPN trên máy.", "ip": ""}
        if self.profile["kind"] == "openvpn":
            result = self._start_child(cmd)
        else:
            result = self._run_cmd(cmd)
        if not result["ok"]:
            self._restore_privacy()
            return result
        return self._mark_connected()

    def disconnect(self) -> dict[str, Any]:
        error = ""
        if self.profile["kind"] in FREE_KINDS:
            result = self._disconnect_free()
            if not result["ok"]:
                error = result["error"]
        else:
            cmd = disconnect_command(self.profile)
            if cmd:
                result = self._run_cmd(cmd)
                if not result["ok"]:
                    error = result["error"]
        self._stop_child()
        self._restore_privacy()
        self.profile["connected"] = False
        self.save()
        if error:
            return {"ok": False, "error": error, "ip": self.profile.get("last_ip") or ""}
        return {"ok": True, "error": "", "ip": self.profile.get("last_ip") or ""}

    def guard(self) -> dict[str, Any]:
        item = self.profile
        if not item.get("privacy_lock"):
            if item.get("enabled") and not item.get("connected") and profile_ready(item):
                result = self.prepare()
                result["blocked"] = False
                return result
            return {"ok": True, "error": "", "ip": item.get("last_ip") or "", "blocked": False}
        if not item.get("enabled"):
            return {
                "ok": False,
                "error": "Chế độ ẩn IP: bật VPN rồi nối trước khi chạy — không dùng địa chỉ thật.",
                "ip": "",
                "blocked": True,
            }
        if not item.get("connected"):
            result = self.prepare()
            result["blocked"] = not result["ok"]
            return result
        leak = self.check_leak(force=True)
        if leak:
            return {"ok": False, "error": leak, "ip": item.get("last_ip") or "", "blocked": True}
        return {"ok": True, "error": "", "ip": item.get("last_ip") or "", "blocked": False}

    def check_leak(self, force: bool = False) -> str:
        if not self.profile.get("privacy_lock"):
            self._leak_text = ""
            return ""
        now = time.monotonic()
        if not force and self._leak_at and now - self._leak_at < 20:
            return self._leak_text
        current = self._snapshot_ip()
        ipv6 = ""
        if self.profile.get("block_ipv6"):
            ipv6 = self._snapshot_ip6()
        text = privacy.leak_message(
            str(self.profile.get("baseline_ip") or ""),
            current,
            ipv6,
            block_ipv6=bool(self.profile.get("block_ipv6")),
        )
        self._leak_at = now
        self._leak_text = text
        if text:
            self.profile["connected"] = False
            self.save()
            return text
        if current:
            self.profile["last_ip"] = current
            self.save()
        return ""

    def prepare(self) -> dict[str, Any]:
        """Harden DNS/WebRTC, install WARP if missing, then connect."""
        self._install_warp()
        return self.connect()

    def inspect(self) -> dict[str, Any]:
        current = self._snapshot_ip()
        ipv6 = self._snapshot_ip6()
        baseline = str(self.profile.get("baseline_ip") or "")
        check = privacy.assess(
            baseline,
            current,
            ipv6,
            privacy_lock=bool(self.profile.get("privacy_lock")),
            block_ipv6=bool(self.profile.get("block_ipv6")),
        )
        hidden = bool(
            current
            and baseline
            and current != baseline
            and check["ok"]
            and self.profile.get("connected")
        )
        report = {
            "ok": hidden,
            "error": "" if hidden else (check["error"] or "Chưa ẩn được địa chỉ."),
            "ip": current,
            "baseline": baseline,
            "ipv6": ipv6,
            "source": free_vpn.persist_source(self.profile.get("last_source")),
            "connected": bool(self.profile.get("connected")),
        }
        report["label"] = privacy.inspect_label(report)
        return report

    def open_private_browser(self) -> dict[str, Any]:
        exe = ""
        edge = False
        if self.tools:
            if self.tools.get("chrome"):
                exe = self.tools["chrome"]
            elif self.tools.get("edge"):
                exe = self.tools["edge"]
                edge = True
        if not exe:
            for path in privacy.chrome_paths():
                if Path(path).is_file():
                    exe = path
                    break
        if not exe:
            for path in privacy.edge_paths():
                if Path(path).is_file():
                    exe = path
                    edge = True
                    break
        if not exe:
            return {"ok": False, "error": "Không tìm thấy Chrome hoặc Edge.", "ip": ""}
        cmd = privacy.browser_command(exe, edge=edge)
        try:
            self.runner(cmd, background=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "ip": ""}
        return {"ok": True, "error": "", "ip": self.profile.get("last_ip") or ""}

    def autostart(self) -> dict[str, Any] | None:
        if not self.profile["enabled"]:
            return None
        return self.prepare()

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

    def _connect_free(self) -> dict[str, Any]:
        tools = self._discover_tools()
        country = free_vpn.country_code(self.profile.get("name"))
        servers = self.servers
        if servers is None and "vpngate" in free_vpn.source_order(self.profile["kind"]):
            try:
                text = free_vpn.fetch_text(free_vpn.VPNGATE_URL, opener=self.fetcher)
                servers = free_vpn.pick_servers(free_vpn.parse_vpngate(text), country)
            except Exception:
                servers = []
        proxies = self.proxies
        if proxies is None:
            proxies = free_vpn.load_proxy_lines(free_vpn.proxies_path())
        attempts = free_vpn.build_attempts(
            kind=self.profile["kind"],
            country=country,
            tools=tools,
            servers=servers or [],
            proxies=proxies or [],
        )
        if not attempts:
            self._restore_privacy()
            return {
                "ok": False,
                "error": "Không tìm thấy nguồn VPN miễn phí (WARP, Proton, Windscribe, VPN Gate, proxy tĩnh).",
                "ip": "",
            }
        errors: list[str] = []
        for step in attempts:
            result = self._run_attempt(step, tools)
            if result["ok"]:
                self.profile["last_source"] = free_vpn.persist_source(step.get("source"))
                return self._mark_connected()
            errors.append(f"{step.get('source')}: {result['error']}")
        self._restore_privacy()
        return {"ok": False, "error": " / ".join(errors)[:200], "ip": ""}

    def _disconnect_free(self) -> dict[str, Any]:
        source = free_vpn.persist_source(self.profile.get("last_source"))
        tools = self._discover_tools()
        commands: list[list[str]] = []
        if source in {"", "warp"} and tools.get("warp"):
            commands.append(free_vpn.warp_disconnect_command(tools["warp"]))
        if source in {"", "proton"} and tools.get("proton"):
            commands.append(free_vpn.proton_disconnect_command(tools["proton"]))
        if source in {"", "windscribe"} and tools.get("windscribe"):
            commands.append(free_vpn.windscribe_disconnect_command(tools["windscribe"]))
        if source in {"", "vpngate"}:
            commands.append(free_vpn.l2tp_disconnect_command())
        if source in {"", "proxy"}:
            commands.extend(free_vpn.proxy_disable_commands())
        error = ""
        for cmd in commands:
            result = self._run_cmd(cmd)
            if not result["ok"] and not error:
                error = result["error"]
        if error:
            return {"ok": False, "error": error, "ip": self.profile.get("last_ip") or ""}
        return {"ok": True, "error": "", "ip": self.profile.get("last_ip") or ""}

    def _run_attempt(self, step: dict[str, Any], tools: dict[str, str]) -> dict[str, Any]:
        source = str(step.get("source") or "")
        if step.get("mode") == "openvpn":
            config = str(step.get("config") or "")
            exe = tools.get("openvpn") or ""
            if not config or not exe:
                return {"ok": False, "error": "Thiếu OpenVPN hoặc file cấu hình.", "ip": ""}
            dest = self.path.parent / "vpngate.ovpn"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(config, encoding="utf-8")
            return self._start_child([exe, "--config", str(dest)])
        commands = list(step.get("commands") or [])
        if not commands:
            return {"ok": False, "error": "Không có lệnh nối.", "ip": ""}
        soft = 0
        if source == "warp":
            soft = 1
        elif step.get("mode") == "l2tp":
            soft = max(0, len(commands) - 1)
        last = {"ok": False, "error": "Không nối được.", "ip": ""}
        for index, cmd in enumerate(commands):
            last = self._run_cmd(cmd)
            if not last["ok"] and index >= soft:
                return last
        if last["ok"]:
            return last
        return {"ok": True, "error": "", "ip": ""}

    def _mark_connected(self) -> dict[str, Any]:
        ip = self._snapshot_ip()
        ipv6 = self._snapshot_ip6() if self.profile.get("block_ipv6") else ""
        check = privacy.assess(
            str(self.profile.get("baseline_ip") or ""),
            ip,
            ipv6,
            privacy_lock=bool(self.profile.get("privacy_lock")),
            block_ipv6=bool(self.profile.get("block_ipv6")),
        )
        if not check["ok"]:
            self._rollback()
            return {"ok": False, "error": check["error"], "ip": ip}
        if self._privacy_applied:
            self._run_cmd(["ipconfig", "/flushdns"])
        self.profile["connected"] = True
        if ip:
            self.profile["last_ip"] = ip
        self.save()
        return {"ok": True, "error": "", "ip": ip}

    def _begin_privacy(self) -> None:
        if not self.profile.get("privacy_lock") and not self.profile.get("block_ipv6"):
            return
        baseline = self._snapshot_ip()
        if baseline:
            self.profile["baseline_ip"] = baseline
        self._apply_privacy()

    def _install_warp(self) -> None:
        if self.profile["kind"] not in FREE_KINDS:
            return
        if self._discover_tools().get("warp"):
            return
        if not self._privacy_os():
            return
        for cmd in privacy.warp_install_commands():
            self._run_cmd(cmd)

    def _snapshot_ip(self) -> str:
        try:
            return privacy.normalize_ip(self.ip_fetch() or "")
        except Exception:
            return ""

    def _snapshot_ip6(self) -> str:
        try:
            return privacy.normalize_ip(self.ip6_fetch() or "")
        except Exception:
            return ""

    def _privacy_os(self) -> bool:
        return sys.platform == "win32" or self.tools is not None

    def _apply_privacy(self) -> None:
        if self._privacy_applied or not self._privacy_os():
            return
        for cmd in privacy.harden_commands():
            self._run_cmd(cmd)
        self._privacy_applied = True

    def _restore_privacy(self) -> None:
        if not self._privacy_applied:
            return
        for cmd in privacy.restore_commands():
            self._run_cmd(cmd)
        self._privacy_applied = False

    def _rollback(self) -> None:
        if self.profile["kind"] in FREE_KINDS:
            self._disconnect_free()
        else:
            cmd = disconnect_command(self.profile)
            if cmd:
                self._run_cmd(cmd)
        self._stop_child()
        self._restore_privacy()
        self.profile["connected"] = False
        self.save()

    def _discover_tools(self) -> dict[str, str]:
        if self.tools is not None:
            return dict(self.tools)
        found: dict[str, str] = {}
        warp = _find_exe(("warp-cli",), _WARP)
        if warp:
            found["warp"] = warp
        proton = _find_exe(("protonvpn-cli", "protonvpn"), ())
        if proton:
            found["proton"] = proton
        windscribe = _find_exe(("windscribe-cli", "windscribe"), ())
        if windscribe:
            found["windscribe"] = windscribe
        openvpn = _find_exe(("openvpn",), _OPENVPN)
        if openvpn:
            found["openvpn"] = openvpn
        rasdial = _find_exe(("rasdial",), ())
        if rasdial:
            found["rasdial"] = rasdial
        powershell = _find_exe(("powershell", "pwsh"), ())
        if powershell:
            found["powershell"] = powershell
        return found

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


def _flag(data: dict[str, Any], key: str, default: bool) -> bool:
    if key in data:
        return bool(data.get(key))
    return default


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
_WARP = (
    r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe",
    r"C:\Program Files (x86)\Cloudflare\Cloudflare WARP\warp-cli.exe",
)
