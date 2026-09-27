from __future__ import annotations

import pytest

from tiktok_osint.config import TikTokSettings
from tiktok_osint.storage.db import build_repository
from tiktok_osint.storage.repo import Repository


@pytest.fixture
def settings(tmp_path) -> TikTokSettings:
    return TikTokSettings(
        database_url=f"sqlite:///{tmp_path / 'osint.db'}",
        redis_url=None,
        request_timeout_sec=1,
        max_attempts=3,
        retry_base_delay_sec=0,
        min_delay_sec=0,
        navigation_timeout_ms=1000,
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        log_level="WARNING",
    )


@pytest.fixture
def repo(settings: TikTokSettings) -> Repository:
    settings.ensure_dirs()
    return build_repository(settings.database_url)
