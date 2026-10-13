"""Chữ Apple Vision trên iPhone, gửi kèm lúc tải khi app đang mở.

Máy chủ vẫn quét 30 fps xem trước và 60 fps lúc mở hồ sơ. Sidecar chỉ thêm
quan sát hồ sơ (tên ngay trên @) trước khi ghép bảng. Điểm thấp vẫn ghép,
rồi classify hạ xuống Cần xem.
"""

from __future__ import annotations

import json
from pathlib import Path

from control_plane.read_fallback import parse_header_text
from control_plane.screen_table import FrameObs, clean_username, is_profile_handle, is_profile_name

_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def frames_from_payload(payload: object) -> list[FrameObs]:
    """Mỗi khung có tên ngay trên @ thành một quan sát hồ sơ. @ không có tên thì bỏ."""
    if not isinstance(payload, dict):
        return []
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list):
        return []
    found: list[FrameObs] = []
    for item in raw_frames:
        if not isinstance(item, dict):
            continue
        try:
            at = float(item.get("t", -1))
        except (TypeError, ValueError):
            continue
        lines = item.get("lines")
        if not isinstance(lines, list):
            continue
        texts: list[str] = []
        handle_scores: list[float] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = " ".join(str(line.get("text") or "").split())
            if not text:
                continue
            texts.append(text)
            if "@" not in text:
                continue
            try:
                score = float(line.get("conf", -1))
            except (TypeError, ValueError):
                score = -1.0
            if score >= 0:
                handle_scores.append(score)
        if not texts:
            continue
        _phone, name, username = parse_header_text("\n".join(texts))
        handle = clean_username(username)
        if not is_profile_handle(handle):
            continue
        shown = name if is_profile_name(name) else ""
        if not shown:
            continue
        confidence = min(handle_scores) if handle_scores else -1.0
        found.append(
            FrameObs(
                "profile",
                (),
                shown,
                handle,
                at=at,
                handle_confidence=confidence,
            )
        )
    return found


def load_sidecar(video: Path) -> list[FrameObs]:
    path = video.with_suffix(".vision.json")
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return frames_from_payload(payload)


def attach_vision(
    observations: list[tuple[Path, FrameObs]],
    paths: list[Path],
) -> list[tuple[Path, FrameObs]]:
    """Ghép sidecar vào dòng thời gian. Không bỏ khung máy chủ đã đọc."""
    extra: list[tuple[Path, FrameObs]] = []
    for video in paths:
        if video.suffix.lower() not in _VIDEO_SUFFIXES:
            continue
        for obs in load_sidecar(video):
            extra.append((video, obs))
    if not extra:
        return observations
    merged = list(observations) + extra
    merged.sort(key=lambda item: item[1].at if item[1].at >= 0 else -1.0)
    return merged
