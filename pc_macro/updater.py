"""Tự tải bản mới từ hub, ghi đè file, rồi mở lại phần mềm."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request

from pc_macro.version import DEFAULT_HUB, PC_MACRO_BUILD, digest_files, read_tree

Fetcher = Callable[[Request, float], tuple[int, bytes]]


class UpdateError(Exception):
    """Không lấy hoặc không áp được bản mới."""


def install_root() -> Path:
    return Path(__file__).resolve().parents[1]


def update_hub() -> str:
    return os.environ.get("PC_MACRO_HUB", "").strip().rstrip("/") or DEFAULT_HUB


def skip_update(root: Path | None = None) -> bool:
    if os.environ.get("PC_MACRO_SKIP_UPDATE", "").strip() in {"1", "true", "yes"}:
        return True
    folder = Path(root) if root is not None else install_root()
    if (folder / ".git").exists() and not os.environ.get("PC_MACRO_FORCE_UPDATE", "").strip():
        return True
    return False


def local_info(root: Path | None = None) -> dict[str, Any]:
    folder = Path(root) if root is not None else install_root()
    files = read_tree(folder)
    return {
        "build": PC_MACRO_BUILD,
        "digest": digest_files(files) if files else "",
        "root": str(folder),
        "file_count": len(files),
    }


def remote_info(*, fetch: Fetcher | None = None, hub: str | None = None) -> dict[str, Any]:
    body = _call("GET", "/v1/pc-macro/version", fetch=fetch, hub=hub)
    if not isinstance(body, dict) or not body.get("digest"):
        raise UpdateError("Hub không trả bản pc-macro.")
    return body


def needs_update(local: dict[str, Any], remote: dict[str, Any]) -> bool:
    remote_digest = str(remote.get("digest") or "")
    local_digest = str(local.get("digest") or "")
    if not remote_digest:
        return False
    if local_digest and remote_digest == local_digest:
        return False
    try:
        remote_build = int(remote.get("build") or 0)
    except (TypeError, ValueError):
        remote_build = 0
    try:
        local_build = int(local.get("build") or 0)
    except (TypeError, ValueError):
        local_build = 0
    if remote_build and local_build and remote_build < local_build:
        return False
    return True


def download_zip(*, fetch: Fetcher | None = None, hub: str | None = None) -> bytes:
    payload = _call_bytes("GET", "/tai/pc-macro.zip", fetch=fetch, hub=hub)
    if len(payload) < 64 or payload[:2] != b"PK":
        raise UpdateError("Gói tải về không phải file zip.")
    return payload


def extract_package(payload: bytes, dest: Path) -> dict[str, bytes]:
    dest.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = _safe_member(info.filename)
            if rel is None:
                continue
            raw = archive.read(info)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            files[rel.replace("\\", "/")] = raw
    if "pc_macro/version.py" not in files and "pc_macro/gui.py" not in files:
        raise UpdateError("Gói mới thiếu mã phần mềm.")
    return files


def apply_package(payload: bytes, root: Path, *, digest: str | None = None) -> dict[str, Any]:
    staging = Path(root) / "data" / "updates" / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    files = extract_package(payload, staging)
    got = digest_files(files)
    if digest and got != digest:
        raise UpdateError("Chữ ký gói mới không khớp hub.")
    _copy_install(staging, Path(root))
    stamp = {
        "ok": True,
        "build": _read_build(Path(root)),
        "digest": got,
        "applied": int(time.time()),
    }
    _write_stamp(Path(root), stamp, just_updated=True)
    return stamp


def apply_if_needed(
    *,
    relaunch: bool = False,
    fetch: Fetcher | None = None,
    root: Path | None = None,
    hub: str | None = None,
) -> dict[str, Any]:
    folder = Path(root) if root is not None else install_root()
    result: dict[str, Any] = {"updated": False, "skipped": False, "relaunch": False}
    if skip_update(folder):
        result["skipped"] = True
        return result
    try:
        remote = remote_info(fetch=fetch, hub=hub)
        local = local_info(folder)
        if not needs_update(local, remote):
            result["current"] = local
            result["remote"] = {"build": remote.get("build"), "digest": remote.get("digest")}
            return result
        payload = download_zip(fetch=fetch, hub=hub)
        applied = apply_package(payload, folder, digest=str(remote.get("digest") or "") or None)
        result["updated"] = True
        result["applied"] = applied
        _ensure_requires()
        if relaunch:
            result["relaunch"] = _relaunch(folder)
    except (UpdateError, OSError, zipfile.BadZipFile) as exc:
        result["error"] = str(exc)
    return result


def consume_just_updated(root: Path | None = None) -> dict[str, Any] | None:
    folder = Path(root) if root is not None else install_root()
    path = folder / "data" / "updates" / "just_updated.json"
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        body = None
    try:
        path.unlink()
    except OSError:
        pass
    return body if isinstance(body, dict) else None


def _copy_install(staging: Path, root: Path) -> None:
    package_src = staging / "pc_macro"
    package_dst = root / "pc_macro"
    if not package_src.is_dir():
        raise UpdateError("Gói mới không có thư mục pc_macro.")
    package_dst.mkdir(parents=True, exist_ok=True)
    for path in package_src.glob("*.py"):
        shutil.copy2(path, package_dst / path.name)
    for name in ("Cai-dat.ps1", "Chay.cmd", "README.txt"):
        src = staging / name
        if src.is_file():
            shutil.copy2(src, root / name)


def _read_build(root: Path) -> int:
    path = root / "pc_macro" / "version.py"
    if not path.is_file():
        return PC_MACRO_BUILD
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("PC_MACRO_BUILD"):
            try:
                return int(line.split("=", 1)[1].strip().split()[0])
            except (TypeError, ValueError, IndexError):
                break
    return PC_MACRO_BUILD


def _write_stamp(root: Path, stamp: dict[str, Any], *, just_updated: bool) -> None:
    folder = Path(root) / "data" / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "applied.json").write_text(json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8")
    if just_updated:
        (folder / "just_updated.json").write_text(json.dumps(stamp, ensure_ascii=False), encoding="utf-8")


def _ensure_requires() -> None:
    if importlib.util.find_spec("pynput") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pynput"], check=False)


def _relaunch(root: Path) -> bool:
    if os.name == "nt":
        helper = Path(root) / "data" / "updates" / "relaunch.cmd"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    "timeout /t 1 /nobreak >nul",
                    f'cd /d "{root}"',
                    f'start "" "{sys.executable}" -m pc_macro --updated',
                ]
            )
            + "\r\n",
            encoding="utf-8",
        )
        subprocess.Popen(["cmd", "/c", str(helper)], cwd=str(root), close_fds=True)
        return True
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, "-m", "pc_macro", "--updated"])
    return True


def _safe_member(name: str) -> str | None:
    raw = name.replace("\\", "/").lstrip("/")
    if raw.endswith("/"):
        return None
    if raw.startswith("pc-macro/"):
        raw = raw[len("pc-macro/") :]
    if not raw or raw.startswith("data/") or ".." in raw.split("/"):
        return None
    return raw


def _headers() -> dict[str, str]:
    headers = {"Accept": "*/*"}
    token = os.environ.get("PC_MACRO_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _call(method: str, path: str, *, fetch: Fetcher | None, hub: str | None) -> Any:
    raw = _call_bytes(method, path, fetch=fetch, hub=hub)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError("Hub trả về dữ liệu không đọc được.") from exc


def _call_bytes(method: str, path: str, *, fetch: Fetcher | None, hub: str | None) -> bytes:
    base = (hub or update_hub()).rstrip("/")
    if not base:
        raise UpdateError("Chưa biết địa chỉ hub để cập nhật.")
    request = Request(f"{base}{path}", headers=_headers(), method=method)
    timeout = 60.0
    try:
        if fetch is not None:
            status, raw = fetch(request, timeout)
            if status >= 400:
                raise UpdateError(f"Hub trả lỗi {status} khi tải {path}.")
            return raw
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Hub trả lỗi {exc.code} khi tải {path}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Không tới được hub để cập nhật: {exc.reason}") from exc
