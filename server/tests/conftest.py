from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.container import Container, build_container
from app.main import create_app
from app.models import Proxy, ProxyHealth
from app.proxies.runtime import ProxyRuntime
from app.security import issue_admin_token
from app.timeutil import utcnow
from tests.netsim import CHECK_URL, NetSim

ADMIN_PASSWORD = "admin-pass-123"
AGENT_TOKEN = "agent-token-for-tests"
POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

BOTH_BACKENDS = pytest.mark.parametrize(
    "database_url",
    [
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.skipif(not POSTGRES_URL, reason="Chưa đặt TEST_POSTGRES_URL")),
    ],
    indirect=True,
)


def make_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": database_url,
        "secret_key": "s" * 48,
        "admin_password": ADMIN_PASSWORD,
        "agent_tokens": AGENT_TOKEN,
        "cors_origins": "",
        "web_dist_dir": None,
        "scheduler_enabled": False,
        "proxy_check_urls": CHECK_URL,
        "proxy_check_timeout_sec": 5,
        "proxy_check_concurrency": 4,
        "proxy_rotation_concurrency": 2,
        "proxy_rotation_call_timeout_sec": 5,
        "proxy_rotation_settle_sec": 0,
        "proxy_rotation_verify_attempts": 2,
        "proxy_rotation_verify_interval_sec": 0,
        "proxy_failure_threshold": 3,
        "proxy_quarantine_base_sec": 60,
        "proxy_health_check_interval_sec": 0,
    }
    values.update(overrides)
    return Settings(**values)


async def reset_postgres(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DROP TABLE IF EXISTS comments, scrape_attempts, scrape_posts, scrape_jobs, "
                    "proxy_leases, proxies, alembic_version CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def database_url(request: pytest.FixtureRequest, tmp_path: Any) -> AsyncIterator[str]:
    backend = getattr(request, "param", "sqlite")
    if backend == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        return
    assert POSTGRES_URL is not None
    await reset_postgres(POSTGRES_URL)
    yield POSTGRES_URL


@pytest.fixture
async def netsim() -> AsyncIterator[NetSim]:
    sim = NetSim()
    await sim.start()
    try:
        yield sim
    finally:
        await sim.stop()


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
def settings(database_url: str, settings_overrides: dict[str, Any]) -> Settings:
    return make_settings(database_url, **settings_overrides)


@dataclass
class AppHarness:
    app: FastAPI
    container: Container
    anon: httpx.AsyncClient
    admin: httpx.AsyncClient
    agent: httpx.AsyncClient

    @property
    def runtime(self) -> ProxyRuntime:
        return self.container.runtime

    async def import_text(self, text: str, **options: Any) -> dict[str, Any]:
        payload = {"text": text, "check_after_import": False, **options}
        response = await self.admin.post("/api/proxies/import", json=payload)
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    async def proxies(self, **params: Any) -> list[dict[str, Any]]:
        response = await self.admin.get("/api/proxies", params={"page_size": 500, "sort": "id", **params})
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        return items

    async def proxy(self, proxy_id: int) -> dict[str, Any]:
        response = await self.admin.get(f"/api/proxies/{proxy_id}")
        assert response.status_code == 200, response.text
        detail: dict[str, Any] = response.json()
        return detail

    async def mark_alive(self, ids: list[int] | None = None) -> None:
        statement = update(Proxy).values(health=ProxyHealth.ALIVE, exit_ip="192.0.2.1", last_checked_at=utcnow())
        if ids is not None:
            statement = statement.where(Proxy.id.in_(ids))
        async with self.container.db.sessionmaker() as session:
            await session.execute(statement)
            await session.commit()

    async def lease(self, **payload: Any) -> httpx.Response:
        return await self.agent.post("/api/agent/proxies/lease", json={"worker_id": "pc-test", **payload})

    async def release(self, lease_id: str, outcome: str = "ok", **payload: Any) -> httpx.Response:
        return await self.agent.post(f"/api/agent/leases/{lease_id}/release", json={"outcome": outcome, **payload})

    async def add(self, line: str, **options: Any) -> int:
        result = await self.import_text(line, **options)
        assert result["created"] == 1, result
        return int((await self.proxies())[-1]["id"])

    async def check(self, proxy_id: int) -> dict[str, Any]:
        response = await self.admin.post(f"/api/proxies/{proxy_id}/check")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert body["ok"], body
        return body

    async def rotate(self, proxy_id: int, **params: Any) -> dict[str, Any]:
        response = await self.admin.post(f"/api/proxies/{proxy_id}/rotate", params=params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def stats(self) -> dict[str, Any]:
        response = await self.admin.get("/api/proxies/stats")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def execute(self, statement: Any) -> None:
        async with self.container.db.sessionmaker() as session:
            await session.execute(statement)
            await session.commit()


@pytest.fixture
async def harness(settings: Settings) -> AsyncIterator[AppHarness]:
    container = build_container(settings)
    app = create_app(settings, container)
    token, _ = issue_admin_token(settings, settings.admin_username)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as anon,
            httpx.AsyncClient(
                transport=transport, base_url="http://testserver", headers={"Authorization": f"Bearer {token}"}
            ) as admin,
            httpx.AsyncClient(
                transport=transport, base_url="http://testserver", headers={"Authorization": f"Bearer {AGENT_TOKEN}"}
            ) as agent,
        ):
            yield AppHarness(app=app, container=container, anon=anon, admin=admin, agent=agent)
            await container.runtime.wait_idle()
