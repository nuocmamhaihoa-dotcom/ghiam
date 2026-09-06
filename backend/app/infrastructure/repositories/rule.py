"""Rule repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import RuleEntity, RuleVersionEntity
from app.domain.enums import RuleSeverity, RuleStatus
from app.infrastructure.db.models import RuleModel, RuleVersionModel


class SqlAlchemyRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: RuleModel) -> RuleEntity:
        return RuleEntity(
            id=row.id,
            rule_code=row.rule_code,
            category=row.category,
            title=row.title,
            description=row.description,
            severity=RuleSeverity(row.severity),
            weight=row.weight,
            auto_fail=row.auto_fail,
            status=RuleStatus(row.status),
            evaluator_type=row.evaluator_type,
            evidence_requirements=dict(row.evidence_requirements or {}),
            evaluator_config=dict(row.evaluator_config or {}),
            current_version=row.current_version,
            subcategory=row.subcategory,
            required=row.required,
            cause_code_on_fail=row.cause_code_on_fail,
            coaching_template_code=row.coaching_template_code,
            revenue_impact_code=row.revenue_impact_code,
            industries=list(row.industries or ["*"]),
        )

    def _snapshot(self, rule: RuleEntity) -> dict[str, Any]:
        return {
            "rule_code": rule.rule_code,
            "category": rule.category,
            "subcategory": rule.subcategory,
            "title": rule.title,
            "description": rule.description,
            "severity": rule.severity.value,
            "weight": rule.weight,
            "auto_fail": rule.auto_fail,
            "required": rule.required,
            "status": rule.status.value,
            "evaluator_type": rule.evaluator_type,
            "evidence_requirements": rule.evidence_requirements,
            "evaluator_config": rule.evaluator_config,
            "cause_code_on_fail": rule.cause_code_on_fail,
            "coaching_template_code": rule.coaching_template_code,
            "revenue_impact_code": rule.revenue_impact_code,
            "industries": rule.industries,
            "version": rule.current_version,
        }

    async def list(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RuleEntity]:
        stmt = select(RuleModel).where(RuleModel.deleted_at.is_(None))
        if category:
            stmt = stmt.where(RuleModel.category == category)
        if status:
            stmt = stmt.where(RuleModel.status == status)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(RuleModel.rule_code.ilike(like), RuleModel.title.ilike(like))
            )
        stmt = stmt.order_by(RuleModel.rule_code).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def get_by_code(self, rule_code: str) -> RuleEntity | None:
        stmt = select(RuleModel).where(
            RuleModel.rule_code == rule_code, RuleModel.deleted_at.is_(None)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_entity(row) if row else None

    async def get(self, rule_id: UUID) -> RuleEntity | None:
        row = await self._session.get(RuleModel, rule_id)
        if row is None or row.deleted_at is not None:
            return None
        return self._to_entity(row)

    async def create(self, rule: RuleEntity, created_by: UUID | None) -> RuleEntity:
        row = RuleModel(
            id=rule.id or uuid4(),
            rule_code=rule.rule_code,
            category=rule.category,
            subcategory=rule.subcategory,
            title=rule.title,
            description=rule.description,
            severity=rule.severity.value,
            weight=rule.weight,
            auto_fail=rule.auto_fail,
            required=rule.required,
            status=rule.status.value,
            evaluator_type=rule.evaluator_type,
            evidence_requirements=rule.evidence_requirements,
            evaluator_config=rule.evaluator_config,
            cause_code_on_fail=rule.cause_code_on_fail,
            coaching_template_code=rule.coaching_template_code,
            revenue_impact_code=rule.revenue_impact_code,
            industries=rule.industries,
            current_version=1,
        )
        self._session.add(row)
        await self._session.flush()
        entity = self._to_entity(row)
        await self.create_version(row.id, self._snapshot(entity), created_by, "initial")
        return entity

    async def update(
        self, rule: RuleEntity, created_by: UUID | None, change_note: str | None
    ) -> RuleEntity:
        row = await self._session.get(RuleModel, rule.id)
        if row is None:
            raise ValueError("Rule not found")
        row.category = rule.category
        row.subcategory = rule.subcategory
        row.title = rule.title
        row.description = rule.description
        row.severity = rule.severity.value
        row.weight = rule.weight
        row.auto_fail = rule.auto_fail
        row.required = rule.required
        row.status = rule.status.value
        row.evaluator_type = rule.evaluator_type
        row.evidence_requirements = rule.evidence_requirements
        row.evaluator_config = rule.evaluator_config
        row.cause_code_on_fail = rule.cause_code_on_fail
        row.coaching_template_code = rule.coaching_template_code
        row.revenue_impact_code = rule.revenue_impact_code
        row.industries = rule.industries
        row.current_version = int(row.current_version) + 1
        await self._session.flush()
        entity = self._to_entity(row)
        await self.create_version(
            row.id, self._snapshot(entity), created_by, change_note or "update"
        )
        return entity

    async def create_version(
        self,
        rule_id: UUID,
        snapshot: dict[str, Any],
        created_by: UUID | None,
        change_note: str | None,
    ) -> RuleVersionEntity:
        row_rule = await self._session.get(RuleModel, rule_id)
        version = int(row_rule.current_version) if row_rule else 1
        ver = RuleVersionModel(
            id=uuid4(),
            rule_id=rule_id,
            version=version,
            snapshot=snapshot,
            created_by=created_by,
            change_note=change_note,
        )
        self._session.add(ver)
        await self._session.flush()
        return RuleVersionEntity(
            id=ver.id,
            rule_id=ver.rule_id,
            version=ver.version,
            snapshot=ver.snapshot,
            created_by=ver.created_by,
            created_at=ver.created_at,
            change_note=ver.change_note,
        )

    async def list_active(self) -> list[RuleEntity]:
        stmt = select(RuleModel).where(
            RuleModel.status == RuleStatus.ACTIVE.value,
            RuleModel.deleted_at.is_(None),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def count(self, *, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(RuleModel).where(RuleModel.deleted_at.is_(None))
        if status:
            stmt = stmt.where(RuleModel.status == status)
        return int((await self._session.execute(stmt)).scalar_one())

    async def upsert_from_seed(self, payload: dict[str, Any]) -> RuleEntity:
        code = str(payload["rule_code"])
        existing = await self.get_by_code(code)
        severity = RuleSeverity(str(payload.get("severity", "major")))
        status = RuleStatus(str(payload.get("status", "active")))
        entity = RuleEntity(
            id=existing.id if existing else uuid4(),
            rule_code=code,
            category=str(payload.get("category", "other")),
            title=str(payload.get("title", code)),
            description=str(payload.get("description", "")),
            severity=severity,
            weight=float(payload.get("weight", 1.0)),
            auto_fail=bool(payload.get("auto_fail", False)),
            status=status,
            evaluator_type=str(payload.get("evaluator_type", "keyword")),
            evidence_requirements=dict(payload.get("evidence_requirements") or {}),
            evaluator_config=dict(payload.get("evaluator_config") or {}),
            current_version=existing.current_version if existing else 1,
            subcategory=payload.get("subcategory"),
            required=bool(payload.get("required", True)),
            cause_code_on_fail=payload.get("cause_code_on_fail"),
            coaching_template_code=payload.get("coaching_template_code"),
            revenue_impact_code=payload.get("revenue_impact_code"),
            industries=list(payload.get("industries") or ["*"]),
        )
        if existing:
            return await self.update(entity, None, "seed upsert")
        return await self.create(entity, None)
