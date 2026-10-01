from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CommentScope"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./commentscope.db"
    auto_migrate: bool = True
    log_level: str = "INFO"

    secret_key: str = Field(min_length=32)
    # Khoá Fernet riêng cho mật khẩu proxy/link đổi IP. Nếu bỏ trống sẽ suy ra từ SECRET_KEY,
    # khi đó đổi SECRET_KEY sẽ làm mất khả năng giải mã dữ liệu proxy đã lưu.
    encryption_key: str | None = None
    admin_username: str = "admin"
    admin_password: str = Field(min_length=8)
    access_token_ttl_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)
    login_max_attempts: int = Field(default=10, ge=1)
    login_window_sec: int = Field(default=300, ge=10)
    agent_tokens: str = ""
    cors_origins: str = ""
    web_dist_dir: str | None = None

    proxy_check_urls: str = "https://ipinfo.io/json,https://api.ipify.org?format=json"
    proxy_check_timeout_sec: float = Field(default=15.0, gt=0, le=120)
    proxy_check_concurrency: int = Field(default=50, ge=1, le=500)
    proxy_rotation_concurrency: int = Field(default=20, ge=1, le=200)
    proxy_rotation_call_timeout_sec: float = Field(default=30.0, gt=0, le=300)
    proxy_rotation_settle_sec: float = Field(default=8.0, ge=0, le=300)
    proxy_rotation_verify_attempts: int = Field(default=4, ge=1, le=20)
    proxy_rotation_verify_interval_sec: float = Field(default=5.0, ge=0, le=120)
    proxy_failure_threshold: int = Field(default=3, ge=1, le=100)
    proxy_quarantine_base_sec: int = Field(default=300, ge=0)
    proxy_quarantine_max_sec: int = Field(default=3600, ge=0)
    proxy_lease_default_ttl_sec: int = Field(default=600, ge=30)
    proxy_lease_max_ttl_sec: int = Field(default=3600, ge=30)
    proxy_lease_retention_days: int = Field(default=7, ge=1)
    proxy_import_max_lines: int = Field(default=20000, ge=1)
    proxy_health_check_interval_sec: int = Field(default=900, ge=0)
    proxy_health_check_batch: int = Field(default=200, ge=1)

    scheduler_enabled: bool = True
    scheduler_tick_sec: float = Field(default=5.0, gt=0)
    attempt_ttl_sec: int = Field(default=300, ge=30, le=3600)
    comment_retention_days: int = Field(default=0, ge=0, le=3650)

    @property
    def agent_token_list(self) -> list[str]:
        return _split_csv(self.agent_tokens)

    @property
    def check_url_list(self) -> list[str]:
        return _split_csv(self.proxy_check_urls)

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)


class DatabaseSettings(BaseSettings):
    """Chỉ đọc DATABASE_URL, dùng cho lệnh `alembic` chạy độc lập."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./commentscope.db"
