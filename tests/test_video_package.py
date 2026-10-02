"""Gói mã PC phải chứa mọi mô-đun mà chương trình đọc video nạp. Thiếu một tệp là PC lỗi ngay sau khi tự cập nhật."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from control_plane.video_package import _SOURCES

ROOT = Path(__file__).resolve().parents[1]
_OURS = ("control_plane", "pc_agent")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _closure(entry: Path) -> set[str]:
    files: set[str] = set()
    pending = [entry]
    while pending:
        path = pending.pop()
        for name in _imported_modules(path):
            if name.split(".")[0] not in _OURS:
                continue
            candidate = ROOT / (name.replace(".", "/") + ".py")
            relative = candidate.relative_to(ROOT).as_posix()
            if candidate.is_file() and relative not in files:
                files.add(relative)
                pending.append(candidate)
    return files


class VideoPackageSourcesTests(unittest.TestCase):
    def test_every_module_the_worker_imports_ships_in_the_package(self) -> None:
        needed = _closure(ROOT / "pc_agent" / "video_worker.py")
        self.assertIn("control_plane/stage_timing.py", needed)
        self.assertIn("pc_agent/pc_power.py", needed)
        missing = sorted(needed - set(_SOURCES))
        self.assertEqual(missing, [], f"Gói PC thiếu: {missing}")

    def test_every_listed_source_exists(self) -> None:
        absent = [name for name in _SOURCES if not (ROOT / name).is_file()]
        self.assertEqual(absent, [])


if __name__ == "__main__":
    unittest.main()
