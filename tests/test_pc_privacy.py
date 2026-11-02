"""Kill switch and leak checks must stop the clicker on the real address."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pc_macro.privacy import (
    assess,
    browser_command,
    harden_commands,
    inspect_label,
    ips_equal,
    leak_message,
    normalize_ip,
    warp_install_commands,
)
from pc_macro.vpn import VpnClient, sanitize_profile, status_label


class _Done:
    returncode = 0
    stderr = ""
    stdout = ""


def _shift_ip(before: str = "203.0.113.1", after: str = "198.51.100.8"):
    state = {"n": 0}

    def fetch() -> str:
        state["n"] += 1
        return before if state["n"] == 1 else after

    return fetch


class PrivacyHelpersTests(unittest.TestCase):
    def test_ips_and_assess(self) -> None:
        self.assertEqual(normalize_ip(" 198.51.100.8\n"), "198.51.100.8")
        self.assertTrue(ips_equal("198.51.100.8", "198.51.100.8"))
        self.assertFalse(ips_equal("198.51.100.8", "203.0.113.1"))
        self.assertTrue(assess("1.1.1.1", "1.1.1.1", privacy_lock=False, block_ipv6=False)["ok"])
        same = assess("1.1.1.1", "1.1.1.1", privacy_lock=True, block_ipv6=False)
        self.assertFalse(same["ok"])
        self.assertIn("không đổi", same["error"])
        changed = assess("1.1.1.1", "9.9.9.9", privacy_lock=True, block_ipv6=True)
        self.assertTrue(changed["ok"])
        leaked6 = assess("1.1.1.1", "9.9.9.9", "2001:db8::1", privacy_lock=True, block_ipv6=True)
        self.assertFalse(leaked6["ok"])
        self.assertIn("IPv6", leaked6["error"])
        self.assertIn("địa chỉ thật", leak_message("1.1.1.1", "1.1.1.1", block_ipv6=False))
        text = " ".join(" ".join(cmd) for cmd in harden_commands())
        self.assertIn("teredo", text)
        self.assertIn("WebRtcIPHandling", text)
        self.assertIn("1.1.1.1", text)
        self.assertIn("9.9.9.9", text)
        self.assertIn("Firefox", text)
        self.assertIn("media.peerconnection.ice.no_host", text)
        self.assertIn("flushdns", text)
        winget = " ".join(" ".join(cmd) for cmd in warp_install_commands())
        self.assertIn("Cloudflare.Warp", winget)
        chrome = browser_command(r"C:\chrome.exe")
        self.assertIn("--incognito", chrome)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", chrome)
        self.assertNotIn("--inprivate", chrome)
        edge = browser_command(r"C:\msedge.exe", edge=True)
        self.assertIn("--inprivate", edge)
        self.assertNotIn("--incognito", edge)
        hidden = inspect_label(
            {"ok": True, "ip": "198.51.100.8", "baseline": "203.0.113.1", "ipv6": "", "source": "warp"}
        )
        self.assertIn("Đã ẩn địa chỉ", hidden)
        self.assertIn("203.0.113.1", hidden)

    def test_free_defaults_lock(self) -> None:
        free = sanitize_profile({"kind": "free"})
        self.assertTrue(free["privacy_lock"])
        self.assertTrue(free["block_ipv6"])
        windows = sanitize_profile({"kind": "windows", "name": "Office"})
        self.assertFalse(windows["privacy_lock"])
        self.assertIn("Ẩn IP", status_label({"kind": "free", "privacy_lock": True, "enabled": False}))


class PrivacyClientTests(unittest.TestCase):
    def test_same_ip_rolls_back(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], background: bool = False) -> _Done:
            calls.append(list(cmd))
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=lambda: "203.0.113.1",
                ip6_fetch=lambda: "",
                tools={"warp": "warp-cli"},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free", "enabled": True})
            result = client.connect()
            self.assertFalse(result["ok"])
            self.assertIn("không đổi", result["error"])
            self.assertFalse(client.profile["connected"])
            self.assertTrue(any(cmd[:2] == ["warp-cli", "disconnect"] for cmd in calls))

    def test_guard_blocks_without_vpn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(Path(tmp) / "vpn.json", runner=lambda *_a, **_k: _Done(), ip_fetch=lambda: "")
            client.save({"kind": "free", "enabled": False, "privacy_lock": True})
            result = client.guard()
            self.assertFalse(result["ok"])
            self.assertTrue(result["blocked"])
            self.assertIn("ẩn", result["error"].lower())
            self.assertIn("địa chỉ thật", result["error"])

    def test_changed_ip_and_mid_run_leak(self) -> None:
        box = {"ip": "203.0.113.1"}

        def fetch() -> str:
            return box["ip"]

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=lambda *_a, **_k: _Done(),
                ip_fetch=fetch,
                ip6_fetch=lambda: "",
                tools={"warp": "warp-cli"},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free", "enabled": True})

            def connect_then_flip(cmd: list[str], background: bool = False) -> _Done:
                if cmd[:2] == ["warp-cli", "connect"]:
                    box["ip"] = "198.51.100.8"
                return _Done()

            client.runner = connect_then_flip
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(client.profile["baseline_ip"], "203.0.113.1")
            self.assertEqual(result["ip"], "198.51.100.8")
            box["ip"] = "203.0.113.1"
            leak = client.check_leak(force=True)
            self.assertIn("địa chỉ thật", leak)
            self.assertFalse(client.profile["connected"])

    def test_prepare_installs_warp_and_inspect(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], background: bool = False) -> _Done:
            calls.append(list(cmd))
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=_shift_ip(),
                ip6_fetch=lambda: "",
                tools={},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free", "enabled": True})
            result = client.prepare()
            self.assertFalse(result["ok"])
            joined = [" ".join(cmd) for cmd in calls]
            self.assertTrue(any("Cloudflare.Warp" in text for text in joined))
            self.assertTrue(any("flushdns" in text for text in joined))
            self.assertTrue(any("Preferences" in text for text in joined))
            report = client.inspect()
            self.assertFalse(report["ok"])
            self.assertTrue(report["label"])

    def test_private_browser_and_hidden_inspect(self) -> None:
        calls: list[tuple[list[str], bool]] = []
        box = {"ip": "203.0.113.1"}

        def fetch() -> str:
            return box["ip"]

        def runner(cmd: list[str], background: bool = False) -> _Done:
            calls.append((list(cmd), background))
            if cmd[:2] == ["warp-cli", "connect"]:
                box["ip"] = "198.51.100.8"
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=fetch,
                ip6_fetch=lambda: "",
                tools={"warp": "warp-cli", "chrome": r"C:\chrome.exe"},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free", "enabled": True})
            result = client.prepare()
            self.assertTrue(result["ok"])
            report = client.inspect()
            self.assertTrue(report["ok"])
            self.assertIn("Đã ẩn địa chỉ", report["label"])
            opened = client.open_private_browser()
            self.assertTrue(opened["ok"])
            browser = next(cmd for cmd, background in calls if cmd and cmd[0] == r"C:\chrome.exe")
            self.assertIn("--incognito", browser)
            self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", browser)
            self.assertNotIn("--inprivate", browser)
            self.assertTrue(any(background for cmd, background in calls if cmd and cmd[0] == r"C:\chrome.exe"))


if __name__ == "__main__":
    unittest.main()
