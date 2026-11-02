"""VPS store for PC desktop macros — record and replay still happen on the PC."""

from __future__ import annotations

import hmac
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from control_plane.settings import ROOT, settings
from pc_macro.script import script_captions
from pc_macro.store import MacroStore
from pc_macro.version import PC_MACRO_BUILD, build_package_files, package_info

router = APIRouter()


def _store() -> MacroStore:
    root = settings.data_dir / "macros"
    root.mkdir(parents=True, exist_ok=True)
    return MacroStore(root)


def _bearer_matches(authorization: str, token: str) -> bool:
    expected = f"Bearer {token}"
    if len(authorization) != len(expected):
        return False
    return hmac.compare_digest(authorization, expected)


def _auth(authorization: str | None) -> None:
    accepted = [token for token in (settings.token, settings.page_token) if token]
    if not accepted:
        return
    if not authorization or not any(_bearer_matches(authorization, token) for token in accepted):
        raise HTTPException(status_code=401, detail="unauthorized")


def _public(item: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    steps = item.get("steps") or []
    body = {
        "name": item.get("name"),
        "slug": item.get("slug"),
        "created": item.get("created"),
        "event_count": item.get("event_count"),
        "step_count": item.get("step_count") or len(steps),
        "captions": script_captions(steps)[:40],
    }
    if item.get("numbers") is not None:
        body["numbers"] = item["numbers"]
    if full:
        body["steps"] = steps
        body["events"] = item.get("events") or []
    return body


class MacroBody(BaseModel):
    name: str = ""
    slug: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    numbers: dict[str, Any] | None = None


@router.get("/v1/macros")
def list_macros(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    items = [_public(item) for item in _store().list()]
    return {"count": len(items), "items": items}


@router.get("/v1/macros/{slug}")
def get_macro(slug: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    item = _store().get(slug)
    if item is None:
        raise HTTPException(status_code=404, detail="macro not found")
    return _public(item, full=True)


@router.post("/v1/macros")
def save_macro(body: MacroBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    try:
        saved = _store().save(
            body.name,
            body.events,
            slug=body.slug,
            numbers=body.numbers,
            steps=body.steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="no replayable steps") from exc
    return {"ok": True, **_public(saved, full=True)}


@router.delete("/v1/macros/{slug}")
def delete_macro(slug: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    if not _store().delete(slug):
        raise HTTPException(status_code=404, detail="macro not found")
    return {"ok": True, "slug": slug}


def _pc_macro_files() -> dict[str, bytes]:
    package = ROOT / "pc_macro"
    if not package.is_dir():
        raise HTTPException(status_code=404, detail="pc_macro missing")
    return build_package_files(
        package,
        {
            "CHAY-O-DAY.cmd": _RUN_CMD,
            "Chay.cmd": _RUN_CMD,
            "Cai-dat.ps1": _INSTALL_PS1,
            "README.txt": _INSTALL_README,
        },
    )


def _pc_macro_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in sorted(files.items()):
            zf.writestr(f"pc-macro/{name}", raw)
    return buf.getvalue()


class RunBody(BaseModel):
    play_cmd: str | None = None
    pc_seen: int | None = None
    pc_bound: bool | None = None
    current: str | None = None
    status: str | None = None


def _run_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "pc-macro-run.json"


def _empty_run() -> dict[str, Any]:
    return {"play_cmd": "", "pc_seen": 0, "pc_bound": False, "current": "", "status": ""}


def _read_run() -> dict[str, Any]:
    path = _run_path()
    if not path.is_file():
        return _empty_run()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return _empty_run()
    if not isinstance(raw, dict):
        return _empty_run()
    cmd = str(raw.get("play_cmd") or "").strip().lower()
    if cmd not in {"run", "stop"}:
        cmd = ""
    try:
        seen = int(raw.get("pc_seen") or 0)
    except (TypeError, ValueError):
        seen = 0
    return {
        "play_cmd": cmd,
        "pc_seen": max(0, seen),
        "pc_bound": bool(raw.get("pc_bound")),
        "current": str(raw.get("current") or "")[:40],
        "status": str(raw.get("status") or "")[:40],
    }


def _write_run(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = _read_run()
    cleaned.update(item)
    _run_path().write_text(json.dumps(cleaned, ensure_ascii=False), encoding="utf-8")
    return cleaned


@router.get("/v1/pc-macro/run")
def get_pc_macro_run(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Hub Bật/Tắt and the PC app share this; the hub never types into Evoshop."""
    _auth(authorization)
    return _read_run()


@router.post("/v1/pc-macro/run")
def set_pc_macro_run(body: RunBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    patch: dict[str, Any] = {}
    if body.play_cmd is not None:
        cmd = body.play_cmd.strip().lower()
        patch["play_cmd"] = cmd if cmd in {"run", "stop"} else ""
    if body.pc_seen is not None:
        patch["pc_seen"] = max(0, int(body.pc_seen or 0))
    elif body.pc_bound is not None or body.current is not None or body.status is not None:
        patch["pc_seen"] = int(time.time())
    if body.pc_bound is not None:
        patch["pc_bound"] = bool(body.pc_bound)
    if body.current is not None:
        patch["current"] = body.current[:40]
    if body.status is not None:
        patch["status"] = body.status[:40]
    return _write_run(patch)


@router.get("/v1/pc-macro/version")
def pc_macro_version() -> dict[str, Any]:
    files = _pc_macro_files()
    info = package_info(files)
    info["size"] = len(_pc_macro_zip(files))
    return info


@router.get("/tai/pc-macro.zip")
def download_pc_macro() -> Response:
    """Zip the PC recorder so a Windows machine can run it locally."""
    files = _pc_macro_files()
    payload = _pc_macro_zip(files)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="pc-macro.zip"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Pc-Macro-Build": str(PC_MACRO_BUILD),
        },
    )


_INSTALL_README = """BẤM ĐÚP file CHAY-O-DAY.cmd

Đây không phải phần mềm. File này chỉ là hướng dẫn.
1. Giải nén CẢ thư mục pc-macro.zip ra Desktop (đừng mở trong zip).
2. Mở thư mục pc-macro (phải thấy folder pc_macro và file CHAY-O-DAY.cmd).
3. Bấm đúp CHAY-O-DAY.cmd — cửa sổ "Gõ số rồi Enter" sẽ mở.

Nếu chưa có Python, file đó tự cài. Nếu thiếu pynput hoặc Pillow, file đó tự cài.
Cửa sổ lỗi sẽ đứng lại (không tắt ngay) để đọc được chữ.

Hướng 2: chỉ 1 tiến trình. Trên PC: nhập mã, bấm GẮN, bấm 1 lần vào ô
Mã khách hàng / số điện thoại trên Evoshop (không bấm trang hub). CHẠY
đưa Evoshop lên trước, gõ mã, Enter, tăng số. Bấm CHẠY lần nữa để tắt. VPN trên PC: một nút Bật VPN
(DNS, IPv6, kill switch, WARP, VPN Gate, miễn phí). Mật khẩu chỉ lưu trên máy,
không đưa lên VPS.

Lần sau chỉ bấm đúp CHAY-O-DAY.cmd (hoặc icon Desktop). Máy tự hỏi hub bản mới.
Muốn cập nhật im lặng: python -m pc_macro update
Đồng bộ kịch bản: python -m pc_macro pull   /   python -m pc_macro push
Kiểm tra máy: python -m pc_macro doctor
"""

_RUN_CMD = r"""@echo off
setlocal EnableExtensions
title Go so roi Enter
cd /d "%~dp0"
if not defined PC_MACRO_HUB set PC_MACRO_HUB=http://103.200.20.245:8088

if not exist "%~dp0pc_macro\__main__.py" (
  echo.
  echo  BAN DANG MO FILE TRONG ZIP hoac sai thu muc.
  echo  Hay giai nen CA thu muc pc-macro.zip ra Desktop,
  echo  mo thu muc pc-macro, roi bam dup CHAY-O-DAY.cmd.
  echo.
  pause
  exit /b 1
)

call :find_python
if not defined PYEXE (
  echo Dang cai Python 3.12 bang winget...
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Khong co winget. Mo https://www.python.org/downloads/
    echo Tick Add python.exe to PATH, cai xong bam dup CHAY-O-DAY.cmd lai.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :refresh_path
  call :find_python
)
if not defined PYEXE (
  echo Khong tim thay Python. Cai tu https://www.python.org/downloads/
  echo Tick Add python.exe to PATH va tcl/tk, roi bam dup CHAY-O-DAY.cmd lai.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

"%PYEXE%" %PYARGS% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo Python thieu tkinter. Cai lai Python 3.11+ tu python.org, tick tcl/tk.
  pause
  exit /b 1
)

"%PYEXE%" %PYARGS% -c "import pynput, PIL" >nul 2>nul
if errorlevel 1 (
  echo Dang cai pynput va Pillow...
  "%PYEXE%" %PYARGS% -m pip install --disable-pip-version-check pynput Pillow
)
"%PYEXE%" %PYARGS% -c "import pynput, PIL" >nul 2>nul
if errorlevel 1 (
  echo Khong cai duoc pynput / Pillow.
  echo Thu: "%PYEXE%" %PYARGS% -m pip install pynput Pillow
  pause
  exit /b 1
)

"%PYEXE%" %PYARGS% -m pc_macro doctor
if errorlevel 1 (
  echo May chua san sang. Doc dong LOI o tren.
  pause
  exit /b 1
)

"%PYEXE%" %PYARGS% -m pc_macro
if errorlevel 1 (
  echo Khong mo duoc cua so. Bam dup CHAY-O-DAY.cmd lai sau khi cai Python.
  pause
  exit /b 1
)
exit /b 0

:refresh_path
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
exit /b 0

:find_python
set "PYEXE="
set "PYARGS="
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
  "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PYEXE=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
    set "PYARGS=-3"
    exit /b 0
  )
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%I in ('where py') do (
    echo %%I | find /i "WindowsApps" >nul
    if errorlevel 1 (
      "%%I" -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
      if not errorlevel 1 (
        set "PYEXE=%%I"
        set "PYARGS=-3"
        exit /b 0
      )
    )
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%I in ('where python') do (
    echo %%I | find /i "WindowsApps" >nul
    if errorlevel 1 (
      set "PYEXE=%%I"
      exit /b 0
    )
  )
)
exit /b 1
"""

_INSTALL_PS1 = r"""param(
  [string]$Hub = $(if ($env:PC_MACRO_HUB) { $env:PC_MACRO_HUB } else { "http://103.200.20.245:8088" }),
  [string]$Token = $env:PC_MACRO_TOKEN
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
function Find-Python {
  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    try {
      $resolved = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $resolved -and $resolved -notmatch "WindowsApps") { return $resolved.Trim() }
    } catch {}
  }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
  }
  return $null
}
$Python = Find-Python
if (-not $Python) {
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget) {
    Write-Host "Dang cai Python 3.12 bang winget..."
    try {
      & $winget.Source install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    } catch {}
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $Python = Find-Python
  }
}
if (-not $Python) {
  Write-Host "Can Python 3.11+. Bam dup CHAY-O-DAY.cmd hoac cai tu python.org (tick Add python.exe to PATH va tcl/tk)."
  exit 1
}
& $Python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Python thieu tkinter. Cai lai Python 3.11+ tu python.org, tick tcl/tk."
  exit 1
}
& $Python -m pip install --upgrade pip
& $Python -m pip install pynput Pillow
& $Python -c "import pynput, PIL, tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Cai pynput/Pillow that bai. Mo PowerShell trong thu muc nay roi: $Python -m pip install pynput Pillow"
  exit 1
}
[Environment]::SetEnvironmentVariable("PC_MACRO_HUB", $Hub, "User")
$env:PC_MACRO_HUB = $Hub
if ($Token) {
  [Environment]::SetEnvironmentVariable("PC_MACRO_TOKEN", $Token, "User")
  $env:PC_MACRO_TOKEN = $Token
}
$Wsh = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$Start = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
New-Item -ItemType Directory -Force -Path $Start | Out-Null
foreach ($LinkPath in @(
  (Join-Path $Desktop "Go so roi Enter.lnk"),
  (Join-Path $Start "Go so roi Enter.lnk")
)) {
  $Shortcut = $Wsh.CreateShortcut($LinkPath)
  $Shortcut.TargetPath = $Python
  $Shortcut.Arguments = "-m pc_macro"
  $Shortcut.WorkingDirectory = $Root
  $Shortcut.WindowStyle = 1
  $Shortcut.Description = "Go so roi Enter — tự cập nhật từ hub"
  $Shortcut.Save()
}
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m pc_macro update" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn
try {
  Register-ScheduledTask -TaskName "PcMacroUpdate" -Action $Action -Trigger $Trigger -Force | Out-Null
} catch {
  schtasks /Create /TN "PcMacroUpdate" /TR "`"$Python`" -m pc_macro update" /SC ONLOGON /F | Out-Null
}
Write-Host "Da cai. Lan sau may tu cap nhat, khong can tai zip."
Write-Host "Mo cua so Ghi / Lam lai. F8 de dung."
& $Python -m pc_macro
"""
