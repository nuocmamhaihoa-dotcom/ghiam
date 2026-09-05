"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.interfaces.api.middleware import AuditMiddleware
from app.interfaces.api.routers import (
    admin,
    analytics,
    appeals,
    auth,
    auto_sop,
    calls,
    coaching,
    dashboard,
    datasets,
    forecast,
    fraud,
    health,
    live_assistant,
    memory_graph,
    multi_product,
    personality,
    pragmatics,
    qa,
    revenue_leak,
    rules,
    scoring,
    sales_os,
    self_learning,
    digital_twin,
    negotiation,
    cltv,
    war_room,
    autonomous,
    simulator,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(level=settings.log_level, json_logs=settings.log_json)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="AI SALES OPERATING SYSTEM ENTERPRISE API",
        description="Evidence-first AI Sales OS: QA, pragmatics, live assist, personality, memory graph, revenue leak, fraud, SOP.",
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness probes"},
            {"name": "auth", "description": "JWT authentication and session"},
            {"name": "calls", "description": "Call ingest and media"},
            {"name": "scoring", "description": "Scoring orchestration and analysis"},
            {"name": "rules", "description": "Rulebook CRUD (DB-backed)"},
            {"name": "coaching", "description": "Coaching plans"},
            {"name": "dashboard", "description": "Ops dashboard aggregates"},
            {"name": "datasets", "description": "Golden calls and calibration datasets"},
            {"name": "pragmatics", "description": "Vietnamese pragmatics / soft-language AI"},
        ],
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(AuditMiddleware)

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "type": "https://aqate.example/errors/bad-request",
                "title": "Bad Request",
                "status": 400,
                "detail": str(exc),
                "instance": str(request.url.path),
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    # Health at root (no /v1) per ops convention
    application.include_router(health.router)
    application.include_router(auth.router, prefix=settings.api_prefix)
    application.include_router(calls.router, prefix=settings.api_prefix)
    application.include_router(scoring.router, prefix=settings.api_prefix)
    application.include_router(rules.router, prefix=settings.api_prefix)
    application.include_router(coaching.router, prefix=settings.api_prefix)
    application.include_router(dashboard.router, prefix=settings.api_prefix)
    application.include_router(datasets.router, prefix=settings.api_prefix)
    application.include_router(revenue_leak.router, prefix=settings.api_prefix)
    application.include_router(appeals.router, prefix=settings.api_prefix)
    application.include_router(qa.router, prefix=settings.api_prefix)
    application.include_router(analytics.router, prefix=settings.api_prefix)
    application.include_router(admin.router, prefix=settings.api_prefix)
    application.include_router(pragmatics.router, prefix=settings.api_prefix)
    application.include_router(live_assistant.router, prefix=settings.api_prefix)
    application.include_router(personality.router, prefix=settings.api_prefix)
    application.include_router(memory_graph.router, prefix=settings.api_prefix)
    application.include_router(simulator.router, prefix=settings.api_prefix)
    application.include_router(fraud.router, prefix=settings.api_prefix)
    application.include_router(auto_sop.router, prefix=settings.api_prefix)
    application.include_router(forecast.router, prefix=settings.api_prefix)
    application.include_router(multi_product.router, prefix=settings.api_prefix)
    application.include_router(sales_os.router, prefix=settings.api_prefix)
    application.include_router(self_learning.router, prefix=settings.api_prefix)
    application.include_router(digital_twin.router, prefix=settings.api_prefix)
    application.include_router(negotiation.router, prefix=settings.api_prefix)
    application.include_router(cltv.router, prefix=settings.api_prefix)
    application.include_router(war_room.router, prefix=settings.api_prefix)
    application.include_router(autonomous.router, prefix=settings.api_prefix)

    return application


app = create_app()
