from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from tiktok_osint.errors import OcrUnavailable, UnsafeUrl
from tiktok_osint.logging_config import log_event
from tiktok_osint.policy import assert_avatar_url

logger = logging.getLogger(__name__)


class AvatarFetcher(Protocol):
    async def fetch_bytes(self, url: str) -> bytes: ...


class TextRecognizer(Protocol):
    def recognize(self, image_bytes: bytes) -> str: ...


async def read_avatar_text(url: str | None, *, fetcher: AvatarFetcher, engine: TextRecognizer) -> str | None:
    if not url:
        return None
    try:
        assert_avatar_url(url)
    except UnsafeUrl as exc:
        log_event(logger, logging.INFO, "ocr.avatar.rejected", url=url, error=exc)
        return None
    try:
        image = await fetcher.fetch_bytes(url)
        text = await asyncio.to_thread(engine.recognize, image)
        return text.strip() or None
    except OcrUnavailable as exc:
        log_event(logger, logging.INFO, "ocr.unavailable", error=exc)
        return None
    except Exception as exc:
        log_event(logger, logging.INFO, "ocr.avatar.failed", url=url, error=exc)
        return None
