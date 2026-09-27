from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.deps import ContainerDep
from app.security import AdminUser, check_admin_credentials, issue_admin_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_at: datetime
    username: str


class MeOut(BaseModel):
    username: str


@router.post("/login")
async def login(data: LoginIn, request: Request, container: ContainerDep) -> LoginOut:
    client_key = request.client.host if request.client else "unknown"
    limiter = container.login_limiter
    retry_after = limiter.retry_after(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Đăng nhập sai quá nhiều lần, hãy thử lại sau {retry_after} giây",
            headers={"Retry-After": str(retry_after)},
        )
    if not check_admin_credentials(container.settings, data.username, data.password):
        limiter.record_failure(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tên đăng nhập hoặc mật khẩu")
    limiter.reset(client_key)
    token, expires_at = issue_admin_token(container.settings, data.username)
    return LoginOut(access_token=token, expires_at=expires_at, username=data.username)


@router.get("/me")
async def me(username: AdminUser) -> MeOut:
    return MeOut(username=username)
