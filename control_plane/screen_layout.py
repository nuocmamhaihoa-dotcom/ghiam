"""Hồ sơ bố cục màn hình theo từng máy (B2) và khóa ROI đầu video (B3).

Tọa độ lưu dạng tỷ lệ 0–1 theo rộng/cao khung hình — ổn qua nhiều độ phân giải iPhone.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass
class LayoutProfile:
    device: str = ""
    # Cột Follow danh bạ (mép trái nút / bề rộng nút / pitch dòng) — tỷ lệ theo khung.
    follow_x0: float = 0.72
    follow_w: float = 0.14
    row_pitch: float = 0.085
    # Vùng header hồ sơ để săn @ (P2 / B2).
    header_x0: float = 0.04
    header_y0: float = 0.06
    header_x1: float = 0.82
    header_y1: float = 0.48
    samples: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object] | None) -> "LayoutProfile":
        if not raw:
            return cls()
        allowed = {item.name for item in fields(cls)}
        data = {key: value for key, value in raw.items() if key in allowed}
        return cls(**data)  # type: ignore[arg-type]

    def header_box(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            max(0, int(self.header_x0 * width)),
            max(0, int(self.header_y0 * height)),
            min(width, int(self.header_x1 * width)),
            min(height, int(self.header_y1 * height)),
        )


def _slug_device(device: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", (device or "default").strip())[:60]
    return text or "default"


def layout_store_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / "layouts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def layout_file(data_dir: Path, device: str) -> Path:
    return layout_store_dir(data_dir) / f"{_slug_device(device)}.json"


def load_layout(data_dir: Path | None, device: str = "") -> LayoutProfile:
    profile = LayoutProfile(device=device or "default")
    if data_dir is None:
        return profile
    path = layout_file(data_dir, device)
    if not path.is_file():
        return profile
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return profile
    loaded = LayoutProfile.from_dict(raw)
    loaded.device = device or loaded.device or "default"
    return loaded


def save_layout(data_dir: Path, profile: LayoutProfile) -> Path:
    profile.updated_at = datetime.now(timezone.utc).isoformat()
    path = layout_file(data_dir, profile.device)
    path.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge_layout(base: LayoutProfile, measured: LayoutProfile, weight: float = 0.35) -> LayoutProfile:
    """EMA: video hiện tại (measured) trộn vào hồ sơ máy (base)."""
    weight = min(0.9, max(0.05, weight))
    out = LayoutProfile(device=base.device or measured.device)
    for name in (
        "follow_x0",
        "follow_w",
        "row_pitch",
        "header_x0",
        "header_y0",
        "header_x1",
        "header_y1",
    ):
        old = float(getattr(base, name))
        new = float(getattr(measured, name))
        setattr(out, name, old * (1.0 - weight) + new * weight)
    out.samples = int(base.samples) + max(1, int(measured.samples))
    return out


def measure_list_layout(image: Image.Image) -> LayoutProfile | None:
    """Đo cột Follow hồng trên một khung danh bạ — dùng calibrate B3 / học B2."""
    # Import muộn để tránh vòng import với screen_read.
    from control_plane.screen_read import _column_anchor, _list_buttons, _pink_boxes, _profile_button

    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 80 or height < 80:
        return None
    buttons = _pink_boxes(rgb)
    listed = _list_buttons(buttons, width)
    if len(listed) < 3:
        return None
    anchor_x, anchor_w = _column_anchor(listed)
    centers = [box.center_y() for box in listed]
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
    if not gaps:
        return None
    pitch = float(np.median(gaps))
    profile = LayoutProfile(
        follow_x0=anchor_x / width,
        follow_w=anchor_w / width,
        row_pitch=pitch / height,
        samples=1,
    )
    # Header @ ước từ nút Follow hồ sơ nếu có; không thì giữ mặc định gần Follow list.
    prof = _profile_button(buttons, width, height)
    if prof is not None:
        profile.header_x0 = max(0.02, (prof.x0 - 0.08 * prof.w) / width)
        profile.header_y0 = max(0.02, (prof.y0 - 3.9 * prof.h) / height)
        profile.header_x1 = min(0.95, (prof.x0 + 1.7 * prof.w) / width)
        profile.header_y1 = min(0.70, (prof.y0 - 0.12 * prof.h) / height)
        if profile.header_y1 <= profile.header_y0 + 0.05:
            profile.header_y0 = 0.06
            profile.header_y1 = 0.48
    return profile


def calibrate_from_paths(paths: list[Path], max_files: int = 16) -> LayoutProfile | None:
    """B3: lấy median các phép đo trên vài khung đầu video."""
    measures: list[LayoutProfile] = []
    for path in paths[:max_files]:
        try:
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except OSError:
            continue
        hit = measure_list_layout(image)
        if hit is not None:
            measures.append(hit)
    if len(measures) < 2:
        return measures[0] if measures else None
    out = LayoutProfile(samples=len(measures))
    for name in (
        "follow_x0",
        "follow_w",
        "row_pitch",
        "header_x0",
        "header_y0",
        "header_x1",
        "header_y1",
    ):
        setattr(out, name, float(np.median([getattr(item, name) for item in measures])))
    return out
