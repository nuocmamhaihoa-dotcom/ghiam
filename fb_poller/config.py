from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{ROOT / 'data' / 'fb_poller.db'}",
        alias="DATABASE_URL",
    )
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    # Tier sizes / intervals
    hot_size: int = Field(default=100, alias="HOT_SIZE")
    hot_interval_sec: int = Field(default=45, alias="HOT_INTERVAL_SEC")
    warm_interval_sec: int = Field(default=150, alias="WARM_INTERVAL_SEC")
    cold_interval_sec: int = Field(default=600, alias="COLD_INTERVAL_SEC")
    boost_on_new_sec: int = Field(default=1200, alias="BOOST_ON_NEW_SEC")

    # Worker / browser
    workers: int = Field(default=2, alias="WORKERS")
    warm_cold_max_inflight: int = Field(default=3, alias="WARM_COLD_MAX_INFLIGHT")
    hot_job_timeout_ms: int = Field(default=7000, alias="HOT_JOB_TIMEOUT_MS")
    hot_max_comments_page: int = Field(default=20, alias="HOT_MAX_COMMENTS_PAGE")
    browser_restart_every_jobs: int = Field(default=80, alias="BROWSER_RESTART_EVERY_JOBS")
    headless: bool = Field(default=True, alias="HEADLESS")
    navigation_timeout_ms: int = Field(default=15000, alias="NAVIGATION_TIMEOUT_MS")

    # Proxy files
    proxies_static_file: Path = Field(
        default=ROOT / "data" / "proxies_static.txt",
        alias="PROXIES_STATIC_FILE",
    )
    proxies_4g_file: Path = Field(
        default=ROOT / "data" / "proxies_4g.txt",
        alias="PROXIES_4G_FILE",
    )

    worker_id: str = Field(default="local-1", alias="WORKER_ID")
    role: Literal["control", "worker", "all"] = Field(default="all", alias="ROLE")

    fail_streak_cooldown: int = Field(default=3, alias="FAIL_STREAK_COOLDOWN")
    recent_ids_keep: int = Field(default=100, alias="RECENT_IDS_KEEP")

    data_dir: Path = Field(default=ROOT / "data", alias="DATA_DIR")


def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
