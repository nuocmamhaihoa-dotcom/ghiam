"""Tài khoản quản lý và nhân viên, phiên đăng nhập, hạn mức tra cứu theo ngày."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from control_plane import db

SESSION_COOKIE = "khach_session"
SESSION_DAYS = 14
_PBKDF2_ROUNDS = 210_000
_MAX_ROUNDS = 1_000_000
_USER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_ZONE = timezone(timedelta(hours=7))
_LOCK_AFTER = 8
_LOCK_MINUTES = 15
_DUMMY_HASH = ""


class AccountError(Exception):
    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class LookupQuotaError(Exception):
    def __init__(self, used: int, limit: int, needed: int) -> None:
        remaining = max(0, limit - used)
        detail = (
            f"Hết hạn mức tra cứu hôm nay. Đã dùng {used}/{limit} số, "
            f"còn {remaining} số, danh sách này có {needed} số mới."
        )
        super().__init__(detail)
        self.detail = detail
        self.used = used
        self.limit = limit
        self.needed = needed


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    display_name: str
    role: str
    daily_lookup_limit: int | None
    via: str

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"


def token_actor() -> Actor:
    return Actor(
        id="token",
        username="quan-ly",
        display_name="Quản lý",
        role="manager",
        daily_lookup_limit=None,
        via="token",
    )


def _bearer_matches(authorization: str, token: str) -> bool:
    expected = f"Bearer {token}"
    if len(authorization) != len(expected):
        return False
    return hmac.compare_digest(authorization, expected)


def actor_from_master_token(authorization: str | None, master_token: str) -> Actor | None:
    if not master_token or not authorization:
        return None
    if _bearer_matches(authorization, master_token):
        return token_actor()
    return None


def local_day(moment: datetime | None = None) -> str:
    """Ngày theo giờ Việt Nam (UTC+7), cùng múi với Tomsk."""
    current = moment or datetime.now(_ZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_ZONE).date().isoformat()


def _now() -> datetime:
    return datetime.now(_ZONE)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


def clean_username(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    if not _USER.fullmatch(text):
        raise AccountError("Tên đăng nhập dùng chữ thường, số, dấu chấm hoặc gạch, dài từ 3 đến 32 ký tự.")
    return text


def clean_display_name(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        text = fallback
    if len(text) > 80:
        text = text[:80].rstrip()
    return text


def clean_password(value: Any) -> str:
    text = str(value or "")
    if len(text) < 8 or len(text) > 200:
        raise AccountError("Mật khẩu dài từ 8 đến 200 ký tự.")
    return text


def clean_daily_limit(value: Any) -> int:
    if isinstance(value, bool) or value is None or value == "":
        raise AccountError("Hãy nhập hạn mức tra cứu mỗi ngày, là số từ 0 đến 1.000.000.")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise AccountError("Hạn mức tra cứu phải là số nguyên.") from exc
    if limit < 0 or limit > 1_000_000:
        raise AccountError("Hạn mức tra cứu mỗi ngày từ 0 đến 1.000.000 số.")
    return limit


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_text, salt_hex, digest_hex = str(stored or "").split("$", 3)
        rounds = int(rounds_text)
        if algo != "pbkdf2_sha256" or rounds < 1 or rounds > _MAX_ROUNDS:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(got, expected)


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("dummy-password-not-used")
    return _DUMMY_HASH


def _limit_for(role: str, raw: Any) -> int | None:
    if role == "manager":
        return None
    if role != "staff":
        return 0
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _actor_from_row(row: Any, via: str) -> Actor:
    role = str(row["role"] or "")
    if role not in ("manager", "staff"):
        role = "staff"
    return Actor(
        id=str(row["id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"] or row["username"]),
        role=role,
        daily_lookup_limit=_limit_for(role, row["daily_lookup_limit"]),
        via=via,
    )


def account_count(db_path: Any) -> int:
    with db.session(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM staff_accounts").fetchone()
    return int(row["n"] if row else 0)


def needs_setup(db_path: Any) -> bool:
    return account_count(db_path) == 0


def _used_today(conn: Any, account_id: str, day: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM lookup_usage WHERE account_id=? AND day=?",
        (account_id, day),
    ).fetchone()
    return int(row["n"] if row else 0)


def public_account(actor: Actor, used: int = 0) -> dict[str, Any]:
    limit = actor.daily_lookup_limit
    remaining = None if limit is None else max(0, limit - used)
    return {
        "id": actor.id,
        "username": actor.username,
        "displayName": actor.display_name,
        "role": actor.role,
        "active": True,
        "dailyLookupLimit": limit,
        "usedToday": used,
        "remainingToday": remaining,
    }


def usage_today(db_path: Any, account_id: str) -> int:
    if not account_id or account_id == "token":
        return 0
    with db.session(db_path) as conn:
        return _used_today(conn, account_id, local_day())


def require_session(db_path: Any, token: str | None) -> Actor:
    if not token:
        raise AccountError("Hãy đăng nhập.", 401)
    now = _iso()
    with db.session(db_path) as conn:
        row = conn.execute(
            """
            SELECT a.id, a.username, a.display_name, a.role, a.daily_lookup_limit, a.active
            FROM staff_sessions s
            JOIN staff_accounts a ON a.id = s.account_id
            WHERE s.token=? AND s.expires_at > ?
            """,
            (token, now),
        ).fetchone()
    if row is None or not int(row["active"] or 0):
        raise AccountError("Phiên đăng nhập đã hết. Hãy đăng nhập lại.", 401)
    return _actor_from_row(row, "session")


def _purge_attempts(conn: Any, username: str, now: datetime) -> None:
    cutoff = (now - timedelta(days=2)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM login_attempts WHERE at < ?", (cutoff,))


def _locked(conn: Any, username: str, now: datetime) -> bool:
    since = (now - timedelta(minutes=_LOCK_MINUTES)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT ok FROM login_attempts WHERE username=? AND at>=? ORDER BY id DESC LIMIT ?",
        (username, since, _LOCK_AFTER),
    ).fetchall()
    return len(rows) >= _LOCK_AFTER and all(not int(row["ok"] or 0) for row in rows)


def login(db_path: Any, username: str, password: str) -> tuple[Actor, str]:
    try:
        name = clean_username(username)
    except AccountError:
        verify_password(password, _dummy_hash())
        raise AccountError("Sai tên đăng nhập hoặc mật khẩu.", 401) from None
    now = _now()
    with db.session(db_path) as conn:
        _purge_attempts(conn, name, now)
        if _locked(conn, name, now):
            raise AccountError("Đăng nhập sai quá nhiều lần. Thử lại sau 15 phút.", 429)
        row = conn.execute(
            """
            SELECT id, username, password_hash, display_name, role, daily_lookup_limit, active
            FROM staff_accounts WHERE username=?
            """,
            (name,),
        ).fetchone()
        ok = bool(row) and verify_password(password, str(row["password_hash"])) and int(row["active"] or 0) == 1
        conn.execute(
            "INSERT INTO login_attempts(username, at, ok) VALUES(?,?,?)",
            (name, _iso(now), 1 if ok else 0),
        )
        if not ok:
            conn.commit()
            raise AccountError("Sai tên đăng nhập hoặc mật khẩu.", 401)
        token = secrets.token_urlsafe(32)
        expires = _iso(now + timedelta(days=SESSION_DAYS))
        conn.execute(
            "INSERT INTO staff_sessions(token, account_id, created_at, expires_at) VALUES(?,?,?,?)",
            (token, row["id"], _iso(now), expires),
        )
        actor = _actor_from_row(row, "session")
    return actor, token


def logout(db_path: Any, token: str | None) -> None:
    if not token:
        return
    with db.session(db_path) as conn:
        conn.execute("DELETE FROM staff_sessions WHERE token=?", (token,))


def _insert_account(
    conn: Any,
    username: str,
    password: str,
    display_name: str,
    role: str,
    daily_limit: int | None,
    now: str,
) -> str:
    account_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO staff_accounts(
          id, username, password_hash, display_name, role, daily_lookup_limit, active, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,1,?,?)
        """,
        (account_id, username, hash_password(password), display_name, role, daily_limit, now, now),
    )
    return account_id


def setup_manager(db_path: Any, username: str, password: str, display_name: str) -> Actor:
    name = clean_username(username)
    secret = clean_password(password)
    shown = clean_display_name(display_name, name)
    now = _iso()
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = int(conn.execute("SELECT COUNT(*) AS n FROM staff_accounts").fetchone()["n"] or 0)
        if count:
            conn.rollback()
            raise AccountError("Đã có tài khoản quản lý.", 409)
        account_id = _insert_account(conn, name, secret, shown, "manager", None, now)
        conn.commit()
    finally:
        conn.close()
    return Actor(account_id, name, shown, "manager", None, "session")


def create_staff(
    db_path: Any,
    username: str,
    password: str,
    display_name: str,
    daily_limit: Any,
) -> Actor:
    name = clean_username(username)
    secret = clean_password(password)
    shown = clean_display_name(display_name, name)
    limit = clean_daily_limit(daily_limit)
    now = _iso()
    try:
        with db.session(db_path) as conn:
            account_id = _insert_account(conn, name, secret, shown, "staff", limit, now)
    except Exception as exc:
        message = str(exc).casefold()
        if "unique" in message:
            raise AccountError("Tên đăng nhập này đã có.", 409) from exc
        raise
    return Actor(account_id, name, shown, "staff", limit, "session")


def list_accounts(db_path: Any) -> list[dict[str, Any]]:
    day = local_day()
    with db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.username, a.display_name, a.role, a.daily_lookup_limit, a.active,
                   COALESCE(u.n, 0) AS used
            FROM staff_accounts a
            LEFT JOIN (
              SELECT account_id, COUNT(*) AS n FROM lookup_usage WHERE day=? GROUP BY account_id
            ) u ON u.account_id = a.id
            ORDER BY CASE a.role WHEN 'manager' THEN 0 ELSE 1 END, a.username
            """,
            (day,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        role = str(row["role"] or "")
        if role not in ("manager", "staff"):
            role = "staff"
        limit = _limit_for(role, row["daily_lookup_limit"])
        used = int(row["used"] or 0)
        items.append(
            {
                "id": row["id"],
                "username": row["username"],
                "displayName": row["display_name"],
                "role": role,
                "active": bool(int(row["active"] or 0)),
                "dailyLookupLimit": limit,
                "usedToday": used,
                "remainingToday": None if limit is None else max(0, limit - used),
            }
        )
    return items


def _drop_sessions(conn: Any, account_id: str) -> None:
    conn.execute("DELETE FROM staff_sessions WHERE account_id=?", (account_id,))


def update_staff(
    db_path: Any,
    account_id: str,
    daily_limit: Any = None,
    active: bool | None = None,
    display_name: Any = None,
    password: Any = None,
) -> dict[str, Any]:
    with db.session(db_path) as conn:
        row = conn.execute("SELECT * FROM staff_accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise AccountError("Không thấy tài khoản này.", 404)
        if str(row["role"]) != "staff":
            raise AccountError("Chỉ sửa tài khoản nhân viên ở đây.", 403)
        fields: list[str] = []
        params: list[Any] = []
        if daily_limit is not None:
            fields.append("daily_lookup_limit=?")
            params.append(clean_daily_limit(daily_limit))
        if display_name is not None:
            fields.append("display_name=?")
            params.append(clean_display_name(display_name, str(row["username"])))
        if active is not None:
            fields.append("active=?")
            params.append(1 if active else 0)
            if not active:
                _drop_sessions(conn, account_id)
        if password not in (None, ""):
            fields.append("password_hash=?")
            params.append(hash_password(clean_password(password)))
            _drop_sessions(conn, account_id)
        if not fields:
            raise AccountError("Không có gì để sửa.")
        fields.append("updated_at=?")
        params.append(_iso())
        params.append(account_id)
        conn.execute(f"UPDATE staff_accounts SET {', '.join(fields)} WHERE id=?", params)
    updated = next(item for item in list_accounts(db_path) if item["id"] == account_id)
    return updated


def change_password(db_path: Any, actor: Actor, current: str, new_password: str) -> None:
    if actor.via != "session":
        raise AccountError("Tài khoản máy chủ đổi mật khẩu ở cấu hình máy, không đổi ở trang này.", 403)
    secret = clean_password(new_password)
    with db.session(db_path) as conn:
        row = conn.execute("SELECT password_hash FROM staff_accounts WHERE id=?", (actor.id,)).fetchone()
        if row is None or not verify_password(current, str(row["password_hash"])):
            raise AccountError("Mật khẩu hiện tại không đúng.", 401)
        conn.execute(
            "UPDATE staff_accounts SET password_hash=?, updated_at=? WHERE id=?",
            (hash_password(secret), _iso(), actor.id),
        )
        _drop_sessions(conn, actor.id)


def consume_lookup(db_path: Any, actor: Actor, phones: list[str], at: str | None = None) -> dict[str, Any]:
    """Ghi số mới trong ngày. Số đã tra hôm nay không tính thêm. Quản lý không bị trừ."""
    if actor.daily_lookup_limit is None:
        return {"limit": None, "used": 0, "remaining": None, "charged": 0}
    day = local_day()
    keys: list[str] = []
    seen: set[str] = set()
    for phone in phones:
        key = db.phone_key(phone) or phone
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    limit = actor.daily_lookup_limit
    stamp = at or _iso()
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {
            str(row["phone_key"])
            for row in conn.execute(
                "SELECT phone_key FROM lookup_usage WHERE account_id=? AND day=?",
                (actor.id, day),
            )
        }
        fresh = [key for key in keys if key not in existing]
        used = len(existing)
        if used + len(fresh) > limit:
            conn.rollback()
            raise LookupQuotaError(used, limit, len(fresh))
        if fresh:
            conn.executemany(
                "INSERT INTO lookup_usage(account_id, day, phone_key, at) VALUES(?,?,?,?)",
                [(actor.id, day, key, stamp) for key in fresh],
            )
        conn.commit()
    finally:
        conn.close()
    used_after = used + len(fresh)
    return {
        "limit": limit,
        "used": used_after,
        "remaining": max(0, limit - used_after),
        "charged": len(fresh),
    }
