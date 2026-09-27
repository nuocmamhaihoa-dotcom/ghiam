from __future__ import annotations

import hmac
import time
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.timeutil import utcnow

if TYPE_CHECKING:
    from app.config import Settings

_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.container.settings
    return settings


def check_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    username_ok = hmac.compare_digest(username.encode(), settings.admin_username.encode())
    password_ok = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    return username_ok and password_ok


def issue_admin_token(settings: Settings, username: str) -> tuple[str, datetime]:
    issued_at = utcnow()
    expires_at = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": username,
        "typ": "admin",
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM), expires_at


def require_admin(request: Request, credentials: BearerCredentials) -> str:
    if credentials is None:
        raise _unauthorized("Chưa đăng nhập")
    try:
        payload = jwt.decode(
            credentials.credentials,
            _settings(request).secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Phiên đăng nhập đã hết hạn") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Token không hợp lệ") from exc
    if payload.get("typ") != "admin":
        raise _unauthorized("Token không hợp lệ")
    return str(payload["sub"])


def require_agent(request: Request, credentials: BearerCredentials) -> str:
    tokens = _settings(request).agent_token_list
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server chưa cấu hình AGENT_TOKENS nên chưa nhận kết nối từ agent",
        )
    if credentials is None:
        raise _unauthorized("Thiếu agent token")
    presented = credentials.credentials.encode()
    for index, token in enumerate(tokens, start=1):
        if hmac.compare_digest(presented, token.encode()):
            return f"agent-token-{index}"
    raise _unauthorized("Agent token không hợp lệ")


AdminUser = Annotated[str, Depends(require_admin)]
AgentIdentity = Annotated[str, Depends(require_agent)]


class LoginRateLimiter:
    """Giới hạn số lần đăng nhập sai theo IP (bộ nhớ trong tiến trình)."""

    def __init__(self, *, max_attempts: int, window_sec: int) -> None:
        self._max_attempts = max_attempts
        self._window_sec = window_sec
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._failures.setdefault(key, deque())
        while attempts and now - attempts[0] > self._window_sec:
            attempts.popleft()
        return attempts

    def retry_after(self, key: str) -> int | None:
        now = time.monotonic()
        attempts = self._prune(key, now)
        if len(attempts) < self._max_attempts:
            return None
        return max(1, int(self._window_sec - (now - attempts[0])) + 1)

    def record_failure(self, key: str) -> None:
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
