from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import agent, auth, health, jobs, proxies
from app.config import Settings
from app.container import Container, build_container
from app.handlers import register_exception_handlers
from app.migrations import upgrade_database

logger = logging.getLogger(__name__)


def _log_configuration_warnings(settings: Settings) -> None:
    if not settings.agent_token_list:
        logger.warning("Chưa cấu hình AGENT_TOKENS: agent trên PC sẽ chưa kết nối được")
    if not settings.encryption_key:
        logger.warning(
            "Chưa đặt ENCRYPTION_KEY: mật khẩu proxy được mã hoá bằng khoá suy ra từ SECRET_KEY, "
            "đừng đổi SECRET_KEY nếu không sẽ mất dữ liệu proxy"
        )


def _mount_web(app: FastAPI, dist_dir: str | None) -> None:
    if not dist_dir:
        return
    root = Path(dist_dir).resolve()
    index = root / "index.html"
    if not index.is_file():
        logger.warning("WEB_DIST_DIR=%s không có index.html, bỏ qua giao diện web", dist_dir)
        return
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Không tìm thấy API")
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or Settings()
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
        _log_configuration_warnings(settings)
        if settings.auto_migrate:
            await upgrade_database(container.db.engine)
        await container.runtime.start()
        try:
            yield
        finally:
            await container.runtime.stop()
            await container.db.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.container = container
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_exception_handlers(app)
    for module in (health, auth, proxies, jobs, agent):
        app.include_router(module.router)
    _mount_web(app, settings.web_dist_dir)
    return app
