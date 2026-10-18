"""Chương trình đọc video trên PC. Tải một lần từ trang video của máy chủ.

Mỗi vòng nó hỏi máy chủ bản đọc và bản nối hiện tại. File trên máy chủ đổi
thì PC tự lấy bản mới, kể cả cách đọc và cách tải, không cần cài lại.
PC chỉ dùng tối đa 80% CPU, RAM và ổ đĩa. Đường truyền không bị giới hạn tốc độ.
Trong lúc đọc, PC giữ tới 50 GB video chờ trên đĩa của máy. Đọc xong video nào thì xóa video đó.
"""

from __future__ import annotations

import ctypes
import hashlib
import inspect
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CONNECTOR_VERSION = 7
# Trần tài nguyên của PC. Máy chủ không đặt biến này nên máy chủ vẫn dùng hết nhân.
PC_HEADROOM = 0.8
# Video chờ đọc nằm trên đĩa PC, tối đa 50 GB. Video đang đọc không tính vào mức này.
PC_CACHE_BYTES = 50 * 1024 * 1024 * 1024
SERVER = "__GHIAM_SERVER__"
TOKEN = "__GHIAM_TOKEN__"

CACHE = Path(__file__).resolve().parent / "ghiam-pc-data"
_LOADED = False
_IDLE = False
_DISK_WAIT = False
_RAM_WAIT = False
_WAIT_NOTE = ""
_AUTH_NOTE = False
_STATUS: dict[str, object] = {"state": "", "message": "", "at": 0.0}
_AUTOSTART_NOTE = ""


def _configured() -> bool:
    return (SERVER.startswith("http://") or SERVER.startswith("https://")) and len(TOKEN) >= 8


def _server_mark() -> str:
    # Tách chữ để dòng này không bị thay khi điền địa chỉ vào file.
    return "__" + "GHIAM" + "_SERVER__"


def _token_mark() -> str:
    return "__" + "GHIAM" + "_TOKEN__"


def restored_template(text: str, server: str, token: str) -> str:
    """Đưa file đã điền địa chỉ và mã về dạng gốc để so với file trên máy chủ."""
    if server:
        text = text.replace(server, _server_mark(), 1)
    if token:
        text = text.replace(token, _token_mark(), 1)
    return text


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_body(name: str) -> dict[str, str]:
    return {"name": name, "version": str(CONNECTOR_VERSION)}


def connector_outdated(local_sha: str, remote_sha: str, remote_version: int, local_version: int = CONNECTOR_VERSION) -> bool:
    """Đổi nội dung file hoặc tăng số phiên bản thì PC phải lấy bản nối mới."""
    if remote_sha and local_sha and remote_sha != local_sha:
        return True
    return remote_version > local_version


def clean_pc_name(raw: str) -> str:
    """Tên máy hiện trên bảng kết quả: một dòng, ngắn, không ký tự điều khiển."""
    text = " ".join(str(raw or "").replace("\n", " ").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:40] or "PC"


def reader_import_path(root: str, current: str) -> str:
    """Đường bộ đọc phải có sẵn trước khi Windows mở tiến trình đọc mới.

    Tiến trình mới không kế thừa đường vừa thêm trong bộ nhớ, chỉ nhận biến môi trường.
    """
    parts = [part for part in current.split(os.pathsep) if part]
    if root and root not in parts:
        parts.insert(0, root)
    return os.pathsep.join(parts)


def reader_launch_lines(lines: list[str], root: str) -> list[str]:
    """Thêm thư mục bộ đọc vào file đường dẫn của Python nhúng.

    File python*._pth thay thế PYTHONPATH. Không ghi lại nếu đường đó đã có.
    """
    wanted = os.path.normcase(os.path.normpath(root))
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if os.path.normcase(os.path.normpath(stripped)) == wanted:
            return list(lines)
    return [*lines, root]


TESSDATA_NAMES = ("eng.traineddata", "vie.traineddata")


def bundle_sha256(root: Path, names: list[str] | tuple[str, ...]) -> str:
    """Hash đúng thứ tự tên file. Máy chủ và PC dùng cùng cách này để biết bộ đọc đã khớp."""
    hasher = hashlib.sha256()
    for relative in names:
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / relative).read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def zip_entry_safe(name: str) -> bool:
    """Tên trong file nén chỉ là đường tương đối, không nhảy ra ngoài thư mục."""
    if not name or name.endswith(("/", "\\")) or "\\" in name or name.startswith(("/", "\\")) or ":" in name:
        return False
    return ".." not in Path(name).parts


def lang_names(listed: str) -> set[str]:
    """Tên bộ chữ trong kết quả tesseract --list-langs. Dòng đầu là đường thư mục, không phải tên."""
    return {line.strip() for line in listed.splitlines()[1:] if line.strip()}


def _replace_tree(staging: Path, target: Path) -> None:
    """Đổi thư mục mới vào chỗ cũ. Đổi hỏng thì bản đang dùng vẫn còn."""
    backup = target.with_name(target.name + ".old")
    if backup.exists():
        shutil.rmtree(backup)
    moved = False
    if target.exists():
        target.rename(backup)
        moved = True
    try:
        staging.rename(target)
    except Exception:
        if moved and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def adoption(saved: str, digest: str, verified: bool, relaunch_on_change: bool) -> tuple[bool, bool]:
    """(sẵn sàng đọc, cần chạy lại). Hash chưa khớp thì PC chưa nhận video."""
    if not digest or not verified:
        return False, False
    return True, bool(saved.strip() != digest and relaunch_on_change)


def pc_name() -> str:
    """Tên ổn định của PC này. Nhiều máy cùng mở thì mỗi máy một tên."""
    raw = os.environ.get("GHIAM_PC_NAME", "").strip()
    saved = CACHE / "name.txt"
    if not raw and saved.is_file():
        try:
            raw = saved.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
    if not raw:
        raw = os.environ.get("COMPUTERNAME", "").strip() or socket.gethostname()
    name = clean_pc_name(raw)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        saved.write_text(name, encoding="utf-8")
    except OSError:
        pass
    return name


def disk_has_headroom(total: int, free: int, extra: int = 0, fraction: float = PC_HEADROOM) -> bool:
    """Còn đủ chỗ trống sau khi giữ lại phần máy không được đụng, kể cả byte sắp ghi."""
    if total <= 0:
        return True
    reserve = math.ceil(total * (1.0 - fraction))
    return free - max(0, extra) >= reserve


def _local_code_sha() -> str:
    text = Path(__file__).resolve().read_text(encoding="utf-8")
    return text_sha256(restored_template(text, SERVER, TOKEN))


def _limit_pc() -> None:
    """PC không dùng quá 80% CPU và RAM. Mỗi tiến trình đọc chỉ một luồng."""
    os.environ["DOC_VIDEO_CPU_FRACTION"] = str(PC_HEADROOM)
    os.environ["DOC_VIDEO_RAM_FRACTION"] = str(PC_HEADROOM)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OMP_THREAD_LIMIT"] = "1"


def _url(path: str) -> str:
    return SERVER.rstrip("/") + path


def _open(method: str, path: str, body: dict | None = None, timeout: float = 60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(_url(path), data=data, headers=headers, method=method)
    return urllib.request.urlopen(request, timeout=timeout)


def _json(method: str, path: str, body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    try:
        with _open(method, path, body, timeout) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return exc.code, payload


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _download(path: str, dest: Path, timeout: float = 600, progress=None) -> None:
    """timeout là số giây không có byte mới, không phải thời gian tải cả file.

    Không nén lại file đã nén. Bộ đệm lớn để đường truyền không bị cắt thành mẩu nhỏ.
    progress(đã tải, tổng) chỉ để báo tiến độ lên trang.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(
        _url(path),
        headers={"Authorization": f"Bearer {TOKEN}", "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as handle:
            if progress is None:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            else:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
                    done += len(block)
                    progress(done, total)
        temporary.replace(dest)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _synced_letters(name: str) -> Path | None:
    folder = CACHE / name
    if all((folder / item).is_file() for item in TESSDATA_NAMES):
        return folder
    return None


def _list_langs(extra: list[str]) -> set[str] | None:
    try:
        listed = subprocess.run(
            ["tesseract", *extra, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return lang_names(listed)


def _tools_ok() -> bool:
    ok = True
    if shutil.which("ffmpeg") is None:
        print("Chưa có ffmpeg. PC đang thử lại.")
        ok = False
    if shutil.which("tesseract") is None:
        print("Chưa có tesseract. PC đang thử lại.")
        return False
    # Bước quét khung đọc bằng bộ chữ mặc định, giống máy chủ. Thiếu bộ này thì mọi khung ra rỗng.
    plain = _list_langs([])
    if plain is None:
        print("Chưa chạy được tesseract. PC đang thử lại.")
        return False
    if not {"vie", "eng"} <= plain:
        print("Tesseract chưa có bộ chữ mặc định giống máy chủ. PC chưa nhận video.")
        ok = False
    folder = _synced_letters("tessdata")
    if folder is not None:
        best = _list_langs(["--tessdata-dir", str(folder)])
        if best is None or not {"vie", "eng"} <= best:
            print("Tesseract thiếu tiếng Việt hoặc tiếng Anh.")
            ok = False
    return ok


def _ensure_runtime() -> bool:
    missing = [name for name in ("PIL", "numpy") if not _importable(name)]
    if missing:
        print("Thiếu thư viện đọc ảnh. PC đang thử lại.")
        return False
    return True


def _importable(module: str) -> bool:
    try:
        __import__(module)
    except Exception:
        return False
    return True


_RUNTIME_PROBE = (
    "import torch, torchvision, einops, yaml, gdown, vietocr\n"
    "from vietocr.tool.config import Cfg\n"
    "from vietocr.tool.predictor import Predictor\n"
)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _runtime_problem() -> str:
    """Nạp bộ đọc dấu trong tiến trình riêng. Rỗng là chạy được, còn lại là lý do lỗi.

    Nạp hỏng torch trong chính tiến trình này thì lần nạp sau vẫn hỏng, nên không nạp ở đây.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-c", _RUNTIME_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type(exc).__name__
    if done.returncode == 0:
        return ""
    return _last_line(done.stderr) or _last_line(done.stdout) or f"mã thoát {done.returncode}"


_SINGLE: list[object] = []


def _single_instance() -> bool:
    """Một PC chỉ chạy một chương trình nối, để không nhận hai video cùng lúc trên một máy."""
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\GhiamPCReader")
    if not handle:
        return True
    # 0: nhận được. 0x80: bản trước đã tắt mà chưa nhả, vẫn tính là nhận được.
    if kernel32.WaitForSingleObject(handle, 0) in (0, 0x80):
        _SINGLE.append(handle)
        return True
    kernel32.CloseHandle(handle)
    return False


def _relaunch() -> int:
    """Mã 2: cửa sổ PC mở lại chương trình nối với bản mới.

    Windows không có execv thật: tiến trình cũ thoát ngay còn bản mới chạy ngầm,
    cửa sổ tưởng đã tắt nên mở thêm một bản nữa. Vì vậy trên Windows chỉ trả mã 2.
    """
    if os.name == "nt":
        return 2
    argv = [sys.executable, str(Path(__file__).resolve())]
    try:
        os.execv(sys.executable, argv)
    except OSError:
        return 2
    return 2


def _update_connector() -> bool:
    status, body = _json("GET", "/v1/pc/connector/version", timeout=120)
    if status != 200:
        return False
    remote_version = int(body.get("version") or 0)
    remote_sha = str(body.get("sha256") or "")
    if not connector_outdated(_local_code_sha(), remote_sha, remote_version):
        return False
    print("Máy chủ có chương trình nối mới. PC đang tự cập nhật.")
    script = Path(__file__).resolve()
    temporary = script.with_suffix(".py.new")
    _download("/v1/pc/connector.py", temporary, timeout=120)
    try:
        new_text = temporary.read_text(encoding="utf-8")
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    if "def main" not in new_text:
        temporary.unlink(missing_ok=True)
        print("Bản nối tải về không đúng. Giữ bản đang chạy.")
        return False
    if remote_sha and text_sha256(restored_template(new_text, SERVER, TOKEN)) != remote_sha:
        temporary.unlink(missing_ok=True)
        print("Bản nối tải về không khớp. Giữ bản đang chạy.")
        return False
    temporary.replace(script)
    return True


def _saved(marker: Path) -> str:
    try:
        return marker.read_text(encoding="utf-8") if marker.is_file() else ""
    except OSError:
        return ""


def _remember(marker: Path, digest: str) -> bool:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest, encoding="utf-8")
    except OSError:
        return False
    return True


def _extract_named(archive: Path, target: Path, names: list[str]) -> bool:
    try:
        with zipfile.ZipFile(archive) as opened:
            present = set(opened.namelist())
            if any(name not in present or not zip_entry_safe(name) for name in names):
                return False
            target.mkdir(parents=True, exist_ok=True)
            for name in names:
                opened.extract(name, target)
    except (OSError, zipfile.BadZipFile):
        return False
    return True


def _sync_reader(digest: str, names: list[str]) -> tuple[bool, bool]:
    if not digest or not names or any(not zip_entry_safe(name) for name in names):
        print("Máy chủ chưa đưa đủ bộ đọc. PC chưa nhận video.")
        return False, False
    marker = CACHE / "reader.sha256"
    target = CACHE / "reader"
    verified = False
    try:
        verified = target.is_dir() and bundle_sha256(target, names) == digest
    except OSError:
        verified = False
    if not verified:
        print("Đang lấy bộ đọc hiện trên máy chủ.")
        archive_path = CACHE / "reader.zip"
        staging = CACHE / "reader-new"
        try:
            _download("/v1/pc/reader.zip", archive_path)
            if staging.exists():
                shutil.rmtree(staging)
            verified = _extract_named(archive_path, staging, names) and bundle_sha256(staging, names) == digest
            if verified:
                _replace_tree(staging, target)
        except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile):
            verified = False
        finally:
            archive_path.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    ready, relaunch = adoption(_saved(marker), digest, verified, _LOADED)
    if not ready:
        print("Bộ đọc tải về chưa khớp máy chủ. PC chưa nhận video.")
        return False, False
    if not _remember(marker, digest):
        return False, False
    return ready, relaunch


def _sync_weights(info: dict | None) -> tuple[bool, bool]:
    if not info:
        return True, False
    digest = str(info.get("sha256") or "")
    path = CACHE / "weights" / "vgg_transformer.pth"
    marker = CACHE / "weights.sha256"
    verified = False
    try:
        verified = bool(digest) and path.is_file() and _file_sha256(path) == digest
    except OSError:
        verified = False
    if digest and not verified:
        print("Đang lấy bộ đọc dấu tiếng Việt.")
        fresh = path.with_name("vgg_transformer.pth.new")
        try:
            if not _disk_ready(max(_info_bytes(info), 1) * 2):
                print("Ổ đĩa PC không còn đủ chỗ cho bộ đọc dấu. PC chờ.")
                return False, False
            _download("/v1/pc/weights", fresh, timeout=1800)
            verified = _file_sha256(fresh) == digest
            if verified:
                path.parent.mkdir(parents=True, exist_ok=True)
                fresh.replace(path)
        except (urllib.error.URLError, TimeoutError, OSError):
            verified = False
        finally:
            if fresh.exists() and not verified:
                fresh.unlink(missing_ok=True)
    ready, relaunch = adoption(_saved(marker), digest, verified, _LOADED)
    if not ready:
        print("Bộ đọc dấu chưa khớp máy chủ. PC chưa nhận video.")
        return False, False
    if not _remember(marker, digest):
        return False, False
    return ready, relaunch


def _sync_tessdata(info: dict | None) -> tuple[bool, bool]:
    return _sync_letters(info, "/v1/pc/tessdata.zip", "tessdata")


def _sync_plain_letters(info: dict | None) -> tuple[bool, bool]:
    """Bộ chữ mặc định của tesseract trên máy chủ. Bước quét khung đọc bằng bộ này."""
    return _sync_letters(info, "/v1/pc/tessdata-default.zip", "tessdata-default")


def _sync_letters(info: dict | None, route: str, name: str) -> tuple[bool, bool]:
    if not info:
        return True, False
    digest = str(info.get("sha256") or "")
    marker = CACHE / f"{name}.sha256"
    target = CACHE / name
    verified = False
    try:
        verified = bool(digest) and all((target / item).is_file() for item in TESSDATA_NAMES)
        verified = verified and bundle_sha256(target, TESSDATA_NAMES) == digest
    except OSError:
        verified = False
    if digest and not verified:
        print("Đang lấy bộ chữ của máy chủ.")
        archive_path = CACHE / f"{name}.zip"
        staging = CACHE / f"{name}-new"
        try:
            _download(route, archive_path)
            if staging.exists():
                shutil.rmtree(staging)
            verified = _extract_named(archive_path, staging, list(TESSDATA_NAMES))
            verified = verified and bundle_sha256(staging, TESSDATA_NAMES) == digest
            if verified:
                _replace_tree(staging, target)
        except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile):
            verified = False
        finally:
            archive_path.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    ready, relaunch = adoption(_saved(marker), digest, verified, _LOADED)
    if not ready:
        print("Bộ chữ chưa khớp máy chủ. PC chưa nhận video.")
        return False, False
    if not _remember(marker, digest):
        return False, False
    return ready, relaunch


def _info_bytes(info: dict | None) -> int:
    if not info:
        return 0
    try:
        return max(0, int(info.get("bytes") or 0))
    except (TypeError, ValueError):
        return 0


def _extract_wheels(archive: Path, target: Path) -> bool:
    try:
        with zipfile.ZipFile(archive) as opened:
            names = [name for name in opened.namelist() if name.endswith(".whl") and zip_entry_safe(name)]
            if not names:
                return False
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for name in names:
                opened.extract(name, target)
    except (OSError, zipfile.BadZipFile):
        return False
    return True


def _install_wheels(folder: Path, force: bool = False) -> bool:
    wheels = sorted(str(path) for path in folder.glob("*.whl"))
    if not wheels:
        return False
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--no-index",
        "--no-deps",
        *(["--force-reinstall"] if force else []),
        "--find-links",
        str(folder),
        *wheels,
    ]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Chưa cài được gói thư viện: {type(exc).__name__}.")
        return False
    if done.returncode != 0:
        print(f"Chưa cài được gói thư viện: {_last_line(done.stderr) or _last_line(done.stdout)}")
        return False
    return True


# Bộ thư viện đã nạp thử được trong lần chạy này, và bộ vừa hỏng kèm lúc hỏng.
_RUNTIME_OK = ""
_RUNTIME_FAIL: tuple[str, float] = ("", 0.0)


def _sync_runtime(info: dict | None, need_model: bool) -> tuple[bool, bool]:
    """Cài bộ thư viện từ máy chủ. PC không nhận video khi máy chủ có bộ dấu mà PC chưa cài được."""
    global _RUNTIME_OK, _RUNTIME_FAIL
    if not need_model:
        return True, False
    if not info:
        print("Máy chủ đọc bằng bộ dấu nhưng chưa phát bộ thư viện cho PC. PC chưa nhận video.")
        return False, False
    digest = str(info.get("sha256") or "")
    if digest and digest == _RUNTIME_OK:
        return True, False
    failed, failed_at = _RUNTIME_FAIL
    # Máy chủ đổi bộ thư viện thì thử ngay, không chờ hết lượt chờ của bản cũ.
    if failed == digest and time.monotonic() - failed_at < 600:
        return False, False
    marker = CACHE / "runtime.sha256"
    archive = CACHE / "runtime.zip"
    folder = CACHE / "runtime"
    repair = False
    if digest and _saved(marker).strip() == digest:
        problem = _runtime_problem()
        if not problem:
            _RUNTIME_OK = digest
            return True, False
        print(f"Bộ đọc dấu chưa chạy được: {problem}. PC cài lại.")
        repair = True
    if not _disk_ready(max(_info_bytes(info), 1) * 4):
        print("Ổ đĩa PC không còn đủ chỗ cho bộ đọc dấu. PC chờ.")
        return False, False
    try:
        cached = bool(digest) and archive.is_file() and _file_sha256(archive) == digest
    except OSError:
        cached = False
    print("Đang lấy bộ thư viện đọc dấu của máy chủ.")
    try:
        if not cached:
            _download("/v1/pc/tools/runtime.zip", archive, timeout=1800)
        verified = _file_sha256(archive) == digest
    except (urllib.error.URLError, TimeoutError, OSError):
        verified = False
    if not verified:
        archive.unlink(missing_ok=True)
        _RUNTIME_FAIL = (digest, time.monotonic())
        print("Bộ thư viện đọc dấu tải về không khớp. PC chưa nhận video.")
        return False, False
    if not _extract_wheels(archive, folder) or not _install_wheels(folder, force=repair):
        _RUNTIME_FAIL = (digest, time.monotonic())
        print("Chưa cài được bộ đọc dấu giống máy chủ. PC chưa nhận video, 10 phút nữa thử lại.")
        return False, False
    problem = _runtime_problem()
    if problem:
        _RUNTIME_FAIL = (digest, time.monotonic())
        print(f"Bộ đọc dấu chưa chạy được: {problem}. PC chưa nhận video, 10 phút nữa thử lại.")
        return False, False
    if not _remember(marker, digest):
        return False, False
    _RUNTIME_OK = digest
    print("Đã cài bộ đọc dấu giống máy chủ. PC chạy lại để đọc giống máy chủ.")
    return True, True


def _windows_temp() -> None:
    """Ảnh tạm để ở thư mục tên ASCII. Bộ đọc chữ trên Windows không mở được đường có dấu."""
    if os.name != "nt":
        return
    folder = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "GhiamPC" / "tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    path = str(folder)
    os.environ["TEMP"] = path
    os.environ["TMP"] = path
    tempfile.tempdir = path


def _publish_reader_path() -> None:
    root = CACHE / "reader"
    if not root.is_dir():
        return
    root_text = str(root)
    os.environ["PYTHONPATH"] = reader_import_path(root_text, os.environ.get("PYTHONPATH", ""))
    if os.name != "nt":
        return
    # File .bat đặt PYTHONUTF8=1. Python nhúng đọc đường dẫn bằng UTF-8, không phải bảng mã cũ.
    os.environ["PYTHONUTF8"] = "1"
    py_dir = Path(sys.executable).resolve().parent
    _write_utf8_line(py_dir / "Lib" / "site-packages" / "ghiam-reader.pth", root_text)
    _append_python_pth(py_dir, root_text)


def _write_utf8_line(marker: Path, line: str) -> None:
    encoded = (line + "\n").encode("utf-8")
    try:
        if marker.is_file() and marker.read_bytes().replace(b"\r\n", b"\n") == encoded:
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(encoded)
    except OSError:
        return


def _append_python_pth(py_dir: Path, root_text: str) -> None:
    matches = sorted(py_dir.glob("python*._pth"))
    if len(matches) != 1:
        return
    path = matches[0]
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    updated = reader_launch_lines(lines, root_text)
    if updated == lines:
        return
    temporary = path.with_suffix(path.suffix + ".new")
    try:
        temporary.write_bytes((newline.join(updated) + newline).encode("utf-8"))
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


def _prepare_env() -> None:
    _limit_pc()
    _windows_temp()
    _publish_reader_path()
    weights = CACHE / "weights" / "vgg_transformer.pth"
    if weights.is_file():
        os.environ["DOC_VIDEO_VIETOCR_WEIGHTS"] = str(weights)
    tessdata = _synced_letters("tessdata")
    if tessdata is not None:
        os.environ["DOC_VIDEO_TESSDATA"] = str(tessdata)
    # Lệnh tesseract không ghi thư mục bộ chữ thì đọc ở đây. Máy có TESSDATA_PREFIX cũ cũng bị thay.
    plain = _synced_letters("tessdata-default")
    if plain is not None:
        os.environ["TESSDATA_PREFIX"] = str(plain)


_SELF_TEST = r'''
import sys
from PIL import Image, ImageDraw, ImageFont
from doc_video import ocr, viet

font = ImageFont.load_default(size=64)
image = Image.new("RGB", (1000, 300), "white")
draw = ImageDraw.Draw(image)
draw.text((40, 40), "0901234567", fill="black", font=font)
draw.text((40, 170), "Nguyen Van An", fill="black", font=font)
found = ["".join(ch for ch in line.text if ch.isdigit()) for line in ocr.find_lines(image)]
if "0901234567" not in found:
    print("bước quét khung không ra số")
    sys.exit(3)
digits = ocr.read_texts([image.crop((0, 0, 1000, 150))], "eng", ocr.DIGITS)[0]
if "".join(ch for ch in digits if ch.isdigit()) != "0901234567":
    print("bước đọc kỹ không ra số")
    sys.exit(4)
if viet.weights_path().is_file():
    if not viet.available():
        print("bộ đọc dấu: " + viet.unavailable_reason())
        sys.exit(5)
    if not viet.read_lines([image.crop((0, 150, 1000, 300))])[0].strip():
        print("bộ đọc dấu đọc ra rỗng")
        sys.exit(6)
'''


def _self_test() -> str:
    """Đọc một ảnh dựng sẵn bằng đúng bộ đọc của máy chủ, trong tiến trình riêng.

    Rỗng là đọc đúng. Còn lại là lý do; khi đó PC không nhận video thật, để không gửi lên bảng rỗng.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-c", _SELF_TEST],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type(exc).__name__
    if done.returncode == 0:
        return ""
    return _last_line(done.stdout) or _last_line(done.stderr) or f"mã thoát {done.returncode}"


def _read(video: Path, progress=None):
    """Nạp bộ đọc vừa tải. Gói doc_video chỉ có sau khi giải nén, nên nhập ở đây.

    Bộ đọc cũ chưa nhận progress thì vẫn đọc, chỉ không báo tiến độ.
    """
    global _LOADED
    root = str(CACHE / "reader")
    if root not in sys.path:
        sys.path.insert(0, root)
    _prepare_env()
    from doc_video.pipeline import read_video

    _LOADED = True
    if progress is not None and "progress" in inspect.signature(read_video).parameters:
        return read_video([video], progress=progress)
    return read_video([video])


def _payload(result) -> dict:
    return {
        "rows": [{"phone": row.phone, "name": row.name, "username": row.handle} for row in result.rows],
        "review": [
            {
                "phone": item.phone,
                "contact_name": item.list_name,
                "profile_name": item.profile_name,
                "username": item.handle,
                "reason": item.reason,
            }
            for item in result.review
        ],
        "unopened": [{"phone": phone, "name": name} for phone, name in result.unopened],
    }


def _beat(video_id: int, lease: str, stop: threading.Event, progress: dict) -> None:
    """Nhịp ngay khi giữ video, rồi mỗi 20 giây, kèm bước đang làm và tiến độ."""
    while not stop.is_set():
        body = {"lease": lease, **{key: progress.get(key) for key in ("stage", "done", "total")}}
        try:
            status, _body = _json("POST", f"/v1/pc/videos/{video_id}/heartbeat", body, timeout=30)
        except (urllib.error.URLError, TimeoutError, OSError):
            if stop.wait(20):
                return
            continue
        if status == 409:
            stop.set()
            return
        if stop.wait(20):
            return


def _release(video_id: int, lease: str, reason: str = "fail", error: str = "") -> None:
    """Trả video về hàng chờ. reason=disk là PC thiếu chỗ, không tính là PC đọc hỏng."""
    try:
        _json(
            "POST",
            f"/v1/pc/videos/{video_id}/release",
            {"lease": lease, "reason": reason, "error": error[:300]},
            timeout=30,
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def _startup_file() -> Path | None:
    """File mở PC trong thư mục Startup của người dùng Windows hiện tại."""
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "ghiam-pc.bat"


def _apply_control(body: dict) -> None:
    """Làm theo điều trang đặt cho PC này. Hiện chỉ có tự chạy khi bật máy, mặc định tắt."""
    global _AUTOSTART_NOTE
    wanted = body.get("autostart")
    target = _startup_file()
    if not isinstance(wanted, bool) or target is None:
        return
    note = ""
    try:
        if wanted and not target.is_file():
            _download("/v1/pc/launcher.bat", target, timeout=120)
            note = "Trang đã bật tự chạy: mỗi lần bật máy, PC tự mở cửa sổ này và nối máy chủ."
        elif not wanted and target.is_file():
            target.unlink()
            note = "Trang đã tắt tự chạy khi bật máy."
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        note = f"Chưa đổi được tự chạy khi bật máy: {exc}"
    if note and note != _AUTOSTART_NOTE:
        print(note)
        _AUTOSTART_NOTE = note


def _status(name: str, state: str, message: str = "") -> None:
    """Báo trang PC đang cài, đang đọc thử hay đang chờ. Chỉ gửi khi đổi trạng thái hoặc mỗi 30 giây."""
    now = time.monotonic()
    if _STATUS["state"] == state and _STATUS["message"] == message and now - float(_STATUS["at"]) < 30:
        return
    try:
        status, body = _json(
            "POST",
            "/v1/pc/status",
            {"name": name, "state": state, "message": message[:300], "version": str(CONNECTOR_VERSION)},
            timeout=30,
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return
    if status != 200:
        return
    _STATUS.update(state=state, message=message, at=now)
    _apply_control(body)


def _auth_lost(body: dict) -> None:
    """Mã nối bị thu hồi hoặc không còn đúng. Chờ lâu giữa các lần thử, không làm ồn máy chủ."""
    global _AUTH_NOTE
    if not _AUTH_NOTE:
        detail = str(body.get("detail") or "")
        reason = "Mã mời của PC này đã bị thu hồi." if "thu hồi" in detail else "Máy chủ không nhận mã nối của PC này."
        print(f"{reason} Mở link mời mới từ trang video để nối lại. PC thử lại mỗi 5 phút.")
        _AUTH_NOTE = True
    time.sleep(300)


def _disk_ready(extra: int = 0) -> bool:
    folder = CACHE
    folder.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(folder)
    return disk_has_headroom(usage.total, usage.free, extra)


def _ram_ready() -> bool:
    """Bộ đọc chỉ có sau khi tải từ máy chủ, nên nhập ở đây chứ không phải đầu file."""
    global _LOADED
    root = str(CACHE / "reader")
    if not Path(root).is_dir():
        return True
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from doc_video import video as video_mod
    except ImportError:
        return True
    check = getattr(video_mod, "within_resource_ceiling", None)
    if check is None:
        return True
    _LOADED = True
    try:
        return bool(check())
    except (OSError, ValueError):
        return True


def cached_ready(path: Path, expected: str, size: int = 0) -> bool:
    """File đã có trên PC thì đọc luôn, không tải lại.

    Biết đúng số byte thì chỉ so kích thước. File chỉ được đổi tên vào chỗ này sau khi tải đủ.
    """
    if not path.is_file():
        return False
    try:
        actual = path.stat().st_size
    except OSError:
        return False
    if actual <= 0:
        return False
    if size > 0:
        return actual == size
    if not expected:
        return True
    try:
        return _file_sha256(path) == expected
    except OSError:
        return False


def waiting_cache_bytes(sizes: list[int], reading: bool) -> int:
    """Byte tính vào 50 GB. Video sắp đọc, khi PC chưa đọc video khác, không tính."""
    clean = [max(0, int(size)) for size in sizes]
    if reading:
        return sum(clean)
    if len(clean) <= 1:
        return 0
    return sum(clean[1:])


def _gb_text(nbytes: int) -> str:
    gb = max(0, int(nbytes)) / (1024 * 1024 * 1024)
    if gb >= 10:
        return f"{round(gb)} GB"
    return f"{gb:.1f}".replace(".", ",") + " GB"


def forget_local(path: Path) -> None:
    """Xóa video và mẩu tải dở khỏi PC."""
    for item in (path, Path(str(path) + ".part")):
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


def clear_video_cache(folder: Path) -> int:
    """Xóa video còn sót từ lần PC tắt giữa chừng. Bản trên máy chủ vẫn còn."""
    return prune_video_cache(folder, set())


def prune_video_cache(folder: Path, keep: set[int]) -> int:
    """Xóa file video không còn trong sổ. Bản trên máy chủ vẫn còn."""
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".mp4.part"):
            stem = name[: -len(".mp4.part")]
            drop = True
            if stem.isdigit() and int(stem) in keep:
                drop = False
        elif path.suffix == ".mp4" and path.stem.isdigit():
            drop = int(path.stem) not in keep
        else:
            continue
        if not drop:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
    return removed


def load_video_ledger(folder: Path) -> list[dict]:
    """Sổ video đã tải đủ trên PC. File hỏng thì bỏ, không chặn PC."""
    path = folder / "ledger.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    rows = raw.get("videos") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    kept: list[dict] = []
    seen: set[int] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            video_id = int(item.get("id") or 0)
            size = max(0, int(item.get("bytes") or 0))
        except (TypeError, ValueError):
            continue
        lease = str(item.get("lease") or "")
        digest = str(item.get("sha256") or "")
        if video_id <= 0 or not lease or video_id in seen:
            continue
        seen.add(video_id)
        kept.append({"id": video_id, "lease": lease, "sha256": digest, "bytes": size})
    return kept


def save_video_ledger(folder: Path, rows: list[dict]) -> None:
    """Ghi sổ rồi đổi tên, để mất điện không để lại sổ viết dở."""
    folder.mkdir(parents=True, exist_ok=True)
    payload = []
    for item in rows:
        try:
            video_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        payload.append(
            {
                "id": video_id,
                "lease": str(item.get("lease") or ""),
                "sha256": str(item.get("sha256") or ""),
                "bytes": max(0, int(item.get("bytes") or 0)),
            }
        )
    target = folder / "ledger.json"
    temporary = folder / "ledger.json.tmp"
    temporary.write_text(json.dumps({"videos": payload}, ensure_ascii=True), encoding="utf-8")
    temporary.replace(target)


def _lease_open(video_id: int, lease: str) -> bool:
    """Phiếu còn đúng thì True. Mất mạng thì vẫn đọc, vì phiếu hết hạn mới bị lấy lại."""
    try:
        status, _body = _json("POST", f"/v1/pc/videos/{video_id}/heartbeat", {"lease": lease}, timeout=30)
    except (urllib.error.URLError, TimeoutError, OSError):
        return True
    return status != 409


def _fetch_video(video_id: int, lease: str, local: Path, expected: str, progress=None) -> bool:
    url = f"/v1/pc/videos/{video_id}/file?lease={lease}"
    for attempt in range(3):
        try:
            _download(url, local, timeout=600, progress=progress)
        except (urllib.error.URLError, TimeoutError, OSError):
            local.unlink(missing_ok=True)
            if attempt == 2:
                raise
            print(f"Video {video_id} tải dở. Tải lại từ đầu, lần {attempt + 2}.")
            time.sleep(2)
            continue
        if not expected or _file_sha256(local) == expected:
            return True
        local.unlink(missing_ok=True)
        print(f"Video {video_id} tải về không khớp. Tải lại.")
    return False


class _Cache:
    """Giữ tới 50 GB video chờ trên đĩa PC trong lúc đang đọc.

    Tải lần lượt, không tải song song. Đọc xong video nào thì xóa file đó.
    Cập nhật chương trình thì giữ file và phiếu, để không tải lại hàng chục GB.
    """

    def __init__(self) -> None:
        self.reason = ""
        self.stages: list[str] = []
        self._name = ""
        self._control: dict = {}
        self._http = 0
        self._ready: list[dict] = []
        self._reading: dict | None = None
        self._beats: dict[int, dict] = {}
        self._filling = False
        self._noted_full = False
        self._resumed = 0
        self._pruned = 0
        self._stop = threading.Event()
        self._beat_stop = threading.Event()
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._beat_thread: threading.Thread | None = None

    def start(self, name: str) -> None:
        self._name = name
        self._load()
        self._beat_thread = threading.Thread(target=self._beat_loop, name="ghiam-cache-beat", daemon=True)
        self._beat_thread.start()
        self._thread = threading.Thread(target=self._run, name="ghiam-cache", daemon=True)
        self._thread.start()
        if self._resumed:
            total = sum(int(item.get("bytes") or 0) for item in self._ready)
            print(f"PC còn {self._resumed} video đã tải sẵn, {_gb_text(total)}. Đọc tiếp, không tải lại.")
        elif self._pruned:
            print(f"Đã xóa {self._pruned} video còn trên PC từ lần đọc trước.")

    def allow(self, enabled: bool) -> None:
        with self._cond:
            self._filling = enabled
            if enabled:
                self.reason = ""
            self._cond.notify_all()

    def take(self, timeout: float = 30) -> dict | None:
        """Lấy video đã tải đủ. Video này rời khỏi 50 GB chờ và sang lượt đọc."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._ready and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if not self._ready:
                return None
            job = self._ready.pop(0)
            self._reading = job
            self._beats.pop(int(job["id"]), None)
            self._save_locked()
            self._noted_full = False
            self._cond.notify_all()
            return job

    def finished(self) -> None:
        """Video đang đọc đã xóa khỏi đĩa. Chỗ trống được dùng để tải thêm."""
        with self._cond:
            self._reading = None
            self._save_locked()
            self._cond.notify_all()

    def control(self) -> dict:
        with self._cond:
            return dict(self._control)

    def http_status(self) -> int:
        with self._cond:
            return self._http

    def close(self, give_back: bool) -> None:
        """Tắt thì trả video chưa đọc. Cập nhật chương trình thì giữ file để đọc tiếp."""
        self._stop.set()
        self._beat_stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._beat_thread is not None:
            self._beat_thread.join(timeout=5)
        if not give_back:
            with self._cond:
                self._save_locked()
            return
        with self._cond:
            jobs = list(self._ready)
            self._ready.clear()
            self._beats.clear()
            self._save_locked()
        for job in jobs:
            video_id = int(job["id"])
            forget_local(CACHE / "videos" / f"{video_id}.mp4")
            _release(video_id, str(job.get("lease") or ""), "return")

    def _waiting_locked(self) -> int:
        sizes = [int(item.get("bytes") or 0) for item in self._ready]
        return waiting_cache_bytes(sizes, self._reading is not None)

    def _rows_locked(self) -> list[dict]:
        rows: list[dict] = []
        if self._reading is not None:
            rows.append(self._reading)
        rows.extend(self._ready)
        return rows

    def _save_locked(self) -> None:
        save_video_ledger(CACHE / "videos", self._rows_locked())

    def _load(self) -> None:
        folder = CACHE / "videos"
        folder.mkdir(parents=True, exist_ok=True)
        ready: list[dict] = []
        for row in load_video_ledger(folder):
            local = folder / f"{int(row['id'])}.mp4"
            if cached_ready(local, str(row.get("sha256") or ""), int(row.get("bytes") or 0)):
                ready.append(dict(row))
            else:
                forget_local(local)
        with self._cond:
            self._ready = ready
            self._beats = {
                int(item["id"]): {"lease": str(item["lease"]), "stage": "hold", "done": 1, "total": 1}
                for item in ready
            }
            self._save_locked()
            keep = {int(item["id"]) for item in ready}
        self._pruned = prune_video_cache(folder, keep)
        lost = self._send_beats()
        if lost:
            with self._cond:
                gone = set(lost)
                self._ready = [item for item in self._ready if int(item["id"]) not in gone]
                for video_id in gone:
                    self._beats.pop(video_id, None)
                    forget_local(folder / f"{video_id}.mp4")
                self._save_locked()
        with self._cond:
            self._resumed = len(self._ready)

    def _send_beats(self) -> list[int]:
        with self._cond:
            items = [
                {
                    "id": video_id,
                    "lease": str(progress.get("lease") or ""),
                    "stage": str(progress.get("stage") or ""),
                    "done": int(progress.get("done") or 0),
                    "total": int(progress.get("total") or 0),
                }
                for video_id, progress in self._beats.items()
            ]
        lost: list[int] = []
        for start in range(0, len(items), 400):
            chunk = items[start : start + 400]
            if not chunk:
                continue
            try:
                status, body = _json("POST", "/v1/pc/heartbeats", {"items": chunk}, timeout=30)
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            if status != 200 or not isinstance(body, dict):
                continue
            raw = body.get("lost")
            if isinstance(raw, list):
                for item in raw:
                    try:
                        lost.append(int(item))
                    except (TypeError, ValueError):
                        continue
        return lost

    def _beat_loop(self) -> None:
        while not self._beat_stop.is_set():
            lost = self._send_beats()
            if lost:
                with self._cond:
                    gone = set(lost)
                    self._ready = [item for item in self._ready if int(item["id"]) not in gone]
                    for video_id in gone:
                        self._beats.pop(video_id, None)
                        forget_local(CACHE / "videos" / f"{video_id}.mp4")
                    self._save_locked()
                    self._cond.notify_all()
            if self._beat_stop.wait(20):
                return

    def _note_full(self) -> None:
        if self._noted_full:
            return
        self._noted_full = True
        print(f"PC đã giữ đủ {_gb_text(PC_CACHE_BYTES)} video chờ đọc. Đọc xong sẽ tải thêm.")

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while not self._filling and not self._stop.is_set():
                    self._cond.wait(1)
                if self._stop.is_set():
                    return
                waiting = self._waiting_locked()
                full = waiting >= PC_CACHE_BYTES
                if full:
                    self._note_full()
            if full:
                self._stop.wait(5)
                continue
            try:
                status, body = _json(
                    "POST",
                    "/v1/pc/claim",
                    claim_body(self._name),
                    timeout=120,
                )
            except (urllib.error.URLError, TimeoutError, OSError):
                self._stop.wait(10)
                continue
            if self._stop.is_set():
                return
            if not isinstance(body, dict):
                body = {}
            with self._cond:
                self._control = body
                self._http = status
            if status != 200:
                self._stop.wait(10)
                continue
            if body.get("wait"):
                self._stop.wait(10)
                continue
            job = body.get("video")
            if not isinstance(job, dict):
                self._stop.wait(15 if waiting else 5)
                continue
            self._store(job)

    def _store(self, job: dict) -> None:
        try:
            video_id = int(job["id"])
            lease = str(job["lease"])
        except (KeyError, TypeError, ValueError):
            return
        expected = str(job.get("sha256") or "")
        size_bytes = max(0, int(job.get("bytes") or 0))
        with self._cond:
            known = int(job["id"]) in self._beats or any(int(item["id"]) == video_id for item in self._ready)
            reading = self._reading is not None and int(self._reading["id"]) == video_id
        if known or reading:
            _release(video_id, lease, "return")
            return
        if not _disk_ready(size_bytes):
            self.reason = "disk"
            _release(video_id, lease, "disk")
            print(f"Video {video_id} cần nhiều chỗ hơn phần PC còn giữ. Tạm dừng tải thêm.")
            self._stop.wait(30)
            return
        folder = CACHE / "videos"
        folder.mkdir(parents=True, exist_ok=True)
        local = folder / f"{video_id}.mp4"
        progress = {"lease": lease, "stage": "download", "done": 0, "total": size_bytes}

        def downloaded(done: int, total: int) -> None:
            progress["stage"] = "download"
            progress["done"] = done
            progress["total"] = total or size_bytes

        with self._cond:
            self._beats[video_id] = progress
        started = time.monotonic()
        try:
            fetched = _fetch_video(video_id, lease, local, expected, downloaded)
        except Exception as exc:
            print(f"Chưa giữ được video {video_id} trên PC: {type(exc).__name__}.")
            forget_local(local)
            with self._cond:
                self._beats.pop(video_id, None)
            _release(video_id, lease, "fail", f"{type(exc).__name__}: {exc}")
            return
        if not fetched or self._stop.is_set():
            forget_local(local)
            with self._cond:
                self._beats.pop(video_id, None)
            if self._stop.is_set():
                _release(video_id, lease, "return")
            else:
                _release(video_id, lease, "fail", "Tải về không khớp mã kiểm.")
            return
        progress["stage"] = "hold"
        progress["done"] = 1
        progress["total"] = 1
        job["download_s"] = round(time.monotonic() - started, 1)
        with self._cond:
            self._ready.append(job)
            self.stages.append("hold")
            self._save_locked()
            held = self._waiting_locked()
            self._cond.notify_all()
        size_mb = max(1, size_bytes // (1024 * 1024))
        print(f"Đã tải sẵn video {video_id} ({size_mb} MB). PC đang giữ {_gb_text(held)} video chờ đọc.")


def _read_one(job: dict, on_ready=None) -> str:
    global _IDLE
    _IDLE = False
    video_id = int(job["id"])
    lease = str(job["lease"])
    expected = str(job.get("sha256") or "")
    folder = CACHE / "videos"
    folder.mkdir(parents=True, exist_ok=True)
    local = folder / f"{video_id}.mp4"
    size_bytes = max(0, int(job.get("bytes") or 0))
    progress: dict = {"stage": "download", "done": 0, "total": size_bytes}

    def downloaded(done: int, total: int) -> None:
        progress.update(stage="download", done=done, total=total or size_bytes)

    def reading(stage: str, done: int, total: int) -> None:
        progress.update(stage=stage, done=done, total=total)

    parked = cached_ready(local, expected, size_bytes)
    if parked:
        progress.update(stage="extract", done=0, total=0)
    stop = threading.Event()
    beater = threading.Thread(target=_beat, args=(video_id, lease, stop, progress), daemon=True)
    beater.start()
    started = time.monotonic()
    posted_ok = False
    release_reason = "fail"
    release_error = ""
    try:
        # File chưa có thì chừa chỗ cho file và khung tạm. File đã có thì chỉ chừa chỗ khung.
        # Giữ 20% ổ đĩa không đụng tới.
        if not _disk_ready(size_bytes if parked else size_bytes * 2):
            if parked:
                forget_local(local)
            print(f"Video {video_id} cần nhiều chỗ hơn phần PC còn giữ. Trả máy chủ đọc.")
            release_reason = "disk"
            return "disk"
        if parked:
            if not _lease_open(video_id, lease):
                print(f"Máy chủ đã nhận lại video {video_id}.")
                forget_local(local)
                posted_ok = True
                return ""
            download_s = float(job.get("download_s") or 0)
            print(f"Video {video_id} đã có trên PC. Bắt đầu đọc.")
        else:
            size_mb = max(1, size_bytes // (1024 * 1024))
            print(f"Đang tải video {video_id} ({size_mb} MB).")
            if not _fetch_video(video_id, lease, local, expected, downloaded):
                print(f"Video {video_id} tải về không khớp. Trả lại hàng chờ.")
                release_error = "Tải về không khớp mã kiểm."
                return "fail"
            download_s = time.monotonic() - started
        if stop.is_set():
            print(f"Máy chủ đã nhận lại video {video_id}.")
            return ""
        if on_ready is not None:
            try:
                on_ready()
            except Exception as exc:
                print(f"Chưa tải sẵn video kế: {type(exc).__name__}.")
        if parked:
            print(f"Đang đọc video {video_id}.")
        else:
            print(f"Đang đọc video {video_id}. Tải mất {download_s:.0f} giây.")
        read_started = time.monotonic()
        result, frames = _read(local, reading)
        read_s = time.monotonic() - read_started
        if stop.is_set():
            print(f"Máy chủ đã nhận lại video {video_id} trước khi gửi kết quả.")
            return ""
        progress.update(stage="send", done=0, total=0)
        status, body = _json(
            "POST",
            f"/v1/pc/videos/{video_id}/result",
            {"lease": lease, **_payload(result), "download_s": round(download_s, 1), "read_s": round(read_s, 1)},
            timeout=120,
        )
        if status == 409:
            print(f"Máy chủ đã nhận lại video {video_id}. Bỏ kết quả lần này.")
            posted_ok = True
            return ""
        if status != 200 or not body.get("ok"):
            print(f"Chưa gửi được kết quả video {video_id}. Trả lại hàng chờ.")
            release_error = f"Máy chủ chưa nhận kết quả (mã {status})."
            return "fail"
        posted_ok = True
        spent = time.monotonic() - started
        print(
            f"Video {video_id} xong trong {spent:.0f} giây (tải {download_s:.0f}, đọc {read_s:.0f}), {len(frames)} khung: "
            f"{len(result.rows)} hàng đủ, {len(result.review)} cần xem, {len(result.unopened)} chưa mở hồ sơ. "
            "Đã gửi lên máy chủ. Đã xóa file trên PC."
        )
        return "ok"
    except Exception as exc:
        print(f"Video {video_id} chưa đọc xong: {exc}. Trả lại hàng chờ.")
        release_error = f"{type(exc).__name__}: {exc}"
        return "fail"
    finally:
        stop.set()
        forget_local(local)
        if not posted_ok:
            _release(video_id, lease, release_reason, release_error)


def main() -> int:
    global _IDLE, _DISK_WAIT, _RAM_WAIT, _WAIT_NOTE, _AUTH_NOTE
    if not _configured():
        print("File này chưa được tải từ trang video. Mở trang máy chủ và bấm Tải bản kết nối PC.")
        return 1
    waited = False
    while not _single_instance():
        if not waited:
            print("PC này đã có một chương trình nối đang chạy. Bản này chờ, không nhận thêm video.")
            waited = True
        time.sleep(30)
    _limit_pc()
    _windows_temp()
    name = pc_name()
    print("PC đang nối máy chủ. Cứ để cửa sổ này mở. Đóng cửa sổ là PC ngừng đọc.")
    print(f"PC này tên là {name}. Nhiều PC có thể mở cùng lúc, mỗi máy đọc một video đang chờ.")
    print(
        f"Trong lúc đang đọc, PC giữ tới {_gb_text(PC_CACHE_BYTES)} video chờ trên đĩa của máy. "
        "Đọc xong video nào thì xóa video đó khỏi máy."
    )
    print("PC dùng tối đa 80% CPU, RAM và ổ đĩa. 20% còn lại để máy vẫn chạy ổn.")
    ready = False
    cache = _Cache()
    cache.start(name)
    while True:
        try:
            if _update_connector():
                cache.close(False)
                return _relaunch()
            status, manifest = _json("GET", "/v1/pc/reader", timeout=120)
            if status == 401:
                _auth_lost(manifest)
                continue
            _AUTH_NOTE = False
            if status != 200:
                print("Chưa lấy được bộ đọc. Thử lại sau.")
                time.sleep(10)
                continue
            listed = manifest.get("files")
            names = [item for item in listed if isinstance(item, str)] if isinstance(listed, list) else []
            if len(names) != (len(listed) if isinstance(listed, list) else 0):
                print("Bộ đọc trên máy chủ không đúng. Thử lại sau.")
                time.sleep(20)
                continue
            reader_ready, reader_relaunch = _sync_reader(str(manifest.get("sha256") or ""), names)
            weights = manifest.get("weights") if isinstance(manifest.get("weights"), dict) else None
            tessdata = manifest.get("tessdata") if isinstance(manifest.get("tessdata"), dict) else None
            plain = manifest.get("tessdata_default") if isinstance(manifest.get("tessdata_default"), dict) else None
            runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else None
            weights_ready, weights_relaunch = _sync_weights(weights)
            tess_ready, tess_relaunch = _sync_tessdata(tessdata)
            plain_ready, plain_relaunch = _sync_plain_letters(plain)
            runtime_ready, runtime_relaunch = _sync_runtime(runtime, weights is not None)
            if not (reader_ready and weights_ready and tess_ready and plain_ready and runtime_ready):
                _status(name, "setup", "Đang tải hoặc cài bộ đọc.")
                time.sleep(30)
                continue
            if reader_relaunch or weights_relaunch or tess_relaunch or plain_relaunch or runtime_relaunch:
                print("Đã cập nhật bộ đọc cho khớp máy chủ. PC chạy lại.")
                cache.close(False)
                return _relaunch()
            _prepare_env()
            if not ready:
                if not _tools_ok() or not _ensure_runtime():
                    print("PC chưa sẵn sàng. Cứ để cửa sổ này mở, PC tự thử lại.")
                    _status(name, "setup", "Thiếu công cụ đọc. PC tự thử lại.")
                    time.sleep(20)
                    continue
                print("PC đang đọc thử một ảnh mẫu bằng bộ đọc của máy chủ.")
                _status(name, "testing")
                problem = _self_test()
                if problem:
                    print(f"PC đọc thử chưa đúng: {problem}. PC chưa nhận video, 2 phút nữa thử lại.")
                    _status(name, "test-failed", problem)
                    time.sleep(120)
                    continue
                print("PC đọc thử đúng. Bắt đầu nhận video.")
                ready = True
            disk_ok = _disk_ready(512 * 1024 * 1024)
            if not disk_ok:
                cache.allow(False)
                if not _DISK_WAIT:
                    print("Ổ đĩa PC đã dùng quá 80%. PC chờ, không nhận video cho đến khi còn chỗ.")
                    _DISK_WAIT = True
                _status(name, "wait-disk")
            else:
                _DISK_WAIT = False
            if not _ram_ready():
                cache.allow(False)
                if not _RAM_WAIT:
                    print("RAM PC đã dùng quá 80%. PC chờ, không nhận video cho đến khi máy còn chỗ.")
                    _RAM_WAIT = True
                _status(name, "wait-ram")
                time.sleep(30)
                continue
            _RAM_WAIT = False
            if disk_ok:
                cache.allow(True)
            # Hết chỗ vẫn đọc video đã tải, vì đọc xong thì file được xóa và ổ đĩa trống ra.
            job = cache.take(5 if disk_ok else 0.2)
            if isinstance(job, dict):
                _IDLE = False
                try:
                    outcome = _read_one(job)
                finally:
                    cache.finished()
                if outcome == "disk":
                    time.sleep(45)
                elif outcome == "fail":
                    time.sleep(20)
                continue
            if not disk_ok:
                time.sleep(30)
                continue
            if cache.http_status() == 401:
                _auth_lost(cache.control())
                continue
            body = cache.control()
            if body:
                _apply_control(body)
            wait = str(body.get("wait") or "")
            if wait:
                notes = {
                    "paused": "Trang đã tạm dừng PC này. Cửa sổ vẫn mở, PC chưa nhận video mới cho đến khi trang cho đọc tiếp.",
                    "schedule": f"Ngoài giờ được đọc ({body.get('schedule') or ''}). PC chờ tới giờ rồi tự nhận video.",
                    "excluded": (
                        "PC này tạm bị loại vì đọc hỏng nhiều video liền nhau"
                        f" ({body.get('message') or 'không rõ lý do'}). Trên trang, bấm Cho đọc lại."
                    ),
                }
                note = notes.get(wait, "PC đang chờ trang cho đọc.")
                if note != _WAIT_NOTE:
                    print(note)
                    _WAIT_NOTE = note
                time.sleep(30)
                continue
            if _WAIT_NOTE:
                print("Trang đã cho PC đọc tiếp.")
                _WAIT_NOTE = ""
            if not _IDLE:
                print("Chưa có video đang chờ. PC vẫn mở, có video mới thì đọc tiếp.")
                _IDLE = True
            time.sleep(5)
        except KeyboardInterrupt:
            cache.close(True)
            print("Đã dừng.")
            return 0
        except urllib.error.URLError:
            print("Mất kết nối máy chủ. Thử lại sau.")
            time.sleep(10)
        except Exception as exc:
            print(f"PC gặp lỗi và vẫn mở, thử lại sau: {type(exc).__name__}: {str(exc)[:200]}")
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
