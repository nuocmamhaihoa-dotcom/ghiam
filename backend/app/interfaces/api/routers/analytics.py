"""Analytics: Conversation DNA."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.services.conversation_dna import ConversationDnaService
from app.core.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/conversation-dna")
async def conversation_dna(
    user: Annotated[CurrentUser, Depends(require_permissions("analytics:read"))],
    session: DbSession,
    call_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    return await ConversationDnaService(session).get_or_build(call_id)
