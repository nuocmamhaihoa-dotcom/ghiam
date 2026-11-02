"""VPS store for PC desktop macros — record and replay still happen on the PC."""

from __future__ import annotations

import hmac
import io
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
            "Cai-dat.ps1": _INSTALL_PS1,
            "Chay.cmd": _RUN_CMD,
            "README.txt": _INSTALL_README,
        },
    )


def _pc_macro_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in sorted(files.items()):
            zf.writestr(f"pc-macro/{name}", raw)
    return buf.getvalue()


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


_INSTALL_README = """Ghi nhớ thao tác PC

Phần mềm này chạy trên máy tính của bạn (Windows / Linux có màn hình).
VPS chỉ giữ kịch bản. Ghi phím/chuột và bấm Làm lại phải làm trên PC.

Cài một lần:
1. Cài Python 3.11+ (tick Add python.exe to PATH)
2. Mở PowerShell trong thư mục này:
   .\\Cai-dat.ps1
Sau đó không cần tải zip hay cài lại. Mỗi lần mở (hoặc khi đăng nhập Windows)
máy tự hỏi hub bản mới, tự tải, tự ghi đè file, rồi tự mở lại.

Hướng 2: mỗi thẻ Gắn N = bấm cửa sổ website rồi dòng ô 5 số. Gắn 4 ô vẫn gắn lần lượt.
Bắt đầu 2 gõ rồi Enter đồng loạt. Chỉ lưu mã của trình duyệt có khung đổi ≥ 20%.
Tiếp N chỉ mở lại trình đó. VPN trên PC: một nút Bật VPN (DNS, IPv6, kill switch,
WARP, VPN Gate, miễn phí). Mật khẩu chỉ lưu trên máy, không đưa lên VPS.

Lần sau chỉ cần:
  .\\Chay.cmd
hoặc icon trên Desktop / Menu Start. Nếu cửa sổ không mở, chạy lại Cai-dat.ps1.
Muốn cập nhật im lặng:
  python -m pc_macro update

Đồng bộ kịch bản:
  python -m pc_macro pull
  python -m pc_macro push
"""

_RUN_CMD = r"""@echo off
cd /d "%~dp0"
if not defined PC_MACRO_HUB set PC_MACRO_HUB=http://103.200.20.245:8088
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Can Python 3.11+ tu python.org. Tick Add python.exe to PATH, roi chay Cai-dat.ps1.
  pause
  exit /b 1
)
%PY% -c "import tkinter, pynput" >nul 2>nul
if errorlevel 1 (
  echo Dang cai pynput...
  %PY% -m pip install --disable-pip-version-check pynput
)
%PY% -m pc_macro
if errorlevel 1 (
  echo Khong mo duoc cua so. Chay Cai-dat.ps1 trong thu muc nay.
  pause
  exit /b 1
)
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
  Write-Host "Can Python 3.11+. Cai tu python.org (tick Add python.exe to PATH va tcl/tk) roi mo lai PowerShell."
  exit 1
}
& $Python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Python thieu tkinter. Cai lai Python 3.11+ tu python.org, tick tcl/tk."
  exit 1
}
& $Python -m pip install --upgrade pip
& $Python -m pip install pynput
& $Python -c "import pynput, tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Cai pynput that bai. Mo PowerShell trong thu muc nay roi: $Python -m pip install pynput"
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
