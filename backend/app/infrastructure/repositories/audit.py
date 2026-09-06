"""Audit log repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AuditLogModel


class SqlAlchemyAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditLogModel(
                id=uuid4(),
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
                before=before,
                after=after,
                metadata_json=metadata or {},
            )
        )
        await self._session.flush()
