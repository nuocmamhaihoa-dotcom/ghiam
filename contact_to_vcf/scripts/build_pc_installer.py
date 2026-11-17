"""Build a Windows installer exe that contains the PC app and the phone pool."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

SFX_PAGE = "https://www.7-zip.org/a/lzma2301.7z"
CONFIG = """;!@Install@!UTF-8!
Title="Cài Danh bạ"
BeginPrompt="Cài Danh bạ vào máy tính này, kèm toàn bộ số đã chia?"
ExtractDialogText="Đang cài Danh bạ và kho số..."
InstallPath="%LOCALAPPDATA%\\DanhBa"
RunProgram="Mo-Danh-Ba.bat"
;!@InstallEnd@!
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    if not (args.folder / "Mo-Danh-Ba.bat").is_file():
        raise SystemExit(f"Không thấy bộ cài trong {args.folder}")
    if not (args.folder / "kho" / "kho.sqlite").is_file():
        raise SystemExit("Bộ cài chưa có kho số")
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sfx = _sfx_stub(args.cache)
    archive = args.cache / "danhba-pc.7z"
    if archive.exists():
        archive.unlink()
    seven = _seven_zip()
    subprocess.check_call([seven, "a", "-mx=0", "-t7z", str(archive), "."], cwd=args.folder)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    with temporary.open("wb") as handle:
        handle.write(sfx.read_bytes())
        handle.write(CONFIG.encode("utf-8"))
        with archive.open("rb") as packed:
            shutil.copyfileobj(packed, handle, length=1024 * 1024)
    temporary.replace(args.output)
    archive.unlink()
    print(f"wrote {args.output} {args.output.stat().st_size}")


def _seven_zip() -> str:
    for name in ("7z", "7zz"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("Chưa có lệnh 7z")


def _sfx_stub(cache: Path) -> Path:
    stub = cache / "7zSD.sfx"
    if stub.is_file() and stub.stat().st_size > 10_000:
        return stub
    extra = cache / "lzma-sdk.7z"
    if not extra.is_file():
        urllib.request.urlretrieve(SFX_PAGE, extra)
    extracted = cache / "lzma-sdk"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir()
    subprocess.check_call([_seven_zip(), "x", f"-o{extracted}", str(extra)], cwd=cache)
    found = next(extracted.rglob("7zSD.sfx"))
    shutil.copyfile(found, stub)
    return stub


if __name__ == "__main__":
    main()
