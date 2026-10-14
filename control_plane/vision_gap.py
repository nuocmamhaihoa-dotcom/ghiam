"""Lượt nhìn ảnh chỉ cho số còn thiếu @.

Không gửi video ra khỏi máy. Chỉ cắt vùng tên + dòng @ của khung gần số đó.
Mặc định tắt (CONTROL_VISION=1 và có rapidocr trên máy). Không gọi API đám mây.
Hàng đã có @ không đọc lại.
"""

from __future__ import annotations

import os
from pathlib import Path

from control_plane.read_fallback import (
    PAIR_WINDOW_SEC,
    gaps_remain,
    pair_leftovers_by_time,
    parse_header_text,
)
from control_plane.screen_table import (
    FrameObs,
    Review,
    Table,
    clean_username,
    is_profile_handle,
    is_profile_name,
)

MAX_VISION_FRAMES = 40
_ENGINE: object | None = None
_ENGINE_FAILED = False


def vision_enabled() -> bool:
    flag = os.environ.get("CONTROL_VISION", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    return _rapidocr_engine() is not None


def _rapidocr_engine() -> object | None:
    """Model nhìn chữ local. Thiếu gói thì bỏ qua, không tải video đi đâu."""
    global _ENGINE, _ENGINE_FAILED
    if _ENGINE is not None:
        return _ENGINE
    if _ENGINE_FAILED:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        _ENGINE_FAILED = True
        return None
    try:
        _ENGINE = RapidOCR()
    except Exception:
        _ENGINE_FAILED = True
        return None
    return _ENGINE


def vision_header_crop(image: object, layout: object | None = None):
    """Ảnh đưa vào model: vùng @, không phải cả khung video."""
    from PIL import Image

    from control_plane.screen_layout import LayoutProfile
    from control_plane.screen_read import at_zone_box

    if not isinstance(image, Image.Image):
        raise TypeError("cần ảnh PIL")
    profile = layout if isinstance(layout, LayoutProfile) else None
    box = at_zone_box(image.width, image.height, profile)
    return image.crop((box.x0, box.y0, max(box.x0 + 1, box.x1), max(box.y0 + 1, box.y1)))


def _parse_rapid_result(result: object) -> tuple[str, str]:
    if not result:
        return "", ""
    rows: list[tuple[float, str]] = []
    for item in result:  # type: ignore[union-attr]
        if not item or len(item) < 2:
            continue
        box, text = item[0], str(item[1] or "").strip()
        if not text:
            continue
        try:
            ys = [float(point[1]) for point in box]
            y = sum(ys) / len(ys)
        except (TypeError, ValueError, IndexError):
            y = 0.0
        rows.append((y, text))
    rows.sort(key=lambda item: item[0])
    grouped: list[list[str]] = []
    centers: list[float] = []
    for y, text in rows:
        if centers and abs(y - centers[-1]) <= 14:
            grouped[-1].append(text)
            centers[-1] = y
        else:
            grouped.append([text])
            centers.append(y)
    block = "\n".join(" ".join(parts) for parts in grouped)
    _phone, name, username = parse_header_text(block)
    if not is_profile_handle(username) or not is_profile_name(name):
        return "", ""
    return name, username


def _vision_read_path(path: Path) -> tuple[str, str]:
    engine = _rapidocr_engine()
    if engine is None:
        return "", ""
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except ImportError:
        return "", ""
    try:
        with Image.open(path) as image:
            rgb = ImageOps.exif_transpose(image).convert("RGB")
    except OSError:
        return "", ""
    crop = vision_header_crop(rgb)
    if crop.width > 720 and crop.width > 0:
        ratio = 720 / crop.width
        crop = crop.resize((720, max(1, int(crop.height * ratio))))
    try:
        result, _elapsed = engine(np.asarray(crop))  # type: ignore[operator]
    except Exception:
        return "", ""
    return _parse_rapid_result(result)


def _phone_times(frames: list[FrameObs]) -> dict[str, float]:
    from control_plane.read_fallback import _phone_times as times

    return times(frames)


def apply_vision_for_gaps(
    table: Table,
    observations: list[tuple[Path, FrameObs]],
    *,
    reader=None,
) -> Table:
    """Chỉ khung gần số chưa có @. Hàng đã đủ @ không gọi reader."""
    if not gaps_remain(table):
        return table
    if reader is None and not vision_enabled():
        return table
    frames = [obs for _path, obs in observations]
    if not any(obs.at >= 0 for obs in frames):
        return table
    phone_at = _phone_times(frames)
    anchors: list[float] = []
    for item in table.unopened:
        if not item.phone:
            continue
        at = phone_at.get(item.phone, -1.0)
        if at >= 0:
            anchors.append(at)
    for item in table.review:
        if not item.phone or item.username:
            continue
        at = phone_at.get(item.phone, -1.0)
        if at >= 0:
            anchors.append(at)
    if not anchors:
        return table

    ranked: list[tuple[float, Path, FrameObs]] = []
    for path, obs in observations:
        if obs.at < 0 or not path:
            continue
        distance = min(abs(obs.at - anchor) for anchor in anchors)
        if distance > PAIR_WINDOW_SEC:
            continue
        ranked.append((distance, Path(path), obs))
    ranked.sort(key=lambda item: item[0])
    chosen: list[tuple[Path, FrameObs]] = []
    seen_paths: set[str] = set()
    for _distance, path, obs in ranked:
        key = str(path)
        if key in seen_paths or not path.exists():
            continue
        seen_paths.add(key)
        chosen.append((path, obs))
        if len(chosen) >= MAX_VISION_FRAMES:
            break
    if not chosen:
        return table

    read = reader or _vision_read_path
    complete_users = {clean_username(row.username) for row in table.rows if row.username}
    known_users = {clean_username(item.username) for item in table.review if item.username}
    extra_frames: list[FrameObs] = list(frames)
    temp_review = list(table.review)
    added_users: set[str] = set()
    for path, obs in chosen:
        name, username = read(path)
        username = clean_username(username)
        if not is_profile_handle(username) or not is_profile_name(name):
            continue
        if username in complete_users or username in known_users or username in added_users:
            continue
        extra_frames.append(FrameObs("profile", (), name, username, at=obs.at))
        temp_review.append(Review("", "", name, username, "nhìn dòng @"))
        added_users.add(username)
    if not added_users:
        return table
    result = pair_leftovers_by_time(
        Table(
            rows=list(table.rows),
            review=temp_review,
            unopened=list(table.unopened),
            strict=table.strict,
        ),
        extra_frames,
    )
    # @ đọc thêm chỉ để gắn vào số đã có. @ không ghép được thì không lưu thành hàng.
    return Table(
        rows=list(result.rows),
        unopened=list(result.unopened),
        review=[
            item
            for item in result.review
            if not (not item.phone and clean_username(item.username) in added_users)
        ],
        strict=table.strict,
    )
