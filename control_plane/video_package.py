"""Gói mã để PC đọc video. Không gồm token, cơ sở dữ liệu, hay thư viện GPU."""

from __future__ import annotations

import zipfile
from pathlib import Path

from control_plane.version import VIDEO_WORKER_BUILD

PACKAGE_NAME = f"fb-poller-video-worker-{VIDEO_WORKER_BUILD}.zip"
SETUP_NAME = "FbPollerVideo.zip"
_REQUIREMENTS = "pillow\n"
_GUIDE = """fb-poller video worker

PC đọc video bằng CPU và Tesseract (vie+eng).
Gói này không chứa token và không chứa dữ liệu đã lưu.
Thư viện GPU không nằm trong gói. Card NVIDIA vẫn đọc bằng CPU cho đến khi tự cài easyocr hoặc paddle sau.
Token và địa chỉ hub nằm ngoài thư mục current, nên bản cập nhật không xóa chúng.
Gói có kèm chương trình nối hub. PC rảnh thì chép chương trình mới ra ngoài current và giữ bản cũ trong file .prev.
"""
_SETUP_GUIDE = """FbPollerVideo

1. Giải nén thư mục này.
2. Bấm đúp Cai-dat.bat.
3. Dán token hiện trên trang tải, rồi Enter.
4. Đợi cửa sổ báo đã cài. Có thể đóng cửa sổ.

Máy tự chạy khi đăng nhập. Mất mạng thì tự nối lại.
Khi hub nâng cấp, máy tự lấy bản mới lúc không đang đọc video.
Không cần tải lại file này.
"""
_SOURCES = (
    "pc_agent/video_worker.py",
    "pc_agent/video_watchdog.py",
    "pc_agent/windows/Install-VideoWorker.ps1",
    "pc_agent/windows/Run-VideoWorker.ps1",
    "pc_agent/windows/Cai-dat.bat",
    "control_plane/__init__.py",
    "control_plane/gpu_read.py",
    "control_plane/screen_steps.py",
    "control_plane/screen_people.py",
    "control_plane/people.py",
    "control_plane/tesseract_keep.py",
)
_SETUP_FILES = (
    ("pc_agent/windows/Cai-dat.bat", "Cai-dat.bat"),
    ("pc_agent/windows/Install-VideoWorker.ps1", "Install-VideoWorker.ps1"),
    ("pc_agent/windows/Run-VideoWorker.ps1", "Run-VideoWorker.ps1"),
    ("pc_agent/video_watchdog.py", "video_watchdog.py"),
)


def ensure_video_package(repo_root: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / PACKAGE_NAME
    marker = dest_dir / "video-worker-build.txt"
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == str(VIDEO_WORKER_BUILD):
        return dest
    temporary = dest_dir / (PACKAGE_NAME + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VERSION", str(VIDEO_WORKER_BUILD))
        archive.writestr("requirements-cpu.txt", _REQUIREMENTS)
        archive.writestr("HUONG-DAN.txt", _GUIDE)
        for name in _SOURCES:
            path = repo_root / name
            if not path.is_file():
                raise FileNotFoundError(name)
            archive.write(path, name)
    temporary.replace(dest)
    marker.write_text(str(VIDEO_WORKER_BUILD), encoding="utf-8")
    return dest


def ensure_pc_setup_package(repo_root: Path, dest_dir: Path) -> Path:
    """Zip tải về trên PC. Không có token. Link cố định, nội dung theo bản worker."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / SETUP_NAME
    marker = dest_dir / "pc-setup-build.txt"
    if dest.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == str(VIDEO_WORKER_BUILD):
        return dest
    temporary = dest_dir / (SETUP_NAME + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("HUONG-DAN.txt", _SETUP_GUIDE)
        for source, name in _SETUP_FILES:
            path = repo_root / source
            if not path.is_file():
                raise FileNotFoundError(source)
            archive.write(path, name)
    temporary.replace(dest)
    marker.write_text(str(VIDEO_WORKER_BUILD), encoding="utf-8")
    return dest
