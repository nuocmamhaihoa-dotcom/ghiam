"""Application settings loaded from environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "aqate-api"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    api_prefix: str = "/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    database_url: str = (
        "postgresql+asyncpg://aqate:aqate@localhost:5432/aqate"
    )
    database_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me-in-production-use-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "aqate"
    s3_region: str = "us-east-1"
    s3_presign_expire_seconds: int = 900

    llm_judge_enabled: bool = False
    openai_api_key: str | None = None
    stt_min_avg_confidence: float = 0.65

    seed_admin_email: str = "admin@aqate.local"
    seed_admin_password: str = "ChangeMeAdmin123!"
    seed_tenant_code: str = "demo"

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, value: object) -> object:
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("["):
                import json

                return json.loads(raw)
            return [part.strip() for part in raw.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
