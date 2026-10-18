"""Gói bộ đọc đang nằm trên máy chủ để PC tải về và đọc giống hệt."""

from __future__ import annotations

import functools
import hashlib
import io
import os
import re
import subprocess
import zipfile
from pathlib import Path

from control_plane.pc_connector import CONNECTOR_VERSION, TESSDATA_NAMES, bundle_sha256
from control_plane.screen_table import Review, Row, Table, Unopened

ROOT = Path(__file__).resolve().parents[1]
CONNECTOR_FILE = Path(__file__).with_name("pc_connector.py")
CONNECTOR_BAT = Path(__file__).with_name("pc_connector.bat")
LAUNCHER_FILE = Path(__file__).with_name("pc_open.bat")
OPEN_SCRIPT = Path(__file__).with_name("pc_open.ps1")
# File cài PC tải từ máy chủ, không tải từ mạng ngoài. Tên URL ngắn, tên file trên đĩa là bản đã kiểm.
PC_TOOL_FILES = {
    "python.zip": "python-3.12.7-embed-amd64.zip",
    "get-pip.py": "get-pip.py",
    "wheels.zip": "pillow-numpy-cp312-win.zip",
    "ffmpeg.zip": "ffmpeg-release-essentials.zip",
    "tesseract.exe": "tesseract-ocr-w64-setup-5.4.0.20240606.exe",
    "tesseract.zip": "tesseract-w64.zip",
    "crt.zip": "vc-crt-x64.zip",
    "runtime.zip": "runtime-cp312-win.zip",
}

READER_FILES = (
    "doc_video/__init__.py",
    "doc_video/digits.py",
    "doc_video/frame.py",
    "doc_video/join.py",
    "doc_video/ocr.py",
    "doc_video/pipeline.py",
    "doc_video/text.py",
    "doc_video/video.py",
    "doc_video/viet.py",
    "doc_video/vietocr_vgg_transformer.json",
)

_FILE_CACHE: dict[str, tuple[object, str]] = {}
MAX_PC_ROWS = 20000
_CELL = 200


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _cached_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    stat = path.stat()
    key = str(path)
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _FILE_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    digest = _sha256(path)
    _FILE_CACHE[key] = (stamp, digest)
    return digest


def code_sha256() -> str:
    for relative in READER_FILES:
        if not (ROOT / relative).is_file():
            raise FileNotFoundError(relative)
    return bundle_sha256(ROOT, READER_FILES)


def pc_tools_dir() -> Path:
    raw = os.environ.get("CONTROL_DATA_DIR", "").strip()
    base = Path(raw) if raw else ROOT / "control_data"
    return base / "pc-tools"


def runtime_info() -> dict[str, object] | None:
    """Gói thư viện Windows để PC đọc dấu bằng đúng bản máy chủ đang dùng."""
    path = pc_tools_dir() / PC_TOOL_FILES["runtime.zip"]
    digest = _cached_sha(path)
    if digest is None:
        return None
    return {"sha256": digest, "bytes": path.stat().st_size}


def reader_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in READER_FILES:
            archive.write(ROOT / relative, relative)
    return buffer.getvalue()


def weights_file() -> Path:
    return ROOT / "models" / "vgg_transformer.pth"


def weights_info() -> dict[str, object] | None:
    path = weights_file()
    digest = _cached_sha(path)
    if digest is None:
        return None
    return {"sha256": digest, "bytes": path.stat().st_size}


def tessdata_dir() -> Path:
    return ROOT / "tessdata_best"


@functools.lru_cache(maxsize=1)
def default_tessdata_dir() -> Path | None:
    """Thư mục bộ chữ tesseract dùng khi lệnh không ghi --tessdata-dir. Bước quét khung đọc bằng bộ này."""
    raw = os.environ.get("CONTROL_TESSDATA_DEFAULT", "").strip()
    if raw:
        return Path(raw)
    try:
        listed = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'"([^"]+)"', listed.stdout + "\n" + listed.stderr)
    return Path(match.group(1)) if match else None


def _letters_info(folder: Path | None) -> dict[str, object] | None:
    if folder is None:
        return None
    files = [folder / name for name in TESSDATA_NAMES]
    if not all(path.is_file() for path in files):
        return None
    total = 0
    stamps = []
    for path in files:
        stat = path.stat()
        stamps.append((stat.st_mtime_ns, stat.st_size))
        total += stat.st_size
    key = str(folder)
    cached = _FILE_CACHE.get(key)
    if cached is not None and cached[0] == tuple(stamps):
        return {"sha256": cached[1], "bytes": total}
    digest = bundle_sha256(folder, TESSDATA_NAMES)
    _FILE_CACHE[key] = (tuple(stamps), digest)
    return {"sha256": digest, "bytes": total}


def _letters_zip(folder: Path | None) -> bytes | None:
    if folder is None or _letters_info(folder) is None:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in TESSDATA_NAMES:
            archive.write(folder / name, name)
    return buffer.getvalue()


def tessdata_info() -> dict[str, object] | None:
    return _letters_info(tessdata_dir())


def tessdata_zip() -> bytes | None:
    return _letters_zip(tessdata_dir())


def default_tessdata_info() -> dict[str, object] | None:
    return _letters_info(default_tessdata_dir())


def default_tessdata_zip() -> bytes | None:
    return _letters_zip(default_tessdata_dir())


def open_script_bytes() -> bytes:
    """Bản cài PC. File .bat tải lại bản này mỗi lượt, nên PC đang mở tự nhận bản sửa mới."""
    return OPEN_SCRIPT.read_bytes()


def reader_manifest() -> dict[str, object]:
    return {
        "sha256": code_sha256(),
        "files": list(READER_FILES),
        "entry": "doc_video.pipeline:read_video",
        "version": CONNECTOR_VERSION,
        "weights": weights_info(),
        "tessdata": tessdata_info(),
        "tessdata_default": default_tessdata_info(),
        "runtime": runtime_info(),
    }


def connector_template_sha256() -> str:
    """Hash file gốc, còn chỗ điền địa chỉ. PC so hash này để biết bản nối đã đổi."""
    return hashlib.sha256(CONNECTOR_FILE.read_bytes()).hexdigest()


def connector_source(server: str, token: str) -> str:
    if not server.startswith(("http://", "https://")) or "\n" in server or "\r" in server:
        raise ValueError("server")
    if not token or "\n" in token or "\r" in token or '"' in token:
        raise ValueError("token")
    text = CONNECTOR_FILE.read_text(encoding="utf-8")
    if '__GHIAM_SERVER__' not in text or '__GHIAM_TOKEN__' not in text:
        raise RuntimeError("connector template missing placeholders")
    filled = text.replace("__GHIAM_SERVER__", server.rstrip("/")).replace("__GHIAM_TOKEN__", token)
    if "__GHIAM_" in filled:
        raise RuntimeError("connector template was not filled")
    return filled


def connector_bytes(server: str, token: str) -> bytes:
    return connector_source(server, token).encode("utf-8")


def launcher_text(server: str, token: str) -> str:
    """File .bat mở ra là PC tự cài đủ và tự nối. Địa chỉ và mã đã điền sẵn."""
    if not server.startswith(("http://", "https://")) or "\n" in server or "\r" in server:
        raise ValueError("server")
    if not token or "\n" in token or "\r" in token or '"' in token:
        raise ValueError("token")
    text = LAUNCHER_FILE.read_text(encoding="utf-8")
    if "__GHIAM_SERVER__" not in text or "__GHIAM_TOKEN__" not in text:
        raise RuntimeError("launcher template missing placeholders")
    filled = text.replace("__GHIAM_SERVER__", server.rstrip("/")).replace("__GHIAM_TOKEN__", token)
    if "__GHIAM_" in filled:
        raise RuntimeError("launcher template was not filled")
    return filled.replace("\r\n", "\n").replace("\n", "\r\n")


def launcher_bytes(server: str, token: str) -> bytes:
    return launcher_text(server, token).encode("utf-8")


def package_zip(server: str, token: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ghiam-pc.py", connector_source(server, token))
        archive.writestr("ghiam-pc.bat", CONNECTOR_BAT.read_bytes())
    return buffer.getvalue()


def _cell(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:_CELL]


def _rows(value: object, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Kết quả không đúng dạng bảng.")
    if len(value) > MAX_PC_ROWS:
        raise ValueError("Kết quả quá dài.")
    items = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Kết quả không đúng dạng bảng.")
        items.append({field: _cell(item.get(field, "")) for field in fields})
    return items


def table_from_payload(payload: dict) -> Table:
    """Đổi bảng PC gửi lên thành bảng kho kết quả đang lưu."""
    rows = _rows(payload.get("rows"), ("phone", "name", "username"))
    review = _rows(payload.get("review"), ("phone", "contact_name", "profile_name", "username", "reason"))
    unopened = _rows(payload.get("unopened"), ("phone", "name"))
    return Table(
        rows=[Row(item["phone"], item["name"], item["username"]) for item in rows if item["phone"]],
        review=[
            Review(item["phone"], item["contact_name"], item["profile_name"], item["username"], item["reason"])
            for item in review
        ],
        unopened=[Unopened(item["phone"], item["name"]) for item in unopened if item["phone"]],
    )
