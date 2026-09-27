"""python -m control_plane  — start high-bandwidth LAN server on this PC."""

from __future__ import annotations

import os
import sys

import uvicorn

from control_plane.settings import settings


def main() -> None:
    settings.ensure_dirs()
    # Tuned for LAN / multi-client fan-in without early congestion collapse.
    kwargs: dict = {
        "app": "control_plane.app:app",
        "host": settings.host,
        "port": settings.port,
        "workers": max(1, settings.workers),
        "limit_concurrency": settings.limit_concurrency,
        "backlog": settings.backlog,
        "timeout_keep_alive": settings.timeout_keep_alive,
        "proxy_headers": True,
        "access_log": True,
    }
    # Prefer uvloop/httptools on Linux when available (uvicorn[standard]).
    if os.name != "nt":
        try:
            import uvloop  # noqa: F401

            kwargs["loop"] = "uvloop"
        except ImportError:
            kwargs["loop"] = "asyncio"
        try:
            import httptools  # noqa: F401

            kwargs["http"] = "httptools"
        except ImportError:
            kwargs["http"] = "auto"
    else:
        kwargs["loop"] = "asyncio"
        kwargs["http"] = "auto"

    print(
        f"fb-poller LAN server listening on http://{settings.host}:{settings.port}/ "
        f"(workers={kwargs['workers']}, max_upload_mb={settings.max_upload_mb})",
        file=sys.stderr,
    )
    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()
