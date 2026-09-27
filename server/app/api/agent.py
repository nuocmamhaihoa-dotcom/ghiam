from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel

from app import __version__
from app.deps import ContainerDep
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


@router.post("/leases/{lease_id}/release")
async def release_lease(
    lease_id: LeaseId, data: ReleaseIn, _agent: AgentIdentity, container: ContainerDep
) -> ReleaseOut:
    result = await leasing.release_lease(container.db, container.settings, lease_id, data)
    if result.released:
        await container.runtime.dispatch_pending_now()
    return result
