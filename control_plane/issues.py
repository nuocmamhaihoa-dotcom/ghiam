"""Ghi lỗi và vấn đề lên hub để trang chủ xem lại."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from control_plane import db
from control_plane.settings import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_issue(
    summary: str,
    *,
    detail: str | None = None,
    source: str = "hub",
    job_id: str | None = None,
    level: str = "warn",
) -> None:
    """Ghi một dòng vào nhật ký lỗi. Không được làm hỏng luồng đang chạy."""
    cleaned = " ".join(str(summary or "").split())[:500]
    if not cleaned:
        return
    payload: dict[str, Any] = {"level": level[:20]}
    if job_id:
        payload["jobId"] = job_id[:64]
    if detail:
        payload["detail"] = " ".join(str(detail).split())[:1500]
    try:
        db.record_action(
            settings.db_path,
            at=_now_iso(),
            actor="hub",
            source=source[:40],
            kind="issue",
            summary=cleaned,
            detail=json.dumps(payload, ensure_ascii=False) if payload else None,
        )
    except Exception:
        return


def record_video_problem(job_id: str, text: str) -> None:
    record_issue(text, source="video", job_id=job_id)


def record_job_failure(job_id: str, error: str) -> None:
    record_issue(error, source="video", job_id=job_id, level="error")


def parse_detail(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"detail": raw}
    except json.JSONDecodeError:
        return {"detail": raw}
