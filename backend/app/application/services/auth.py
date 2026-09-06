"""Authentication application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.rbac import permissions_for_roles
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.user import SqlAlchemyUserRepository


class AuthService:
    def __init__(
        self,
        users: SqlAlchemyUserRepository,
        audit: SqlAlchemyAuditRepository,
    ) -> None:
        self._users = users
        self._audit = audit

    async def login(
        self,
        *,
        email: str,
        password: str,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        user = await self._users.get_by_email(email)
        if user is None or not user.password_hash:
            raise ValueError("Invalid email or password")
        if user.status.value != "active":
            raise ValueError("User is not active")
        if not verify_password(password, user.password_hash):
            raise ValueError("Invalid email or password")

        await self._users.update_last_login(user.id, datetime.now(UTC))
        access = create_access_token(
            user.id,
            email=user.email,
            roles=user.roles,
            tenant_id=user.tenant_id,
        )
        refresh = create_refresh_token(user.id)
        perms = sorted(permissions_for_roles(user.roles))
        await self._audit.record(
            action="auth.login",
            actor_user_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
            request_id=request_id,
            ip_address=ip_address,
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": 900,
            "user": {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "roles": user.roles,
                "permissions": perms,
            },
        }

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise ValueError("Invalid refresh token") from exc
        if payload.get("type") != "refresh":
            raise ValueError("Invalid refresh token")
        user = await self._users.get_by_id(UUID(payload["sub"]))
        if user is None or user.status.value != "active":
            raise ValueError("User not found or inactive")
        access = create_access_token(
            user.id,
            email=user.email,
            roles=user.roles,
            tenant_id=user.tenant_id,
        )
        return {
            "access_token": access,
            "token_type": "bearer",
            "expires_in": 900,
        }

    async def logout(
        self,
        *,
        user_id: UUID,
        request_id: str | None = None,
    ) -> None:
        await self._audit.record(
            action="auth.logout",
            actor_user_id=user_id,
            resource_type="user",
            resource_id=str(user_id),
            request_id=request_id,
        )

    @staticmethod
    def hash_password(password: str) -> str:
        return hash_password(password)
