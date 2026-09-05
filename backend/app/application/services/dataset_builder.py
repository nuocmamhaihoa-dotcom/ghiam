"""Dataset builder for training / calibration exports."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import DatasetExportModel, ScoreModel


class DatasetBuilderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(
        self,
        *,
        name: str,
        dataset_type: str = "scoring",
        created_by: UUID | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        scores = list(
            (
                await self._session.execute(
                    select(ScoreModel).order_by(ScoreModel.evaluated_at.desc()).limit(limit)
                )
            ).scalars()
        )
        rows = [
            {
                "call_id": str(score.call_id),
                "score": float(score.overall_score),
                "result": score.result,
                "stage_scores": score.stage_scores,
                "payload": {
                    "score": float(score.overall_score),
                    "stage_scores": score.stage_scores,
                    "violations": (score.payload or {}).get("violations", []),
                    "evidence": (score.payload or {}).get("evidence", []),
                    "root_cause": (score.payload or {}).get("root_cause", {}),
                    "coaching": (score.payload or {}).get("coaching", {}),
                    "revenue_leak": (score.payload or {}).get("revenue_leak", {}),
                },
            }
            for score in scores
        ]
        export = DatasetExportModel(
            id=uuid4(),
            name=name,
            dataset_type=dataset_type,
            status="ready",
            row_count=len(rows),
            filters=filters or {},
            sample_rows=rows[:20],
            created_by=created_by,
        )
        self._session.add(export)
        await self._session.flush()
        return {
            "id": str(export.id),
            "name": export.name,
            "dataset_type": export.dataset_type,
            "status": export.status,
            "row_count": export.row_count,
            "sample_rows": export.sample_rows,
        }

    async def list_exports(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = list(
            (
                await self._session.execute(
                    select(DatasetExportModel).order_by(DatasetExportModel.created_at.desc()).limit(limit)
                )
            ).scalars()
        )
        return [
            {
                "id": str(row.id),
                "name": row.name,
                "dataset_type": row.dataset_type,
                "status": row.status,
                "row_count": row.row_count,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
