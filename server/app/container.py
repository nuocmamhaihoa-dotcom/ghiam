from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.crypto import SecretBox
from app.db import Database
from app.proxies.runtime import ProxyRuntime
from app.security import LoginRateLimiter


@dataclass(slots=True)
class Container:
    settings: Settings
    db: Database
    box: SecretBox
    runtime: ProxyRuntime
    login_limiter: LoginRateLimiter


def build_container(settings: Settings) -> Container:
    db = Database(settings.database_url)
    box = SecretBox(encryption_key=settings.encryption_key, secret_key=settings.secret_key)
    return Container(
        settings=settings,
        db=db,
        box=box,
        runtime=ProxyRuntime(settings=settings, db=db, box=box),
        login_limiter=LoginRateLimiter(max_attempts=settings.login_max_attempts, window_sec=settings.login_window_sec),
    )
