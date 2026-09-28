"""Gói phần mềm để điện thoại và máy khác tải. Không gồm token hay cơ sở dữ liệu."""

from __future__ import annotations

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
