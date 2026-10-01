from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from app import __version__
from app.deps import ContainerDep
from app.jobs.schemas import ClaimOut, CompleteIn, CompleteOut, ProgressIn, ProgressOut
from app.jobs.work import claim_work, complete_attempt, report_progress
from app.proxies import leasing
from app.proxies.schemas import LeaseIn, LeaseOut, ReleaseIn, ReleaseOut, RenewIn, RenewOut
from app.security import AgentIdentity
from app.timeutil import utcnow

router = APIRouter(prefix="/api/agent", tags=["agent"])

LeaseId = Annotated[str, Path(min_length=1, max_length=64)]


class PingOut(BaseModel):
    ok: bool
    agent: str
    server_time: datetime
    version: str
    check_urls: list[str]


@router.get("/ping")
async def ping(agent: AgentIdentity, container: ContainerDep) -> PingOut:
    return PingOut(
        ok=True,
        agent=agent,
        server_time=utcnow(),
        version=__version__,
        check_urls=container.settings.check_url_list,
    )


@router.post("/proxies/lease")
async def lease_proxy(data: LeaseIn, _agent: AgentIdentity, container: ContainerDep) -> LeaseOut:
    return await leasing.acquire_lease(container.db, container.box, container.settings, data)


@router.post("/leases/{lease_id}/renew")
async def renew_lease(lease_id: LeaseId, data: RenewIn, _agent: AgentIdentity, container: ContainerDep) -> RenewOut:
    return await leasing.renew_lease(container.db, container.settings, lease_id, data.ttl_sec)


class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    ttl_sec: int | None = Field(default=None, ge=30, le=86_400)


@router.post("/work/claim")
async def claim(data: ClaimIn, _agent: AgentIdentity, container: ContainerDep) -> ClaimOut:
    return await claim_work(
        container.db, container.box, container.settings, worker_id=data.worker_id, ttl_sec=data.ttl_sec
    )


@router.post("/attempts/{attempt_id}/progress")
async def progress(
    attempt_id: LeaseId, data: ProgressIn, _agent: AgentIdentity, container: ContainerDep
) -> ProgressOut:
    return await report_progress(container.db, container.settings, attempt_id, data)


@router.post("/attempts/{attempt_id}/complete")
async def complete(
    attempt_id: LeaseId, data: CompleteIn, _agent: AgentIdentity, container: ContainerDep
) -> CompleteOut:
    result = await complete_attempt(container.db, container.settings, attempt_id, data)
    if result.released:
        await container.runtime.dispatch_pending_now()
    return result


@router.post("/leases/{lease_id}/release")
async def release_lease(
    lease_id: LeaseId, data: ReleaseIn, _agent: AgentIdentity, container: ContainerDep
) -> ReleaseOut:
    result = await leasing.release_lease(container.db, container.settings, lease_id, data)
    if result.released:
        await container.runtime.dispatch_pending_now()
    return result
