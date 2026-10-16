"""Danh sách PC cùng đọc: trạng thái, tiến độ, tạm dừng, giờ được đọc, mã mời.

Mọi thứ nằm trong video.db, vì trang web chạy nhiều tiến trình cùng lúc.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from control_plane.video_store import (
    PC_FAIL_LIMIT,
    VN_TZ,
    clean_device,
    clean_note,
    connect,
    utcnow,
    vn_day_bounds,
)

# PC rảnh hỏi video mỗi vài giây, PC đang đọc gửi nhịp mỗi 20 giây.
ONLINE_SECONDS = 90
PC_STATES = {"setup", "testing", "test-failed", "idle", "reading", "wait-disk", "wait-ram"}
_WINDOW = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$")
_RECENT = 20


def clean_schedule(raw: str) -> str:
    """Giờ được đọc theo giờ Việt Nam, ví dụ 22:00-06:00. Nhiều khoảng cách nhau bằng dấu phẩy.

    Rỗng là đọc mọi lúc. Sai dạng thì báo lỗi, không đoán.
    """
    text = "".join(str(raw or "").split())
    if not text:
        return ""
    windows = []
    for part in text.split(","):
        match = _WINDOW.match(part)
        if match is None:
            raise ValueError(part)
        start = f"{int(match.group(1)):02d}:{match.group(2)}"
        end = f"{int(match.group(3)):02d}:{match.group(4)}"
        if start == end:
            raise ValueError(part)
        windows.append(f"{start}-{end}")
    return ",".join(windows)


def _minutes(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def schedule_open(schedule: str, now: datetime | None = None) -> bool:
    """Lúc này có nằm trong giờ được đọc không. Khoảng qua nửa đêm như 22:00-06:00 cũng đúng."""
    if not schedule:
        return True
    moment = (now or datetime.now(timezone.utc)).astimezone(VN_TZ)
    minute = moment.hour * 60 + moment.minute
    for part in schedule.split(","):
        start_text, _, end_text = part.partition("-")
        start, end = _minutes(start_text), _minutes(end_text)
        if start < end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:
            return True
    return False


def pc_checkin(
    db_path: Path,
    name: str,
    *,
    state: str = "",
    message: str = "",
    version: str = "",
    invite: str = "",
    now: datetime | None = None,
) -> dict[str, object]:
    """PC báo đang làm gì. Trả về điều trang đã đặt cho PC: tạm dừng, giờ đọc, bị loại, tự chạy."""
    pc = clean_device(name) or "PC"
    stamp = utcnow()
    fields: dict[str, object] = {"invite": str(invite or "")[:64]}
    if state in PC_STATES:
        fields["state"] = state
        fields["message"] = clean_note(message)
    if version:
        fields["version"] = clean_note(version, 40)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO pcs (name, first_seen, last_seen) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen
            """,
            (pc, stamp, stamp),
        )
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE pcs SET {assignments} WHERE name = ?", (*fields.values(), pc))
        row = conn.execute(
            "SELECT paused, schedule, autostart, excluded, last_error FROM pcs WHERE name = ?",
            (pc,),
        ).fetchone()
        conn.execute("COMMIT")
    excluded = bool(row["excluded"])
    paused = bool(row["paused"])
    schedule = str(row["schedule"] or "")
    open_now = schedule_open(schedule, now)
    wait = "excluded" if excluded else "paused" if paused else "" if open_now else "schedule"
    return {
        "name": pc,
        "wait": wait,
        "paused": paused,
        "schedule": schedule,
        "schedule_open": open_now,
        "excluded": excluded,
        "autostart": bool(row["autostart"]),
        "message": str(row["last_error"] or "") if excluded else "",
    }


def _parse(stamp: object) -> datetime | None:
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _age_seconds(stamp: object, moment: datetime) -> float | None:
    then = _parse(stamp)
    if then is None:
        return None
    return max(0.0, (moment - then).total_seconds())


def _span_seconds(start: object, end: object) -> float | None:
    first, last = _parse(start), _parse(end)
    if first is None or last is None or last < first:
        return None
    return (last - first).total_seconds()


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def list_pcs(db_path: Path, now: datetime | None = None) -> list[dict[str, object]]:
    """Mọi PC đã từng nối, kèm video đang đọc, tiến độ, tốc độ gần đây và số video xong hôm nay."""
    moment = now or datetime.now(timezone.utc)
    _day, start, end = vn_day_bounds(moment)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM pcs ORDER BY name COLLATE NOCASE").fetchall()
        reading = {
            str(row["helper"]): row
            for row in conn.execute(
                """
                SELECT id, helper, started_at, size_bytes, check_pass,
                       progress_stage, progress_done, progress_total, progress_at
                FROM videos WHERE status = 'pc'
                """
            )
        }
        today = {
            str(row["helper"]): int(row["n"])
            for row in conn.execute(
                """
                SELECT helper, COUNT(*) AS n FROM videos
                WHERE status = 'done' AND finished_at >= ? AND finished_at < ? AND helper != ''
                GROUP BY helper
                """,
                (start, end),
            )
        }
        recent: dict[str, list] = {}
        for row in conn.execute(
            """
            SELECT helper, pc_download_s, pc_read_s, started_at, finished_at FROM videos
            WHERE status = 'done' AND helper != '' AND helper != 'VPS' AND finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 5000
            """
        ):
            bucket = recent.setdefault(str(row["helper"]), [])
            if len(bucket) < _RECENT:
                bucket.append(row)
        labels = {str(row["code"]): str(row["label"] or "") for row in conn.execute("SELECT code, label FROM pc_invites")}
    items = []
    for row in rows:
        name = str(row["name"])
        seen = _age_seconds(row["last_seen"], moment)
        online = seen is not None and seen <= ONLINE_SECONDS
        video = reading.get(name)
        current = None
        if video is not None:
            current = {
                "id": int(video["id"]),
                "stage": str(video["progress_stage"] or ""),
                "done": int(video["progress_done"] or 0),
                "total": int(video["progress_total"] or 0),
                "bytes": int(video["size_bytes"] or 0),
                "seconds": _age_seconds(video["started_at"], moment),
                "check": int(video["check_pass"] or 0),
            }
        history = recent.get(name, [])
        downloads = [float(item["pc_download_s"]) for item in history if item["pc_download_s"] is not None]
        reads = [float(item["pc_read_s"]) for item in history if item["pc_read_s"] is not None]
        totals = [
            value for value in (_span_seconds(item["started_at"], item["finished_at"]) for item in history) if value is not None
        ]
        schedule = str(row["schedule"] or "")
        invite = str(row["invite"] or "")
        items.append(
            {
                "name": name,
                "online": online,
                "seen_seconds": seen,
                "state": str(row["state"] or ""),
                "message": str(row["message"] or ""),
                "version": str(row["version"] or ""),
                "invite": labels.get(invite, "Mã mời đã xoá" if invite else ""),
                "video": current,
                "paused": bool(row["paused"]),
                "schedule": schedule,
                "schedule_open": schedule_open(schedule, moment),
                "autostart": bool(row["autostart"]),
                "excluded": bool(row["excluded"]),
                "fail_streak": int(row["fail_streak"] or 0),
                "fail_limit": PC_FAIL_LIMIT,
                "last_error": str(row["last_error"] or ""),
                "last_error_seconds": _age_seconds(row["last_error_at"], moment),
                "ok_count": int(row["ok_count"] or 0),
                "fail_count": int(row["fail_count"] or 0),
                "done_today": today.get(name, 0),
                "avg_download_s": _mean(downloads),
                "avg_read_s": _mean(reads),
                "avg_total_s": _mean(totals),
                "recent": len(history),
            }
        )
    return items


def update_pc(
    db_path: Path,
    name: str,
    *,
    paused: bool | None = None,
    schedule: str | None = None,
    autostart: bool | None = None,
    enable: bool = False,
) -> bool:
    """Đổi điều trang đặt cho một PC. PC đang đọc vẫn đọc xong video đang dở."""
    fields: dict[str, object] = {}
    if paused is not None:
        fields["paused"] = 1 if paused else 0
    if schedule is not None:
        fields["schedule"] = clean_schedule(schedule)
    if autostart is not None:
        fields["autostart"] = 1 if autostart else 0
    if enable:
        fields["excluded"] = 0
        fields["fail_streak"] = 0
    if not fields:
        return False
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect(db_path) as conn:
        cur = conn.execute(f"UPDATE pcs SET {assignments} WHERE name = ?", (*fields.values(), name))
        return cur.rowcount == 1


def forget_pc(db_path: Path, name: str, now: datetime | None = None) -> bool:
    """Bỏ PC khỏi danh sách. PC đang nối hoặc đang giữ video thì không bỏ."""
    moment = now or datetime.now(timezone.utc)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT last_seen FROM pcs WHERE name = ?", (name,)).fetchone()
        busy = conn.execute("SELECT 1 FROM videos WHERE status = 'pc' AND helper = ? LIMIT 1", (name,)).fetchone()
        seen = _age_seconds(row["last_seen"], moment) if row is not None else None
        if row is None or busy is not None or (seen is not None and seen <= ONLINE_SECONDS):
            conn.execute("ROLLBACK")
            return False
        conn.execute("DELETE FROM pcs WHERE name = ?", (name,))
        conn.execute("COMMIT")
        return True


def create_invite(db_path: Path, label: str) -> dict[str, object]:
    """Mã mời không hết hạn. Mỗi mã cho ra một mã nối riêng, thu hồi mã thì các PC dùng mã đó ngừng nối."""
    code = secrets.token_urlsafe(18)
    stamp = utcnow()
    text = clean_note(label, 60)
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO pc_invites (code, label, created_at) VALUES (?, ?, ?)",
            (code, text, stamp),
        )
        invite_id = int(cur.lastrowid or 0)
    return {"id": invite_id, "code": code, "label": text, "created_at": stamp, "revoked_at": None}


def list_invites(db_path: Path) -> list[dict[str, object]]:
    with connect(db_path) as conn:
        counts = {
            str(row["invite"]): int(row["n"])
            for row in conn.execute("SELECT invite, COUNT(*) AS n FROM pcs WHERE invite != '' GROUP BY invite")
        }
        rows = conn.execute("SELECT id, code, label, created_at, revoked_at FROM pc_invites ORDER BY id DESC").fetchall()
    return [
        {
            "id": int(row["id"]),
            "code": str(row["code"]),
            "label": str(row["label"] or ""),
            "created_at": str(row["created_at"]),
            "revoked_at": row["revoked_at"],
            "pcs": counts.get(str(row["code"]), 0),
        }
        for row in rows
    ]


def revoke_invite(db_path: Path, invite_id: int) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE pc_invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (utcnow(), invite_id),
        )
        return cur.rowcount == 1


def invite_codes(db_path: Path) -> list[tuple[str, bool]]:
    """(mã, đã thu hồi) của mọi mã mời, để nhận ra mã nối của PC."""
    with connect(db_path) as conn:
        return [(str(row["code"]), row["revoked_at"] is not None) for row in conn.execute("SELECT code, revoked_at FROM pc_invites")]


def invite_open(db_path: Path, code: str) -> bool:
    with connect(db_path) as conn:
        row = conn.execute("SELECT revoked_at FROM pc_invites WHERE code = ?", (code,)).fetchone()
    return row is not None and row["revoked_at"] is None
