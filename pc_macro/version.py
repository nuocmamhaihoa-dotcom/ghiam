"""Bản phần mềm PC và chữ ký gói — hub và máy dùng chung hàm này."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

PC_MACRO_NAME = "pc-macro"
PC_MACRO_BUILD = 2026110206
DEFAULT_HUB = "http://103.200.20.245:8088"
REQUIRES = ["pynput"]
_EXTRA_NAMES = ("CHAY-O-DAY.cmd", "Chay.cmd", "Cai-dat.ps1", "README.txt")


def digest_files(files: dict[str, bytes]) -> str:
    hashed = hashlib.sha256()
    for name in sorted(files):
        raw = files[name]
        hashed.update(name.encode("utf-8"))
        hashed.update(b"\0")
        hashed.update(str(len(raw)).encode("ascii"))
        hashed.update(b"\0")
        hashed.update(raw)
        hashed.update(b"\n")
    return hashed.hexdigest()


def read_tree(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    package = Path(root) / "pc_macro"
    if package.is_dir():
        for path in sorted(package.glob("*.py")):
            files[f"pc_macro/{path.name}"] = path.read_bytes()
    for name in _EXTRA_NAMES:
        path = Path(root) / name
        if path.is_file():
            files[name] = path.read_bytes()
    return files


def build_package_files(package_dir: Path, extras: dict[str, str | bytes] | None = None) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    folder = Path(package_dir)
    for path in sorted(folder.glob("*.py")):
        files[f"pc_macro/{path.name}"] = path.read_bytes()
    for name, body in (extras or {}).items():
        files[name] = body.encode("utf-8") if isinstance(body, str) else body
    return files


def package_info(files: dict[str, bytes]) -> dict[str, Any]:
    return {
        "name": PC_MACRO_NAME,
        "build": PC_MACRO_BUILD,
        "digest": digest_files(files),
        "file_count": len(files),
        "files": sorted(files),
        "requires": list(REQUIRES),
        "url": "/tai/pc-macro.zip",
        "hub": DEFAULT_HUB,
    }
