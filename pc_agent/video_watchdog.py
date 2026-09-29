"""Hỏi hub vài phút một lần. Máy đang đọc video thì chờ. Rảnh thì tải zip, kiểm sha256, đổi mã, chạy lại.

Token và địa chỉ hub nằm trong config.json, không nằm trong zip.
Khi gói mới có chương trình nối hub, file này tự thay chính nó và giữ bản cũ ở video_watchdog.py.prev.
Lần chạy kế tiếp xóa dấu watchdog-replaced. Nếu file mới không chạy được, lịch Update khôi phục bản .prev.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

STALE_AFTER_SEC = 180.0
_TASK = "FbPollerVideoWorker"


_SUPPORT = (
    ("pc_agent/video_watchdog.py", "video_watchdog.py"),
    ("pc_agent/windows/Run-VideoWorker.ps1", "Run-VideoWorker.ps1"),
    ("pc_agent/windows/Install-VideoWorker.ps1", "Install-VideoWorker.ps1"),
    ("pc_agent/windows/Cai-dat.bat", "Cai-dat.bat"),
)


def should_start_worker(*, pid_alive: bool) -> bool:
    """Tiến trình đọc đã tắt thì mở lại. Đang sống thì không mở thêm một bản."""
    return not pid_alive


def clear_replaced_marker(root: Path) -> None:
    marker = root / "watchdog-replaced"
    if marker.is_file():
        marker.unlink()


def publish_support_files(root: Path) -> None:
    """Chép chương trình nối hub ra ngoài current. Bản đang chạy được giữ trong file .prev."""
    current = root / "current"
    replaced_watchdog = False
    for relative, dest_name in _SUPPORT:
        src = current.joinpath(*relative.split("/"))
        if not src.is_file():
            continue
        dest = root / dest_name
        if dest.exists() and dest.resolve() == src.resolve():
            continue
        if dest.is_file():
            shutil.copy2(dest, root / f"{dest_name}.prev")
        shutil.copy2(src, dest)
        if dest_name == "video_watchdog.py":
            replaced_watchdog = True
    if replaced_watchdog:
        (root / "watchdog-replaced").write_text("1", encoding="utf-8")


def upgrade_allowed(
    *,
    local: int,
    remote: int,
    reading: bool,
    age_sec: float,
    pid_alive: bool,
    stale_after: float = STALE_AFTER_SEC,
) -> str:
    """current khi đã đúng bản. busy khi đang đọc và tiến trình còn sống. apply khi được đổi mã."""
    if remote <= local:
        return "current"
    if reading and pid_alive and age_sec <= stale_after:
        return "busy"
    return "apply"


def read_config(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("config")
    hub = str(data.get("hub") or "").strip().rstrip("/")
    token = str(data.get("token") or "").strip()
    if not hub or not token:
        raise ValueError("config")
    return {"hub": hub, "token": token}


def read_local_version(current: Path) -> int:
    path = current / "VERSION"
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    try:
        return int(text)
    except ValueError:
        return 0


def read_reading_state(path: Path, now: float | None = None) -> tuple[bool, float, int]:
    """Trả về đang đọc, số giây kể từ lần ghi, và pid. Thiếu file thì coi như đang rảnh."""
    if not path.is_file():
        return False, STALE_AFTER_SEC + 1, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False, STALE_AFTER_SEC + 1, 0
    if not isinstance(data, dict):
        return False, STALE_AFTER_SEC + 1, 0
    reading = bool(data.get("reading"))
    pid = data.get("pid")
    at = data.get("at")
    worker_pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else 0
    stamp = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0
    moment = time.time() if now is None else now
    age = max(0.0, moment - stamp) if stamp else STALE_AFTER_SEC + 1
    return reading, age, worker_pid


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def absolute_url(hub: str, package_url: str) -> str:
    if package_url.startswith("http://") or package_url.startswith("https://"):
        return package_url
    return hub.rstrip("/") + "/" + package_url.lstrip("/")


def verify_sha256(blob: bytes, expected: str) -> bool:
    wanted = expected.strip().lower()
    if len(wanted) != 64:
        return False
    return hashlib.sha256(blob).hexdigest() == wanted


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in archive.infolist():
        target = (dest / info.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError("zip")
    archive.extractall(dest)


def replace_tree(current: Path, blob: bytes) -> None:
    parent = current.parent
    staging = parent / "current-staging"
    backup = parent / "current-backup"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        _safe_extract(archive, staging)
    if backup.exists():
        shutil.rmtree(backup)
    if current.exists():
        current.rename(backup)
    try:
        staging.rename(current)
    except OSError:
        if backup.exists() and not current.exists():
            backup.rename(current)
        raise


def venv_python(root: Path) -> Path | None:
    for relative in ("py/Scripts/python.exe", "py/bin/python"):
        path = root / relative
        if path.is_file():
            return path
    return None


def install_cpu_requirements(root: Path) -> None:
    python = venv_python(root)
    requirements = root / "current" / "requirements-cpu.txt"
    if python is None or not requirements.is_file():
        return
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=False)


def _request(url: str, token: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_manifest(hub: str, token: str) -> dict[str, object]:
    raw = _request(hub.rstrip("/") + "/v1/updates/video-worker/manifest", token, 60)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest")
    return data


def _stop_worker(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["schtasks", "/End", "/TN", _TASK], check=False)
    if pid == os.getpid() or not process_alive(pid):
        return
    if process_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
        else:
            os.kill(pid, 15)


def _start_worker() -> None:
    if os.name == "nt":
        subprocess.run(["schtasks", "/Run", "/TN", _TASK], check=False)


def run_once(root: Path, *, installing: bool) -> int:
    config = read_config(root / "config.json")
    try:
        manifest = fetch_manifest(config["hub"], config["token"])
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        print("Chưa hỏi được bản mới.", file=sys.stderr)
        return 1 if installing else 0
    try:
        remote = int(str(manifest.get("version") or "0"))
    except ValueError:
        remote = 0
    package_url = str(manifest.get("package_url") or "")
    expected = str(manifest.get("sha256") or "")
    local = read_local_version(root / "current")
    reading, age, pid = read_reading_state(root / "state.json")
    choice = upgrade_allowed(local=local, remote=remote, reading=reading, age_sec=age, pid_alive=process_alive(pid))
    if choice == "current":
        if should_start_worker(pid_alive=process_alive(pid)):
            _start_worker()
            print("PC chưa chạy. Đã khởi động lại.", flush=True)
        else:
            print(f"Đang ở bản {local}.", flush=True)
        return 0
    if choice == "busy":
        print("Đang đọc video. Sẽ cập nhật khi xong.", flush=True)
        return 0
    try:
        blob = _request(absolute_url(config["hub"], package_url), config["token"], 180)
    except (OSError, urllib.error.URLError, TimeoutError):
        print("Chưa tải được gói mới.", file=sys.stderr)
        return 1 if installing else 0
    if not verify_sha256(blob, expected):
        print("Gói mới không khớp checksum.", file=sys.stderr)
        return 1
    if not installing:
        reading, age, pid = read_reading_state(root / "state.json")
        if upgrade_allowed(local=local, remote=remote, reading=reading, age_sec=age, pid_alive=process_alive(pid)) == "busy":
            print("Đang đọc video. Sẽ cập nhật khi xong.", flush=True)
            return 0
        _stop_worker(pid)
        for _ in range(20):
            if not process_alive(pid):
                break
            time.sleep(0.25)
        if process_alive(pid):
            print("Chưa dừng được lần đọc. Giữ bản hiện tại.", flush=True)
            _start_worker()
            return 0
    replace_tree(root / "current", blob)
    publish_support_files(root)
    install_cpu_requirements(root)
    if not installing:
        _start_worker()
    print(f"Đã đặt bản {remote}.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cập nhật mã đọc video khi PC đang rảnh")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else Path(__file__).resolve().parent
    clear_replaced_marker(root)
    try:
        return run_once(root, installing=bool(args.install))
    except (OSError, ValueError) as error:
        print("Chưa cập nhật được.", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
