"""Gói phần mềm để điện thoại và máy khác tải. Không gồm token hay cơ sở dữ liệu."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from control_plane.version import IPHONE_BUILD

PACKAGE_NAME = f"fb-poller-iphone-{IPHONE_BUILD}.zip"
_FILES = (
    "iphone.html",
    "dashboard.html",
    "phone.html",
    "sample-people.html",
    "manifest.webmanifest",
    "apple-touch-icon.png",
    "watch.js",
    "version.js",
)
_GUIDE = """fb-poller — đường truyền tải

Trên iPhone, mở Safari rồi vào trang /tai của hub.
Bấm Mở app. Sau đó bấm Chia sẻ, rồi Thêm vào Màn hình chính.

Gói zip này là bản giao diện để lưu. App chạy khi mở /iphone trên hub.
Không có token và không có dữ liệu đã lưu trong gói này.
"""


def ensure_package(static_dir: Path, data_dir: Path) -> Path:
    folder = data_dir / "delivery"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / PACKAGE_NAME
    marker = folder / "build.txt"
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == str(IPHONE_BUILD):
        return dest
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fb-poller/HUONG-DAN.txt", _GUIDE)
        for name in _FILES:
            path = static_dir / name
            if path.is_file():
                archive.write(path, f"fb-poller/{name}")
    marker.write_text(str(IPHONE_BUILD), encoding="utf-8")
    return dest


DANHBA_NAME = "danh-ba-iphone.zip"
_DANHBA_GUIDE = """Danh bạ — chạy trên iPhone

Cách cài để máy tự chạy và tự lấy bản mới:
1. Mở Safari trên iPhone, vào trang /danhba/ của hub.
2. Bấm Chia sẻ, rồi Thêm vào Màn hình chính.
3. Mở icon Danh bạ. Lần sau có bản mới, mở lại icon là máy tự cập nhật.

Gói zip này là mã nguồn Xcode, không phải file cài trên iPhone.
Trong app, tên mặc định là Khach. Dán số, bấm Nạp lên iPhone.
App chia mỗi 5000 số một nhóm. Một số chỉ nằm trong một nhóm.

Gói này không kèm danh bạ của bạn.
"""


def _danhba_skipped(path: Path) -> bool:
    if path.name.startswith(".") or path.name == ".DS_Store":
        return True
    return "xcuserdata" in path.parts


def _danhba_stamp(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if not path.is_file() or _danhba_skipped(path):
            continue
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(_DANHBA_GUIDE.encode())
    return digest.hexdigest()


def ensure_danhba_package(source: Path, data_dir: Path) -> Path | None:
    if not source.is_dir():
        return None
    folder = data_dir / "delivery"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / DANHBA_NAME
    marker = folder / "danhba-stamp.txt"
    stamp = _danhba_stamp(source)
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == stamp:
        return dest
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DanhBa/HUONG-DAN.txt", _DANHBA_GUIDE)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or _danhba_skipped(path):
                continue
            archive.write(path, f"DanhBa/{path.relative_to(source).as_posix()}")
    marker.write_text(stamp, encoding="utf-8")
    return dest


VIDEOUP_NAME = "videoup.zip"
_VIDEOUP_GUIDE = """VideoUp — tải video lên VPS (cắt khúc + nền)

1. Giải nén, mở VideoUp.xcodeproj trên Mac.
2. Signing → Team Apple ID → cắm iPhone → Run.
3. Trong app: điền URL hub (http://IP:8088), Token, tên máy.
4. Chọn nhiều video — khóa máy vẫn gửi tiếp; VPS xếp hàng đọc OCR.

Safari vẫn tải được trên trang video (cũng cắt khúc), nhưng không upload nền.
"""


def ensure_videoup_package(source: Path, data_dir: Path) -> Path | None:
    if not source.is_dir():
        return None
    folder = data_dir / "delivery"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / VIDEOUP_NAME
    marker = folder / "videoup-stamp.txt"
    stamp = _danhba_stamp(source) + hashlib.sha256(_VIDEOUP_GUIDE.encode()).hexdigest()[:16]
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == stamp:
        return dest
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VideoUp/HUONG-DAN.txt", _VIDEOUP_GUIDE)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or _danhba_skipped(path):
                continue
            archive.write(path, f"VideoUp/{path.relative_to(source).as_posix()}")
    marker.write_text(stamp, encoding="utf-8")
    return dest
