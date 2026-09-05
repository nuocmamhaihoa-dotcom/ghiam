"""API routers package."""

from app.interfaces.api.routers import (
    auth,
    calls,
    coaching,
    dashboard,
    datasets,
    health,
    rules,
    scoring,
)

__all__ = [
    "auth",
    "calls",
    "coaching",
    "dashboard",
    "datasets",
    "health",
    "rules",
    "scoring",
]
