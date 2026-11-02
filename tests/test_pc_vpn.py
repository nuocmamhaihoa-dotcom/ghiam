"""Local VPN profile for the PC clicker stays on this machine."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pc_macro.vpn import (
    VpnClient,
    connect_command,
    disconnect_command,
    fetch_public_ip,
    normalize_kind,
    profile_ready,
    public_profile,
    sanitize_profile,
    status_label,
    vpn_path,
)


class _Done:
    returncode = 0
    stderr = ""
    stdout = ""

    def kill(self) -> None:
        self.killed = True


class _Reply:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class SanitizeVpnTests(unittest.TestCase):
    def test_aliases_and_ready(self) -> None:
        self.assertEqual(normalize_kind("rasdial"), "windows")
        self.assertEqual(normalize_kind("wg"), "wireguard")
        self.assertEqual(normalize_kind("ovpn"), "openvpn")
        self.assertEqual(normalize_kind("custom"), "command")
        self.assertEqual(normalize_kind("nope"), "windows")
        self.assertTrue(profile_ready({"kind": "windows", "name": "Office"}))
        self.assertFalse(profile_ready({"kind": "windows", "name": ""}))
        self.assertTrue(profile_ready({"kind": "wireguard", "config": r"C:\t.conf"}))
        self.assertTrue(profile_ready({"kind": "openvpn", "config": r"C:\t.ovpn"}))
        self.assertFalse(profile_ready({"kind": "openvpn", "name": "only"}))
        self.assertTrue(profile_ready({"kind": "command", "connect_cmd": "echo up"}))
        item = sanitize_profile({"kind": "command", "config": "wg-quick up home"})
        self.assertEqual(item["connect_cmd"], "wg-quick up home")
        named = sanitize_profile({"kind": "wireguard", "config": r"C:\Tunnels\home.conf"})
        self.assertEqual(named["name"], "home")

    def test_commands_and_redacted_password(self) -> None:
        self.assertEqual(
            connect_command({"kind": "windows", "name": "Office", "username": "u", "password": "secret"}),
            ["rasdial", "Office", "u", "secret"],
        )
        self.assertEqual(disconnect_command({"kind": "windows", "name": "Office"}), ["rasdial", "Office", "/disconnect"])
        self.assertEqual(connect_command({"kind": "command", "connect_cmd": "echo up"}), ["echo", "up"])
        self.assertEqual(disconnect_command({"kind": "command", "disconnect_cmd": "echo down"}), ["echo", "down"])
        self.assertEqual(disconnect_command({"kind": "openvpn", "config": "x.ovpn"}), [])
        with patch("pc_macro.vpn._find_exe", return_value=r"C:\openvpn.exe"):
            self.assertEqual(
                connect_command({"kind": "openvpn", "config": r"C:\t.ovpn"}),
                [r"C:\openvpn.exe", "--config", r"C:\t.ovpn"],
            )
        with patch("pc_macro.vpn._find_exe", return_value=r"C:\wireguard.exe"):
            self.assertEqual(
                connect_command({"kind": "wireguard", "config": r"C:\home.conf"}),
                [r"C:\wireguard.exe", "/installtunnelservice", r"C:\home.conf"],
            )
            self.assertEqual(
                disconnect_command({"kind": "wireguard", "config": r"C:\home.conf"}),
                [r"C:\wireguard.exe", "/uninstalltunnelservice", "home"],
            )
        shown = public_profile({"kind": "windows", "name": "Office", "password": "secret"})
        self.assertEqual(shown["password"], "")
        self.assertTrue(shown["has_password"])
        self.assertNotIn("secret", json.dumps(shown))

    def test_status_and_path(self) -> None:
        self.assertIn("tắt", status_label({"enabled": False}))
        self.assertIn("sẵn sàng", status_label({"enabled": True, "kind": "windows", "name": "Office"}))
        self.assertIn("IP 1.2.3.4", status_label({"connected": True, "kind": "windows", "name": "Office"}, "1.2.3.4"))
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "mine.json"
            os.environ["PC_MACRO_VPN"] = str(custom)
            try:
                self.assertEqual(vpn_path(), custom)
            finally:
                os.environ.pop("PC_MACRO_VPN", None)
            store = Path(tmp) / "macros"
            os.environ["PC_MACRO_DIR"] = str(store)
            try:
                self.assertEqual(vpn_path(), store.parent / "vpn.json")
            finally:
                os.environ.pop("PC_MACRO_DIR", None)

    def test_fetch_public_ip(self) -> None:
        def opener(_req: object, timeout: int = 8) -> _Reply:
            self.assertGreater(timeout, 0)
            return _Reply(b"203.0.113.10\n")

        self.assertEqual(fetch_public_ip(opener), "203.0.113.10")


class VpnClientTests(unittest.TestCase):
    def test_connect_keeps_password_and_autostart(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], background: bool = False) -> _Done:
            calls.append(list(cmd))
            self.assertFalse(background)
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vpn.json"
            client = VpnClient(path, runner=runner, ip_fetch=lambda: "198.51.100.8")
            client.save(
                {
                    "enabled": True,
                    "disconnect_on_exit": True,
                    "kind": "windows",
                    "name": "Office",
                    "username": "u",
                    "password": "secret",
                }
            )
            client.save({"enabled": True, "kind": "windows", "name": "Office", "username": "u", "password": ""})
            self.assertEqual(client.profile["password"], "secret")
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(result["ip"], "198.51.100.8")
            self.assertEqual(calls[0], ["rasdial", "Office", "u", "secret"])
            self.assertTrue(client.profile["connected"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_ip"], "198.51.100.8")
            self.assertEqual(saved["password"], "secret")
            dropped = client.disconnect()
            self.assertTrue(dropped["ok"])
            self.assertEqual(calls[1], ["rasdial", "Office", "/disconnect"])
            self.assertFalse(client.profile["connected"])

            client.profile["connected"] = True
            client.profile["disconnect_on_exit"] = True
            client.shutdown()
            self.assertFalse(client.profile["connected"])

            off = VpnClient(path, runner=runner, ip_fetch=lambda: "")
            off.profile["enabled"] = False
            self.assertIsNone(off.autostart())
            off.profile["enabled"] = True
            off.profile["connected"] = False
            started = off.autostart()
            assert started is not None
            self.assertTrue(started["ok"])

    def test_openvpn_starts_child(self) -> None:
        child = _Done()
        calls: list[tuple[list[str], bool]] = []

        def runner(cmd: list[str], background: bool = False) -> _Done:
            calls.append((list(cmd), background))
            return child

        with tempfile.TemporaryDirectory() as tmp, patch("pc_macro.vpn._find_exe", return_value="openvpn"):
            client = VpnClient(Path(tmp) / "vpn.json", runner=runner, ip_fetch=lambda: "")
            client.save({"kind": "openvpn", "config": "home.ovpn"})
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(calls[0], (["openvpn", "--config", "home.ovpn"], True))
            client.disconnect()
            self.assertTrue(child.killed)

    def test_missing_profile_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(Path(tmp) / "vpn.json", runner=lambda *_a, **_k: _Done(), ip_fetch=lambda: "")
            result = client.connect()
            self.assertFalse(result["ok"])
            self.assertIn("tên VPN", result["error"])


if __name__ == "__main__":
    unittest.main()
