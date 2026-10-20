from __future__ import annotations

import subprocess
from pathlib import Path


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(self.root), "symbolic-ref", "HEAD", "refs/heads/main"],
                check=True,
                capture_output=True,
            )
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "coderd@localhost"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "coderd"], check=True)
        ignore = self.root / ".gitignore"
        marker = "learn/cache/"
        if ignore.is_file():
            text = ignore.read_text(encoding="utf-8", errors="replace")
            if marker not in text:
                ignore.write_text(text.rstrip() + "\n" + marker + "\n", encoding="utf-8")
        else:
            ignore.write_text(marker + "\n", encoding="utf-8")

    def resolve(self, raw: str) -> Path:
        if not str(raw or "").strip():
            raise PermissionError("đường dẫn trống")
        root = self.root.resolve()
        path = Path(raw)
        candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            raise PermissionError("ngoài workspace")
        return candidate
