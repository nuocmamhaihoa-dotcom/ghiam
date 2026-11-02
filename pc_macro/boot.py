"""Check that this folder can actually open the PC window."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def install_root() -> Path:
    return Path(__file__).resolve().parents[1]


def checks() -> list[tuple[str, bool, str]]:
    root = install_root()
    package = root / "pc_macro" / "__main__.py"
    rows: list[tuple[str, bool, str]] = [
        ("thu muc phan mem", package.is_file(), str(root)),
        ("python", True, sys.executable or ""),
        (
            "tkinter",
            importlib.util.find_spec("tkinter") is not None,
            "can Python 3.11+ tu python.org, tick tcl/tk",
        ),
        (
            "pynput",
            importlib.util.find_spec("pynput") is not None,
            "pip install pynput",
        ),
    ]
    return rows


def ready() -> bool:
    return all(ok for _name, ok, _detail in checks())


def print_report() -> int:
    rows = checks()
    failed = False
    for name, ok, detail in rows:
        mark = "OK" if ok else "LOI"
        if not ok:
            failed = True
        print(f"{mark}  {name}  {detail}")
    if failed:
        print("Giai nen zip ra Desktop, roi bam dup CHAY-O-DAY.cmd trong thu muc pc-macro.")
        return 1
    print("San sang mo cua so.")
    return 0
