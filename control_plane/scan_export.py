"""Mật khẩu để tải toàn bộ kết quả đã quét. Máy chủ chỉ giữ bản scrypt, không giữ mật khẩu gốc."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_PREFIX = "scrypt"
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_ENV = "CONTROL_SCAN_EXPORT_PASSWORD"


def seal_export_password(password: str, salt: bytes | None = None) -> str:
    """Đổi mật khẩu thành chuỗi lưu trong môi trường máy chủ."""
    raw = str(password or "").strip()
    if not raw or len(raw) > 200:
        raise ValueError("Mật khẩu tải về cần từ 1 đến 200 ký tự")
    used = salt if salt is not None else secrets.token_bytes(16)
    if len(used) < 8:
        raise ValueError("Muối mật khẩu quá ngắn")
    digest = hashlib.scrypt(raw.encode("utf-8"), salt=used, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"{_PREFIX}${used.hex()}${digest.hex()}"


def export_configured() -> bool:
    return bool(os.environ.get(_ENV, "").strip())


def export_password_ok(password: str) -> bool:
    """Đối chiếu mật khẩu vừa nhập với bản scrypt. Sai hoặc chưa đặt thì không cho tải."""
    stored = os.environ.get(_ENV, "").strip()
    given = str(password or "").strip()
    if not stored or not given or len(given) > 200:
        return False
    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return False
    try:
        salt = bytes.fromhex(parts[1])
        expect = bytes.fromhex(parts[2])
    except ValueError:
        return False
    if len(salt) < 8 or len(expect) != _DKLEN:
        return False
    digest = hashlib.scrypt(given.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return hmac.compare_digest(digest, expect)
