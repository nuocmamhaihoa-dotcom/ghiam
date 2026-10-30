from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from worker.workspace import Workspace

CAP = 32768


def schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "Liệt kê một thư mục trong workspace",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Đọc một file trong workspace",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Ghi một file trong workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_cmd",
                "description": "Chạy lệnh với cwd là workspace, tối đa 30 giây",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_cmd(
    command: str,
    workspace: Path,
    cancel: threading.Event,
    procs: dict,
    timeout: float = 30,
) -> dict:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return {"error": "từ chối vì worker đang là root", "exit_code": 126, "stdout": "", "stderr": ""}
    if not command.strip():
        return {"error": "lệnh trống", "exit_code": 126, "stdout": "", "stderr": ""}
    if len(command) > 4000:
        return {"error": "lệnh quá dài", "exit_code": 126, "stdout": "", "stderr": ""}
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    procs["proc"] = proc
    cancelled = False
    timed_out = False
    start = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel.is_set():
                cancelled = True
                _kill(proc)
                break
            if time.monotonic() - start > timeout:
                timed_out = True
                _kill(proc)
                break
            time.sleep(0.05)
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            _kill(proc)
            out, err = proc.communicate(timeout=2)
    finally:
        procs["proc"] = None
    out = out or ""
    err = err or ""
    truncated = False
    if len(out) > CAP:
        out = out[:CAP]
        truncated = True
    if len(err) > CAP:
        err = err[:CAP]
        truncated = True
    if cancelled:
        return {"cancelled": True, "exit_code": -1, "stdout": out, "stderr": err, "truncated": truncated}
    if timed_out:
        return {"error": "quá thời gian", "exit_code": -1, "stdout": out, "stderr": err, "truncated": truncated}
    return {"exit_code": proc.returncode, "stdout": out, "stderr": err, "truncated": truncated}


def run_tool(workspace: Workspace, name: str, args: dict, cancel: threading.Event, procs: dict) -> dict:
    try:
        if name == "list_dir":
            folder = workspace.resolve(str(args.get("path") or "."))
            names = []
            if folder.is_dir():
                names = sorted(item.name for item in folder.iterdir())[:200]
            return {"path": str(args.get("path") or "."), "names": names}
        if name == "read_file":
            target = workspace.resolve(str(args.get("path") or ""))
            if not target.is_file():
                return {"error": "không phải file"}
            data = target.read_text(encoding="utf-8", errors="replace")
            truncated = len(data) > 128 * 1024
            return {"path": str(args.get("path")), "content": data[: 128 * 1024], "truncated": truncated}
        if name == "write_file":
            content = str(args.get("content") or "")
            if len(content) > 1024 * 1024:
                return {"error": "nội dung quá lớn"}
            target = workspace.resolve(str(args.get("path") or ""))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": str(args.get("path")), "bytes": len(content.encode("utf-8"))}
        if name == "run_cmd":
            return run_cmd(str(args.get("command") or ""), workspace.root, cancel, procs)
        return {"error": "tool không có"}
    except PermissionError as exc:
        return {"error": str(exc)}
