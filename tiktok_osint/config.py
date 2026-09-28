from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class TikTokSettings(BaseSettings):
    """Settings for the public scanner. Prefixed so they do not collide with fb-poller."""

    model_config = SettingsConfigDict(
        env_prefix="TIKTOK_",
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default=f"sqlite:///{ROOT / 'data' / 'tiktok_osint.db'}")
    redis_url: str | None = None
    request_timeout_sec: float = 20.0
    max_attempts: int = 3
    retry_base_delay_sec: float = 0.8
    min_delay_sec: float = 1.5
    navigation_timeout_ms: int = 20_000
    headless: bool = True
    max_bio_link_bytes: int = 500_000
    max_avatar_bytes: int = 2_000_000
    max_import_lines: int = 5_000
    data_dir: Path = Field(default=ROOT / "data")
    export_dir: Path = Field(default=ROOT / "data" / "exports")
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> TikTokSettings:
    settings = TikTokSettings()
    settings.ensure_dirs()
    return settings
