"""HTTP API for the operator action journal."""

from __future__ import annotations

import io
import os
import sqlite3
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

from control_plane import db as people_db  # noqa: E402
from control_plane import video_helpers  # noqa: E402
from control_plane.app import app  # noqa: E402
from control_plane.settings import settings  # noqa: E402
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
        self.assertIn('id="dupTable"', page.text)
        self.assertIn('id="dupMore"', page.text)
        self.assertIn("/v1/people/duplicates", page.text)
        self.assertIn("/v1/people", page.text)
        self.assertIn("multiple", page.text)
        self.assertIn("nhiều video", page.text)
        self.assertIn("Không giới hạn số video, dung lượng hay thời lượng", page.text)
        self.assertIn("mọi lõi của PC", page.text)
        self.assertIn("Mỗi máy đọc một video", page.text)
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
        self.assertEqual(build, "29")
        self.assertEqual(body["videoHelper"]["connected"], False)
        self.assertEqual(body["videoHelper"]["cpus"], 0)
        self.assertEqual(body["videoHelper"]["count"], 0)
        self.assertEqual(body["videoHelper"]["cores"], 0)
        self.assertEqual(body["videoHelper"]["gpu"], 0)

        info = self.client.get("/v1/delivery")
        self.assertEqual(info.status_code, 200, info.text)
        payload = info.json()
        self.assertEqual(payload["iphoneBuild"], 29)
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
            json={"name": "pc-nha", "cpus": 16},
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


if __name__ == "__main__":
    unittest.main()
