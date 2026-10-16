"""Chương trình đọc video trên PC. Tải một lần từ trang video của máy chủ.

Mỗi vòng nó hỏi máy chủ bản đọc và bản nối hiện tại. File trên máy chủ đổi
thì PC tự lấy bản mới, kể cả cách đọc và cách tải, không cần cài lại.
PC chỉ dùng tối đa 80% CPU, RAM và ổ đĩa. Đường truyền không bị giới hạn tốc độ.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CONNECTOR_VERSION = 3
# Trần tài nguyên của PC. Máy chủ không đặt biến này nên máy chủ vẫn dùng hết nhân.
PC_HEADROOM = 0.8
SERVER = "__GHIAM_SERVER__"
TOKEN = "__GHIAM_TOKEN__"

CACHE = Path(__file__).resolve().parent / "ghiam-pc-data"
_LOADED = False
_IDLE = False
_DISK_WAIT = False
_RAM_WAIT = False


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


def _download(path: str, dest: Path, timeout: float = 600) -> None:
    """timeout là số giây không có byte mới, không phải thời gian tải cả file.

    Không nén lại file đã nén. Bộ đệm lớn để đường truyền không bị cắt thành mẩu nhỏ.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(
        _url(path),
        headers={"Authorization": f"Bearer {TOKEN}", "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        temporary.replace(dest)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _tools_ok() -> bool:
    ok = True
    if shutil.which("ffmpeg") is None:
        print("Chưa có ffmpeg.")
        print("Windows: cài ffmpeg và thêm vào PATH.")
        print("Ubuntu: sudo apt install ffmpeg")
        ok = False
    if shutil.which("tesseract") is None:
        print("Chưa có tesseract.")
        print("Windows: cài Tesseract, thêm vào PATH, gồm gói chữ tiếng Việt (vie) và tiếng Anh (eng).")
        print("Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng")
        ok = False
    else:
        listed = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
        if "vie" not in listed or "eng" not in listed:
            print("Tesseract thiếu tiếng Việt hoặc tiếng Anh.")
            print("Ubuntu: sudo apt install tesseract-ocr-vie tesseract-ocr-eng")
            ok = False
    return ok


def _ensure_import(module: str, package: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        print(f"Đang cài {package}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        except subprocess.CalledProcessError:
            print(f"Chưa cài được {package}.")
            return False
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _ensure_runtime() -> bool:
    if not _ensure_import("PIL", "pillow"):
        return False
    if not _ensure_import("numpy", "numpy"):
        return False
    return True


def _maybe_vietocr(weights: Path) -> None:
    if not weights.is_file():
        return
    marker = CACHE / "vietocr.tried"
    try:
        __import__("torch")
        __import__("vietocr")
        return
    except ImportError:
        pass
    if marker.is_file():
        return
    print("Máy chủ có bộ đọc dấu tiếng Việt. Lần đầu PC cài thêm, có thể mất một lúc.")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "torch", "vietocr"])
    except subprocess.CalledProcessError:
        print("Chưa cài được bộ đọc dấu. PC vẫn đọc bằng tesseract.")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")


def _relaunch() -> int:
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


def _marker_matches(marker: Path, digest: str) -> bool:
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest


def _sync_reader(digest: str) -> bool:
    if not digest:
        return False
    marker = CACHE / "reader.sha256"
    if _marker_matches(marker, digest):
        return False
    print("Đang lấy bộ đọc hiện trên máy chủ.")
    archive_path = CACHE / "reader.zip"
    _download("/v1/pc/reader.zip", archive_path)
    target = CACHE / "reader"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                continue
            archive.extract(info, target)
    marker.write_text(digest, encoding="utf-8")
    archive_path.unlink(missing_ok=True)
    return _LOADED


def _sync_weights(info: dict | None) -> bool:
    weights = CACHE / "weights" / "vgg_transformer.pth"
    marker = CACHE / "weights.sha256"
    if not info:
        return False
    digest = str(info.get("sha256") or "")
    if not digest or _marker_matches(marker, digest):
        return False
    print("Đang lấy bộ đọc dấu tiếng Việt.")
    _download("/v1/pc/weights", weights, timeout=1800)
    marker.write_text(digest, encoding="utf-8")
    return _LOADED


def _sync_tessdata(info: dict | None) -> bool:
    marker = CACHE / "tessdata.sha256"
    if not info:
        return False
    digest = str(info.get("sha256") or "")
    if not digest or _marker_matches(marker, digest):
        return False
    print("Đang lấy bộ chữ của máy chủ.")
    archive_path = CACHE / "tessdata.zip"
    _download("/v1/pc/tessdata.zip", archive_path)
    target = CACHE / "tessdata"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info_item in archive.infolist():
            name = info_item.filename
            if name.startswith("/") or ".." in Path(name).parts:
                continue
            archive.extract(info_item, target)
    marker.write_text(digest, encoding="utf-8")
    archive_path.unlink(missing_ok=True)
    return _LOADED


def _prepare_env() -> None:
    _limit_pc()
    weights = CACHE / "weights" / "vgg_transformer.pth"
    if weights.is_file():
        os.environ["DOC_VIDEO_VIETOCR_WEIGHTS"] = str(weights)
    tessdata = CACHE / "tessdata"
    if (tessdata / "eng.traineddata").is_file() and (tessdata / "vie.traineddata").is_file():
        os.environ["DOC_VIDEO_TESSDATA"] = str(tessdata)


def _read(video: Path):
    """Nạp bộ đọc vừa tải. Gói doc_video chỉ có sau khi giải nén, nên nhập ở đây."""
    global _LOADED
    root = str(CACHE / "reader")
    if root not in sys.path:
        sys.path.insert(0, root)
    _prepare_env()
    from doc_video.pipeline import read_video

    _LOADED = True
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


def _beat(video_id: int, lease: str, stop: threading.Event) -> None:
    while not stop.wait(20):
        try:
            status, _body = _json("POST", f"/v1/pc/videos/{video_id}/heartbeat", {"lease": lease}, timeout=30)
        except urllib.error.URLError:
            continue
        if status == 409:
            stop.set()
            return


def _release(video_id: int, lease: str) -> None:
    try:
        _json("POST", f"/v1/pc/videos/{video_id}/release", {"lease": lease}, timeout=30)
    except urllib.error.URLError:
        return


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


def _fetch_video(video_id: int, lease: str, local: Path, expected: str) -> bool:
    url = f"/v1/pc/videos/{video_id}/file?lease={lease}"
    for attempt in range(3):
        try:
            _download(url, local, timeout=600)
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


def _read_one(job: dict) -> str:
    global _IDLE
    _IDLE = False
    video_id = int(job["id"])
    lease = str(job["lease"])
    expected = str(job.get("sha256") or "")
    folder = CACHE / "videos"
    folder.mkdir(parents=True, exist_ok=True)
    local = folder / f"{video_id}.mp4"
    stop = threading.Event()
    beater = threading.Thread(target=_beat, args=(video_id, lease, stop), daemon=True)
    beater.start()
    started = time.monotonic()
    posted_ok = False
    try:
        size_bytes = max(0, int(job.get("bytes") or 0))
        # File tải về cộng khung hình tạm. Giữ 20% ổ đĩa không đụng tới.
        if not _disk_ready(size_bytes * 2):
            print(f"Video {video_id} cần nhiều chỗ hơn phần PC còn giữ. Trả máy chủ đọc.")
            return "disk"
        size_mb = max(1, size_bytes // (1024 * 1024))
        print(f"Đang tải video {video_id} ({size_mb} MB).")
        if not _fetch_video(video_id, lease, local, expected):
            print(f"Video {video_id} tải về không khớp. Trả lại hàng chờ.")
            return ""
        if stop.is_set():
            print(f"Máy chủ đã nhận lại video {video_id}.")
            return ""
        print(f"Đang đọc video {video_id}.")
        result, frames = _read(local)
        if stop.is_set():
            print(f"Máy chủ đã nhận lại video {video_id} trước khi gửi kết quả.")
            return ""
        status, body = _json(
            "POST",
            f"/v1/pc/videos/{video_id}/result",
            {"lease": lease, **_payload(result)},
            timeout=120,
        )
        if status == 409:
            print(f"Máy chủ đã nhận lại video {video_id}. Bỏ kết quả lần này.")
            posted_ok = True
            return ""
        if status != 200 or not body.get("ok"):
            print(f"Chưa gửi được kết quả video {video_id}. Trả lại hàng chờ.")
            return ""
        posted_ok = True
        spent = time.monotonic() - started
        print(
            f"Video {video_id} xong trong {spent:.0f} giây, {len(frames)} khung: "
            f"{len(result.rows)} hàng đủ, {len(result.review)} cần xem, {len(result.unopened)} chưa mở hồ sơ. "
            "Đã gửi lên máy chủ."
        )
        return ""
    except Exception as exc:
        print(f"Video {video_id} chưa đọc xong: {exc}. Trả lại hàng chờ.")
        return ""
    finally:
        stop.set()
        local.unlink(missing_ok=True)
        if not posted_ok:
            _release(video_id, lease)


def main() -> int:
    global _IDLE, _DISK_WAIT, _RAM_WAIT
    if not _configured():
        print("File này chưa được tải từ trang video. Mở trang máy chủ và bấm Tải chương trình đọc trên PC.")
        return 1
    if not _tools_ok() or not _ensure_runtime():
        return 1
    _limit_pc()
    name = pc_name()
    print("PC đang nối máy chủ. Cứ để cửa sổ này mở. Đóng cửa sổ là PC ngừng đọc.")
    print(f"PC này tên là {name}. Nhiều PC có thể mở cùng lúc, mỗi máy đọc một video đang chờ.")
    print("PC dùng tối đa 80% CPU, RAM và ổ đĩa. 20% còn lại để máy vẫn chạy ổn.")
    while True:
        try:
            if _update_connector():
                return _relaunch()
            status, manifest = _json("GET", "/v1/pc/reader", timeout=120)
            if status != 200:
                print("Chưa lấy được bộ đọc. Thử lại sau.")
                time.sleep(10)
                continue
            relaunch = _sync_reader(str(manifest.get("sha256") or ""))
            weights = manifest.get("weights") if isinstance(manifest.get("weights"), dict) else None
            tessdata = manifest.get("tessdata") if isinstance(manifest.get("tessdata"), dict) else None
            relaunch = _sync_weights(weights) or relaunch
            relaunch = _sync_tessdata(tessdata) or relaunch
            if relaunch:
                print("Đã có bộ đọc mới. Chạy lại để đọc giống máy chủ.")
                return _relaunch()
            _maybe_vietocr(CACHE / "weights" / "vgg_transformer.pth")
            _prepare_env()
            if not _disk_ready(512 * 1024 * 1024):
                if not _DISK_WAIT:
                    print("Ổ đĩa PC đã dùng quá 80%. PC chờ, không nhận video cho đến khi còn chỗ.")
                    _DISK_WAIT = True
                time.sleep(30)
                continue
            _DISK_WAIT = False
            if not _ram_ready():
                if not _RAM_WAIT:
                    print("RAM PC đã dùng quá 80%. PC chờ, không nhận video cho đến khi máy còn chỗ.")
                    _RAM_WAIT = True
                time.sleep(30)
                continue
            _RAM_WAIT = False
            status, body = _json("POST", "/v1/pc/claim", {"name": name}, timeout=120)
            if status != 200:
                print("Chưa nhận được video. Thử lại sau.")
                time.sleep(10)
                continue
            job = body.get("video")
            if not isinstance(job, dict):
                if not _IDLE:
                    print("Chưa có video đang chờ. PC vẫn mở, có video mới thì đọc tiếp.")
                    _IDLE = True
                time.sleep(5)
                continue
            if _read_one(job) == "disk":
                # Nhả video trước, rồi chờ máy chủ nhận. Không nhận lại ngay.
                time.sleep(45)
        except KeyboardInterrupt:
            print("Đã dừng.")
            return 0
        except urllib.error.URLError:
            print("Mất kết nối máy chủ. Thử lại sau.")
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
