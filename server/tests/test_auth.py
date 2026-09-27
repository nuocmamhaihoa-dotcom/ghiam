from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.timeutil import utcnow
from tests.conftest import ADMIN_PASSWORD, AGENT_TOKEN, AppHarness


async def test_login_issues_token_usable_for_admin_api(harness: AppHarness) -> None:
    response = await harness.anon.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = await harness.anon.get("/api/auth/me", headers=headers)
    assert me.json() == {"username": "admin"}
    assert (await harness.anon.get("/api/proxies/stats", headers=headers)).status_code == 200


async def test_wrong_password_is_rejected_then_rate_limited(harness: AppHarness) -> None:
    for _ in range(harness.container.settings.login_max_attempts):
        response = await harness.anon.post("/api/auth/login", json={"username": "admin", "password": "sai-mat-khau"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu"
    blocked = await harness.anon.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "thử lại sau" in blocked.json()["detail"]


async def test_admin_api_requires_valid_token(harness: AppHarness) -> None:
    missing = await harness.anon.get("/api/proxies")
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Chưa đăng nhập"
    garbage = await harness.anon.get("/api/proxies", headers={"Authorization": "Bearer abc.def.ghi"})
    assert garbage.json()["detail"] == "Token không hợp lệ"

    settings = harness.container.settings
    expired = jwt.encode(
        {"sub": "admin", "typ": "admin", "exp": int((utcnow() - timedelta(minutes=1)).timestamp())},
        settings.secret_key,
        algorithm="HS256",
    )
    response = await harness.anon.get("/api/proxies", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Phiên đăng nhập đã hết hạn"


async def test_agent_token_and_admin_token_are_not_interchangeable(harness: AppHarness) -> None:
    assert (await harness.agent.get("/api/proxies")).status_code == 401
    assert (await harness.admin.get("/api/agent/ping")).status_code == 401
    ping = await harness.agent.get("/api/agent/ping")
    assert ping.status_code == 200
    assert ping.json()["agent"] == "agent-token-1"


async def test_wrong_agent_token_is_rejected(harness: AppHarness) -> None:
    response = await harness.anon.get("/api/agent/ping", headers={"Authorization": f"Bearer {AGENT_TOKEN}x"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Agent token không hợp lệ"


@pytest.mark.parametrize("settings_overrides", [{"agent_tokens": ""}])
async def test_agent_api_disabled_without_configured_tokens(harness: AppHarness) -> None:
    response = await harness.agent.get("/api/agent/ping")
    assert response.status_code == 503
    assert "AGENT_TOKENS" in response.json()["detail"]


async def test_health_endpoint_is_public(harness: AppHarness) -> None:
    response = await harness.anon.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
