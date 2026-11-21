"""Build a Windows folder that runs the address book without a VPS.

The zip contains the program, a portable Python, and a copy of the phone pool.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", "uploads", "exports"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kho", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    staging = args.cache / "DanhBaPC"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    _copy_program(args.source, staging / "contact_to_vcf")
    _write_guide(staging / "HUONG_DAN.txt")
    shutil.copyfile(args.source / "Mo-Danh-Ba.bat", staging / "Mo-Danh-Ba.bat")
    _install_python(staging / "python", staging / "vendor", args.cache)
    _copy_database(args.kho / "kho.sqlite", staging / "kho" / "kho.sqlite")
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w") as archive:
        for path in staging.rglob("*"):
            if not path.is_file():
                continue
            stored = path.suffix in {".sqlite", ".zip", ".pyd", ".dll"}
            mode = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            archive.write(path, f"DanhBaPC/{path.relative_to(staging).as_posix()}", compress_type=mode)
    temporary.replace(args.output)
    print(f"wrote {args.output} {args.output.stat().st_size}")


def _copy_program(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.relative_to(source).parts):
            continue
        if path.suffix in {".sqlite", ".pyc"}:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _write_guide(path: Path) -> None:
    path.write_text(
        """DANH BẠ TRÊN MÁY TÍNH
=====================

Bản này chạy trên máy tính. Trình duyệt chỉ mở http://127.0.0.1:8080/

1. Giải nén cả thư mục DanhBaPC.
2. Bấm đúp Mo-Danh-Ba.bat.
3. Trình duyệt mở http://127.0.0.1:8080/
4. Lần sau bấm biểu tượng Danh ba trên màn hình nền.

Kho trong file này có 15.472.123 số thuộc 7.120 danh bạ đã chia. Không xóa thư mục kho.
Những danh bạ đã chia giữ nguyên số lượng. Số đã có sẽ không bị chia lại.
Mỗi danh bạ mới là 1.000 số.
Đóng cửa sổ đen để tắt phần mềm.
""",
        encoding="utf-8",
    )


def _install_python(python_dir: Path, vendor_dir: Path, cache: Path) -> None:
    embed = cache / "python-embed.zip"
    if not embed.is_file() or embed.stat().st_size < 1_000_000:
        urllib.request.urlretrieve(PYTHON_EMBED_URL, embed)
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(embed) as archive:
        archive.extractall(python_dir)
    pth = next(python_dir.glob("python*._pth"))
    pth.write_text(
        "python312.zip\n.\n..\\vendor\n..\\contact_to_vcf\\src\nimport site\n",
        encoding="utf-8",
    )
    wheels = cache / "wheels"
    wheels.mkdir(exist_ok=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "download", "openpyxl", "-d", str(wheels)]
    )
    vendor_dir.mkdir(parents=True)
    for wheel in wheels.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(vendor_dir)


def _copy_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"Không thấy kho {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    origin = sqlite3.connect(source)
    copied = sqlite3.connect(destination)
    try:
        origin.backup(copied)
    finally:
        copied.close()
        origin.close()


if __name__ == "__main__":
    main()
