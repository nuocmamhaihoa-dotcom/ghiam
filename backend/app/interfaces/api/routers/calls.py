"""Calls ingest & query router."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.deps import (
    CurrentUser,
    get_audit_repo,
    get_call_repo,
    get_evidence_repo,
    get_s3,
    get_user_repo,
    require_permissions,
)
from app.domain.entities import CallEntity, EvidenceEntity, TranscriptEntity
from app.domain.enums import CallDirection, CallStatus
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.call import SqlAlchemyCallRepository
from app.infrastructure.repositories.evidence import SqlAlchemyEvidenceRepository
from app.infrastructure.repositories.user import SqlAlchemyUserRepository
from app.infrastructure.s3.client import S3Client
from app.interfaces.api.schemas import (
    CallCreateRequest,
    CallListResponse,
    CallResponse,
    EvidenceIngestRequest,
    TranscriptIngestRequest,
)

router = APIRouter(prefix="/calls", tags=["calls"])


def _to_response(call: CallEntity, upload: dict[str, Any] | None = None) -> CallResponse:
    return CallResponse(
        id=call.id,
        external_call_id=call.external_call_id,
        status=call.status.value,
        direction=call.direction.value,
        campaign_code=call.campaign_code,
        agent_user_id=call.agent_user_id,
        duration_sec=call.duration_sec,
        crm_outcome=call.crm_outcome,
        upload=upload,
        created_at=call.created_at,
    )


@router.post("", response_model=CallResponse, status_code=status.HTTP_201_CREATED)
async def create_call(
    body: CallCreateRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:write"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    users: Annotated[SqlAlchemyUserRepository, Depends(get_user_repo)],
    evidence_repo: Annotated[SqlAlchemyEvidenceRepository, Depends(get_evidence_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
    s3: Annotated[S3Client, Depends(get_s3)],
) -> CallResponse:
    existing = await calls.get_by_external(user.tenant_id, body.external_call_id)
    if existing:
        return _to_response(existing)

    agent_id = body.agent_user_id
    if agent_id is None and body.agent_email:
        agent = await users.get_by_email(body.agent_email)
        agent_id = agent.id if agent else None

    call = CallEntity(
        id=uuid4(),
        external_call_id=body.external_call_id,
        status=CallStatus.RECEIVED,
        direction=CallDirection(body.direction),
        agent_user_id=agent_id,
        tenant_id=user.tenant_id,
        campaign_code=body.campaign_code,
        started_at=body.started_at,
        ended_at=body.ended_at,
        duration_sec=body.duration_sec,
        customer_phone=body.customer_phone,
        crm_outcome=body.crm_outcome,
        crm_order_value=body.crm_order_value,
        currency=body.currency,
        metadata=body.metadata,
    )
    created = await calls.create(call)

    s3_key = f"calls/{created.id}/audio"
    upload = s3.presign_put(s3_key, body.audio_content_type)
    await calls.set_audio_key(created.id, s3_key)

    if body.transcript_text or body.transcript_turns:
        turns = body.transcript_turns or []
        await calls.save_transcript(
            TranscriptEntity(
                id=uuid4(),
                call_id=created.id,
                language="vi",
                full_text=body.transcript_text or "",
                turns=turns,
                avg_confidence=float(
                    sum(float(t.get("confidence", 0.9)) for t in turns) / len(turns)
                )
                if turns
                else 0.9,
                turn_count=len(turns),
            )
        )

    if body.evidence:
        items = [
            EvidenceEntity(
                id=uuid4(),
                call_id=created.id,
                quote=str(e.get("quote", "")),
                speaker=str(e.get("speaker", "agent")),
                stage_key=e.get("stage_key"),
                slot=e.get("slot"),
                confidence=float(e.get("confidence", 0.8)),
                audio_ts_start=e.get("audio_ts_start"),
                audio_ts_end=e.get("audio_ts_end"),
                turn_index=e.get("turn_index"),
                labels=list(e.get("labels") or []),
                metadata=dict(e.get("metadata") or {}),
            )
            for e in body.evidence
            if e.get("quote")
        ]
        if items:
            await evidence_repo.bulk_create(items)

    await audit.record(
        action="call.created",
        actor_user_id=user.id,
        resource_type="call",
        resource_id=str(created.id),
        request_id=getattr(request.state, "request_id", None),
        after={"external_call_id": created.external_call_id},
    )
    refreshed = await calls.get(created.id)
    assert refreshed is not None
    return _to_response(refreshed, upload=upload)


@router.get("", response_model=CallListResponse)
async def list_calls(
    user: Annotated[CurrentUser, Depends(require_permissions("calls:read"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    agent_user_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> CallListResponse:
    # Agents only see their own calls unless they have broader roles
    scoped_agent = agent_user_id
    if "agent" in user.roles and not {"admin", "qa_lead", "coach", "viewer"} & set(user.roles):
        scoped_agent = user.id
    rows = await calls.list(
        agent_user_id=scoped_agent, status=status_filter, limit=limit, offset=offset
    )
    return CallListResponse(
        data=[_to_response(r) for r in rows], limit=limit, offset=offset
    )


@router.get("/{call_id}", response_model=CallResponse)
async def get_call(
    call_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:read"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
) -> CallResponse:
    call = await calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if (
        "agent" in user.roles
        and not {"admin", "qa_lead", "coach", "viewer"} & set(user.roles)
        and call.agent_user_id != user.id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return _to_response(call)


@router.post("/{call_id}/audio:complete", response_model=CallResponse)
async def complete_audio(
    call_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:write"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
    request: Request,
) -> CallResponse:
    call = await calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    await calls.update_status(call_id, CallStatus.QUEUED.value)
    await audit.record(
        action="call.audio_complete",
        actor_user_id=user.id,
        resource_type="call",
        resource_id=str(call_id),
        request_id=getattr(request.state, "request_id", None),
    )
    updated = await calls.get(call_id)
    assert updated is not None
    return _to_response(updated)


@router.post("/{call_id}/transcript", status_code=status.HTTP_201_CREATED)
async def ingest_transcript(
    call_id: UUID,
    body: TranscriptIngestRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:write"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
) -> dict[str, Any]:
    call = await calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    saved = await calls.save_transcript(
        TranscriptEntity(
            id=uuid4(),
            call_id=call_id,
            language=body.language,
            full_text=body.full_text,
            turns=body.turns,
            avg_confidence=body.avg_confidence,
            turn_count=len(body.turns),
        )
    )
    return {"id": str(saved.id), "turn_count": saved.turn_count}


@router.post("/{call_id}/evidence", status_code=status.HTTP_201_CREATED)
async def ingest_evidence(
    call_id: UUID,
    body: EvidenceIngestRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:write"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    evidence_repo: Annotated[SqlAlchemyEvidenceRepository, Depends(get_evidence_repo)],
) -> dict[str, Any]:
    call = await calls.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    items = [
        EvidenceEntity(
            id=uuid4(),
            call_id=call_id,
            quote=str(e.get("quote", "")),
            speaker=str(e.get("speaker", "agent")),
            stage_key=e.get("stage_key"),
            slot=e.get("slot"),
            confidence=float(e.get("confidence", 0.8)),
            audio_ts_start=e.get("audio_ts_start"),
            audio_ts_end=e.get("audio_ts_end"),
            turn_index=e.get("turn_index"),
            labels=list(e.get("labels") or []),
            metadata=dict(e.get("metadata") or {}),
        )
        for e in body.items
        if e.get("quote")
    ]
    created = await evidence_repo.bulk_create(items)
    return {"count": len(created)}


@router.get("/{call_id}/audio-url")
async def audio_url(
    call_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:read"))],
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    s3: Annotated[S3Client, Depends(get_s3)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> dict[str, str]:
    call = await calls.get(call_id)
    if not call or not call.audio_s3_key:
        raise HTTPException(status_code=404, detail="Audio not found")
    url = s3.presign_get(call.audio_s3_key)
    await audit.record(
        action="media.access",
        actor_user_id=user.id,
        resource_type="call",
        resource_id=str(call_id),
    )
    return {"url": url}
