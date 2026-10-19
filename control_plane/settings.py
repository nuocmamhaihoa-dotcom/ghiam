from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_int(name: str, default: int) -> int:
    """Số trong biến môi trường. Giá trị không phải số thì dùng mặc định, không làm sập tiến trình."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def derive_secret(secret: str, label: str) -> str:
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), label.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


class ServerSettings:
    def __init__(self) -> None:
        self.host = os.environ.get("CONTROL_HOST", "0.0.0.0")
        self.port = env_int("CONTROL_PORT", 8088)
        self.token = os.environ.get("CONTROL_TOKEN", "")
        # Trang video tự điền mã này vào trình duyệt, nên nó chỉ mở được API video, không mở API quản trị cũ.
        self.page_token = os.environ.get("CONTROL_PAGE_TOKEN", "") or derive_secret(self.token, "video-page")
        self.data_dir = Path(os.environ.get("CONTROL_DATA_DIR", ROOT / "control_data"))
        self.packages_dir = Path(os.environ.get("CONTROL_PACKAGES_DIR", self.data_dir / "packages"))
        self.db_path = Path(os.environ.get("CONTROL_DB", self.data_dir / "server.db"))
        # High-bandwidth defaults (LAN / multi-gigabit friendly)
        self.max_upload_mb = env_int("CONTROL_MAX_UPLOAD_MB", 4096)
        self.video_db_path = Path(os.environ.get("CONTROL_VIDEO_DB", self.data_dir / "video.db"))
        self.video_dir = Path(os.environ.get("CONTROL_VIDEO_DIR", self.data_dir / "videos"))
        self.video_disk_gb = env_int("CONTROL_VIDEO_DISK_GB", 80)
        self.workers = env_int("CONTROL_UVICORN_WORKERS", 2)
        self.limit_concurrency = env_int("CONTROL_LIMIT_CONCURRENCY", 200)
        self.backlog = env_int("CONTROL_BACKLOG", 2048)
        self.keep_alive = env_int("CONTROL_KEEPALIVE", 75)
        self.timeout_keep_alive = env_int("CONTROL_TIMEOUT_KEEPALIVE", 75)
        self.h11_max_incomplete_size = env_int("CONTROL_H11_MAX_INCOMPLETE", 16 * 1024 * 1024)
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
        self.proxy_check_interval_sec = env_int("CONTROL_PROXY_CHECK_SEC", 300)
        self.proxy_check_concurrency = env_int("CONTROL_PROXY_CHECK_CONCURRENCY", 40)
        self.proxy_check_timeout_sec = env_float("CONTROL_PROXY_CHECK_TIMEOUT", 8.0)
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
