from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class SecretDecryptionError(RuntimeError):
    pass


class SecretBox:
    """Mã hoá đối xứng (Fernet) cho mật khẩu proxy và link đổi IP lưu trong DB."""

    def __init__(self, *, encryption_key: str | None, secret_key: str) -> None:
        if encryption_key:
            key = encryption_key.encode()
        else:
            derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"commentscope/proxy-secrets").derive(
                secret_key.encode()
            )
            key = base64.urlsafe_b64encode(derived)
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretDecryptionError(
                "Không giải mã được dữ liệu proxy: ENCRYPTION_KEY/SECRET_KEY đã bị thay đổi"
            ) from exc
