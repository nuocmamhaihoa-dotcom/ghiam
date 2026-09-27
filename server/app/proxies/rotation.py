"""Gọi link đổi IP của proxy 4G và đọc phản hồi của nhà cung cấp."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, replace

import httpx

from app.models import ProxyProtocol
from app.proxies.checker import USER_AGENT, short_error
from app.proxies.parser import Endpoint, ProxyParseError, parse_endpoint

_MESSAGE_KEYS = ("message", "msg", "error", "description", "detail")
_FAILURE_STATUSES = frozenset({"error", "fail", "failed", "false", "failure"})
_WHITESPACE_RE = re.compile(r"\s+")
_HTTP_ENDPOINT_KEYS = ("proxyhttp", "proxy_http", "http", "https", "proxy")
_ENDPOINT_KEYS: dict[str, tuple[str, ...]] = {
    ProxyProtocol.HTTP: _HTTP_ENDPOINT_KEYS,
    ProxyProtocol.HTTPS: _HTTP_ENDPOINT_KEYS,
    ProxyProtocol.SOCKS5: ("proxysocks5", "proxy_socks5", "socks5", "socks", "proxy"),
}


class RotationCallError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RotationCallResult:
    status_code: int
    message: str
    new_endpoint: Endpoint | None


def new_session_id() -> str:
    return secrets.token_hex(6)


def _clip(text: str, limit: int = 200) -> str:
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _message_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in _MESSAGE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value)
    return None


def _reports_failure(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is False or payload.get("ok") is False:
        return True
    status = payload.get("status")
    if status is False or (isinstance(status, str) and status.strip().lower() in _FAILURE_STATUSES):
        return True
    error = payload.get("error")
    return error is True or (isinstance(error, str) and bool(error.strip()) and "success" not in payload)


def extract_endpoint(payload: object, protocol: str) -> Endpoint | None:
    """Tìm endpoint proxy mới trong JSON trả về (nhà cung cấp kiểu 'lấy proxy mới theo key')."""
    if not isinstance(payload, dict):
        return None
    containers: list[dict[str, object]] = [payload]
    containers += [value for key, value in payload.items() if str(key).lower() == "data" and isinstance(value, dict)]
    for container in containers:
        lowered = {str(key).lower(): value for key, value in container.items()}
        for key in _ENDPOINT_KEYS.get(protocol, _HTTP_ENDPOINT_KEYS):
            value = lowered.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                endpoint = parse_endpoint(value.strip())
            except ProxyParseError:
                continue
            username = lowered.get("username") or lowered.get("user")
            password = lowered.get("password") or lowered.get("pass")
            if not endpoint.username and isinstance(username, str) and username and isinstance(password, str):
                endpoint = replace(endpoint, username=username, password=password or None)
            return endpoint
    return None


async def call_rotation_url(url: str, *, method: str, timeout_sec: float, protocol: str) -> RotationCallResult:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec),
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
        ) as client:
            response = await client.request(method, url)
    except httpx.TimeoutException as exc:
        raise RotationCallError("Gọi link đổi IP quá thời gian chờ") from exc
    except httpx.HTTPError as exc:
        raise RotationCallError(f"Không gọi được link đổi IP: {short_error(exc)}") from exc

    body = response.text[:8000]
    try:
        payload: object = json.loads(body)
    except ValueError:
        payload = None
    message = _message_from_payload(payload) or (_clip(body) if payload is None and body.strip() else "")
    if response.status_code >= 400:
        raise RotationCallError(f"Link đổi IP trả về HTTP {response.status_code}. {message}".strip())
    if _reports_failure(payload):
        raise RotationCallError(f"Nhà cung cấp báo lỗi: {message or 'không rõ lý do'}")
    return RotationCallResult(
        status_code=response.status_code, message=message, new_endpoint=extract_endpoint(payload, protocol)
    )
