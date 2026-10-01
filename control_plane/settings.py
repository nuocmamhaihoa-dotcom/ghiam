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
        # 0 nghĩa là không chặn dung lượng. Đĩa đầy thì lần ghi báo hết chỗ.
        self.max_upload_mb = int(os.environ.get("CONTROL_MAX_UPLOAD_MB", "0"))
        self.workers = int(os.environ.get("CONTROL_UVICORN_WORKERS", "2"))
        self.limit_concurrency = int(os.environ.get("CONTROL_LIMIT_CONCURRENCY", "200"))
        self.backlog = int(os.environ.get("CONTROL_BACKLOG", "2048"))
        self.keep_alive = int(os.environ.get("CONTROL_KEEPALIVE", "75"))
        self.timeout_keep_alive = int(os.environ.get("CONTROL_TIMEOUT_KEEPALIVE", "75"))
        self.h11_max_incomplete_size = int(
            os.environ.get("CONTROL_H11_MAX_INCOMPLETE", str(16 * 1024 * 1024))
        )
        # Proxy health
        env_proxies = os.environ.get("CONTROL_PROXIES_FILE")
        if env_proxies:
            self.proxies_file = Path(env_proxies)
        else:
            cd_proxies = self.data_dir / "proxies_static.txt"
            repo_proxies = ROOT / "data" / "proxies_static.txt"
            deploy_proxies = ROOT / "deploy" / "vps" / "proxies_static.txt"
            if cd_proxies.exists():
                self.proxies_file = cd_proxies
            elif repo_proxies.exists():
                self.proxies_file = repo_proxies
            else:
                self.proxies_file = deploy_proxies
        self.proxy_check_interval_sec = int(os.environ.get("CONTROL_PROXY_CHECK_SEC", "300"))
        self.proxy_check_concurrency = int(os.environ.get("CONTROL_PROXY_CHECK_CONCURRENCY", "40"))
        self.proxy_check_timeout_sec = float(os.environ.get("CONTROL_PROXY_CHECK_TIMEOUT", "8"))
        self.proxy_check_url = os.environ.get("CONTROL_PROXY_CHECK_URL", "http://ident.me")
        # Optional shared auth for host:port lines (ProxyVN packages)
        self.proxy_user = os.environ.get("CONTROL_PROXY_USER", "")
        self.proxy_pass = os.environ.get("CONTROL_PROXY_PASS", "")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sync").mkdir(parents=True, exist_ok=True)


settings = ServerSettings()
settings.ensure_dirs()
