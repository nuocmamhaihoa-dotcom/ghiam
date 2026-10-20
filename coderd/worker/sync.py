from __future__ import annotations

import subprocess
from pathlib import Path

from worker.workspace import Workspace


def publish(workspace: Workspace, post) -> bool:
    workspace.ensure()
    root = str(workspace.root)
    status = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True, check=True)
    if not status.stdout.strip():
        return False
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-m", "coderd: cập nhật workspace"], check=True)
    bundle = subprocess.check_output(["git", "-C", root, "bundle", "create", "-", "main"])
    post(bundle)
    return True
