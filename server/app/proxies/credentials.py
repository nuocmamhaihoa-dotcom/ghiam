from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from app.crypto import SecretBox
from app.models import Proxy
from app.proxies.parser import SESSION_PLACEHOLDER, host_for_url


@dataclass(frozen=True, slots=True)
class ProxyCredentials:
    protocol: str
    host: str
    port: int
    username: str | None
    password: str | None

    @property
    def server(self) -> str:
        return f"{self.protocol}://{host_for_url(self.host)}:{self.port}"

    @property
    def url(self) -> str:
        auth = ""
        if self.username:
            auth = quote(self.username, safe="")
            if self.password is not None:
                auth += ":" + quote(self.password, safe="")
            auth += "@"
        return f"{self.protocol}://{auth}{host_for_url(self.host)}:{self.port}"


def _fill_session(value: str | None, session_id: str | None) -> str | None:
    if value is None or session_id is None:
        return value
    return value.replace(SESSION_PLACEHOLDER, session_id)


def build_credentials(
    protocol: str, host: str, port: int, username: str, password: str | None, session_id: str | None
) -> ProxyCredentials:
    filled_username = _fill_session(username, session_id) or None
    return ProxyCredentials(
        protocol=protocol,
        host=host,
        port=port,
        username=filled_username,
        password=_fill_session(password, session_id) if filled_username else None,
    )


def resolve_credentials(proxy: Proxy, box: SecretBox, *, session_id: str | None = None) -> ProxyCredentials:
    """Tạo thông tin kết nối thật (đã giải mã mật khẩu và điền session hiện tại)."""
    return build_credentials(
        proxy.protocol,
        proxy.host,
        proxy.port,
        proxy.username,
        box.decrypt(proxy.password_enc),
        session_id if session_id is not None else proxy.session_id,
    )
