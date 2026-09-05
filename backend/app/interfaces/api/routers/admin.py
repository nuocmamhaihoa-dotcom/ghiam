"""Admin APIs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession, require_permissions
from app.infrastructure.db.models import UserModel

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
async def list_users(
    user: Annotated[CurrentUser, Depends(require_permissions("admin:users"))],
    session: DbSession,
) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                select(UserModel)
                .options(selectinload(UserModel.roles))
                .where(UserModel.deleted_at.is_(None))
                .order_by(UserModel.full_name.asc())
            )
        ).scalars()
    )
    data = [
        {
            "id": str(row.id),
            "email": row.email,
            "full_name": row.full_name,
            "roles": [role.code for role in row.roles],
            "team_name": "Unassigned",
            "active": row.status == "active",
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        }
        for row in rows
    ]
    return {"data": data}
