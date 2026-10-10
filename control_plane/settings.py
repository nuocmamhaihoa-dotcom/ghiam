from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def derive_secret(secret: str, label: str) -> str:
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), label.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


class ServerSettings:
    def __init__(self) -> None:
        self.host = os.environ.get("CONTROL_HOST", "0.0.0.0")
        self.port = int(os.environ.get("CONTROL_PORT", "8088"))
        self.token = os.environ.get("CONTROL_TOKEN", "")
        # Trang video tự điền mã này vào trình duyệt, nên nó chỉ mở được API video, không mở API quản trị cũ.
        self.page_token = os.environ.get("CONTROL_PAGE_TOKEN", "") or derive_secret(self.token, "video-page")
        self.data_dir = Path(os.environ.get("CONTROL_DATA_DIR", ROOT / "control_data"))
        self.packages_dir = Path(os.environ.get("CONTROL_PACKAGES_DIR", self.data_dir / "packages"))
        self.db_path = Path(os.environ.get("CONTROL_DB", self.data_dir / "server.db"))
        # High-bandwidth defaults (LAN / multi-gigabit friendly)
        # Video iPhone thường 800MB–1.5GB; cho phép tới 2GB/file.
        self.max_upload_mb = int(os.environ.get("CONTROL_MAX_UPLOAD_MB", "2048"))
        self.video_db_path = Path(os.environ.get("CONTROL_VIDEO_DB", self.data_dir / "video.db"))
        self.video_dir = Path(os.environ.get("CONTROL_VIDEO_DIR", self.data_dir / "videos"))
        # Trần hàng đợi trên VPS ~148GB: mặc định 120GB, chừa ~20GB trống.
        self.video_disk_gb = int(os.environ.get("CONTROL_VIDEO_DISK_GB", "120"))
        self.workers = int(os.environ.get("CONTROL_UVICORN_WORKERS", "2"))
        self.limit_concurrency = int(os.environ.get("CONTROL_LIMIT_CONCURRENCY", "400"))
        self.backlog = int(os.environ.get("CONTROL_BACKLOG", "4096"))
        self.keep_alive = int(os.environ.get("CONTROL_KEEPALIVE", "120"))
        self.timeout_keep_alive = int(os.environ.get("CONTROL_TIMEOUT_KEEPALIVE", "120"))
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
        self.proxy_check_url = os.environ.get("CONTROL_PROXY_CHECK_URL", "http://api.ipify.org")
        # Optional shared auth for host:port lines (ProxyVN packages)
        self.proxy_user = os.environ.get("CONTROL_PROXY_USER", "")
        self.proxy_pass = os.environ.get("CONTROL_PROXY_PASS", "")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sync").mkdir(parents=True, exist_ok=True)


settings = ServerSettings()
settings.ensure_dirs()
