from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "tiktok_osint"
BANNED_SNIPPETS = (
    "commit/contact",
    "upload_contacts",
    "find_user_by_phone",
    "/passport/",
)


def test_sync_package_does_not_call_network_clients() -> None:
    for path in (PACKAGE / "sync").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert "httpx" not in names
            assert "playwright" not in names
            assert "requests" not in names


def test_source_has_no_private_contact_endpoint() -> None:
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for snippet in BANNED_SNIPPETS:
            assert snippet not in text, f"{path} contains {snippet}"
