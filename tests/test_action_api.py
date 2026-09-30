"""HTTP API for the operator action journal."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="fb-actions-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from control_plane import app as app_module  # noqa: E402
from control_plane import db as people_db  # noqa: E402
from control_plane import video_helpers  # noqa: E402
from control_plane.app import app  # noqa: E402
from control_plane.settings import settings  # noqa: E402
from control_plane.version import VIDEO_WORKER_BUILD  # noqa: E402
from control_plane.video_jobs import VideoJob, jobs  # noqa: E402


class ActionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        video_helpers.helpers.clear()
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def test_requires_token(self) -> None:
        response = self.client.get("/v1/actions")
        self.assertEqual(response.status_code, 401)

    def test_note_roundtrip_and_search(self) -> None:
        created = self.client.post(
            "/v1/actions",
            headers=self.headers,
            json={"summary": "import 300 URL rồi rebalance hot", "kind": "note", "source": "manual"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["id"])

        listed = self.client.get("/v1/actions", headers=self.headers, params={"q": "rebalance"})
        self.assertEqual(listed.status_code, 200)
        items = listed.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["summary"], "import 300 URL rồi rebalance hot")
        self.assertEqual(items[0]["kind"], "note")

    def test_rejects_unknown_kind(self) -> None:
        response = self.client.post(
            "/v1/actions",
            headers=self.headers,
            json={"summary": "x", "kind": "drop-table"},
        )
        self.assertEqual(response.status_code, 400)

    def test_proxy_check_is_remembered(self) -> None:
        response = self.client.post("/v1/proxies/check", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        listed = self.client.get("/v1/actions", headers=self.headers, params={"kind": "proxy_check"})
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 1)
        self.assertIn("Check proxy", listed.json()["items"][0]["summary"])

    def test_dashboard_has_journal(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nhật ký thao tác", response.text)
        self.assertIn("Ghi nhớ", response.text)
        self.assertNotIn("Làm theo", response.text)
        self.assertNotIn("Ghi bấm phím", response.text)
        self.assertNotIn('id="recordDock"', response.text)
        self.assertIn("Đã lưu", response.text)
        self.assertIn("Mở giả lập điện thoại trên PC", response.text)
        self.assertIn("Thả video vào đây", response.text)
        self.assertIn("/v1/recordings/from-video/job", response.text)
        self.assertIn("/v1/recordings/uploads", response.text)
        self.assertIn("Tiếp tục đọc nối", response.text)
        self.assertIn('id="videoFile"', response.text)
        self.assertIn("multiple", response.text)
        self.assertIn("người trong danh bạ", response.text)
        self.assertIn("Kết quả đã lưu", response.text)
        self.assertNotIn("test-token", (self.client.get("/static/dashboard.html")).text)

    def test_phone_emulator_page(self) -> None:
        response = self.client.get("/phone")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Giả lập", response.text)
        self.assertIn('src="/sample-people"', response.text)
        self.assertIn("/?as=phone", response.text)
        self.assertIn("Kết nối PC", response.text)
        self.assertIn("Tên trong danh bạ", response.text)

    def test_scroll_sightings_merge_into_saved_people(self) -> None:
        denied = self.client.get("/v1/people")
        self.assertEqual(denied.status_code, 401)
        contact = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"}]},
        )
        self.assertEqual(contact.status_code, 200, contact.text)
        self.assertEqual(contact.json()["count"], 0)
        self.assertEqual(contact.json()["saved"], 1)
        profile = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "profile", "name": "Trần Tùng", "username": "trn.tng751"}]},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["count"], 1)
        self.assertEqual(profile.json()["saved"], 1)
        row = profile.json()["items"][0]
        self.assertEqual(row["name"], "Trần Tùng")
        self.assertEqual(row["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(row["username"], "@trn.tng751")
        repeat = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={
                "items": [
                    {"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"},
                    {"kind": "profile", "name": "Trần Tùng", "username": "@khac"},
                ]
            },
        )
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertEqual(repeat.json()["saved"], 0)
        self.assertEqual(repeat.json()["items"][0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(repeat.json()["items"][0]["username"], "@trn.tng751")
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)
        page = self.client.get("/")
        self.assertIn("Đã lưu", page.text)
        self.assertIn("Tên trong danh bạ", page.text)
        sample = self.client.get("/sample-people")
        self.assertEqual(sample.status_code, 200)
        self.assertIn("A Tùng Bán Gạch", sample.text)
        health = self.client.get("/health")
        build = str(health.json()["iphoneBuild"])
        self.assertIn(f"/static/watch.js?v={build}", sample.text)
        phone = self.client.get("/phone")
        self.assertIn('src="/sample-people"', phone.text)
        self.assertIn("Tên trong danh bạ", phone.text)
        self.assertIn('location.replace("/iphone")', phone.text)

    def test_iphone_app_is_installable(self) -> None:
        page = self.client.get("/iphone")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("apple-mobile-web-app-capable", page.text)
        self.assertIn("viewport-fit=cover", page.text)
        health = self.client.get("/health")
        build = str(health.json()["iphoneBuild"])
        self.assertEqual(page.headers["cache-control"], "no-cache")
        self.assertIn(f'content="{build}"', page.text)
        self.assertIn(f"/static/watch.js?v={build}", page.text)
        self.assertIn("/static/version.js", page.text)
        self.assertNotIn('data-app="browse"', page.text)
        self.assertNotIn("followTung", page.text)
        self.assertIn("/tai-pc", page.text)
        self.assertIn("Tải phần mềm cho PC", page.text)
        self.assertIn("Chọn video", page.text)
        self.assertIn("Không cần bấm phát", page.text)
        self.assertIn("tự lưu", page.text)
        self.assertIn("Đã lưu", page.text)
        self.assertIn("Tên danh bạ", page.text)
        self.assertIn('id="peopleTable"', page.text)
        self.assertIn("<th>Tên</th>", page.text)
        self.assertIn("<th>Tài khoản</th>", page.text)
        self.assertIn('id="peopleBox"', page.text)
        self.assertIn('id="savedBox"', page.text)
        self.assertIn('id="savedTable"', page.text)
        self.assertIn("Kết quả đã lưu", page.text)
        self.assertIn("50 triệu", page.text)
        self.assertIn('id="savedMore"', page.text)
        self.assertIn("Xem thêm", page.text)
        self.assertIn("Dữ liệu trùng", page.text)
        self.assertIn('id="dupBox"', page.text)
        self.assertIn('id="readTally"', page.text)
        self.assertIn("người trong danh bạ", page.text)
        self.assertIn("người có tài khoản", page.text)
        self.assertIn("ghi được", page.text)
        self.assertIn('id="dupTable"', page.text)
        self.assertIn('id="dupMore"', page.text)
        self.assertIn("/v1/people/duplicates", page.text)
        self.assertIn("/v1/people", page.text)
        self.assertIn("multiple", page.text)
        self.assertIn("nhiều video", page.text)
        self.assertIn("Không giới hạn số video, dung lượng hay thời lượng", page.text)
        self.assertIn("80% CPU và RAM", page.text)
        self.assertIn("Mỗi máy đọc tối đa hai video", page.text)
        self.assertIn("Video từ 4 phút được chia đôi cho hai máy", page.text)
        self.assertIn("navigator.wakeLock", page.text)
        self.assertIn("Đang giữ màn hình sáng", page.text)
        self.assertIn("fb_upload:", page.text)
        self.assertIn("restartLostJob", page.text)
        self.assertIn("PC đang tự lên bản", page.text)
        self.assertIn("máy đọc bằng GPU", page.text)
        self.assertIn('id="helperLine"', page.text)
        self.assertIn("PC phụ chưa nối", page.text)
        self.assertIn("PC phụ đang nối", page.text)
        self.assertNotIn("40 - queue.length", page.text)
        self.assertNotIn('id="stepList"', page.text)
        self.assertNotIn("Lưu thông tin", page.text)
        self.assertNotIn("/v1/people/confirm", page.text)
        self.assertIn("Bắt đầu ghi", page.text)
        self.assertIn("Dừng ghi", page.text)
        self.assertIn("liên tục", page.text)
        self.assertIn("getDisplayMedia", page.text)
        self.assertIn('id="playback"', page.text)
        self.assertIn("/v1/recordings/from-video", page.text)
        self.assertIn("/v1/recordings/from-video/job", page.text)
        self.assertIn("/v1/recordings/uploads", page.text)
        self.assertIn('id="progressBox"', page.text)
        self.assertIn('id="progressFill"', page.text)
        self.assertIn('id="problemList"', page.text)
        self.assertIn("Vấn đề khi xử lý", page.text)
        self.assertIn('id="continueBtn"', page.text)
        self.assertIn("Tiếp tục đọc nối", page.text)
        self.assertIn("/continue", page.text)
        self.assertIn("/v1/recordings/from-frame", page.text)
        self.assertNotIn("Quay màn hình", page.text)
        self.assertIn("Trung tâm điều khiển", page.text)
        self.assertIn("nhìn thấy", page.text)
        self.assertIn("Cài app", page.text)
        self.assertIn('href="/cai-app"', page.text)
        self.assertNotIn("kịch bản", page.text)
        self.assertIn('accept="video/*"', page.text)
        self.assertNotIn("confirmOk", page.text)
        self.assertNotIn("beginPhoneUse", page.text)
        self.assertNotIn('src="/?as=iphone"', page.text)
        self.assertIn('"test-token"', page.text)
        raw = self.client.get("/static/iphone.html")
        self.assertNotIn("test-token", raw.text)
        icon = self.client.get("/apple-touch-icon.png")
        self.assertEqual(icon.status_code, 200)
        self.assertIn("image/png", icon.headers["content-type"])
        self.assertTrue(icon.content.startswith(b"\x89PNG"))
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200, manifest.text)
        body = manifest.json()
        self.assertEqual(body["start_url"], "/iphone")
        self.assertEqual(body["display"], "standalone")
        home = self.client.get("/")
        self.assertIn("fb_upload:", home.text)
        self.assertIn("restartLostJob", home.text)
        self.assertIn("Video từ 4 phút được chia đôi cho hai máy", home.text)
        self.assertIn("Mở app iPhone", home.text)
        self.assertIn("Mở giả lập điện thoại trên PC", home.text)
        self.assertNotIn("Chạm trên iPhone được ghi để làm lại.", home.text)
        self.assertIn("/static/version.js", home.text)
        self.assertIn('location.replace("/iphone")', home.text)
        self.assertIn('href="/iphone">Mở trên iPhone', home.text)
        self.assertNotIn("pushPhoneEvent", home.text)
        self.assertNotIn('type: "fb-arm"', home.text)
        self.assertNotIn('href="/tai"', page.text)

    def test_screen_video_requires_token(self) -> None:
        denied = self.client.post(
            "/v1/recordings/from-video",
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(denied.status_code, 401)
        opened = self.client.post(
            "/v1/recordings/from-video",
            headers=self.headers,
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(opened.status_code, 400, opened.text)
        frame = self.client.post(
            "/v1/recordings/from-frame",
            files={"file": ("khung.jpg", b"not-a-photo", "image/jpeg")},
        )
        self.assertEqual(frame.status_code, 401)
        blank = self.client.post(
            "/v1/recordings/from-frame",
            headers=self.headers,
            files={"file": ("khung.jpg", b"not-a-photo", "image/jpeg")},
        )
        self.assertEqual(blank.status_code, 200, blank.text)
        self.assertEqual(blank.json()["line"], "")
        denied_lines = self.client.post("/v1/recordings/seen", json={"lines": ["0:01 — TikTok"]})
        self.assertEqual(denied_lines.status_code, 401)
        empty_lines = self.client.post(
            "/v1/recordings/seen",
            headers=self.headers,
            json={"lines": ["   "]},
        )
        self.assertEqual(empty_lines.status_code, 400, empty_lines.text)
        saved = self.client.post(
            "/v1/recordings/seen",
            headers=self.headers,
            json={"lines": ["0:01 — TikTok", "0:01 — TikTok"]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["count"], 1)
        self.assertEqual(saved.json()["steps"][0]["caption"], "0:01 — TikTok")

        missing = self.client.get("/khong-co-trang-nay")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Mở app", missing.text)
        self.assertIn('href="/iphone"', missing.text)

    def test_video_job_keeps_the_percent_when_it_fails(self) -> None:
        job = VideoJob("thu")
        job.update(40, "Đọc chữ")
        job.update(12, "Đọc chữ, khung 1/4")
        self.assertEqual(job.percent, 40)
        self.assertEqual(job.task, "Đọc chữ, khung 1/4")
        job.add_problem("2 khung không có chữ.")
        job.fail("Video không có hình.")
        body = job.public()
        self.assertTrue(body["done"])
        self.assertEqual(body["percent"], 40)
        self.assertEqual(body["task"], "Gặp vấn đề")
        self.assertEqual(body["error"], "Video không có hình.")
        self.assertIn("2 khung không có chữ.", body["problems"])
        self.assertNotIn("people", body)
        job.update(90, "không nhận nữa")
        self.assertEqual(job.percent, 40)
        job.finish([], 3, [])
        self.assertEqual(job.error, "Video không có hình.")
        self.assertNotIn("people", job.public())
        held = VideoJob("giu")
        self.assertFalse(held.claim("pc"))
        held.bind(Path("a.mp4"))
        self.assertTrue(held.claim("pc"))
        self.assertFalse(held.claim("khac"))
        self.assertFalse(held.stale(30))
        held.lease = 0
        self.assertTrue(held.stale(1))
        self.assertTrue(held.release("pc"))
        self.assertTrue(held.take_hub())

    def test_video_job_reports_a_problem(self) -> None:
        denied = self.client.post(
            "/v1/recordings/from-video/job",
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(denied.status_code, 401)
        missing = self.client.get("/v1/recordings/jobs/khong-co", headers=self.headers)
        self.assertEqual(missing.status_code, 404, missing.text)
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        job_id = opened.json()["jobId"]
        self.assertTrue(job_id)
        deadline = time.time() + 20
        body: dict[str, object] = {}
        while time.time() < deadline:
            polled = self.client.get(f"/v1/recordings/jobs/{job_id}", headers=self.headers)
            self.assertEqual(polled.status_code, 200, polled.text)
            body = polled.json()
            if body.get("done"):
                break
            time.sleep(0.2)
        self.assertTrue(body.get("done"), body)
        self.assertTrue(body.get("error"), body)
        self.assertLess(int(body.get("percent") or 0), 100)
        problems = body.get("problems")
        self.assertIsInstance(problems, list)
        self.assertIn(body["error"], problems)
        self.assertTrue(body.get("canContinue"))
        denied_continue = self.client.post(f"/v1/recordings/jobs/{job_id}/continue")
        self.assertEqual(denied_continue.status_code, 401)
        continued = self.client.post(f"/v1/recordings/jobs/{job_id}/continue", headers=self.headers)
        self.assertEqual(continued.status_code, 200, continued.text)
        again = self._wait_job(job_id)
        self.assertTrue(again.get("done"))
        self.assertTrue(again.get("error"))
        self.assertTrue(again.get("canContinue"))
        missing_continue = self.client.post("/v1/recordings/jobs/khong-co/continue", headers=self.headers)
        self.assertEqual(missing_continue.status_code, 404, missing_continue.text)

    def test_continue_keeps_frames_already_read(self) -> None:
        folder = Path(_TMP)
        video = folder / "tiep.mp4"
        video.write_bytes(b"x")
        job = jobs.create()
        job.bind(video)
        job.remember_frame(
            1.25,
            ["Tran Tung"],
            [{"kind": "profile", "name": "Tran Tung", "contactName": "", "username": "@trn.tng751"}],
        )
        job.stage_people([{"name": "Tran Tung", "contactName": "A Tung Ban Gach", "username": "@trn.tng751"}])
        job.update(70, "Đọc chữ, khung 4/8")
        job.fail("Không xử lý được video.")
        body = job.public()
        self.assertTrue(body["canContinue"])
        self.assertEqual(body["percent"], 70)
        self.assertTrue(job.reopen())
        self.assertFalse(job.public()["done"])
        self.assertEqual(job.public()["task"], "Đọc tiếp")
        self.assertEqual(job.public()["percent"], 70)
        self.assertEqual(job.remembered()["1.250"][0], ["Tran Tung"])
        job.note_tally(12, 4, 1)
        job.finish([{"name": "Tran Tung", "contactName": "A Tung", "username": "@trn.tng751"}], 1, [], 1, [])
        finished = job.public()
        self.assertEqual(finished["seenContacts"], 12)
        self.assertEqual(finished["seenAccounts"], 4)
        self.assertEqual(finished["readSaved"], 1)
        for index in range(1, 1002):
            job.remember_frame(float(index), ["a"], [])
        self.assertIn("1001.000", job.remembered())
        staged = job.staged_people()
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertEqual(staged[0]["username"], "@trn.tng751")
        self.assertFalse(job.reopen())
        job.finish([], 0, [])
        blocked = self.client.post(f"/v1/recordings/jobs/{job.id}/continue", headers=self.headers)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        job.discard()

    def test_upload_size_is_open_unless_a_cap_is_set(self) -> None:
        previous = settings.max_upload_mb
        payload = b"x" * (2 * 1024 * 1024)
        try:
            settings.max_upload_mb = 1
            blocked = self.client.post(
                "/v1/recordings/from-video",
                headers=self.headers,
                files={"file": ("clip.mp4", payload, "video/mp4")},
            )
            self.assertEqual(blocked.status_code, 413, blocked.text)
            settings.max_upload_mb = 0
            opened = self.client.post(
                "/v1/recordings/from-video",
                headers=self.headers,
                files={"file": ("clip.mp4", payload, "video/mp4")},
            )
            self.assertEqual(opened.status_code, 400, opened.text)
        finally:
            settings.max_upload_mb = previous

    def test_public_delivery_package(self) -> None:
        opened = self.client.get("/tai", follow_redirects=False)
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(opened.headers["location"], "/iphone")
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        body = health.json()
        build = str(body["iphoneBuild"])
        self.assertEqual(body["delivery"], "/tai")
        self.assertEqual(build, "37")
        self.assertEqual(body["videoHelper"]["connected"], False)
        self.assertEqual(body["videoHelper"]["cpus"], 0)
        self.assertEqual(body["videoHelper"]["count"], 0)
        self.assertEqual(body["videoHelper"]["cores"], 0)
        self.assertEqual(body["videoHelper"]["gpu"], 0)

        info = self.client.get("/v1/delivery")
        self.assertEqual(info.status_code, 200, info.text)
        payload = info.json()
        self.assertEqual(payload["iphoneBuild"], 37)
        self.assertEqual(payload["iphonePath"], "/iphone")
        self.assertEqual(payload["installPath"], "/tai")
        package = payload["package"]
        self.assertEqual(package["path"], "/tai/goi.zip")
        self.assertEqual(package["name"], f"fb-poller-iphone-{build}.zip")
        self.assertGreater(package["bytes"], 0)
        self.assertEqual(len(package["sha256"]), 64)

        downloaded = self.client.get("/tai/goi.zip")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["cache-control"], "no-cache")
        self.assertTrue(downloaded.content.startswith(b"PK"))
        self.assertIn(package["name"], downloaded.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            names = archive.namelist()
            self.assertIn("fb-poller/HUONG-DAN.txt", names)
            self.assertIn("fb-poller/iphone.html", names)
            self.assertNotIn("server.env", " ".join(names))
            joined = " ".join(names)
            self.assertNotIn(".db", joined)
            guide = archive.read("fb-poller/HUONG-DAN.txt").decode("utf-8")
            self.assertIn("/iphone", guide)
            self.assertIn("Không có token", guide)
            self.assertNotIn("test-token", archive.read("fb-poller/iphone.html").decode("utf-8"))
        self.assertFalse(any(Path(_TMP, "packages").glob("*.zip")))
        denied = self.client.get(f"/v1/updates/packages/{package['name']}")
        self.assertEqual(denied.status_code, 401)

    def test_ios_app_reads_other_apps_as_text(self) -> None:
        page = self.client.get("/cai-app")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Xcode", page.text)
        self.assertIn("không bấm", page.text.casefold())
        bundle = self.client.get("/tai/ios.zip")
        self.assertEqual(bundle.status_code, 200, bundle.text)
        self.assertTrue(bundle.content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            names = archive.namelist()
            self.assertIn("ios/FbPollerBroadcast/SampleHandler.swift", names)
            self.assertIn("ios/Shared/HubStore.swift", names)
            joined = "\n".join(
                archive.read(name).decode("utf-8", errors="ignore")
                for name in names
                if name.endswith((".swift", ".plist", ".txt", ".html"))
            )
            self.assertNotIn("test-token", joined)
        denied = self.client.post("/v1/screen/live", json={"text": "Nguyễn Anh @nguyen.anh"})
        self.assertEqual(denied.status_code, 401)
        empty = self.client.post("/v1/screen/live", headers=self.headers, json={"text": "   "})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["line"], "")
        self.assertFalse(empty.json()["saved"])
        self.assertEqual(empty.json()["people"], 0)
        saved = self.client.post(
            "/v1/screen/live",
            headers=self.headers,
            json={"text": "Nguyễn Anh @nguyen.anh"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertIn("@nguyen.anh", body["line"])
        self.assertTrue(body["saved"])
        self.assertEqual(body["people"], 1)
        again = self.client.post(
            "/v1/screen/live",
            headers=self.headers,
            json={"text": "Nguyễn Anh @nguyen.anh"},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertFalse(again.json()["saved"])
        listed = self.client.get("/v1/screen/live", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["count"], 1)
        with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
            conn.execute("DELETE FROM saved_people WHERE username = ?", ("@nguyen.anh",))
            conn.execute("DELETE FROM people_meta")

    def test_confirm_saves_chosen_rows_without_overwrite(self) -> None:
        denied = self.client.post(
            "/v1/people/confirm",
            json={"rows": [{"name": "Lê Hoa", "contactName": "Chị Hoa", "username": "@le.hoa"}]},
        )
        self.assertEqual(denied.status_code, 401)
        saved = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Lê Hoa", "contactName": "Chị Hoa", "username": "le.hoa"}]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["saved"], 2)
        row = next(item for item in saved.json()["items"] if item["username"] == "@le.hoa")
        self.assertEqual(row["name"], "Lê Hoa")
        self.assertEqual(row["contactName"], "Chị Hoa")
        repeat = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Lê Hoa", "contactName": "Tên khác", "username": "@khac.hoa"}]},
        )
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertEqual(repeat.json()["saved"], 0)
        kept = next(item for item in repeat.json()["items"] if item["name"] == "Lê Hoa")
        self.assertEqual(kept["contactName"], "Chị Hoa")
        self.assertEqual(kept["username"], "@le.hoa")
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertTrue(any(item["username"] == "@le.hoa" for item in listed.json()["items"]))
        skipped = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Lê Hoa", "contactName": "Lê Hoa", "username": "@le.hoa"}]},
        )
        self.assertEqual(skipped.json()["saved"], 0)
        with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
            conn.execute("DELETE FROM saved_people WHERE username = ?", ("@le.hoa",))
            conn.execute("DELETE FROM scan_duplicates")
            conn.execute("DELETE FROM people_meta")

    def test_repeat_scan_is_listed_as_duplicate(self) -> None:
        denied = self.client.get("/v1/people/duplicates")
        self.assertEqual(denied.status_code, 401)
        _total, ready = people_db.people_counts(Path(os.environ["CONTROL_DB"]))
        first = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Đỗ Nam", "contactName": "Anh Nam", "username": "@do.nam.dup"}]},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["saved"], 2)
        second = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Mai Hoa", "contactName": "Chị Hoa Mai", "username": "@mai.hoa.dup"}]},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["saved"], 2)
        contact = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "contact", "name": "Phạm Lẻ", "contactName": "Chị Lẻ"}]},
        )
        self.assertEqual(contact.status_code, 200, contact.text)
        filled = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Phạm Lẻ", "contactName": "Chị Lẻ", "username": "@pham.le.dup"}]},
        )
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertGreater(filled.json()["saved"], 0)
        kept_fill = next(item for item in filled.json()["items"] if item["username"] == "@pham.le.dup")
        self.assertEqual(kept_fill["contactName"], "Chị Lẻ")
        repeat = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Đỗ Nam", "contactName": "Tên quét lại", "username": "@khac.nam"}]},
        )
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertEqual(repeat.json()["saved"], 0)
        kept = next(item for item in repeat.json()["items"] if item["name"] == "Đỗ Nam")
        self.assertEqual(kept["contactName"], "Anh Nam")
        self.assertEqual(kept["username"], "@do.nam.dup")
        other = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Võ Nữ", "contactName": "Chị Nữ", "username": "@mai.hoa.dup"}]},
        )
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(other.json()["saved"], 0)
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertEqual(listed.json()["count"], ready + 3)
        self.assertFalse(any(item["name"] == "Võ Nữ" for item in listed.json()["items"]))
        dupes = self.client.get("/v1/people/duplicates", headers=self.headers, params={"limit": 1})
        self.assertEqual(dupes.status_code, 200, dupes.text)
        body = dupes.json()
        self.assertGreaterEqual(body["count"], 2)
        seen = {(item["name"], item["contactName"], item["username"]) for item in body["items"]}
        cursor = body["cursor"]
        self.assertTrue(cursor)
        for _ in range(10):
            if not cursor:
                break
            page = self.client.get(
                "/v1/people/duplicates",
                headers=self.headers,
                params={"limit": 1, "cursor": cursor},
            )
            self.assertEqual(page.status_code, 200, page.text)
            seen.update(
                (item["name"], item["contactName"], item["username"]) for item in page.json()["items"]
            )
            cursor = page.json()["cursor"]
        self.assertIn(("Đỗ Nam", "Tên quét lại", "@khac.nam"), seen)
        self.assertIn(("Võ Nữ", "Chị Nữ", "@mai.hoa.dup"), seen)
        self.assertNotIn(("Phạm Lẻ", "Chị Lẻ", "@pham.le.dup"), seen)
        again = self.client.post(
            "/v1/people/confirm",
            headers=self.headers,
            json={"rows": [{"name": "Đỗ Nam", "contactName": "Tên quét lại", "username": "@khac.nam"}]},
        )
        self.assertEqual(again.json()["saved"], 0)
        counted = self.client.get("/v1/people/duplicates", headers=self.headers)
        self.assertEqual(counted.json()["count"], body["count"])
        with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
            conn.execute(
                "DELETE FROM saved_people WHERE username IN ('@do.nam.dup', '@mai.hoa.dup', '@pham.le.dup')"
            )
            conn.execute("DELETE FROM scan_duplicates")
            conn.execute("DELETE FROM people_meta")

    def _wait_job(self, job_id: str) -> dict[str, object]:
        deadline = time.time() + 20
        body: dict[str, object] = {}
        while time.time() < deadline:
            polled = self.client.get(f"/v1/recordings/jobs/{job_id}", headers=self.headers)
            self.assertEqual(polled.status_code, 200, polled.text)
            body = polled.json()
            if body.get("done"):
                return body
            time.sleep(0.1)
        self.fail(f"job did not finish: {body}")
        return body

    def test_a_connected_pc_saves_the_rows_it_reads(self) -> None:
        denied = self.client.post("/v1/video-workers/heartbeat", json={"name": "pc-nha", "cpus": 16})
        self.assertEqual(denied.status_code, 401)
        denied_claim = self.client.post("/v1/recordings/jobs/claim", json={"workerId": "pc"})
        self.assertEqual(denied_claim.status_code, 401)
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-nha", "cpus": 16, "workers": 12},
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        worker_id = beat.json()["workerId"]
        self.assertTrue(worker_id)
        health = self.client.get("/health")
        helper = health.json()["videoHelper"]
        self.assertTrue(helper["connected"])
        self.assertEqual(helper["cpus"], 16)
        self.assertEqual(helper["name"], "pc-nha")
        self.assertEqual(helper["count"], 1)
        self.assertEqual(helper["cores"], 16)
        self.assertEqual(helper["gpu"], 0)
        self.assertEqual(helper["workers"], 12)
        empty = self.client.post(
            "/v1/recordings/jobs/claim",
            headers=self.headers,
            json={"workerId": worker_id},
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["jobId"], "")
        stranger = self.client.post(
            "/v1/recordings/jobs/claim",
            headers=self.headers,
            json={"workerId": "khong-co"},
        )
        self.assertEqual(stranger.status_code, 409, stranger.text)
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        job_id = opened.json()["jobId"]
        claimed_id = ""
        for _ in range(30):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            self.assertEqual(claimed.status_code, 200, claimed.text)
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, job_id)
        video = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers=self.headers,
            params={"workerId": worker_id},
        )
        self.assertEqual(video.status_code, 200, video.text)
        self.assertEqual(video.content, b"not-a-video")
        progress = self.client.post(
            f"/v1/recordings/jobs/{job_id}/progress",
            headers=self.headers,
            json={"workerId": worker_id, "percent": 48, "task": "Đọc chữ, khung 1/1", "problems": []},
        )
        self.assertEqual(progress.status_code, 200, progress.text)
        self.assertGreaterEqual(progress.json()["percent"], 48)
        done = self.client.post(
            f"/v1/recordings/jobs/{job_id}/complete",
            headers=self.headers,
            json={
                "workerId": worker_id,
                "people": [
                    {"name": "Mai Lan", "contactName": "Chị Mai", "username": "@mai.lan.pc"},
                ],
            },
        )
        self.assertEqual(done.status_code, 200, done.text)
        self.assertTrue(done.json()["done"])
        self.assertEqual(done.json()["error"], "")
        self.assertEqual(done.json()["savedPeople"], 1)
        self.assertEqual(done.json()["percent"], 100)
        time.sleep(0.4)
        again = self._wait_job(job_id)
        self.assertEqual(again.get("error"), "")
        self.assertEqual(again.get("task"), "Đã ghi xong")
        self.assertTrue(any(item.get("username") == "@mai.lan.pc" for item in again.get("archive", [])))
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertTrue(any(item["username"] == "@mai.lan.pc" for item in listed.json()["items"]))
        with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
            conn.execute("DELETE FROM saved_people WHERE username = ?", ("@mai.lan.pc",))
            conn.execute("DELETE FROM people_meta")
        failed_open = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"still-not-a-video", "video/mp4")},
        )
        failed_id = failed_open.json()["jobId"]
        claimed_id = ""
        for _ in range(30):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, failed_id)
        failed = self.client.post(
            f"/v1/recordings/jobs/{failed_id}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertTrue(failed.json()["done"])
        self.assertEqual(failed.json()["error"], "Không đọc được video.")
        time.sleep(0.4)
        stayed = self._wait_job(failed_id)
        self.assertEqual(stayed.get("error"), "Không đọc được video.")
        self.assertTrue(all("Máy chủ đọc tiếp" not in str(item) for item in stayed.get("problems", [])))

    def test_a_pc_can_resume_a_large_video_without_losing_the_lease(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-tai", "cpus": 8},
        )
        worker_id = beat.json()["workerId"]
        payload = b"abcdefghijklmnopqrstuvwxyz"
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", payload, "video/mp4")},
        )
        job_id = opened.json()["jobId"]
        claimed_id = ""
        for _ in range(30):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, job_id)
        job = jobs.get(job_id)
        assert job is not None
        time.sleep(0.05)
        before = job.lease
        part = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers={**self.headers, "Range": "bytes=0-3", "Accept-Encoding": "gzip"},
            params={"workerId": worker_id},
        )
        self.assertEqual(part.status_code, 206, part.text)
        self.assertEqual(part.content, b"abcd")
        self.assertEqual(part.headers.get("content-length"), "4")
        self.assertEqual(part.headers.get("accept-ranges"), "bytes")
        self.assertIn("bytes 0-3/26", part.headers.get("content-range", ""))
        self.assertNotEqual(part.headers.get("content-encoding"), "gzip")
        self.assertGreater(job.lease, before)
        self.assertTrue(self.client.get("/health").json()["videoHelper"]["connected"])
        rest = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers={**self.headers, "Range": "bytes=4-"},
            params={"workerId": worker_id},
        )
        self.assertEqual(rest.status_code, 206, rest.text)
        self.assertEqual(rest.content, payload[4:])
        whole = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers=self.headers,
            params={"workerId": worker_id},
        )
        self.assertEqual(whole.status_code, 200, whole.text)
        self.assertEqual(whole.content, payload)
        too_far = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers={**self.headers, "Range": "bytes=999-1000"},
            params={"workerId": worker_id},
        )
        self.assertEqual(too_far.status_code, 416, too_far.text)
        started = self.client.post(
            "/v1/recordings/uploads",
            headers=self.headers,
            json={"name": "lon.mp4", "size": 10},
        )
        self.assertEqual(started.status_code, 200, started.text)
        upload_id = started.json()["uploadId"]
        early = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(early.status_code, 409, early.text)
        first = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=0",
            headers=self.headers,
            content=b"01234",
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["offset"], 5)
        clash = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=0",
            headers=self.headers,
            content=b"xx",
        )
        self.assertEqual(clash.status_code, 409, clash.text)
        self.assertEqual(clash.json()["offset"], 5)
        second = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=5",
            headers=self.headers,
            content=b"56789",
        )
        self.assertEqual(second.status_code, 200, second.text)
        released = self.client.post(
            f"/v1/recordings/jobs/{job_id}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )
        self.assertEqual(released.status_code, 200, released.text)
        finished = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(finished.status_code, 200, finished.text)
        big_id = finished.json()["jobId"]
        claimed_big = ""
        for _ in range(40):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_big = claimed.json()["jobId"]
            if claimed_big:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_big, big_id)
        video = self.client.get(
            f"/v1/recordings/jobs/{big_id}/video",
            headers=self.headers,
            params={"workerId": worker_id},
        )
        self.assertEqual(video.status_code, 200, video.text)
        self.assertEqual(video.content, b"0123456789")
        self.client.post(
            f"/v1/recordings/jobs/{big_id}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )

    def test_later_chunk_can_arrive_before_the_first(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-song", "cpus": 4},
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        worker_id = beat.json()["workerId"]
        started = self.client.post(
            "/v1/recordings/uploads",
            headers=self.headers,
            json={"name": "lon.mp4", "size": 10},
        )
        self.assertEqual(started.status_code, 200, started.text)
        upload_id = started.json()["uploadId"]
        later = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=5",
            headers=self.headers,
            content=b"56789",
        )
        self.assertEqual(later.status_code, 200, later.text)
        self.assertEqual(later.json()["end"], 10)
        self.assertEqual(later.json()["offset"], 0)
        early = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(early.status_code, 409, early.text)
        self.assertEqual(early.json()["offset"], 0)
        again = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=5",
            headers=self.headers,
            content=b"56789",
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["end"], 10)
        first = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=0",
            headers=self.headers,
            content=b"01234",
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["offset"], 10)
        self.assertEqual(first.json()["end"], 5)
        finished = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(finished.status_code, 200, finished.text)
        job_id = finished.json()["jobId"]
        claimed_id = ""
        for _ in range(40):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, job_id)
        video = self.client.get(
            f"/v1/recordings/jobs/{job_id}/video",
            headers=self.headers,
            params={"workerId": worker_id},
        )
        self.assertEqual(video.status_code, 200, video.text)
        self.assertEqual(video.content, b"0123456789")
        self.client.post(
            f"/v1/recordings/jobs/{job_id}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )

    def test_upload_status_shows_bytes_the_phone_already_sent(self) -> None:
        missing = self.client.get("/v1/recordings/uploads/khong-co", headers=self.headers)
        self.assertEqual(missing.status_code, 404, missing.text)
        locked = self.client.get("/v1/recordings/uploads/khong-co")
        self.assertEqual(locked.status_code, 401, locked.text)
        started = self.client.post(
            "/v1/recordings/uploads",
            headers=self.headers,
            json={"name": "lon.mp4", "size": 10},
        )
        self.assertEqual(started.status_code, 200, started.text)
        upload_id = started.json()["uploadId"]
        empty = self.client.get(f"/v1/recordings/uploads/{upload_id}", headers=self.headers)
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["offset"], 0)
        self.assertEqual(empty.json()["size"], 10)
        self.assertEqual(empty.json()["spans"], [])
        later = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=5",
            headers=self.headers,
            content=b"56789",
        )
        self.assertEqual(later.status_code, 200, later.text)
        seen = self.client.get(f"/v1/recordings/uploads/{upload_id}", headers=self.headers)
        self.assertEqual(seen.status_code, 200, seen.text)
        self.assertEqual(seen.json()["offset"], 0)
        self.assertEqual(seen.json()["spans"], [[5, 10]])
        first = self.client.put(
            f"/v1/recordings/uploads/{upload_id}?offset=0",
            headers=self.headers,
            content=b"01234",
        )
        self.assertEqual(first.status_code, 200, first.text)
        done = self.client.get(f"/v1/recordings/uploads/{upload_id}", headers=self.headers)
        self.assertEqual(done.json()["offset"], 10)
        self.assertEqual(done.json()["spans"], [[0, 5], [5, 10]])

    def test_two_pcs_each_take_one_video(self) -> None:
        first = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-a", "cpus": 8, "gpu": True, "gpuName": "RTX 4060"},
        )
        second = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-b", "cpus": 16},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        worker_a = first.json()["workerId"]
        worker_b = second.json()["workerId"]
        self.assertNotEqual(worker_a, worker_b)
        helper = self.client.get("/health").json()["videoHelper"]
        self.assertEqual(helper["count"], 2)
        self.assertEqual(helper["cores"], 24)
        self.assertEqual(helper["gpu"], 1)
        self.assertEqual(helper["cpus"], 16)
        self.assertTrue(helper["connected"])
        opened = [
            self.client.post(
                "/v1/recordings/from-video/job",
                headers=self.headers,
                files={"file": ("clip.mp4", body, "video/mp4")},
            )
            for body in (b"video-a", b"video-b")
        ]
        job_ids = [item.json()["jobId"] for item in opened]
        self.assertEqual(len(set(job_ids)), 2)
        claimed: dict[str, str] = {}
        for worker_id in (worker_a, worker_b):
            claimed_id = ""
            for _ in range(40):
                response = self.client.post(
                    "/v1/recordings/jobs/claim",
                    headers=self.headers,
                    json={"workerId": worker_id},
                )
                self.assertEqual(response.status_code, 200, response.text)
                claimed_id = response.json()["jobId"]
                if claimed_id:
                    break
                time.sleep(0.05)
            self.assertTrue(claimed_id)
            claimed[worker_id] = claimed_id
        self.assertEqual(set(claimed.values()), set(job_ids))
        for worker_id, job_id in claimed.items():
            failed = self.client.post(
                f"/v1/recordings/jobs/{job_id}/fail",
                headers=self.headers,
                json={"workerId": worker_id, "error": "Không đọc được video."},
            )
            self.assertEqual(failed.status_code, 200, failed.text)
        time.sleep(0.4)
        for job_id in job_ids:
            body = self._wait_job(job_id)
            self.assertEqual(body.get("error"), "Không đọc được video.")

    def test_one_pc_reads_two_videos_and_the_third_waits(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-hang", "cpus": 20},
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        worker_id = beat.json()["workerId"]
        opened = [
            self.client.post(
                "/v1/recordings/from-video/job",
                headers=self.headers,
                files={"file": ("clip.mp4", body, "video/mp4")},
            )
            for body in (b"video-mot", b"video-hai", b"video-ba")
        ]
        job_ids = [item.json()["jobId"] for item in opened]
        taken: list[str] = []
        for _ in range(2):
            claimed_id = ""
            for _attempt in range(80):
                claimed = self.client.post(
                    "/v1/recordings/jobs/claim",
                    headers=self.headers,
                    json={"workerId": worker_id},
                )
                self.assertEqual(claimed.status_code, 200, claimed.text)
                claimed_id = claimed.json()["jobId"]
                if claimed_id:
                    break
                time.sleep(0.05)
            self.assertTrue(claimed_id)
            taken.append(claimed_id)
        self.assertEqual(len(set(taken)), 2)
        third_id = next(job_id for job_id in job_ids if job_id not in taken)
        refused = self.client.post(
            "/v1/recordings/jobs/claim",
            headers=self.headers,
            json={"workerId": worker_id},
        )
        self.assertEqual(refused.status_code, 200, refused.text)
        self.assertEqual(refused.json()["jobId"], "")
        body: dict[str, object] = {}
        deadline = time.time() + 8
        while time.time() < deadline:
            waiting = self.client.get(f"/v1/recordings/jobs/{third_id}", headers=self.headers)
            self.assertEqual(waiting.status_code, 200, waiting.text)
            body = waiting.json()
            if "Chờ PC" in str(body.get("task")):
                break
            time.sleep(0.1)
        self.assertFalse(body.get("done"))
        self.assertIn("Chờ PC", str(body.get("task")), body)
        failed = self.client.post(
            f"/v1/recordings/jobs/{taken[0]}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )
        self.assertEqual(failed.status_code, 200, failed.text)
        claimed_third = ""
        for _attempt in range(80):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            self.assertEqual(claimed.status_code, 200, claimed.text)
            claimed_third = claimed.json()["jobId"]
            if claimed_third:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_third, third_id)
        for job_id in (taken[1], third_id):
            closed = self.client.post(
                f"/v1/recordings/jobs/{job_id}/fail",
                headers=self.headers,
                json={"workerId": worker_id, "error": "Không đọc được video."},
            )
            self.assertEqual(closed.status_code, 200, closed.text)

    def test_hub_reads_when_the_pc_does_not_take_the_video(self) -> None:
        previous = video_helpers.OFFER_SECONDS
        video_helpers.OFFER_SECONDS = 0.2
        try:
            beat = self.client.post(
                "/v1/video-workers/heartbeat",
                headers=self.headers,
                json={"name": "pc-ban", "cpus": 8},
            )
            self.assertEqual(beat.status_code, 200, beat.text)
            opened = self.client.post(
                "/v1/recordings/from-video/job",
                headers=self.headers,
                files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
            )
            job_id = opened.json()["jobId"]
            body = self._wait_job(job_id)
        finally:
            video_helpers.OFFER_SECONDS = previous
        self.assertTrue(body.get("error"), body)
        self.assertLess(int(body.get("percent") or 0), 100)

    def test_hub_reads_when_the_pc_stops_reporting(self) -> None:
        previous = video_helpers.LEASE_SECONDS
        video_helpers.LEASE_SECONDS = 0.2
        try:
            beat = self.client.post(
                "/v1/video-workers/heartbeat",
                headers=self.headers,
                json={"name": "pc-dut", "cpus": 4},
            )
            worker_id = beat.json()["workerId"]
            opened = self.client.post(
                "/v1/recordings/from-video/job",
                headers=self.headers,
                files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
            )
            job_id = opened.json()["jobId"]
            claimed_id = ""
            for _ in range(30):
                claimed = self.client.post(
                    "/v1/recordings/jobs/claim",
                    headers=self.headers,
                    json={"workerId": worker_id},
                )
                claimed_id = claimed.json()["jobId"]
                if claimed_id:
                    break
                time.sleep(0.05)
            self.assertEqual(claimed_id, job_id)
            body = self._wait_job(job_id)
        finally:
            video_helpers.LEASE_SECONDS = previous
        self.assertTrue(body.get("error"), body)
        problems = body.get("problems")
        self.assertIsInstance(problems, list)
        self.assertTrue(any("Máy chủ đọc tiếp" in str(item) for item in problems), problems)

    def test_pc_reports_its_build_models_and_reader(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={
                "name": "pc-moi",
                "cpus": 20,
                "workers": 16,
                "build": VIDEO_WORKER_BUILD,
                "models": "fast",
                "readerOk": True,
                "readerMode": "api",
            },
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        self.assertEqual(beat.json()["build"], VIDEO_WORKER_BUILD)
        helper = self.client.get("/health").json()["videoHelper"]
        self.assertEqual(helper["build"], VIDEO_WORKER_BUILD)
        self.assertEqual(helper["latestBuild"], VIDEO_WORKER_BUILD)
        self.assertEqual(helper["models"], "fast")
        self.assertIs(helper["readerOk"], True)
        self.assertEqual(helper["readerMode"], "api")
        self.assertEqual(helper["outdated"], 0)
        self.assertEqual(helper["broken"], 0)
        old = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-cu", "cpus": 8, "build": VIDEO_WORKER_BUILD - 1},
        )
        self.assertEqual(old.status_code, 200, old.text)
        helper = self.client.get("/health").json()["videoHelper"]
        self.assertEqual(helper["count"], 2)
        self.assertEqual(helper["outdated"], 1)
        self.assertEqual(helper["name"], "pc-moi")

    def test_a_pc_that_cannot_read_gets_no_video(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-hong", "cpus": 8, "readerOk": False, "readerNote": "PC chưa chạy được Tesseract."},
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        worker_id = beat.json()["workerId"]
        helper = self.client.get("/health").json()["videoHelper"]
        self.assertTrue(helper["connected"])
        self.assertIs(helper["readerOk"], False)
        self.assertEqual(helper["broken"], 1)
        self.assertIn("Tesseract", helper["readerNote"])
        started = time.time()
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        job_id = opened.json()["jobId"]
        refused = self.client.post(
            "/v1/recordings/jobs/claim",
            headers=self.headers,
            json={"workerId": worker_id},
        )
        self.assertEqual(refused.status_code, 200, refused.text)
        self.assertEqual(refused.json()["jobId"], "")
        body = self._wait_job(job_id)
        self.assertTrue(body.get("error"), body)
        self.assertLess(time.time() - started, video_helpers.OFFER_SECONDS)

    def test_an_upload_survives_a_hub_restart(self) -> None:
        chunk = 4 * 1024 * 1024
        payload = b"a" * chunk + b"b" * (3 * 1024 * 1024)
        started = self.client.post(
            "/v1/recordings/uploads",
            headers=self.headers,
            json={"name": "clip.mp4", "size": len(payload)},
        )
        self.assertEqual(started.status_code, 200, started.text)
        upload_id = started.json()["uploadId"]
        first = self.client.put(
            f"/v1/recordings/uploads/{upload_id}",
            headers=self.headers,
            params={"offset": 0},
            content=payload[:chunk],
        )
        self.assertEqual(first.status_code, 200, first.text)
        with app_module._uploads_lock:
            dropped = list(app_module._uploads.values())
            app_module._uploads.clear()
        for item in dropped:
            app_module._close_upload(item)
        status = self.client.get(f"/v1/recordings/uploads/{upload_id}", headers=self.headers)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["offset"], chunk)
        self.assertEqual(status.json()["spans"], [[0, chunk]])
        self.assertFalse(status.json()["finished"])
        second = self.client.put(
            f"/v1/recordings/uploads/{upload_id}",
            headers=self.headers,
            params={"offset": chunk},
            content=payload[chunk:],
        )
        self.assertEqual(second.status_code, 200, second.text)
        with app_module._uploads_lock:
            stored = Path(app_module._uploads[upload_id]["path"])
        self.assertEqual(stored.read_bytes(), payload)
        done = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(done.status_code, 200, done.text)
        job_id = done.json()["jobId"]
        again = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(again.json()["jobId"], job_id)
        self._wait_job(job_id)
        with jobs._lock:
            jobs._jobs.pop(job_id, None)
        lost = self.client.get(f"/v1/recordings/jobs/{job_id}", headers=self.headers)
        self.assertEqual(lost.status_code, 404)
        restarted = self.client.post(f"/v1/recordings/uploads/{upload_id}/finish", headers=self.headers)
        self.assertEqual(restarted.status_code, 200, restarted.text)
        self.assertNotEqual(restarted.json()["jobId"], job_id)
        body = self._wait_job(restarted.json()["jobId"])
        self.assertTrue(body.get("error"), body)

    def test_a_long_video_is_split_between_two_pcs(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required")
        folder = Path(tempfile.mkdtemp(prefix="fb-split-"))
        clip = folder / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=s=320x480:r=30:d=10",
                "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
            timeout=60,
        )
        previous = app_module._SPLIT_MIN_SECONDS
        app_module._SPLIT_MIN_SECONDS = 2.0
        try:
            workers = []
            for name in ("pc-mot", "pc-hai"):
                beat = self.client.post(
                    "/v1/video-workers/heartbeat",
                    headers=self.headers,
                    json={"name": name, "cpus": 16, "readerOk": True},
                )
                self.assertEqual(beat.status_code, 200, beat.text)
                workers.append(beat.json()["workerId"])
            opened = self.client.post(
                "/v1/recordings/from-video/job",
                headers=self.headers,
                files={"file": ("clip.mp4", clip.read_bytes(), "video/mp4")},
            )
            self.assertEqual(opened.status_code, 200, opened.text)
            parent_id = opened.json()["jobId"]

            def claim(worker_id: str, tries: int) -> str:
                for _ in range(tries):
                    response = self.client.post(
                        "/v1/recordings/jobs/claim",
                        headers=self.headers,
                        json={"workerId": worker_id},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    found = str(response.json()["jobId"])
                    if found:
                        return found
                    time.sleep(0.05)
                return ""

            first = claim(workers[0], 200)
            self.assertTrue(first)
            self.assertNotEqual(first, parent_id)
            self.assertEqual(claim(workers[0], 1), "")
            second = claim(workers[1], 200)
            self.assertTrue(second)
            self.assertNotIn(second, (first, parent_id))
            for worker_id, part_id in ((workers[0], first), (workers[1], second)):
                video = self.client.get(
                    f"/v1/recordings/jobs/{part_id}/video",
                    headers=self.headers,
                    params={"workerId": worker_id},
                )
                self.assertEqual(video.status_code, 200, video.text)
                self.assertEqual(video.content[4:8], b"ftyp")
            running = self.client.get(f"/v1/recordings/jobs/{parent_id}", headers=self.headers).json()
            self.assertIn("Hai PC cùng đọc", str(running.get("task")), running)
            seen = (
                (workers[0], first, {"kind": "contact", "name": "Mai Lan Chia", "contactName": "Chị Mai Chia", "username": ""}),
                (workers[1], second, {"kind": "profile", "name": "Mai Lan Chia", "contactName": "", "username": "@mai.lan.chia"}),
            )
            for worker_id, part_id, sighting in seen:
                noted = self.client.post(
                    f"/v1/recordings/jobs/{part_id}/checkpoint",
                    headers=self.headers,
                    json={"workerId": worker_id, "frames": [{"t": 1.0, "captions": [], "sightings": [sighting]}]},
                )
                self.assertEqual(noted.status_code, 200, noted.text)
            for worker_id, part_id, _sighting in seen:
                finished = self.client.post(
                    f"/v1/recordings/jobs/{part_id}/complete",
                    headers=self.headers,
                    json={"workerId": worker_id, "people": [], "wordSeen": 40, "wordKept": 30},
                )
                self.assertEqual(finished.status_code, 200, finished.text)
            body = self._wait_job(parent_id)
        finally:
            app_module._SPLIT_MIN_SECONDS = previous
        self.assertEqual(body.get("error"), "", body)
        self.assertEqual(body.get("savedPeople"), 1, body)
        self.assertEqual(body.get("wordSeen"), 80)
        rows = body.get("people") or []
        self.assertTrue(
            any(row.get("username") == "@mai.lan.chia" and row.get("contactName") == "Chị Mai Chia" for row in rows),
            rows,
        )
        with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
            conn.execute("DELETE FROM saved_people WHERE username = ?", ("@mai.lan.chia",))
            conn.execute("DELETE FROM people_meta")

    def test_fast_models_are_served_to_pcs(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="fb-fast-"))
        (folder / "vie.traineddata").write_bytes(b"vie-model")
        previous = os.environ.get("CONTROL_TESSDATA_FAST")
        os.environ["CONTROL_TESSDATA_FAST"] = str(folder)
        try:
            self.assertEqual(self.client.get("/v1/updates/tessdata/vie").status_code, 401)
            served = self.client.get("/v1/updates/tessdata/vie", headers=self.headers)
            self.assertEqual(served.status_code, 200, served.text)
            self.assertEqual(served.content, b"vie-model")
            self.assertEqual(self.client.get("/v1/updates/tessdata/osd", headers=self.headers).status_code, 404)
        finally:
            if previous is None:
                os.environ.pop("CONTROL_TESSDATA_FAST", None)
            else:
                os.environ["CONTROL_TESSDATA_FAST"] = previous

    def test_people_store_pages_and_stops_at_fifty_million(self) -> None:
        total, ready = people_db.people_counts(Path(os.environ["CONTROL_DB"]))
        previous = people_db.PEOPLE_CAPACITY
        people_db.PEOPLE_CAPACITY = total + 2
        names = (
            ("Một A", "Danh Một", "@mot.aa"),
            ("Hai B", "Danh Hai", "@hai.bb"),
            ("Ba C", "Danh Ba", "@ba.cc"),
        )
        try:
            for name, contact, username in names:
                saved = self.client.post(
                    "/v1/people/confirm",
                    headers=self.headers,
                    json={"rows": [{"name": name, "contactName": contact, "username": username}]},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
            listed = self.client.get("/v1/people", headers=self.headers, params={"limit": 1})
            self.assertEqual(listed.status_code, 200, listed.text)
            body = listed.json()
            self.assertEqual(body["count"], ready + 2)
            self.assertEqual(body["capacity"], total + 2)
            self.assertEqual(len(body["items"]), 1)
            seen = {item["username"] for item in body["items"]}
            cursor = body["cursor"]
            self.assertTrue(cursor)
            for _ in range(20):
                if not cursor:
                    break
                page = self.client.get(
                    "/v1/people",
                    headers=self.headers,
                    params={"limit": 1, "cursor": cursor},
                )
                self.assertEqual(page.status_code, 200, page.text)
                seen.update(item["username"] for item in page.json()["items"])
                cursor = page.json()["cursor"]
            self.assertIn("@mot.aa", seen)
            self.assertIn("@hai.bb", seen)
            self.assertNotIn("@ba.cc", seen)
        finally:
            people_db.PEOPLE_CAPACITY = previous
            with sqlite3.connect(os.environ["CONTROL_DB"]) as conn:
                conn.execute(
                    "DELETE FROM saved_people WHERE username IN ('@mot.aa', '@hai.bb', '@ba.cc')"
                )
                conn.execute("DELETE FROM people_meta")


    def test_pc_without_text_makes_the_hub_reread(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-trong", "cpus": 4},
        )
        self.assertEqual(beat.status_code, 200, beat.text)
        worker_id = beat.json()["workerId"]
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
        )
        job_id = opened.json()["jobId"]
        claimed_id = ""
        for _ in range(40):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, job_id)
        time.sleep(0.3)
        done = self.client.post(
            f"/v1/recordings/jobs/{job_id}/complete",
            headers=self.headers,
            json={
                "workerId": worker_id,
                "people": [],
                "noText": True,
                "wordSeen": 0,
                "wordKept": 0,
                "seenContacts": 0,
                "seenAccounts": 0,
                "readSaved": 0,
            },
        )
        self.assertEqual(done.status_code, 200, done.text)
        waiting = done.json()
        self.assertFalse(waiting.get("done"), waiting)
        problems = waiting.get("problems")
        self.assertIsInstance(problems, list)
        self.assertTrue(any("Máy chủ đọc lại" in str(item) for item in problems), problems)
        self.assertEqual(waiting.get("wordSeen"), 0)
        self.assertEqual(waiting.get("wordKept"), 0)
        body = self._wait_job(job_id)
        self.assertTrue(body.get("error"), body)
        again = body.get("problems")
        self.assertIsInstance(again, list)
        self.assertTrue(any("Máy chủ đọc lại" in str(item) for item in again), again)

    def test_sample_frames_need_the_token(self) -> None:
        beat = self.client.post(
            "/v1/video-workers/heartbeat",
            headers=self.headers,
            json={"name": "pc-khung", "cpus": 4},
        )
        worker_id = beat.json()["workerId"]
        opened = self.client.post(
            "/v1/recordings/from-video/job",
            headers=self.headers,
            files={"file": ("clip.mp4", b"video-khung", "video/mp4")},
        )
        job_id = opened.json()["jobId"]
        claimed_id = ""
        for _ in range(40):
            claimed = self.client.post(
                "/v1/recordings/jobs/claim",
                headers=self.headers,
                json={"workerId": worker_id},
            )
            claimed_id = claimed.json()["jobId"]
            if claimed_id:
                break
            time.sleep(0.05)
        self.assertEqual(claimed_id, job_id)
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="JPEG")
        raw = buf.getvalue()
        posted = self.client.post(
            f"/v1/recordings/jobs/{job_id}/samples",
            headers=self.headers,
            json={"workerId": worker_id, "images": [base64.b64encode(raw).decode("ascii")]},
        )
        self.assertEqual(posted.status_code, 200, posted.text)
        denied = self.client.get(f"/v1/recordings/jobs/{job_id}/samples/0")
        self.assertEqual(denied.status_code, 401)
        image = self.client.get(f"/v1/recordings/jobs/{job_id}/samples/0", headers=self.headers)
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.content, raw)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        listed = self.client.get(f"/v1/recordings/jobs/{job_id}", headers=self.headers)
        samples = listed.json().get("samples")
        self.assertEqual(samples, [{"index": 0, "source": "pc"}])
        closed = self.client.post(
            f"/v1/recordings/jobs/{job_id}/fail",
            headers=self.headers,
            json={"workerId": worker_id, "error": "Không đọc được video."},
        )
        self.assertEqual(closed.status_code, 200, closed.text)

    def test_video_worker_package_is_separate_from_the_comment_agent(self) -> None:
        dummy = Path(os.environ["CONTROL_PACKAGES_DIR"]) / "comment-agent.zip"
        dummy.parent.mkdir(parents=True, exist_ok=True)
        dummy.write_bytes(b"PK\x03\x04comment")
        denied = self.client.get("/v1/updates/video-worker/manifest")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(self.client.get("/v1/updates/video-worker.zip").status_code, 401)

        manifest = self.client.get("/v1/updates/manifest", headers=self.headers)
        self.assertEqual(manifest.status_code, 200, manifest.text)
        body = manifest.json()
        self.assertIn("comment-agent.zip", body["agent"]["package_url"])
        worker = body["video_worker"]
        self.assertEqual(worker["version"], "11")
        self.assertEqual(worker["package_url"], "/v1/updates/video-worker.zip")
        self.assertEqual(worker["engine"], "cpu")
        self.assertEqual(len(worker["sha256"]), 64)

        info = self.client.get("/v1/updates/video-worker/manifest", headers=self.headers)
        self.assertEqual(info.status_code, 200, info.text)
        self.assertEqual(info.json(), worker)
        downloaded = self.client.get("/v1/updates/video-worker.zip", headers=self.headers)
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), worker["sha256"])
        self.assertTrue(downloaded.content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            names = archive.namelist()
            self.assertIn("pc_agent/video_worker.py", names)
            self.assertIn("pc_agent/video_watchdog.py", names)
            self.assertIn("pc_agent/windows/Run-VideoWorker.ps1", names)
            self.assertIn("control_plane/screen_steps.py", names)
            self.assertIn("control_plane/version.py", names)
            self.assertEqual(archive.read("requirements-cpu.txt").decode("utf-8").strip(), "pillow")
            self.assertEqual(archive.read("VERSION").decode("utf-8").strip(), "11")
            self.assertIn("pc_agent/windows/Open-FbPoller.ps1", names)
            guide = archive.read("HUONG-DAN.txt").decode("utf-8")
            self.assertNotIn("test-token", guide)
            self.assertNotIn(".db", " ".join(names))

        installer = self.client.get("/cai-video.ps1")
        self.assertEqual(installer.status_code, 200, installer.text)
        script = installer.text
        self.assertIn("python-3.12.10-amd64.exe", script)
        self.assertIn("ffmpeg-release-essentials.zip", script)
        self.assertIn("tesseract-ocr-w64-setup-5.4.0.20240606.exe", script)
        self.assertNotIn("winget", script.lower())
        self.assertIn("FbPollerVideoWorker", script)
        self.assertIn("schtasks /Delete", script)
        self.assertNotIn("schtasks /Run", script)
        self.assertIn("import PIL", script)
        self.assertIn("FbPollerVideoUpdate", script)
        self.assertIn("RestartCount", script)
        self.assertIn("PSScriptRoot", script)
        self.assertIn("watchdog-replaced", script)
        self.assertIn("222.255.214.202:8088", script)
        self.assertIn("tessdata_fast", script)
        self.assertIn("/v1/updates/tessdata/", script)
        for banned in ("chromium", "playwright", "easyocr", "paddle"):
            self.assertNotIn(banned, script.lower())
        self.assertNotIn("test-token", script)
        runner = self.client.get("/cai-video-run.ps1")
        self.assertEqual(runner.status_code, 200)
        self.assertIn("FB_VIDEO_STATE", runner.text)
        self.assertIn("tessdata-fast", runner.text)
        self.assertIn("PYTHONUTF8", runner.text)
        self.assertIn("TESSDATA_PREFIX", runner.text)
        self.assertIn("tools\\ffmpeg", runner.text)
        self.assertIn("-u", runner.text)
        self.assertIn("ForEach-Object", runner.text)
        self.assertNotIn("Tee-Object", runner.text)
        opened = self.client.get("/cai-video-open.ps1")
        self.assertEqual(opened.status_code, 200)
        self.assertIn("Run-VideoWorker.ps1", opened.text)
        self.assertIn("Startup", opened.text)
        self.assertIn("Mutex", opened.text)
        self.assertIn("WaitOne", opened.text)
        self.assertNotIn("test-token", opened.text)
        self.assertNotIn("set /p", opened.text.lower())
        watchdog = self.client.get("/cai-video-watchdog.py")
        self.assertEqual(watchdog.status_code, 200)
        self.assertIn("upgrade_allowed", watchdog.text)
        self.assertNotIn("test-token", watchdog.text)

        page = self.client.get("/tai-pc")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("/tai-pc/FbPoller.bat", page.text)
        self.assertIn("FbPoller.bat", page.text)
        self.assertIn("tự cập nhật", page.text)
        self.assertIn("Không cần nhập token", page.text)
        self.assertIn("Để cửa sổ mở", page.text)
        self.assertNotIn("test-token", page.text)
        downloaded_bat = self.client.get("/tai-pc/FbPoller.bat")
        self.assertEqual(downloaded_bat.status_code, 200, downloaded_bat.text)
        self.assertEqual(downloaded_bat.headers["cache-control"], "no-cache")
        self.assertIn("FbPoller.bat", downloaded_bat.headers["content-disposition"])
        self.assertIn("test-token", downloaded_bat.text)
        self.assertIn("CONTROL_HUB", downloaded_bat.text)
        self.assertNotIn("set /p", downloaded_bat.text.lower())
        self.assertNotIn("__CONTROL_TOKEN__", downloaded_bat.text)
        downloaded_setup = self.client.get("/tai-pc/FbPollerVideo.zip")
        self.assertEqual(downloaded_setup.status_code, 200, downloaded_setup.text)
        self.assertEqual(downloaded_setup.headers["cache-control"], "no-cache")
        self.assertIn("FbPollerVideo.zip", downloaded_setup.headers["content-disposition"])
        self.assertTrue(downloaded_setup.content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(downloaded_setup.content)) as archive:
            setup_names = archive.namelist()
            self.assertIn("Cai-dat.bat", setup_names)
            self.assertIn("FbPoller.bat", setup_names)
            self.assertIn("Open-FbPoller.ps1", setup_names)
            self.assertIn("Install-VideoWorker.ps1", setup_names)
            self.assertIn("Run-VideoWorker.ps1", setup_names)
            self.assertIn("video_watchdog.py", setup_names)
            config = json.loads(archive.read("config.json").decode("utf-8"))
            self.assertEqual(config["token"], "test-token")
            self.assertTrue(str(config["hub"]).startswith("http"))
            launcher = archive.read("FbPoller.bat").decode("utf-8")
            self.assertIn("test-token", launcher)
            self.assertNotIn("set /p", launcher.lower())
            self.assertNotIn("__CONTROL_TOKEN__", launcher)
            bat = archive.read("Cai-dat.bat").decode("utf-8").lower()
            self.assertNotIn("set /p", bat)
            self.assertNotIn("test-token", bat)
            self.assertIn("upgrade_allowed", archive.read("video_watchdog.py").decode("utf-8"))
            self.assertNotIn("winget", archive.read("Install-VideoWorker.ps1").decode("utf-8").lower())


if __name__ == "__main__":
    unittest.main()
