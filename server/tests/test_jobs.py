from __future__ import annotations

import asyncio

from app.timeutil import utcnow
from tests.conftest import BOTH_BACKENDS, AppHarness

POSTS = "\n".join(
    [
        "https://m.facebook.com/trang/posts/123?fbclid=abc",
        "https://www.facebook.com/trang/posts/123",
        "https://youtu.be/dQw4w9WgXcQ",
        "không phải link",
        "https://www.facebook.com/trang",
    ]
)


async def test_preview_keeps_only_public_facebook_posts(harness: AppHarness) -> None:
    response = await harness.admin.post("/api/jobs/preview", json={"text": POSTS})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["new"], body["duplicate"], body["unsupported"], body["invalid"]) == (1, 1, 1, 2)
    fresh = next(item for item in body["lines"] if item["status"] == "new")
    assert fresh["url"] == "https://www.facebook.com/trang/posts/123"
    assert fresh["platform"] == "facebook"


async def test_claim_progress_and_export(harness: AppHarness) -> None:
    proxy_id = await harness.add("203.0.113.10:8000:user:pass")
    await harness.mark_alive([proxy_id])
    created = await harness.admin.post(
        "/api/jobs",
        json={"text": "https://www.facebook.com/trang/posts/123", "name": "Bài mẫu", "max_parallel": 2},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    assert created.json()["posts"] == 1

    claimed = await harness.agent.post("/api/agent/work/claim", json={"worker_id": "pc-van-phong"})
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["url"] == "https://www.facebook.com/trang/posts/123"
    assert claim["proxy"]["id"] == proxy_id
    attempt_id = claim["attempt_id"]

    comments = [
        {
            "external_id": "c1",
            "text": "Bình luận công khai",
            "author": "Nguyễn Văn A",
            "author_id": "100001",
            "author_url": "https://www.facebook.com/profile.php?id=100001",
            "time": "2026-09-26T15:03:00Z",
            "time_raw": "17 giờ",
            "likes": 1200,
            "likes_raw": "1,2K",
            "reply_count": 0,
        },
        {
            "external_id": "c2",
            "text": "=1+1",
            "author": "Trần B",
            "likes": 3,
        },
    ]
    first = await harness.agent.post(
        f"/api/agent/attempts/{attempt_id}/progress", json={"seq": 1, "comments": comments}
    )
    assert first.status_code == 200, first.text
    assert first.json()["stored"] == 2
    again = await harness.agent.post(
        f"/api/agent/attempts/{attempt_id}/progress", json={"seq": 1, "comments": comments}
    )
    assert again.json()["duplicate"] is True

    done = await harness.agent.post(
        f"/api/agent/attempts/{attempt_id}/complete",
        json={
            "outcome": "done",
            "proxy_outcome": "ok",
            "detail": "Đọc được 2 comment công khai",
            "complete": True,
            "stop_reason": "exhausted",
            "pages": 1,
            "comments_reported": 2,
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["post_status"] == "done"

    listed = await harness.admin.get(f"/api/jobs/{job_id}/comments")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["text"] for item in body["items"]] == ["Bình luận công khai", "=1+1"]
    assert body["items"][0]["likes"] == 1200
    assert body["items"][0]["author"] == "Nguyễn Văn A"

    exported = await harness.admin.get(f"/api/jobs/{job_id}/export", params={"format": "csv"})
    assert exported.status_code == 200, exported.text
    text = exported.content.decode("utf-8-sig")
    assert text.startswith("post_url,")
    assert "'=1+1" in text
    job = await harness.admin.get(f"/api/jobs/{job_id}")
    assert job.json()["status"] == "completed"
    assert job.json()["comments"] == 2


async def test_blocked_post_is_retried_on_another_proxy(harness: AppHarness) -> None:
    first = await harness.add("203.0.113.11:8000")
    second = await harness.add("203.0.113.12:8000")
    await harness.mark_alive([first, second])
    created = await harness.admin.post(
        "/api/jobs",
        json={"text": "https://www.facebook.com/trang/posts/999", "max_attempts": 3, "max_parallel": 1},
    )
    job_id = created.json()["id"]
    claimed = (await harness.agent.post("/api/agent/work/claim", json={"worker_id": "pc-1"})).json()
    await harness.agent.post(
        f"/api/agent/attempts/{claimed['attempt_id']}/complete",
        json={
            "outcome": "blocked",
            "proxy_outcome": "blocked",
            "detail": "Tường đăng nhập",
            "stop_reason": "login_wall",
        },
    )
    posts = (await harness.admin.get(f"/api/jobs/{job_id}/posts")).json()["items"]
    assert posts[0]["status"] == "pending"
    # Chưa tới giờ thử lại.
    waiting = (await harness.agent.post("/api/agent/work/claim", json={"worker_id": "pc-1"})).json()
    assert waiting["attempt_id"] is None
    from sqlalchemy import update

    from app.models import ScrapePost

    await harness.execute(update(ScrapePost).values(not_before=utcnow()))
    again = (await harness.agent.post("/api/agent/work/claim", json={"worker_id": "pc-1"})).json()
    assert again["attempt_id"] is not None
    assert again["proxy"]["id"] != claimed["proxy"]["id"]


@BOTH_BACKENDS
async def test_parallel_claims_do_not_share_a_post(harness: AppHarness) -> None:
    ids = [await harness.add(f"203.0.113.{index}:8000") for index in range(20, 25)]
    await harness.mark_alive(ids)
    text = "\n".join(f"https://www.facebook.com/trang/posts/{index}" for index in range(5))
    created = await harness.admin.post("/api/jobs", json={"text": text, "max_parallel": 5})
    assert created.status_code == 200, created.text

    async def claim(name: str) -> dict[str, object]:
        response = await harness.agent.post("/api/agent/work/claim", json={"worker_id": name})
        assert response.status_code == 200, response.text
        body: dict[str, object] = response.json()
        return body

    claimed = await asyncio.gather(*(claim(f"pc-{index}") for index in range(5)))
    urls = [item["url"] for item in claimed]
    assert all(item["attempt_id"] for item in claimed)
    assert len(set(urls)) == 5
