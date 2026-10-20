"""Dispatch one engine at a time. Engines do not call each other."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fb_poller.engines.guard import public_error
from fb_poller.engines.repair import heal_result
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


_ALIAS_TO_NAME = {
    "2": "guest-graphql",
    "guest-graphql": "guest-graphql",
    "guest_graphql": "guest-graphql",
    "3": "managed-api",
    "managed-api": "managed-api",
    "managed_api": "managed-api",
    "4": "apify",
    "apify": "apify",
    "5": "pa1-newest",
    "pa1-newest": "pa1-newest",
    "pa1": "pa1-newest",
    "pa1_newest": "pa1-newest",
    "6": "graph-ppca",
    "graph-ppca": "graph-ppca",
    "graph_ppca": "graph-ppca",
    "7": "guest-ssr",
    "guest-ssr": "guest-ssr",
    "guest_ssr": "guest-ssr",
    "8": "octoparse",
    "octoparse": "octoparse",
}


def identity(name: str) -> tuple[str, int]:
    """Canonical engine name and rank. Unknown names raise KeyError."""
    canonical = _ALIAS_TO_NAME.get(name.strip().lower())
    if canonical is None:
        known = ", ".join(str(item["name"]) for item in CATALOG)
        raise KeyError(f"Unknown engine '{name}'. Choose: {known}")
    for item in CATALOG:
        if item["name"] == canonical:
            return canonical, int(item["rank"])
    known = ", ".join(str(item["name"]) for item in CATALOG)
    raise KeyError(f"Unknown engine '{name}'. Choose: {known}")


def resolve(name: str) -> EngineFn:
    canonical, _rank = identity(name)
    if canonical == "guest-graphql":
        from fb_poller.engines.guest_graphql import scan
    elif canonical == "managed-api":
        from fb_poller.engines.managed_api import scan
    elif canonical == "apify":
        from fb_poller.engines.apify_actor import scan
    elif canonical == "pa1-newest":
        from fb_poller.engines.pa1_newest import scan
    elif canonical == "graph-ppca":
        from fb_poller.engines.graph_ppca import scan
    elif canonical == "guest-ssr":
        from fb_poller.engines.guest_ssr import scan
    else:
        from fb_poller.engines.octoparse_import import scan
    return scan


async def scan_one(name: str, url: str, **kwargs: Any) -> EngineResult:
    """Run one engine, then repair known defects before the result is returned."""
    canonical, rank = identity(name)
    try:
        result = await resolve(name)(url, **kwargs)
    except KeyError:
        raise
    except Exception as exc:
        result = EngineResult(canonical, rank, False, error=public_error(exc))
    return heal_result(result)


async def scan_each_independent(url: str, **kwargs: Any) -> list[EngineResult]:
    """Run every engine in sequence. One failure is recorded and the rest still run."""
    results: list[EngineResult] = []
    for item in CATALOG:
        name = str(item["name"])
        rank = int(item["rank"])
        try:
            results.append(await scan_one(name, url, **kwargs))
        except Exception as exc:
            results.append(heal_result(EngineResult(name, rank, False, error=public_error(exc))))
    return results
