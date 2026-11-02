"""Salt + encrypt the PC address so hub and disk cannot be reversed to the machine."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from typing import Any

SEAL_PREFIX = "pcm1."
TOKEN_LEN = 64
SALT_BYTES = 32
MASTER_BYTES = 32
NONCE_BYTES = 16
TAG_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
STRETCH_LEN = 64
PBKDF2_ROUNDS = 210_000
PEPPER = b"pc-macro/cloak/v1/identity-never-leave-this-box"
LAYERS = (
    "Muối ngẫu nhiên 32 byte trên máy",
    "Mật mã scrypt / PBKDF2 + HMAC",
    "Một chiều HMAC — không giải ra IP",
    "Đĩa chỉ lưu bản niêm, không địa chỉ thô",
    "Hub chỉ nhận token, không hostname / LAN",
)


def cloak_dir() -> Path:
    env = os.environ.get("PC_MACRO_CLOAK", "").strip()
    if env:
        return Path(env)
    store = os.environ.get("PC_MACRO_DIR", "").strip()
    if store:
        return Path(store).parent / "cloak"
    vpn = os.environ.get("PC_MACRO_VPN", "").strip()
    if vpn:
        return Path(vpn).parent / "cloak"
    return Path.cwd() / "data" / "cloak"


def layer_names() -> list[str]:
    return list(LAYERS)


def is_sealed(raw: Any) -> bool:
    text = str(raw or "").strip()
    return text.startswith(SEAL_PREFIX) and len(text) > len(SEAL_PREFIX) + 16


def hold_address(raw: Any) -> str:
    """Keep a sealed blob intact; only normalize a bare IP."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if is_sealed(text):
        return text[:400]
    token = "".join(ch for ch in text.splitlines()[0] if ch.isdigit() or ch in ".:abcdefABCDEF")
    if token.count(".") == 3 and all(part.isdigit() and int(part) <= 255 for part in token.split(".")):
        return token[:64]
    if ":" in token and any(ch.isalnum() for ch in token):
        return token[:64]
    return ""


def mask_address(raw: Any) -> str:
    text = hold_address(raw)
    if not text or is_sealed(text):
        return "đã muối"
    if "." in text and ":" not in text:
        parts = text.split(".")
        return f"•••.•••.{parts[2]}.{parts[3]}" if len(parts) == 4 else "đã muối"
    if ":" in text:
        return "••••:••••"
    return "đã muối"


def looks_like_address(raw: Any) -> bool:
    text = str(raw or "").strip()
    if not text or is_sealed(text):
        return False
    return bool(hold_address(text))


def collect_marks() -> list[str]:
    """Local hostname / LAN / MAC — never persist or send these raw."""
    marks: list[str] = []
    for take in (socket.gethostname, socket.getfqdn):
        try:
            value = str(take() or "").strip()
        except OSError:
            value = ""
        if value and value not in marks:
            marks.append(value)
    try:
        node = format(uuid.getnode(), "012x")
    except Exception:
        node = ""
    if node:
        marks.append(node)
    host = marks[0] if marks else ""
    if host:
        try:
            for info in socket.getaddrinfo(host, None):
                ip = str(info[4][0] or "")
                if ip and ip not in {"127.0.0.1", "::1"} and ip not in marks:
                    marks.append(ip)
        except OSError:
            pass
    return marks


def public_token(vault: "CloakVault", extra: str = "") -> str:
    marks = list(collect_marks())
    if extra:
        marks.append(str(extra))
    return vault.token("|".join(marks))


class CloakVault:
    """Per-machine salt + master key. Without both files the address cannot be opened."""

    def __init__(self, root: Path | None = None, *, salt: bytes | None = None, master: bytes | None = None) -> None:
        self.root = Path(root) if root is not None else cloak_dir()
        self.salt = salt if salt is not None else _read_secret(self.root / "salt.bin", SALT_BYTES)
        self.master = master if master is not None else _read_secret(self.root / "master.bin", MASTER_BYTES)

    def seal(self, raw: Any) -> str:
        text = str(raw or "")
        if not text:
            return ""
        if is_sealed(text):
            return text[:400]
        nonce = secrets.token_bytes(NONCE_BYTES)
        material = self._stretch(b"seal", nonce)
        enc_key, mac_key = material[:32], material[32:]
        plain = text.encode("utf-8")
        cipher = bytes(a ^ b for a, b in zip(plain, _keystream(enc_key, nonce, len(plain))))
        tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
        blob = urlsafe_b64encode(nonce + tag + cipher).decode("ascii").rstrip("=")
        return f"{SEAL_PREFIX}{blob}"

    def open(self, raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if not is_sealed(text):
            return hold_address(text) or text
        packed = text[len(SEAL_PREFIX) :]
        pad = "=" * (-len(packed) % 4)
        try:
            blob = urlsafe_b64decode(packed + pad)
        except (ValueError, OSError):
            return ""
        if len(blob) < NONCE_BYTES + TAG_BYTES:
            return ""
        nonce = blob[:NONCE_BYTES]
        tag = blob[NONCE_BYTES : NONCE_BYTES + TAG_BYTES]
        cipher = blob[NONCE_BYTES + TAG_BYTES :]
        material = self._stretch(b"seal", nonce)
        enc_key, mac_key = material[:32], material[32:]
        expect = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expect):
            return ""
        plain = bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, nonce, len(cipher))))
        try:
            return plain.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def token(self, raw: Any) -> str:
        text = str(raw or "")
        if not text:
            return ""
        key = self._stretch(b"token", b"one-way")
        digest = hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest[:TOKEN_LEN]

    def _stretch(self, purpose: bytes, extra: bytes) -> bytes:
        secret = self.master + PEPPER + purpose
        salt = self.salt + extra
        try:
            return hashlib.scrypt(secret, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=STRETCH_LEN)
        except (ValueError, TypeError, OSError):
            return hashlib.pbkdf2_hmac("sha256", secret, salt, PBKDF2_ROUNDS, dklen=STRETCH_LEN)


def _read_secret(path: Path, size: int) -> bytes:
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) >= size:
            return raw[:size]
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(size)
    path.write_bytes(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]
