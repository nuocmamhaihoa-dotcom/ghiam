"""Official free VPN sources stay local and skip public config scripts."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from pc_macro.free_vpn import (
    L2TP_NAME,
    L2TP_PASS,
    L2TP_USER,
    build_attempts,
    country_code,
    decode_ovpn,
    load_proxy_lines,
    normalize_source,
    parse_proxy_line,
    parse_vpngate,
    persist_source,
    pick_servers,
    sanitize_ovpn,
    source_order,
)
from pc_macro.vpn import VpnClient, sanitize_profile, status_label


def _shift_ip(before: str = "203.0.113.1", after: str = "198.51.100.8"):
    state = {"n": 0}

    def fetch() -> str:
        state["n"] += 1
        return before if state["n"] == 1 else after

    return fetch


class _Done:
    returncode = 0
    stderr = ""
    stdout = ""

    def kill(self) -> None:
        self.killed = True


class _Fail:
    returncode = 1
    stderr = "fail"
    stdout = ""


def _ovpn_blob() -> str:
    raw = "\n".join(
        [
            "client",
            "dev tun",
            "proto udp",
            "remote 203.0.113.10 1194",
            "script-security 2",
            "up /bin/true",
            "plugin evil.so",
            "<ca>",
            "CERT",
            "</ca>",
        ]
    )
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _csv() -> str:
    return "\n".join(
        [
            "*vpn_servers",
            "#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,OpenVPN_ConfigData_Base64",
            f"jp1,203.0.113.10,90,20,100,Japan,JP,1,{_ovpn_blob()}",
            f"us1,198.51.100.20,10,80,10,United States,US,1,{_ovpn_blob()}",
        ]
    )


class FreeSourceTests(unittest.TestCase):
    def test_source_and_country(self) -> None:
        self.assertEqual(normalize_source("mienphi"), "auto")
        self.assertEqual(normalize_source("1111"), "warp")
        self.assertEqual(normalize_source("gate"), "vpngate")
        self.assertEqual(source_order("free"), ("warp", "proton", "windscribe", "vpngate", "proxy"))
        self.assertEqual(source_order("warp"), ("warp",))
        self.assertEqual(source_order("vpngate"), ("vpngate",))
        self.assertEqual(source_order("proxy"), ("proxy",))
        self.assertEqual(country_code("jp"), "JP")
        self.assertEqual(country_code("Japan"), "")
        self.assertEqual(persist_source("WARP"), "warp")
        self.assertEqual(persist_source("nope"), "")

    def test_proxy_lines(self) -> None:
        self.assertEqual(
            parse_proxy_line("http://u:p@10.0.0.1:8080"),
            {"host": "10.0.0.1", "port": "8080", "username": "u", "password": "p", "server": "10.0.0.1:8080"},
        )
        self.assertEqual(
            parse_proxy_line("10.0.0.2:3128"),
            {"host": "10.0.0.2", "port": "3128", "username": "", "password": "", "server": "10.0.0.2:3128"},
        )
        self.assertEqual(
            parse_proxy_line("10.0.0.3:8000:alice:secret"),
            {
                "host": "10.0.0.3",
                "port": "8000",
                "username": "alice",
                "password": "secret",
                "server": "10.0.0.3:8000",
            },
        )
        self.assertIsNone(parse_proxy_line("# comment"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies_static.txt"
            path.write_text("10.1.1.1:8080\n# skip\nbad\n", encoding="utf-8")
            found = load_proxy_lines(path)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["server"], "10.1.1.1:8080")

    def test_vpngate_and_sanitize(self) -> None:
        rows = parse_vpngate(_csv())
        self.assertEqual([row["country"] for row in rows], ["JP", "US"])
        self.assertEqual(pick_servers(rows, "JP")[0]["ip"], "203.0.113.10")
        raw = decode_ovpn(rows[0]["config"])
        self.assertIn("remote 203.0.113.10 1194", raw)
        clean = sanitize_ovpn(raw)
        self.assertIn("remote 203.0.113.10 1194", clean)
        self.assertIn("<ca>", clean)
        self.assertIn("CERT", clean)
        self.assertNotIn("script-security", clean)
        self.assertNotIn("up /bin/true", clean)
        self.assertNotIn("plugin", clean)
        self.assertEqual(sanitize_ovpn("script-security 2\nup /bin/true\n"), "")

    def test_build_attempts_order(self) -> None:
        steps = build_attempts(
            kind="free",
            country="JP",
            tools={"warp": "warp-cli", "rasdial": "rasdial", "powershell": "powershell", "openvpn": "openvpn"},
            servers=pick_servers(parse_vpngate(_csv()), "JP"),
            proxies=[{"server": "10.0.0.9:8080", "host": "10.0.0.9", "port": "8080", "username": "", "password": ""}],
        )
        sources = [step["source"] for step in steps]
        self.assertEqual(sources[0], "warp")
        self.assertIn("vpngate", sources)
        self.assertEqual(sources[-1], "proxy")
        l2tp = next(step for step in steps if step.get("mode") == "l2tp")
        self.assertEqual(l2tp["commands"][-1], ["rasdial", L2TP_NAME, L2TP_USER, L2TP_PASS])
        ovpn = next(step for step in steps if step.get("mode") == "openvpn")
        self.assertIn("remote 203.0.113.10 1194", ovpn["config"])
        self.assertNotIn("script-security", ovpn["config"])


class FreeVpnClientTests(unittest.TestCase):
    def test_warp_ignores_existing_registration(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], background: bool = False) -> object:
            calls.append(list(cmd))
            self.assertFalse(background)
            if cmd[-1] == "new":
                return _Fail()
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=_shift_ip(),
                ip6_fetch=lambda: "",
                tools={"warp": "warp-cli"},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free", "enabled": True})
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(client.profile["last_source"], "warp")
            self.assertEqual(calls[0], ["warp-cli", "registration", "new"])
            self.assertEqual(calls[1], ["warp-cli", "connect"])
            saved = json.loads((Path(tmp) / "vpn.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["last_source"], "warp")
            client.disconnect()
            self.assertEqual(calls[-1], ["warp-cli", "disconnect"])

    def test_falls_back_to_proxy(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], background: bool = False) -> object:
            calls.append(list(cmd))
            if cmd[0] == "warp-cli":
                return _Fail()
            return _Done()

        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=_shift_ip("203.0.113.1", "203.0.113.50"),
                ip6_fetch=lambda: "",
                tools={"warp": "warp-cli"},
                servers=[],
                proxies=[{"server": "10.0.0.9:8080", "host": "10.0.0.9", "port": "8080", "username": "", "password": ""}],
            )
            client.save({"kind": "free"})
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(client.profile["last_source"], "proxy")
            self.assertTrue(any("10.0.0.9:8080" in " ".join(cmd) for cmd in calls))
            self.assertIn("proxy tĩnh", status_label(client.profile, "203.0.113.50"))

    def test_vpngate_l2tp_and_openvpn(self) -> None:
        calls: list[tuple[list[str], bool]] = []

        def runner(cmd: list[str], background: bool = False) -> object:
            calls.append((list(cmd), background))
            return _Done()

        servers = pick_servers(parse_vpngate(_csv()), "JP")
        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=_shift_ip(),
                ip6_fetch=lambda: "",
                tools={"rasdial": "rasdial", "powershell": "powershell"},
                servers=servers,
                proxies=[],
            )
            client.save({"kind": "vpngate", "name": "JP"})
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(client.profile["last_source"], "vpngate")
            self.assertEqual(calls[-1][0], ["rasdial", L2TP_NAME, L2TP_USER, L2TP_PASS])
            self.assertFalse(calls[-1][1])

            child = VpnClient(
                Path(tmp) / "vpn.json",
                runner=runner,
                ip_fetch=_shift_ip(),
                ip6_fetch=lambda: "",
                tools={"openvpn": "openvpn"},
                servers=servers,
                proxies=[],
            )
            child.save({"kind": "vpngate"})
            opened = child.connect()
            self.assertTrue(opened["ok"])
            dest = Path(tmp) / "vpngate.ovpn"
            self.assertTrue(dest.is_file())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("remote 203.0.113.10 1194", text)
            self.assertNotIn("script-security", text)
            self.assertEqual(calls[-1], (["openvpn", "--config", str(dest)], True))

    def test_missing_free_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = VpnClient(
                Path(tmp) / "vpn.json",
                runner=lambda *_a, **_k: _Done(),
                ip_fetch=lambda: "",
                tools={},
                servers=[],
                proxies=[],
            )
            client.save({"kind": "free"})
            result = client.connect()
            self.assertFalse(result["ok"])
            self.assertIn("miễn phí", result["error"])
            self.assertEqual(sanitize_profile({"kind": "free"}).get("last_source"), "")


if __name__ == "__main__":
    unittest.main()
