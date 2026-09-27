from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.deps import ContainerDep

router = APIRouter(prefix="/api", tags=["health"])


class HealthOut(BaseModel):
    status: Literal["ok"]
    app: str
    version: str


@router.get("/health")
async def health(container: ContainerDep) -> HealthOut:
    async with container.db.engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return HealthOut(status="ok", app=container.settings.app_name, version=__version__)
