"""User repository SQLAlchemy implementation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.entities import UserEntity
from app.domain.enums import UserStatus
from app.infrastructure.db.models import RoleModel, UserModel, UserRoleModel


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: UserModel) -> UserEntity:
        return UserEntity(
            id=row.id,
            email=row.email,
            full_name=row.full_name,
            status=UserStatus(row.status),
            password_hash=row.password_hash,
            tenant_id=row.tenant_id,
            roles=[r.code for r in row.roles],
            created_at=row.created_at,
            last_login_at=row.last_login_at,
        )

    async def get_by_email(self, email: str) -> UserEntity | None:
        stmt = (
            select(UserModel)
            .options(selectinload(UserModel.roles))
            .where(UserModel.email == email.lower(), UserModel.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_entity(row) if row else None

    async def get_by_id(self, user_id: UUID) -> UserEntity | None:
        stmt = (
            select(UserModel)
            .options(selectinload(UserModel.roles))
            .where(UserModel.id == user_id, UserModel.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_entity(row) if row else None

    async def ensure_roles(self, codes: list[tuple[str, str]]) -> None:
        for code, name in codes:
            existing = await self._session.execute(
                select(RoleModel).where(RoleModel.code == code)
            )
            if existing.scalar_one_or_none() is None:
                self._session.add(RoleModel(id=uuid4(), code=code, name=name))
        await self._session.flush()

    async def create(
        self,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        role_codes: list[str],
        tenant_id: UUID | None = None,
    ) -> UserEntity:
        user = UserModel(
            id=uuid4(),
            email=email.lower(),
            full_name=full_name,
            password_hash=password_hash,
            tenant_id=tenant_id,
            status=UserStatus.ACTIVE.value,
        )
        self._session.add(user)
        await self._session.flush()

        if role_codes:
            roles = (
                await self._session.execute(
                    select(RoleModel).where(RoleModel.code.in_(role_codes))
                )
            ).scalars().all()
            for role in roles:
                self._session.add(UserRoleModel(user_id=user.id, role_id=role.id))
        await self._session.flush()
        await self._session.refresh(user, attribute_names=["roles"])
        return self._to_entity(user)

    async def update_last_login(self, user_id: UUID, when: datetime) -> None:
        row = await self._session.get(UserModel, user_id)
        if row:
            row.last_login_at = when
            await self._session.flush()
