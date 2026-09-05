"""Rulebook CRUD router — rules live in DB, never hardcoded for scoring."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.deps import (
    CurrentUser,
    get_audit_repo,
    get_rule_repo,
    require_permissions,
)
from app.domain.entities import RuleEntity
from app.domain.enums import RuleSeverity, RuleStatus
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.rule import SqlAlchemyRuleRepository
from app.interfaces.api.schemas import (
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleUpdateRequest,
)

router = APIRouter(prefix="/rulebook", tags=["rules"])


def _to_response(rule: RuleEntity) -> RuleResponse:
    return RuleResponse(
        id=rule.id,
        rule_code=rule.rule_code,
        category=rule.category,
        title=rule.title,
        description=rule.description,
        severity=rule.severity.value,
        weight=rule.weight,
        auto_fail=rule.auto_fail,
        status=rule.status.value,
        evaluator_type=rule.evaluator_type,
        evidence_requirements=rule.evidence_requirements,
        evaluator_config=rule.evaluator_config,
        current_version=rule.current_version,
        subcategory=rule.subcategory,
        required=rule.required,
        cause_code_on_fail=rule.cause_code_on_fail,
        coaching_template_code=rule.coaching_template_code,
        revenue_impact_code=rule.revenue_impact_code,
        industries=rule.industries,
    )


@router.get("/rules", response_model=RuleListResponse)
async def list_rules(
    user: Annotated[CurrentUser, Depends(require_permissions("rulebook:read"))],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
    category: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> RuleListResponse:
    rows = await rules.list(
        category=category, status=status_filter, q=q, limit=limit, offset=offset
    )
    total = await rules.count(status=status_filter)
    return RuleListResponse(
        data=[_to_response(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/rules/{rule_code}", response_model=RuleResponse)
async def get_rule(
    rule_code: str,
    user: Annotated[CurrentUser, Depends(require_permissions("rulebook:read"))],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
) -> RuleResponse:
    rule = await rules.get_by_code(rule_code)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _to_response(rule)


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreateRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("rulebook:write"))],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> RuleResponse:
    existing = await rules.get_by_code(body.rule_code)
    if existing:
        raise HTTPException(status_code=409, detail="Rule code already exists")
    entity = RuleEntity(
        id=uuid4(),
        rule_code=body.rule_code,
        category=body.category,
        title=body.title,
        description=body.description,
        severity=RuleSeverity(body.severity),
        weight=body.weight,
        auto_fail=body.auto_fail,
        status=RuleStatus(body.status),
        evaluator_type=body.evaluator_type,
        evidence_requirements=body.evidence_requirements,
        evaluator_config=body.evaluator_config,
        current_version=1,
        subcategory=body.subcategory,
        required=body.required,
        cause_code_on_fail=body.cause_code_on_fail,
        coaching_template_code=body.coaching_template_code,
        revenue_impact_code=body.revenue_impact_code,
        industries=body.industries,
    )
    created = await rules.create(entity, user.id)
    await audit.record(
        action="rule.created",
        actor_user_id=user.id,
        resource_type="rule",
        resource_id=created.rule_code,
        request_id=getattr(request.state, "request_id", None),
        after={"rule_code": created.rule_code},
    )
    return _to_response(created)


@router.patch("/rules/{rule_code}", response_model=RuleResponse)
async def update_rule(
    rule_code: str,
    body: RuleUpdateRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("rulebook:write"))],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> RuleResponse:
    existing = await rules.get_by_code(rule_code)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    updated_entity = RuleEntity(
        id=existing.id,
        rule_code=existing.rule_code,
        category=body.category or existing.category,
        title=body.title or existing.title,
        description=body.description if body.description is not None else existing.description,
        severity=RuleSeverity(body.severity) if body.severity else existing.severity,
        weight=body.weight if body.weight is not None else existing.weight,
        auto_fail=body.auto_fail if body.auto_fail is not None else existing.auto_fail,
        status=RuleStatus(body.status) if body.status else existing.status,
        evaluator_type=body.evaluator_type or existing.evaluator_type,
        evidence_requirements=body.evidence_requirements or existing.evidence_requirements,
        evaluator_config=body.evaluator_config or existing.evaluator_config,
        current_version=existing.current_version,
        subcategory=body.subcategory if body.subcategory is not None else existing.subcategory,
        required=body.required if body.required is not None else existing.required,
        cause_code_on_fail=(
            body.cause_code_on_fail
            if body.cause_code_on_fail is not None
            else existing.cause_code_on_fail
        ),
        coaching_template_code=(
            body.coaching_template_code
            if body.coaching_template_code is not None
            else existing.coaching_template_code
        ),
        revenue_impact_code=(
            body.revenue_impact_code
            if body.revenue_impact_code is not None
            else existing.revenue_impact_code
        ),
        industries=body.industries or existing.industries,
    )
    updated = await rules.update(updated_entity, user.id, body.change_note)
    await audit.record(
        action="rule.updated",
        actor_user_id=user.id,
        resource_type="rule",
        resource_id=rule_code,
        request_id=getattr(request.state, "request_id", None),
        after={"version": updated.current_version},
    )
    return _to_response(updated)


@router.post("/rules/{rule_code}/versions", response_model=RuleResponse)
async def create_version(
    rule_code: str,
    body: RuleUpdateRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("rulebook:write"))],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> RuleResponse:
    """Create a new rule version by applying updates (bumps current_version)."""
    return await update_rule(rule_code, body, request, user, rules, audit)
