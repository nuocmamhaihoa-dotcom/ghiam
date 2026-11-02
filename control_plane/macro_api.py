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


@router.get("/tai/pc-macro.zip")
def download_pc_macro() -> Response:
    """Zip the PC recorder so a Windows machine can run it locally."""
    buf = io.BytesIO()
    package = ROOT / "pc_macro"
    if not package.is_dir():
        raise HTTPException(status_code=404, detail="pc_macro missing")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package.glob("*.py")):
            zf.write(path, arcname=f"pc-macro/pc_macro/{path.name}")
        zf.writestr("pc-macro/Cai-dat.ps1", _INSTALL_PS1)
        zf.writestr("pc-macro/README.txt", _INSTALL_README)
    payload = buf.getvalue()
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pc-macro.zip"'},
    )


_INSTALL_README = """Ghi nhớ thao tác PC

Phần mềm này chạy trên máy tính của bạn (Windows / Linux có màn hình).
VPS chỉ giữ kịch bản. Ghi phím/chuột và bấm Làm lại phải làm trên PC.

1. Cài Python 3.11+
2. Mở PowerShell trong thư mục này:
   .\\Cai-dat.ps1
3. Cửa sổ hiện ra. Nhập 5 ký tự cố định, số bắt đầu và số kết thúc (5 số).
   Từng bước: Bôi màu trên ảnh (hoặc Chụp màn) chỗ cần bấm, hoặc Chọn vị trí trên màn.
   Máy tìm tâm vùng tô rồi bấm trái đúng chỗ đó. Nhớ tỷ lệ màn hình. Chưa nhận thì bấm lại.
   Bắt đầu gồm cả hai kịch bản: gõ mã rồi Enter; màn đổi ≥ 80% thì tự bấm 4 nút trái, lưu số, rồi tăng số tiếp.
   Hướng 2 (độc lập): mỗi dãy số một trình duyệt. Gắn 4 ô = bấm ô 5 số từng website (khung theo dõi tự lấy quanh ô). Bắt đầu 2 gõ 4 mã rồi Enter đồng loạt. Chỉ lưu mã của trình duyệt có khung đổi ≥ 20%; các mã khác vẫn chạy. Tiếp tục chỉ mở lại cửa sổ đã dừng.
   VPN trên PC: một nút Bật VPN. Bật là siết DNS/IPv6/WebRTC/Firefox, cài WARP (winget) nếu thiếu, rồi nối nguồn miễn phí (WARP, Proton/Windscribe CLI, VPN Gate, proxy tĩnh). Tắt là ngắt. Mật khẩu chỉ lưu trên máy, không đưa lên VPS.

Đồng bộ với VPS (sau khi đặt địa chỉ):
  python -m pc_macro pull
  python -m pc_macro push
"""

_INSTALL_PS1 = r"""param(
  [string]$Hub = $env:PC_MACRO_HUB,
  [string]$Token = $env:PC_MACRO_TOKEN
)
$ErrorActionPreference = "Stop"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Can Python 3.11+. Cai tu python.org roi mo lai PowerShell."
  exit 1
}
python -m pip install --upgrade pip
python -m pip install pynput
if ($Hub) { [Environment]::SetEnvironmentVariable("PC_MACRO_HUB", $Hub, "User"); $env:PC_MACRO_HUB = $Hub }
if ($Token) { [Environment]::SetEnvironmentVariable("PC_MACRO_TOKEN", $Token, "User"); $env:PC_MACRO_TOKEN = $Token }
Write-Host "Mo cua so Ghi / Lam lai. F8 de dung."
python -m pc_macro
"""
