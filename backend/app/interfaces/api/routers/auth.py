"""Auth router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.services.auth import AuthService
from app.core.deps import CurrentUser, get_audit_repo, get_current_user, get_user_repo
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.user import SqlAlchemyUserRepository
from app.interfaces.api.schemas import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    users: Annotated[SqlAlchemyUserRepository, Depends(get_user_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> AuthService:
    return AuthService(users, audit)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    try:
        result = await auth.login(
            email=body.email,
            password=body.password,
            request_id=getattr(request.state, "request_id", None),
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    try:
        result = await auth.refresh(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**result)


@router.post("/logout")
async def logout(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, bool]:
    await auth.logout(
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "roles": user.roles,
        "permissions": sorted(user.permissions),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
    }
