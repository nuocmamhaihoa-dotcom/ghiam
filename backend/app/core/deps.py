"""FastAPI dependency injection wiring."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.rbac import permissions_for_roles
from app.core.security import decode_token
from app.infrastructure.redis.client import RedisClient, get_redis_client
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.call import SqlAlchemyCallRepository
from app.infrastructure.repositories.coaching import SqlAlchemyCoachingRepository
from app.infrastructure.repositories.evidence import SqlAlchemyEvidenceRepository
from app.infrastructure.repositories.golden import SqlAlchemyGoldenCallRepository
from app.infrastructure.repositories.revenue import SqlAlchemyRevenueLeakRepository
from app.infrastructure.repositories.root_cause import SqlAlchemyRootCauseRepository
from app.infrastructure.repositories.rule import SqlAlchemyRuleRepository
from app.infrastructure.repositories.score import SqlAlchemyScoreRepository
from app.infrastructure.repositories.user import SqlAlchemyUserRepository
from app.infrastructure.s3.client import S3Client, get_s3_client

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    email: str
    roles: list[str]
    tenant_id: UUID | None
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    roles = list(payload.get("roles") or [])
    tenant_raw = payload.get("tenant_id")
    tenant_id = UUID(tenant_raw) if tenant_raw else None
    perms = frozenset(permissions_for_roles(roles))
    return CurrentUser(
        id=UUID(payload["sub"]),
        email=str(payload.get("email") or ""),
        roles=roles,
        tenant_id=tenant_id,
        permissions=perms,
    )


def require_permissions(*needed: str) -> Callable[..., CurrentUser]:
    async def _dep(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        for permission in needed:
            user.require(permission)
        return user

    return _dep


async def get_user_repo(session: DbSession) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(session)


async def get_call_repo(session: DbSession) -> SqlAlchemyCallRepository:
    return SqlAlchemyCallRepository(session)


async def get_rule_repo(session: DbSession) -> SqlAlchemyRuleRepository:
    return SqlAlchemyRuleRepository(session)


async def get_score_repo(session: DbSession) -> SqlAlchemyScoreRepository:
    return SqlAlchemyScoreRepository(session)


async def get_evidence_repo(session: DbSession) -> SqlAlchemyEvidenceRepository:
    return SqlAlchemyEvidenceRepository(session)


async def get_root_cause_repo(session: DbSession) -> SqlAlchemyRootCauseRepository:
    return SqlAlchemyRootCauseRepository(session)


async def get_coaching_repo(session: DbSession) -> SqlAlchemyCoachingRepository:
    return SqlAlchemyCoachingRepository(session)


async def get_revenue_repo(session: DbSession) -> SqlAlchemyRevenueLeakRepository:
    return SqlAlchemyRevenueLeakRepository(session)


async def get_audit_repo(session: DbSession) -> SqlAlchemyAuditRepository:
    return SqlAlchemyAuditRepository(session)


async def get_golden_repo(session: DbSession) -> SqlAlchemyGoldenCallRepository:
    return SqlAlchemyGoldenCallRepository(session)


async def get_redis() -> AsyncGenerator[RedisClient, None]:
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.close()


async def get_s3() -> S3Client:
    return get_s3_client()


async def optional_request_id(
    x_request_id: Annotated[str | None, Header(alias="X-Request-Id")] = None,
) -> str | None:
    return x_request_id
