"""Dispatch one engine at a time. Engines do not call each other."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fb_poller.engines.guard import public_error
from fb_poller.engines.types import EngineResult

EngineFn = Callable[..., Awaitable[EngineResult]]

CATALOG: list[dict[str, Any]] = [
    {"rank": 2, "name": "guest-graphql", "summary": "Logged-out browser, public GraphQL responses"},
    {"rank": 3, "name": "managed-api", "summary": "One managed comments API (key in env)"},
    {"rank": 4, "name": "apify", "summary": "One Apify actor run"},
    {"rank": 5, "name": "pa1-newest", "summary": "Guest browser, Newest, first page"},
    {"rank": 6, "name": "graph-ppca", "summary": "Official Graph comments edge"},
    {"rank": 7, "name": "guest-ssr", "summary": "One guest HTML fetch"},
    {"rank": 8, "name": "octoparse", "summary": "Import an Octoparse CSV/JSON export"},
]


def resolve(name: str) -> EngineFn:
    key = name.strip().lower()
    if key in {"2", "guest-graphql", "guest_graphql"}:
        from fb_poller.engines.guest_graphql import scan
    elif key in {"3", "managed-api", "managed_api"}:
        from fb_poller.engines.managed_api import scan
    elif key in {"4", "apify"}:
        from fb_poller.engines.apify_actor import scan
    elif key in {"5", "pa1-newest", "pa1", "pa1_newest"}:
        from fb_poller.engines.pa1_newest import scan
    elif key in {"6", "graph-ppca", "graph_ppca"}:
        from fb_poller.engines.graph_ppca import scan
    elif key in {"7", "guest-ssr", "guest_ssr"}:
        from fb_poller.engines.guest_ssr import scan
    elif key in {"8", "octoparse"}:
        from fb_poller.engines.octoparse_import import scan
    else:
        known = ", ".join(str(item["name"]) for item in CATALOG)
        raise KeyError(f"Unknown engine '{name}'. Choose: {known}")
    return scan


async def scan_one(name: str, url: str, **kwargs: Any) -> EngineResult:
    return await resolve(name)(url, **kwargs)


async def scan_each_independent(url: str, **kwargs: Any) -> list[EngineResult]:
    """Run every engine in sequence. One failure is recorded and the rest still run."""
    results: list[EngineResult] = []
    for item in CATALOG:
        name = str(item["name"])
        rank = int(item["rank"])
        try:
            results.append(await scan_one(name, url, **kwargs))
        except Exception as exc:
            results.append(EngineResult(name, rank, False, error=public_error(exc)))
    return results
