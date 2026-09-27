from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ServerSettings:
    def __init__(self) -> None:
        self.host = os.environ.get("CONTROL_HOST", "0.0.0.0")
        self.port = int(os.environ.get("CONTROL_PORT", "8088"))
        self.token = os.environ.get("CONTROL_TOKEN", "")
        self.data_dir = Path(os.environ.get("CONTROL_DATA_DIR", ROOT / "control_data"))
        self.packages_dir = Path(os.environ.get("CONTROL_PACKAGES_DIR", self.data_dir / "packages"))
        self.db_path = Path(os.environ.get("CONTROL_DB", self.data_dir / "server.db"))
        # High-bandwidth defaults (LAN / multi-gigabit friendly)
        self.max_upload_mb = int(os.environ.get("CONTROL_MAX_UPLOAD_MB", "512"))
        self.workers = int(os.environ.get("CONTROL_UVICORN_WORKERS", "2"))
        self.limit_concurrency = int(os.environ.get("CONTROL_LIMIT_CONCURRENCY", "200"))
        self.backlog = int(os.environ.get("CONTROL_BACKLOG", "2048"))
        self.keep_alive = int(os.environ.get("CONTROL_KEEPALIVE", "75"))
        self.timeout_keep_alive = int(os.environ.get("CONTROL_TIMEOUT_KEEPALIVE", "75"))
        self.h11_max_incomplete_size = int(
            os.environ.get("CONTROL_H11_MAX_INCOMPLETE", str(16 * 1024 * 1024))
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sync").mkdir(parents=True, exist_ok=True)


settings = ServerSettings()
settings.ensure_dirs()
