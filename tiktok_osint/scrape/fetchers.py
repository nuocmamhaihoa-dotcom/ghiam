from __future__ import annotations

import httpx

from tiktok_osint.domain.normalize import parse_profile_input
from tiktok_osint.errors import InvalidProfileInput, TransientScrapeError, UnsafeUrl
from tiktok_osint.policy import assert_avatar_url, assert_fetchable_public_url


class PublicRedirectResolver:
    """Follow a public TikTok short link until it lands on /@username."""

    def __init__(self, timeout_sec: float) -> None:
        self.timeout_sec = timeout_sec

    async def resolve(self, url: str) -> str:
        parsed = parse_profile_input(url)
        if not parsed.short_link or not parsed.profile_url:
            raise InvalidProfileInput("Chỉ phân giải short link công khai vm.tiktok.com hoặc vt.tiktok.com")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=True) as client:
                response = await client.get(parsed.profile_url)
        except httpx.HTTPError as exc:
            raise TransientScrapeError(str(exc)) from exc
        final = parse_profile_input(str(response.url))
        if final.reject_reason or not final.username or not final.profile_url:
            raise InvalidProfileInput("Short link không dẫn tới hồ sơ công khai")
        return final.profile_url


class HttpxBioLinkFetcher:
    def __init__(self, timeout_sec: float, max_bytes: int) -> None:
        self.timeout_sec = timeout_sec
        self.max_bytes = max_bytes

    async def fetch_text(self, url: str) -> str:
        assert_fetchable_public_url(url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=True, max_redirects=3) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise TransientScrapeError(str(exc)) from exc
        assert_fetchable_public_url(str(response.url))
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type and "text" not in content_type:
            raise UnsafeUrl("Trang liên kết không phải nội dung văn bản công khai")
        raw = response.content[: self.max_bytes]
        return raw.decode(response.encoding or "utf-8", errors="replace")


class HttpxAvatarFetcher:
    def __init__(self, timeout_sec: float, max_bytes: int) -> None:
        self.timeout_sec = timeout_sec
        self.max_bytes = max_bytes

    async def fetch_bytes(self, url: str) -> bytes:
        assert_avatar_url(url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=True, max_redirects=2) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise TransientScrapeError(str(exc)) from exc
        assert_avatar_url(str(response.url))
        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.startswith("image/"):
            raise UnsafeUrl("CDN không trả về ảnh")
        if len(response.content) > self.max_bytes:
            raise UnsafeUrl("Ảnh hồ sơ vượt quá giới hạn kích thước")
        return response.content
