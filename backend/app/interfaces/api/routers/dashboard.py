"""Dashboard summary router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession, require_permissions
from app.infrastructure.db.models import CallModel, RuleModel, ScoreModel
from app.interfaces.api.schemas import DashboardSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    user: Annotated[CurrentUser, Depends(require_permissions("dashboard:read"))],
    session: DbSession,
) -> DashboardSummary:
    calls_total = int(
        (await session.execute(select(func.count()).select_from(CallModel))).scalar_one()
    )
    calls_scored = int(
        (
            await session.execute(
                select(func.count()).select_from(CallModel).where(CallModel.status == "scored")
            )
        ).scalar_one()
    )
    rules_active = int(
        (
            await session.execute(
                select(func.count())
                .select_from(RuleModel)
                .where(RuleModel.status == "active", RuleModel.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    avg = (
        await session.execute(select(func.avg(ScoreModel.overall_score)))
    ).scalar_one()
    return DashboardSummary(
        calls_total=calls_total,
        calls_scored=calls_scored,
        rules_active=rules_active,
        avg_score=round(float(avg), 2) if avg is not None else None,
    )
