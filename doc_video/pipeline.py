"""Điểm vào duy nhất để đọc một video.

Máy chủ và PC cùng gọi read_video. Đổi cách đọc thì sửa ở đây: PC tải file
này về và đọc giống máy chủ, không cần cài lại chương trình trên PC.
"""

from __future__ import annotations

from pathlib import Path

from doc_video.frame import FrameRead
from doc_video.join import Result, build
from doc_video.video import read_inputs

# Một khung đọc mất vài giây. Chờ một khung quá lâu là nhóm tiến trình đọc đã hỏng.
FRAME_TIMEOUT_SECONDS = 900


def read_video(
    paths: list[Path],
    submit=None,
    work_dir: Path | None = None,
    progress=None,
) -> tuple[Result, list[FrameRead]]:
    """Đọc video hoặc ảnh và ghép bảng số, tên, id. progress chỉ để hiện tiến độ, không đổi kết quả."""
    frames = read_inputs(
        paths, submit=submit, work_dir=work_dir, task_timeout=FRAME_TIMEOUT_SECONDS, progress=progress
    )
    return build(frames), frames
