"""Tải video theo từng mảnh nhỏ để mạng đứt vẫn nối tiếp được.

Một video lớn gửi trong một kết nối thì đứt ở giữa là mất hết phần đã lên.
Mỗi mảnh là một yêu cầu ngắn. Byte đã ghi nằm trên đĩa. Lần sau chỉ gửi phần còn thiếu.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from control_plane.video_scan import IMAGE_SUFFIXES, VIDEO_SUFFIXES

_ALLOWED = VIDEO_SUFFIXES | IMAGE_SUFFIXES
_UPLOAD_ID = re.compile(r"^[0-9a-f]{16,64}$")
MAX_CHUNK_BYTES = 8 * 1024 * 1024
PARTIAL_TTL_SECONDS = 6 * 3600
DONE_TTL_SECONDS = 2 * 3600
LOCK_STALE_SECONDS = 120
FINISH_STALE_SECONDS = 600
# Khi đĩa chặt, bản tải dở lâu hơn mức này nhường chỗ. Bản mới hơn vẫn nối tiếp được.
TIGHT_PARTIAL_SECONDS = 30 * 60


class UploadRejected(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
        *,
        received: int | None = None,
        restart: bool = False,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.received = received
        self.restart = restart


def check_upload_id(raw: str) -> str:
    upload_id = str(raw or "").strip().lower()
    if not _UPLOAD_ID.fullmatch(upload_id):
        raise UploadRejected(400, "Mã tải không hợp lệ.")
    return upload_id


def _folder(root: Path) -> Path:
    folder = root / "partial"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _paths(root: Path, upload_id: str) -> tuple[Path, Path, Path, Path]:
    folder = _folder(root)
    return (
        folder / f"{upload_id}.part",
        folder / f"{upload_id}.json",
        folder / f"{upload_id}.lock",
        folder / f"{upload_id}.done",
    )


def _group_key(name: str) -> str:
    key = name
    for suffix in (".part", ".json", ".done", ".lock", ".tmp"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def reclaim_stale_partials(
    root: Path,
    *,
    keep_seconds: int = TIGHT_PARTIAL_SECONDS,
    now: float | None = None,
) -> int:
    """Xóa bản tải dở đã nằm im. Bản đang gửi trong keep_seconds được giữ để điện thoại nối tiếp."""
    folder = root / "partial"
    if not folder.is_dir():
        return 0
    moment = time.time() if now is None else now
    groups: dict[str, list[Path]] = {}
    for path in folder.iterdir():
        groups.setdefault(_group_key(path.name), []).append(path)
    freed = 0
    for paths in groups.values():
        newest = 0.0
        sized: list[tuple[Path, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            newest = max(newest, stat.st_mtime)
            sized.append((path, stat.st_size if path.is_file() else 0))
        if newest and moment - newest < keep_seconds:
            continue
        for path, size in sized:
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
            freed += size
    return freed


def sweep_parts(root: Path, now: float | None = None, *, locks: bool = True) -> int:
    """Xóa mảnh bỏ dở quá hạn và kết quả tạm đã trả cho trình duyệt.

    locks=False giữ thư mục khóa. Lần tự sửa dùng cách đó: khóa đang giữ một
    lần ghi dài không bị gỡ chỉ vì mtime chưa được làm mới.
    """
    folder = root / "partial"
    if not folder.is_dir():
        return 0
    moment = time.time() if now is None else now
    removed = 0
    for path in folder.iterdir():
        try:
            age = moment - path.stat().st_mtime
        except OSError:
            continue
        try:
            if path.is_dir():
                if locks and age > LOCK_STALE_SECONDS:
                    path.rmdir()
                    removed += 1
                continue
            if path.name.endswith(".done"):
                if age > DONE_TTL_SECONDS:
                    path.unlink(missing_ok=True)
                    removed += 1
            elif age > PARTIAL_TTL_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


@contextmanager
def _hold(
    lock_dir: Path,
    *,
    interval: float = 30,
    keep: tuple[Path, ...] = (),
) -> Iterator[None]:
    """Giữ khóa thư mục trong lúc ghi mảnh.

    Ghi và fsync một mảnh lớn có thể lâu hơn hạn khóa. Nhịp làm mới mtime của
    khóa và file đang ghi, để lần dọn của yêu cầu khác không gỡ chúng giữa chừng.
    """
    deadline = time.time() + 30
    while True:
        try:
            lock_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            try:
                if time.time() - lock_dir.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock_dir.rmdir()
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise UploadRejected(503, "Máy chủ đang ghi mảnh này. Thử lại.") from None
            time.sleep(0.02)
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(interval):
            for path in (lock_dir, *keep):
                try:
                    if path.exists():
                        os.utime(path, None)
                except OSError:
                    continue

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def _write_json(path: Path, body: dict[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def lookup_part(root: Path, upload_id: str) -> dict[str, object] | None:
    """None khi chưa có mảnh nào. Có khóa done thì trả đúng kết quả đã lưu."""
    upload_id = check_upload_id(upload_id)
    sweep_parts(root)
    part, meta_path, _lock, done = _paths(root, upload_id)
    saved = _read_json(done)
    if saved is not None:
        saved["complete"] = True
        return saved
    meta = _read_json(meta_path)
    if meta is None:
        return None
    try:
        size = int(meta.get("size") or 0)
        received = int(meta.get("received") or 0)
    except (TypeError, ValueError):
        return None
    try:
        on_disk = part.stat().st_size
    except OSError:
        on_disk = 0
    if received > on_disk:
        received = on_disk
    pending = bool(meta.get("finishing"))
    return {
        "received": received,
        "size": size,
        "name": str(meta.get("name") or ""),
        "complete": False,
        "pending": pending,
    }


def _check_declared(name: str, size: int, max_bytes: int) -> str:
    original = Path(str(name or "video.mp4")).name
    if not original or original in {".", ".."}:
        raise UploadRejected(400, "Thiếu tên video.")
    if Path(original).suffix.lower() not in _ALLOWED:
        raise UploadRejected(400, "Chỉ nhận video hoặc ảnh chụp màn hình.")
    if size <= 0:
        raise UploadRejected(400, "File rỗng.")
    if size > max_bytes:
        raise UploadRejected(413, "Video quá lớn.")
    return original[:180]


def write_part(
    root: Path,
    *,
    upload_id: str,
    name: str,
    size: int,
    device: str,
    offset: int,
    data: bytes,
    max_bytes: int,
) -> dict[str, object]:
    """Ghi một mảnh. complete=True nghĩa là lời gọi này được quyền chốt file."""
    upload_id = check_upload_id(upload_id)
    if offset < 0:
        raise UploadRejected(400, "Vị trí mảnh không hợp lệ.")
    if len(data) > MAX_CHUNK_BYTES:
        raise UploadRejected(413, "Mảnh quá lớn.")
    original = _check_declared(name, size, max_bytes)
    if offset > size or offset + len(data) > size:
        raise UploadRejected(400, "Mảnh vượt quá độ dài file.")
    sweep_parts(root)
    part, meta_path, lock, done = _paths(root, upload_id)
    with _hold(lock, keep=(part, meta_path)):
        saved = _read_json(done)
        if saved is not None:
            saved["complete"] = True
            return {"saved": saved}
        meta = _read_json(meta_path) or {}
        if meta:
            if str(meta.get("name") or "") != original or int(meta.get("size") or 0) != size:
                raise UploadRejected(409, "Mã tải đang dùng cho file khác.", restart=True)
        else:
            meta = {"name": original, "size": size, "device": device, "received": 0, "finishing": False}
        try:
            on_disk = part.stat().st_size if part.exists() else 0
        except OSError:
            on_disk = 0
        received = int(meta.get("received") or 0)
        if received > on_disk:
            received = on_disk
        meta["received"] = received
        if meta.get("finishing"):
            try:
                stale = time.time() - meta_path.stat().st_mtime > FINISH_STALE_SECONDS
            except OSError:
                stale = True
            if not stale:
                return {"received": received, "size": size, "complete": False, "pending": True}
            meta["finishing"] = False
        if offset > received:
            raise UploadRejected(409, "Máy chủ chưa có đoạn này.", received=received)
        if offset < received:
            skip = received - offset
            if skip >= len(data):
                # Mảnh này đã nằm trên đĩa. Gửi lại sau khi đứt mạng thì không ghi thêm.
                if received == size:
                    meta["received"] = received
                    meta["finishing"] = True
                    _write_json(meta_path, meta)
                    return {
                        "received": received,
                        "size": size,
                        "name": original,
                        "device": str(meta.get("device") or ""),
                        "complete": True,
                        "path": str(part),
                    }
                return {"received": received, "size": size, "complete": False, "pending": False}
            data = data[skip:]
            offset = received
        if not data and received != size:
            raise UploadRejected(400, "Mảnh rỗng.")
        if data:
            mode = "r+b" if part.exists() else "w+b"
            with part.open(mode) as handle:
                handle.seek(offset)
                handle.write(data)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            received = offset + len(data)
        meta["received"] = received
        meta["device"] = str(meta.get("device") or device)
        if received == size:
            meta["finishing"] = True
            _write_json(meta_path, meta)
            return {
                "received": received,
                "size": size,
                "name": original,
                "device": str(meta.get("device") or ""),
                "complete": True,
                "path": str(part),
            }
        meta["finishing"] = False
        _write_json(meta_path, meta)
        return {"received": received, "size": size, "complete": False, "pending": False}


def release_finish(root: Path, upload_id: str) -> None:
    """Chốt file thất bại thì nhả cờ để trình duyệt gửi lại mảnh cuối."""
    try:
        upload_id = check_upload_id(upload_id)
    except UploadRejected:
        return
    _part, meta_path, lock, _done = _paths(root, upload_id)
    with _hold(lock):
        meta = _read_json(meta_path)
        if not meta or not meta.get("finishing"):
            return
        meta["finishing"] = False
        _write_json(meta_path, meta)


def save_done(root: Path, upload_id: str, payload: dict[str, object]) -> None:
    """Nhớ kết quả đã trả, rồi xóa file tạm. Mất phản hồi thì lần hỏi sau vẫn nhận đúng kết quả đó."""
    upload_id = check_upload_id(upload_id)
    part, meta_path, lock, done = _paths(root, upload_id)
    body = dict(payload)
    body["complete"] = True
    with _hold(lock):
        _write_json(done, body)
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
