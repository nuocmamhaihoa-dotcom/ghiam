"""File gửi lên theo từng mảnh, nối tiếp được khi rớt mạng; cùng một file chỉ cần gửi một lần."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, AsyncIterator

from control_plane.settings import settings

UPLOAD_TTL_SEC = 3 * 24 * 3600
_CLEANUP_EVERY_SEC = 3600
_ID = re.compile(r"^[0-9a-f]{32}$")
_last_cleanup = 0.0


class UploadError(Exception):
    def __init__(self, status: int, detail: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra or {}


def uploads_root() -> Path:
    root = settings.data_dir / "khach-uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_name(name: str) -> str:
    base = Path(str(name or "")).name.replace("\x00", "").strip() or "khach.xlsx"
    return base[:180]


def _folder(upload_id: str) -> Path:
    if not _ID.fullmatch(upload_id or ""):
        raise UploadError(404, "Không thấy file đã gửi, hãy chọn lại file.")
    return uploads_root() / upload_id


def _read_meta(upload_id: str) -> dict[str, Any]:
    path = _folder(upload_id) / "meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UploadError(404, "Không thấy file đã gửi, hãy chọn lại file.") from exc
    if not isinstance(meta, dict):
        raise UploadError(404, "Không thấy file đã gửi, hãy chọn lại file.")
    return meta


def _write_meta(upload_id: str, meta: dict[str, Any]) -> None:
    folder = _folder(upload_id)
    tmp = folder / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / "meta.json")


def data_path(upload_id: str) -> Path:
    return _folder(upload_id) / "data"


def _received(upload_id: str) -> int:
    try:
        return data_path(upload_id).stat().st_size
    except OSError:
        return 0


def status(upload_id: str) -> dict[str, Any]:
    meta = _read_meta(upload_id)
    size = int(meta.get("size") or 0)
    received = _received(upload_id)
    return {
        "uploadId": upload_id,
        "name": str(meta.get("name") or ""),
        "size": size,
        "received": received,
        "complete": size > 0 and received == size,
    }


def create_upload(name: str, size: int, last_modified: int) -> dict[str, Any]:
    """Cùng tên + dung lượng + giờ sửa là cùng file: trả lại bản đã có, gửi tiếp từ chỗ dừng."""
    if size <= 0:
        raise UploadError(400, f"{safe_name(name)} đang trống.")
    clean = safe_name(name)
    upload_id = hashlib.sha256(f"{clean}\0{size}\0{last_modified}".encode("utf-8")).hexdigest()[:32]
    folder = uploads_root() / upload_id
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        meta = _read_meta(upload_id)
    except UploadError:
        meta = {}
    if meta.get("size") != size or meta.get("name") != clean:
        meta = {"name": clean, "size": size, "lastModified": last_modified, "createdAt": now}
        (folder / "data").unlink(missing_ok=True)
    meta["usedAt"] = now
    _write_meta(upload_id, meta)
    (folder / "data").touch(exist_ok=True)
    if _received(upload_id) > size:
        (folder / "data").unlink(missing_ok=True)
        (folder / "data").touch()
    cleanup_expired()
    return status(upload_id)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


async def append_chunk(upload_id: str, offset: int, chunks: AsyncIterator[bytes]) -> dict[str, Any]:
    """Ghi tiếp một mảnh đúng vị trí. Sai vị trí thì báo số byte máy chủ đang có để trang gửi tiếp cho đúng."""
    meta = _read_meta(upload_id)
    size = int(meta.get("size") or 0)
    fd = os.open(data_path(upload_id), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UploadError(409, "Mảnh trước còn đang ghi.", {"received": os.fstat(fd).st_size, "busy": True}) from exc
        try:
            current = os.fstat(fd).st_size
            if offset != current:
                raise UploadError(409, "Gửi lệch vị trí.", {"received": current})
            os.lseek(fd, current, os.SEEK_SET)
            written = current
            async for chunk in chunks:
                if not chunk:
                    continue
                if written + len(chunk) > size:
                    raise UploadError(400, "File gửi lên dài hơn dung lượng đã báo.", {"received": written})
                _write_all(fd, chunk)
                written += len(chunk)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return status(upload_id)


def resolve_names(names: list[str]) -> list[tuple[str, Path]]:
    """Tìm file đã gửi đủ theo đúng tên, để nối lại lần nạp khi không còn mã file."""
    wanted = [str(name or "") for name in names]
    if not wanted or any(not name for name in wanted):
        raise UploadError(404, "Danh sách file không đủ để nối tiếp.")
    needed = set(wanted)
    found: dict[str, tuple[str, Path]] = {}
    for folder in uploads_root().iterdir():
        if len(found) == len(needed):
            break
        if not folder.is_dir() or not _ID.fullmatch(folder.name):
            continue
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("name") or "")
        if name not in needed or name in found:
            continue
        size = int(meta.get("size") or 0)
        try:
            got = (folder / "data").stat().st_size
        except OSError:
            got = 0
        if size > 0 and got == size:
            found[name] = (name, data_path(folder.name))
    missing = [name for name in wanted if name not in found]
    if missing:
        sample = missing[0]
        raise UploadError(
            404,
            f"Thiếu {len(missing)} file đã gửi, không nối tiếp được. File đầu còn thiếu: {sample}",
        )
    return [found[name] for name in wanted]


def resolve(upload_ids: list[Any]) -> list[tuple[str, Path]]:
    """Mã file → (tên gốc, đường dẫn). Chỉ nhận file đã gửi đủ."""
    files: list[tuple[str, Path]] = []
    for raw in upload_ids:
        upload_id = str(raw or "")
        info = status(upload_id)
        if not info["complete"]:
            raise UploadError(409, f"{info['name']} chưa gửi xong, hãy gửi tiếp.", {"uploadId": upload_id})
        meta = _read_meta(upload_id)
        meta["usedAt"] = time.time()
        _write_meta(upload_id, meta)
        files.append((info["name"], data_path(upload_id)))
    return files


def link_or_copy(source: Path, dest: Path) -> bool:
    """Giữ một bản trong thư mục đã nạp mà không tốn thêm ổ đĩa (hard link), không được thì chép."""
    try:
        os.link(source, dest)
        return True
    except OSError:
        try:
            shutil.copy2(source, dest)
            return True
        except OSError:
            return False


def cleanup_expired(force: bool = False) -> int:
    global _last_cleanup
    now = time.time()
    if not force and now - _last_cleanup < _CLEANUP_EVERY_SEC:
        return 0
    _last_cleanup = now
    removed = 0
    for folder in uploads_root().iterdir():
        if not folder.is_dir() or not _ID.fullmatch(folder.name):
            continue
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            used = float(meta.get("usedAt") or meta.get("createdAt") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            used = folder.stat().st_mtime
        if now - used > UPLOAD_TTL_SEC:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed
