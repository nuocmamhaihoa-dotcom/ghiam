from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from tests.conftest import make_settings

INDEX_HTML = "<!doctype html><title>CommentScope</title>"


@pytest.fixture
async def web_client(tmp_path: Path, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (dist / "assets" / "app-1a2b.js").write_text("console.log('ok')")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("KHONG-DUOC-LO")
    app = create_app(make_settings(database_url, web_dist_dir=str(dist)))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_dashboard_routes_fall_back_to_index(web_client: httpx.AsyncClient) -> None:
    for path in ("/", "/proxies", "/proxies/12?tab=lich-su"):
        response = await web_client.get(path)
        assert response.status_code == 200
        assert response.text == INDEX_HTML
        assert response.headers["cache-control"] == "no-cache"


async def test_static_files_are_served(web_client: httpx.AsyncClient) -> None:
    asset = await web_client.get("/assets/app-1a2b.js")
    assert (asset.status_code, asset.text) == (200, "console.log('ok')")
    favicon = await web_client.get("/favicon.svg")
    assert (favicon.status_code, favicon.text) == (200, "<svg/>")
    assert (await web_client.get("/assets/khong-co.js")).status_code == 404


async def test_unknown_api_paths_are_not_swallowed_by_the_spa(web_client: httpx.AsyncClient) -> None:
    missing = await web_client.get("/api/khong-co")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Không tìm thấy API"}
    assert (await web_client.get("/api/health")).json()["status"] == "ok"


async def test_files_outside_the_web_directory_are_not_served(web_client: httpx.AsyncClient) -> None:
    for path in ("/..%2Fsecret.txt", "/%2E%2E/secret.txt", "/assets/..%2F..%2Fsecret.txt"):
        response = await web_client.get(path)
        assert "KHONG-DUOC-LO" not in response.text


async def test_missing_build_directory_only_disables_the_dashboard(tmp_path: Path, database_url: str) -> None:
    app = create_app(make_settings(database_url, web_dist_dir=str(tmp_path / "khong-co")))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        assert (await client.get("/")).status_code == 404
        assert (await client.get("/api/health")).status_code == 200
