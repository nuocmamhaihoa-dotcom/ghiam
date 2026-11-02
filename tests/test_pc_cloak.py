"""Machine address is salted and sealed so disk/hub cannot be reversed."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pc_macro.cloak import (
    CloakVault,
    SEAL_PREFIX,
    collect_marks,
    hold_address,
    is_sealed,
    layer_names,
    mask_address,
    public_token,
)
from pc_macro.vpn import VpnClient


class _Done:
    returncode = 0
    stderr = ""
    stdout = ""


class CloakVaultTests(unittest.TestCase):
    def test_seal_roundtrip_and_one_way_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = CloakVault(Path(tmp) / "cloak")
            sealed = vault.seal("203.0.113.10")
            self.assertTrue(is_sealed(sealed))
            self.assertTrue(sealed.startswith(SEAL_PREFIX))
            self.assertNotIn("203.0.113.10", sealed)
            self.assertEqual(vault.open(sealed), "203.0.113.10")
            token = vault.token("203.0.113.10")
            self.assertEqual(len(token), 64)
            self.assertTrue(all(ch in "0123456789abcdef" for ch in token))
            self.assertNotIn("203", token)
            other = CloakVault(Path(tmp) / "other", salt=b"x" * 32, master=b"y" * 32)
            self.assertEqual(other.open(sealed), "")
            self.assertNotEqual(other.token("203.0.113.10"), token)
            again = CloakVault(Path(tmp) / "cloak")
            self.assertEqual(again.open(sealed), "203.0.113.10")
            self.assertEqual(again.token("203.0.113.10"), token)

    def test_hold_mask_and_layers(self) -> None:
        self.assertEqual(hold_address(" 198.51.100.8\n"), "198.51.100.8")
        sealed = "pcm1." + "A" * 40
        self.assertEqual(hold_address(sealed), sealed)
        self.assertEqual(mask_address("198.51.100.8"), "•••.•••.100.8")
        self.assertEqual(mask_address(sealed), "đã muối")
        names = " ".join(layer_names())
        self.assertIn("Muối", names)
        self.assertIn("Mật mã", names)
        self.assertIn("Một chiều", names)
        marks = collect_marks()
        self.assertTrue(isinstance(marks, list))
        with tempfile.TemporaryDirectory() as tmp:
            vault = CloakVault(Path(tmp))
            token = public_token(vault, "198.51.100.8")
            self.assertEqual(len(token), 64)
            for mark in marks:
                self.assertNotIn(mark, token)


class CloakOnDiskTests(unittest.TestCase):
    def test_vpn_file_has_no_plaintext_ip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vpn.json"
            client = VpnClient(path, runner=lambda *_a, **_k: _Done(), ip_fetch=lambda: "198.51.100.8")
            client.save({"kind": "windows", "name": "Office", "enabled": True, "cloak_lock": True})
            result = client.connect()
            self.assertTrue(result["ok"])
            self.assertEqual(client.profile["last_ip"], "198.51.100.8")
            saved = json.loads(path.read_text(encoding="utf-8"))
            dumped = json.dumps(saved)
            self.assertNotIn("198.51.100.8", dumped)
            self.assertTrue(is_sealed(saved["last_ip"]))
            self.assertTrue(saved["cloak_lock"])
            self.assertEqual(len(saved.get("cloak_token") or ""), 64)
            again = VpnClient(path, runner=lambda *_a, **_k: _Done(), ip_fetch=lambda: "198.51.100.8")
            self.assertEqual(again.profile["last_ip"], "198.51.100.8")
            self.assertNotIn("198.51.100.8", again.identity_token())


if __name__ == "__main__":
    unittest.main()
