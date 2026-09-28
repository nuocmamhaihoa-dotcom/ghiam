from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from tiktok_osint.errors import ReverseLookupForbidden, UnsafeUrl

BLOCKED_OPERATIONS = frozenset(
    {
        "phone_to_user_id",
        "reverse_phone_lookup",
        "contact_hash_match",
        "infer_account_from_phone",
    }
)

_AVATAR_SUFFIXES = (
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
)


def reject_reverse_lookup(operation: str) -> None:
    """Fail closed for any phone → account inference, including renamed aliases."""
    normalized = operation.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in BLOCKED_OPERATIONS:
        raise ReverseLookupForbidden()
    if "phone" in normalized and any(token in normalized for token in ("lookup", "resolve", "infer", "match", "hash")):
        raise ReverseLookupForbidden()


def navigation_host_allowed(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host == "tiktok.com" or host.endswith(".tiktok.com") or host.endswith(".tiktokcdn.com")


def assert_avatar_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise UnsafeUrl("Ảnh hồ sơ phải là URL HTTPS trên CDN công khai của TikTok")
    if parsed.username or parsed.password:
        raise UnsafeUrl("URL ảnh không được chứa thông tin đăng nhập")
    allowed = any(host.endswith(suffix) for suffix in _AVATAR_SUFFIXES)
    if not allowed:
        raise UnsafeUrl("Ảnh hồ sơ không thuộc CDN công khai của TikTok")
    return url


def assert_fetchable_public_url(
    url: str,
    *,
    resolve: Callable[[str], list[str]] | None = None,
) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrl("Chỉ tải trang http(s) công khai")
    if parsed.username or parsed.password:
        raise UnsafeUrl("URL không được nhúng tài khoản hoặc mật khẩu")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeUrl("Host nội bộ bị chặn")
    if host.isdigit():
        raise UnsafeUrl("Host dạng số không được phép")
    try:
        _reject_ip(ipaddress.ip_address(host))
        return url
    except ValueError:
        pass
    resolver = resolve if resolve is not None else default_resolve
    addresses = resolver(host)
    if not addresses:
        raise UnsafeUrl("Không phân giải được host công khai")
    for address in addresses:
        _reject_ip(ipaddress.ip_address(address))
    return url


def default_resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrl("Không phân giải được host công khai") from exc
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _reject_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_reserved,
            ip.is_multicast,
            ip.is_unspecified,
        )
    ):
        raise UnsafeUrl("Chỉ truy cập địa chỉ công khai")
