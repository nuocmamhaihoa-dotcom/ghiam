from __future__ import annotations

import asyncio
import json

import pytest

from tiktok_osint.errors import ProfileNotFound, TransientScrapeError, UnsafeUrl
from tiktok_osint.policy import assert_avatar_url, assert_fetchable_public_url, navigation_host_allowed, reject_reverse_lookup
from tiktok_osint.scrape.parser import parse_public_profile_html
from tiktok_osint.scrape.playwright_scraper import PlaywrightPublicScraper
from tiktok_osint.scrape.retry import call_with_retry
from tiktok_osint.config import TikTokSettings


def _page(payload: dict) -> str:
    body = json.dumps({"__DEFAULT_SCOPE__": payload})
    return f'<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{body}</script></html>'


def test_parser_reads_public_card() -> None:
    html = _page(
        {
            "webapp.user-detail": {
                "statusCode": 0,
                "userInfo": {
                    "user": {
                        "uniqueId": "publicshop",
                        "nickname": "Shop",
                        "signature": "Zalo 0901234567",
                        "verified": True,
                        "privateAccount": False,
                        "avatarLarger": "https://p16.tiktokcdn.com/avatar.jpeg",
                        "bioLink": {"link": "https://shop.example"},
                    },
                    "stats": {"followerCount": 10, "followingCount": 2, "heartCount": 30, "videoCount": 4},
                },
            }
        }
    )
    parsed = parse_public_profile_html(html, source_url="https://www.tiktok.com/@publicshop")
    assert parsed.status == "ok"
    assert parsed.snapshot is not None
    assert parsed.snapshot.username == "publicshop"
    assert parsed.snapshot.followers == 10
    assert parsed.snapshot.likes == 30
    assert parsed.snapshot.verified is True
    assert parsed.snapshot.bio_link == "https://shop.example"
    assert parsed.snapshot.private_account is False


def test_parser_keeps_private_shell_without_extra_fields() -> None:
    html = _page(
        {
            "webapp.user-detail": {
                "userInfo": {
                    "user": {"uniqueId": "hidden", "nickname": "H", "privateAccount": True, "signature": ""},
                    "stats": {"followerCount": 3, "heartCount": 1},
                }
            }
        }
    )
    parsed = parse_public_profile_html(html, source_url="https://www.tiktok.com/@hidden")
    assert parsed.snapshot is not None
    assert parsed.snapshot.private_account is True
    assert not hasattr(parsed.snapshot, "videos")


def test_parser_not_found_and_login_wall() -> None:
    missing = _page({"webapp.user-detail": {"statusCode": 10221}})
    assert parse_public_profile_html(missing, source_url="https://www.tiktok.com/@missing").status == "not_found"
    wall = "<html><title>Log in | TikTok</title><body>Log in to TikTok</body></html>"
    assert parse_public_profile_html(wall, source_url="https://www.tiktok.com/@x").status == "not_visible"


def test_open_graph_fallback_is_partial() -> None:
    html = '<meta property="og:title" content="Shop (@publicshop) | TikTok"><meta property="og:description" content="hello">'
    parsed = parse_public_profile_html(html, source_url="https://www.tiktok.com/@publicshop")
    assert parsed.snapshot is not None
    assert parsed.snapshot.partial is True
    assert parsed.snapshot.followers is None


def test_policy_blocks_private_targets_and_reverse_lookup() -> None:
    assert navigation_host_allowed("www.tiktok.com")
    assert not navigation_host_allowed("evil.com")
    assert_avatar_url("https://p16-sign.tiktokcdn.com/a.jpeg?x=1")
    with pytest.raises(UnsafeUrl):
        assert_avatar_url("https://evil.com/a.jpg")
    with pytest.raises(UnsafeUrl):
        assert_fetchable_public_url("http://127.0.0.1/secret", resolve=lambda _host: ["127.0.0.1"])
    with pytest.raises(UnsafeUrl):
        assert_fetchable_public_url("http://169.254.169.254/", resolve=lambda _host: ["169.254.169.254"])
    with pytest.raises(UnsafeUrl):
        assert_fetchable_public_url("file:///etc/passwd")
    assert assert_fetchable_public_url("https://shop.example/lien-he", resolve=lambda _host: ["8.8.8.8"])
    with pytest.raises(Exception):
        reject_reverse_lookup("phone_to_user_id")
    with pytest.raises(Exception):
        reject_reverse_lookup("infer-account-from-phone")


class _Page:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []

    async def goto(self, url: str, timeout: int, wait_until: str) -> None:
        self.urls.append(url)

    async def content(self) -> str:
        return self.html

    async def route(self, pattern: str, handler: object) -> None:
        del pattern, handler

    async def close(self) -> None:
        return None


class _Context:
    def __init__(self, page: _Page) -> None:
        self._page = page

    async def new_page(self) -> _Page:
        return self._page

    async def close(self) -> None:
        return None


class _Browser:
    def __init__(self, page: _Page) -> None:
        self.page = page

    async def new_context(self) -> _Context:
        return _Context(self.page)


def test_playwright_scraper_only_opens_public_profile() -> None:
    html = _page(
        {
            "webapp.user-detail": {
                "userInfo": {
                    "user": {"uniqueId": "publicshop", "nickname": "Shop", "signature": "hi"},
                    "stats": {"followerCount": 1, "heartCount": 2},
                }
            }
        }
    )
    page = _Page(html)
    settings = TikTokSettings(database_url="sqlite://", redis_url=None, navigation_timeout_ms=1000)
    scraper = PlaywrightPublicScraper(settings, browser=_Browser(page))
    snapshot = asyncio.run(scraper.fetch("publicshop"))
    assert page.urls == ["https://www.tiktok.com/@publicshop"]
    assert snapshot.nickname == "Shop"
    with pytest.raises(Exception):
        asyncio.run(scraper.fetch("bad name"))
    assert page.urls == ["https://www.tiktok.com/@publicshop"]


def test_retry_then_timeout_budget() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientScrapeError("tam")
        return "ok"

    async def scenario() -> None:
        result = await call_with_retry(flaky, attempts=3, timeout_sec=1, base_delay_sec=0)
        assert result == "ok"

        async def missing() -> str:
            raise ProfileNotFound("ghost")

        with pytest.raises(ProfileNotFound):
            await call_with_retry(missing, attempts=3, timeout_sec=1, base_delay_sec=0)

    asyncio.run(scenario())
