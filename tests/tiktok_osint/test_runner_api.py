from __future__ import annotations

import csv
import sqlite3

from fastapi.testclient import TestClient

from tiktok_osint.api.app import create_app
from tiktok_osint.config import TikTokSettings
from tiktok_osint.domain.models import PublicSnapshot
from tiktok_osint.errors import TransientScrapeError
from tiktok_osint.ocr.engine import flatten_paddle_result
from tiktok_osint.queue.redis_queue import MemoryJobQueue
from tiktok_osint.scrape.runner import execute_scan
from tiktok_osint.storage.repo import Repository
from tiktok_osint.worker import run_worker


class FakeScraper:
    def __init__(self, fail_once: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_once = set(fail_once or [])

    async def fetch(self, username: str) -> PublicSnapshot:
        self.calls.append(username)
        if username in self.fail_once:
            self.fail_once.remove(username)
            raise TransientScrapeError("mat mang")
        return PublicSnapshot(
            username=username,
            nickname="Shop",
            bio="Liên hệ Zalo 0901.234.567",
            followers=12,
            following=1,
            likes=40,
            verified=True,
            private_account=username == "hidden",
            avatar_url="https://p16.tiktokcdn.com/avatar.jpeg",
            bio_link="https://shop.example/lien-he",
            source_url=f"https://www.tiktok.com/@{username}",
        )


class FakeResolver:
    async def resolve(self, url: str) -> str:
        assert url.startswith("https://vm.tiktok.com/")
        return "https://www.tiktok.com/@fromshort"


class FakeBio:
    async def fetch_text(self, url: str) -> str:
        assert url == "https://shop.example/lien-he"
        return "Email owner@shop.example"


class FakeAvatar:
    async def fetch_bytes(self, url: str) -> bytes:
        assert url.startswith("https://p16.tiktokcdn.com/")
        return "Hotline 0912.000.111".encode()


class FakeOcr:
    def recognize(self, image_bytes: bytes) -> str:
        return image_bytes.decode()


def test_checkpoint_resume_ocr_and_biolink(repo: Repository, settings: TikTokSettings) -> None:
    import asyncio

    job = repo.create_job("m1")
    repo.add_targets(
        job["id"],
        ["@publicshop", "@second", "https://vm.tiktok.com/ZMabc/"],
        max_lines=100,
    )
    scraper = FakeScraper(fail_once={"publicshop"})
    paused = {"hit": False}

    async def pause_after_first(_target_id: str) -> None:
        if not paused["hit"]:
            repo.set_job_status(job["id"], "paused")
            paused["hit"] = True

    async def scenario() -> None:
        first = await execute_scan(
            job["id"],
            repo=repo,
            scraper=scraper,
            resolver=FakeResolver(),
            bio_fetcher=FakeBio(),
            avatar_fetcher=FakeAvatar(),
            ocr=FakeOcr(),
            settings=settings,
            on_after_target=pause_after_first,
        )
        assert first["status"] == "paused"
        assert scraper.calls == ["publicshop", "publicshop"]
        second = await execute_scan(
            job["id"],
            repo=repo,
            scraper=scraper,
            resolver=FakeResolver(),
            bio_fetcher=FakeBio(),
            avatar_fetcher=FakeAvatar(),
            ocr=FakeOcr(),
            settings=settings,
        )
        assert second["status"] == "completed"
        assert scraper.calls == ["publicshop", "publicshop", "second", "fromshort"]

    asyncio.run(scenario())
    profile = repo.get_profile("publicshop")
    assert profile is not None
    found = {(item["kind"], item["source"], item["normalized_value"]) for item in profile["contacts"]}
    assert ("phone", "bio", "+84901234567") in found
    assert ("zalo", "bio", "+84901234567") in found
    assert ("email", "bio_link", "owner@shop.example") in found
    assert ("phone", "ocr_avatar", "+84912000111") in found
    checkpoint = repo.get_job(job["id"])["checkpoint"]
    assert checkpoint["done"] == 3


def test_worker_drains_memory_queue(repo: Repository, settings: TikTokSettings) -> None:
    import asyncio

    job = repo.create_job("worker")
    repo.add_targets(job["id"], ["@queuedshop"], max_lines=10)
    repo.set_job_status(job["id"], "queued")
    queue = MemoryJobQueue()
    queue.push(job["id"])

    async def scenario() -> None:
        await run_worker(settings, once=True, repo=repo, queue=queue, scraper=FakeScraper())

    asyncio.run(scenario())
    assert repo.get_profile("queuedshop") is not None
    assert repo.get_job(job["id"])["status"] == "completed"


def test_api_import_export_and_official_sync(repo: Repository, settings: TikTokSettings, tmp_path) -> None:
    queue = MemoryJobQueue()
    app = create_app(settings, repo=repo, queue=queue)
    client = TestClient(app)

    assert client.get("/api/health").json()["policy"] == "public-only"
    job = client.post("/api/jobs", json={"name": "đợt công khai"}).json()
    imported = client.post(
        f"/api/jobs/{job['id']}/targets",
        json={"lines": ["@publicshop", "https://www.tiktok.com/foryou", "# ghi chú"]},
    ).json()
    assert len(imported["accepted"]) == 1
    assert imported["rejected"][0]["raw"].endswith("/foryou")
    queued = client.post(f"/api/jobs/{job['id']}/enqueue").json()
    assert queued["status"] == "queued"
    assert queue.pop(timeout=0) == job["id"]

    refused = client.post("/api/official-sync/phone-to-id", json={"phone": "0901234567"})
    assert refused.status_code == 403
    assert "số điện thoại" in refused.json()["detail"]["message"]

    book = client.post(
        "/api/contact-books",
        json={"name": "Cá nhân", "contacts": [{"display_name": "An", "phone": "0901.234.567", "email": "an@example.com"}]},
    ).json()
    assert book["contacts"][0]["phone_e164"] == "+84901234567"
    bulk = client.post(
        f"/api/contact-books/{book['id']}/phones",
        json={"text": "0901234567\nMai, 0912345678\n0901234567\nkhông phải số"},
    ).json()
    assert bulk["imported"] == 1
    assert bulk["skipped_duplicates"] == 2
    assert bulk["rejected_count"] == 1
    assert bulk["contacts"][0]["phone_e164"] == "+84912345678"
    pasted_file = client.post(
        f"/api/contact-books/{book['id']}/import",
        files={"file": ("phones.txt", "0987000111\n0987000111\n".encode(), "text/plain")},
    ).json()
    assert pasted_file["imported"] == 1
    assert pasted_file["skipped_duplicates"] == 1
    stored = client.get(f"/api/contact-books/{book['id']}").json()
    assert {row["phone_e164"] for row in stored["contacts"]} == {"+84901234567", "+84912345678", "+84987000111"}
    session = client.post("/api/official-sync/sessions", json={"book_id": book["id"], "note": "Đã thấy trong app TikTok"}).json()
    phone_record = client.post(
        f"/api/official-sync/sessions/{session['id']}/results",
        json=[{"tiktok_username": "0901234567"}],
    )
    assert phone_record.status_code == 403
    recorded = client.post(
        f"/api/official-sync/sessions/{session['id']}/results",
        json=[
            {
                "tiktok_username": "@publicshop",
                "display_name_shown": "Shop",
                "user_contact_id": book["contacts"][0]["id"],
            }
        ],
    )
    assert recorded.status_code == 200

    import asyncio

    asyncio.run(
        execute_scan(
            job["id"],
            repo=repo,
            scraper=FakeScraper(),
            resolver=FakeResolver(),
            bio_fetcher=FakeBio(),
            avatar_fetcher=FakeAvatar(),
            ocr=FakeOcr(),
            settings=settings,
        )
    )
    recon = client.get(f"/api/official-sync/sessions/{session['id']}/reconciliation").json()
    assert recon["policy"] == "match-by-displayed-username-only"
    assert recon["rows"][0]["matched_public_profile"] is True
    assert recon["rows"][0]["match_key"] == "tiktok_username"
    assert recon["rows"][0]["public_profile"]["username"] == "publicshop"

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["profiles_total"] == 1
    assert dashboard["policy"] == "public-only"
    csv_response = client.get(f"/api/jobs/{job['id']}/export.csv")
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(csv_response.text.lstrip("\ufeff").splitlines()))
    assert any(row["contact_value"] == "+84901234567" for row in rows)
    xlsx_response = client.get(f"/api/jobs/{job['id']}/export.xlsx")
    assert xlsx_response.status_code == 200
    assert xlsx_response.content[:2] == b"PK"
    sqlite_response = client.get(f"/api/jobs/{job['id']}/export.sqlite")
    assert sqlite_response.status_code == 200
    destination = tmp_path / "snap.sqlite"
    destination.write_bytes(sqlite_response.content)
    with sqlite3.connect(destination) as conn:
        count = conn.execute("select count(*) from public_profiles").fetchone()
    assert count == (1,)


def test_paddle_flatten_and_redis_fake_queue() -> None:
    text = flatten_paddle_result([[[(0, 0), ("Zalo 090", 0.9)], [(1, 1), ("shop@example.com", 0.8)]]])
    assert "Zalo 090" in text
    assert "shop@example.com" in text

    class FakeRedis:
        def __init__(self) -> None:
            self.items: list[str] = []

        def lpush(self, key: str, value: str) -> None:
            del key
            self.items.insert(0, value)

        def brpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
            del key, timeout
            if not self.items:
                return None
            return ("k", self.items.pop())

    from tiktok_osint.queue.redis_queue import RedisJobQueue

    queue = RedisJobQueue.__new__(RedisJobQueue)
    queue._redis = FakeRedis()
    queue.key = "tiktok_osint:jobs"
    queue.push("a")
    queue.push("b")
    assert queue.pop() == "a"
    assert queue.pop() == "b"
