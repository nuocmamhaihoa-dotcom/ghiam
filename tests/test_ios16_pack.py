"""iOS 16: một file nền, đọc lại khi vùng đổi, điểm tin cậy, sửa dòng, xuất danh bạ, Vision sidecar."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from control_plane.screen_read import Box, Line, Word, _ProfileBits, _line_confidence
from control_plane.screen_table import (
    REASON_LOW_CONF,
    ContactHit,
    FrameObs,
    Row,
    Table,
    build_table,
)
from control_plane.video_scan import _collapse_similar_frames, _frame_dhash, _same_screen
from control_plane.video_store import (
    BUCKET_OK,
    BUCKET_REVIEW,
    _reclassify_results,
    classify_result,
    connect,
    correct_result,
    export_contact_rows,
    finish,
    init_db,
    render_csv,
    render_vcf,
    search_results,
)
from control_plane.video_upload_sessions import (
    TailOffsetError,
    _chunks_covered,
    contiguous_offset,
    get_upload,
    init_upload,
    note_tail,
    open_tail,
    put_chunk,
    pwrite_at,
    relocate_vision_sidecar,
    save_vision_payload,
)
from control_plane.vision_sidecar import attach_vision, frames_from_payload


class RegionHashTests(unittest.TestCase):
    def test_status_bar_does_not_force_a_reread_but_at_zone_and_column_do(self) -> None:
        folder = Path(tempfile.mkdtemp())
        base = Image.new("L", (200, 400), 255)
        clock = base.copy()
        clock.paste(0, (0, 0, 200, 20))
        at_zone = base.copy()
        at_zone.paste(0, (10, 40, 160, 80))
        column = base.copy()
        column.paste(0, (50, 200, 120, 280))
        paths = []
        for name, image in (("base", base), ("clock", clock), ("at", at_zone), ("col", column)):
            path = folder / f"{name}.png"
            image.save(path)
            paths.append(path)
        hashes = [_frame_dhash(str(path)) for path in paths]
        self.assertTrue(all(item is not None for item in hashes))
        self.assertTrue(_same_screen(hashes[0], hashes[1]))
        self.assertFalse(_same_screen(hashes[0], hashes[2]))
        self.assertFalse(_same_screen(hashes[0], hashes[3]))

        targets = [
            (0, str(paths[0]), 0.0),
            (1, str(paths[1]), 0.5),
            (2, str(paths[2]), 1.0),
            (3, str(paths[0]), 3.5),
        ]
        unique, copies = _collapse_similar_frames(targets)
        self.assertEqual([item[0] for item in unique], [0, 2, 3])
        self.assertEqual(copies, [(1, 0, 0.5)])


class ConfidenceTests(unittest.TestCase):
    def test_measured_low_score_is_review_unmeasured_stays_complete(self) -> None:
        self.assertEqual(
            classify_result("0901234567", "Lan Anh", "@lananh.1", "", handle_confidence=-1),
            (BUCKET_OK, ""),
        )
        self.assertEqual(
            classify_result("0901234567", "Lan Anh", "@lananh.1", "", handle_confidence=80),
            (BUCKET_OK, ""),
        )
        self.assertEqual(
            classify_result("0901234567", "Lan Anh", "@lananh.1", "", handle_confidence=59),
            (BUCKET_REVIEW, REASON_LOW_CONF),
        )
        self.assertEqual(
            classify_result("0901234567", "Lan Anh", "@lananh.1", REASON_LOW_CONF),
            (BUCKET_REVIEW, REASON_LOW_CONF),
        )

    def test_line_confidence_reaches_the_profile_observation(self) -> None:
        line = Line(
            [
                Word("@lan", 40.0, Box(0, 0, 10, 10)),
                Word(".ok", 80.0, Box(12, 0, 30, 10)),
            ]
        )
        self.assertEqual(_line_confidence(line), 60.0)
        obs = _ProfileBits("Lan Anh", "@lananh.1", confidence=40.0).as_obs()
        self.assertIsNotNone(obs)
        assert obs is not None
        self.assertEqual(obs.handle_confidence, 40.0)

    def test_build_table_keeps_the_lowest_measured_handle_score(self) -> None:
        low = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Lan Anh"),), at=1.0),
                FrameObs("profile", (), "Lan Anh", "@lananh.1", at=1.4, handle_confidence=80),
                FrameObs("profile", (), "Lan Anh", "@lananh.1", at=1.6, handle_confidence=55),
            ]
        )
        self.assertEqual(len(low.rows), 1)
        self.assertEqual(low.rows[0].handle_confidence, 55)
        plain = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Lan Anh"),), at=1.0),
                FrameObs("profile", (), "Lan Anh", "@lananh.1", at=1.4),
            ]
        )
        self.assertEqual(plain.rows[0].handle_confidence, -1)

    def test_reclassify_does_not_promote_a_low_score_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "video.db"
            init_db(db)
            finish(db, _video(db), Table(rows=[Row("0901234567", "Lan Anh", "@lananh.1", 40.0)]))
            found = search_results(db, view="all", limit=10)
            self.assertEqual(found[0]["bucket"], "Cần xem")
            self.assertEqual(found[0]["reason"], REASON_LOW_CONF)
            with connect(db) as conn:
                _reclassify_results(conn)
            again = search_results(db, view="all", limit=10)
            self.assertEqual(again[0]["bucket"], "Cần xem")
            self.assertEqual(again[0]["reason"], REASON_LOW_CONF)


class EditAndExportTests(unittest.TestCase):
    def test_human_confirm_becomes_complete_and_duplicate_phone_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "video.db"
            init_db(db)
            finish(
                db,
                _video(db),
                Table(
                    rows=[
                        Row("0901234567", "Lan Anh", "@lananh.1", 40.0),
                        Row("0901234568", "Minh Tam", "@minhtam.2"),
                    ]
                ),
            )
            rows = {item["phone"]: item for item in search_results(db, view="all", limit=10)}
            fixed = correct_result(db, int(rows["0901234567"]["id"]), "0901234567", "Lan Anh", "@lananh.1")
            self.assertEqual(fixed["bucket"], "Đủ")
            self.assertEqual(fixed["reason"], "")
            with self.assertRaises(ValueError):
                correct_result(db, int(rows["0901234567"]["id"]), "0901234568", "Lan Anh", "@lananh.1")
            with self.assertRaises(ValueError):
                correct_result(db, int(rows["0901234568"]["id"]), "123", "Minh Tam", "@minhtam.2")

    def test_export_is_one_card_per_phone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "video.db"
            init_db(db)
            finish(
                db,
                _video(db),
                Table(
                    rows=[
                        Row("0901234568", "Minh Tam", "@minhtam.2"),
                        Row("0901234567", "Lan Anh", "@lananh.1"),
                    ]
                ),
            )
            exported = export_contact_rows(db)
            self.assertEqual([item[0] for item in exported], ["0901234567", "0901234568"])
            vcf = render_vcf(exported)
            self.assertEqual(vcf.count("BEGIN:VCARD"), 2)
            self.assertIn("TEL;TYPE=CELL:0901234567", vcf)
            self.assertIn("NOTE:@lananh.1", vcf)
            csv_text = render_csv(exported)
            self.assertTrue(csv_text.startswith("phone,name,username\n"))
            self.assertEqual(csv_text.count("0901234567"), 1)


class TailUploadTests(unittest.TestCase):
    def test_contiguous_offset_stops_at_the_first_hole(self) -> None:
        self.assertEqual(contiguous_offset([0, 1], 10, 25), 20)
        self.assertEqual(contiguous_offset([1], 10, 25), 0)
        self.assertEqual(contiguous_offset([0, 1, 2], 10, 25), 25)
        self.assertEqual(_chunks_covered(0, 15, 10, 25), [0])
        self.assertEqual(_chunks_covered(0, 25, 10, 25), [0, 1, 2])

    def test_tail_marks_only_chunks_fully_inside_the_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "video.db"
            uploads = root / "uploads.db"
            videos = root / "videos"
            videos.mkdir()
            init_db(db)
            session = init_upload(
                uploads,
                videos,
                name="clip.mp4",
                size_bytes=48 * 1024,
                client_key="ios16-tail",
                chunk_size=16 * 1024,
                disk_limit_bytes=80 * 1024 * 1024 * 1024,
                video_db_path=db,
            )
            upload_id = str(session["upload_id"])
            self.assertEqual(session["contiguous_offset"], 0)
            chunk = b"a" * (16 * 1024)
            put_chunk(uploads, upload_id, 0, chunk)
            body = get_upload(uploads, upload_id)
            assert body is not None
            self.assertEqual(body["contiguous_offset"], 16 * 1024)
            with self.assertRaises(TailOffsetError):
                open_tail(uploads, upload_id, 0)
            path = open_tail(uploads, upload_id, 16 * 1024)
            pwrite_at(path, 16 * 1024, b"b" * 100)
            partial = note_tail(uploads, upload_id, 16 * 1024, 100)
            self.assertEqual(partial["contiguous_offset"], 16 * 1024)
            self.assertNotIn(1, partial["received"])
            pwrite_at(path, 16 * 1024, chunk)
            done = note_tail(uploads, upload_id, 16 * 1024, 16 * 1024)
            self.assertEqual(done["contiguous_offset"], 32 * 1024)
            self.assertIn(1, done["received"])

    def test_vision_sidecar_moves_next_to_the_finished_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "video.db"
            uploads = root / "uploads.db"
            videos = root / "videos"
            videos.mkdir()
            init_db(db)
            session = init_upload(
                uploads,
                videos,
                name="clip.mp4",
                size_bytes=16 * 1024,
                client_key="ios16-vision",
                chunk_size=16 * 1024,
                disk_limit_bytes=80 * 1024 * 1024 * 1024,
                video_db_path=db,
            )
            upload_id = str(session["upload_id"])
            payload = json.dumps(
                {
                    "frames": [
                        {
                            "t": 1.5,
                            "lines": [
                                {"text": "Lan Anh", "conf": 90},
                                {"text": "@lananh.1", "conf": 40},
                            ],
                        }
                    ]
                }
            ).encode()
            saved = save_vision_payload(uploads, upload_id, payload)
            self.assertEqual(saved["frames"], 1)
            body = get_upload(uploads, upload_id)
            assert body is not None
            partial = Path(str(body.get("upload_id")))
            # Path lives beside the partial, not inside the session dict.
            from control_plane.video_upload_sessions import uploads_connect

            with uploads_connect(uploads) as conn:
                row = conn.execute("SELECT path FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
            sidecar = Path(str(row["path"])).with_name(f"{upload_id}.vision.json")
            self.assertTrue(sidecar.is_file())
            final = videos / "9.mp4"
            final.write_bytes(b"video")
            relocate_vision_sidecar(sidecar, final)
            moved = videos / "9.vision.json"
            self.assertTrue(moved.is_file())
            self.assertFalse(sidecar.exists())
            _ = partial


class VisionSidecarTests(unittest.TestCase):
    def test_payload_becomes_a_profile_frame_and_low_score_is_not_complete(self) -> None:
        frames = frames_from_payload(
            {
                "frames": [
                    {
                        "t": 1.5,
                        "lines": [
                            {"text": "Lan Anh", "conf": 91},
                            {"text": "@lananh.1", "conf": 40},
                        ],
                    },
                    {"t": 2.0, "lines": [{"text": "@onlyhandle", "conf": 99}]},
                ]
            }
        )
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].kind, "profile")
        self.assertEqual(frames[0].profile_username, "@lananh.1")
        self.assertEqual(frames[0].handle_confidence, 40)
        self.assertEqual(frames[0].at, 1.5)
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "9.mp4"
            video.write_bytes(b"not-a-real-video")
            video.with_suffix(".vision.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "t": 1.5,
                                "lines": [
                                    {"text": "Lan Anh", "conf": 90},
                                    {"text": "@lananh.1", "conf": 40},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ordered = attach_vision([], [video])
            self.assertEqual(len(ordered), 1)
            self.assertEqual(ordered[0][1].profile_username, "@lananh.1")
            db = Path(tmp) / "video.db"
            init_db(db)
            table = build_table(
                [
                    FrameObs("list", (ContactHit("0982117072", "Lan Anh"),), at=1.0),
                    ordered[0][1],
                ]
            )
            self.assertEqual(table.rows[0].handle_confidence, 40)
            finish(db, _video(db), table)
            saved = search_results(db, view="all", limit=5)[0]
            self.assertEqual(saved["bucket"], "Cần xem")
            self.assertEqual(saved["reason"], REASON_LOW_CONF)


class ResultApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from control_plane.app import app
        from control_plane.settings import settings

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db = root / "video.db"
        self.uploads = root / "uploads.db"
        self.videos = root / "videos"
        self.videos.mkdir()
        init_db(self.db)
        self.settings = settings
        self._old = (
            settings.video_db_path,
            settings.video_uploads_db_path,
            settings.video_dir,
            settings.token,
            settings.page_token,
        )
        settings.video_db_path = self.db
        settings.video_uploads_db_path = self.uploads
        settings.video_dir = self.videos
        settings.token = "ios16-token"
        settings.page_token = "ios16-page"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        (
            self.settings.video_db_path,
            self.settings.video_uploads_db_path,
            self.settings.video_dir,
            self.settings.token,
            self.settings.page_token,
        ) = self._old
        self._tmp.cleanup()

    def test_body_offset_mismatch_is_409_and_a_full_chunk_advances(self) -> None:
        session = init_upload(
            self.uploads,
            self.videos,
            name="clip.mp4",
            size_bytes=48 * 1024,
            client_key="api-tail",
            chunk_size=16 * 1024,
            disk_limit_bytes=80 * 1024 * 1024 * 1024,
            video_db_path=self.db,
        )
        upload_id = str(session["upload_id"])
        headers = {"Authorization": "Bearer ios16-page"}
        put_chunk(self.uploads, upload_id, 0, b"a" * (16 * 1024))
        mismatch = self.client.put(
            f"/v1/videos/uploads/{upload_id}/body",
            content=b"b" * 32,
            headers={**headers, "X-Upload-Offset": "0"},
        )
        self.assertEqual(mismatch.status_code, 409)
        chunk = b"c" * (16 * 1024)
        ok = self.client.put(
            f"/v1/videos/uploads/{upload_id}/body",
            content=chunk,
            headers={**headers, "X-Upload-Offset": str(16 * 1024)},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["contiguous_offset"], 32 * 1024)
        vision = self.client.post(
            f"/v1/videos/uploads/{upload_id}/vision",
            content=json.dumps({"frames": [{"t": 1.0, "lines": [{"text": "Lan Anh", "conf": 90}]}]}),
            headers={**headers, "Content-Type": "application/json"},
        )
        self.assertEqual(vision.status_code, 200, vision.text)

    def test_patch_and_export_use_the_page_token(self) -> None:
        finish(
            self.db,
            _video(self.db),
            Table(rows=[Row("0901234567", "Lan Anh", "@lananh.1", 40.0)]),
        )
        item = search_results(self.db, view="all", limit=5)[0]
        headers = {"Authorization": "Bearer ios16-page"}
        patched = self.client.patch(
            f"/v1/results/{item['id']}",
            json={"phone": "0901234567", "name": "Lan Anh", "username": "@lananh.1"},
            headers=headers,
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["bucket"], "Đủ")
        csv_body = self.client.get("/v1/results/export.csv", headers=headers)
        self.assertEqual(csv_body.status_code, 200)
        self.assertIn("0901234567", csv_body.text)
        vcf = self.client.get("/v1/results/export.vcf", headers=headers)
        self.assertEqual(vcf.status_code, 200)
        self.assertIn("BEGIN:VCARD", vcf.text)


class ClientContractTests(unittest.TestCase):
    def test_web_and_ios16_strings_stay_in_place(self) -> None:
        page = Path("control_plane/static/video.html").read_text(encoding="utf-8")
        self.assertIn("PHONE_PARALLEL = 2", page)
        self.assertIn("Safari khỏi bị sập", page)
        self.assertIn("mất kết nối, giữ trong hàng", page)
        self.assertIn("RESULT_PAGE = 80", page)
        self.assertIn("Không đoán chữ dưới chấm. Khung cắt không còn sau khi đọc.", page)
        self.assertIn("/v1/results/export.vcf", page)
        self.assertIn("data-edit", page)
        root = Path("ios/VideoUp/VideoUp/RootView.swift").read_text(encoding="utf-8")
        self.assertNotIn("{ _,", root)
        self.assertIn("iOS 16", root)
        project = Path("ios/VideoUp/VideoUp.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
        self.assertNotIn("IPHONEOS_DEPLOYMENT_TARGET = 17.0;", project)
        self.assertIn("IPHONEOS_DEPLOYMENT_TARGET = 16.0;", project)
        queue = Path("ios/VideoUp/VideoUp/UploadQueue.swift").read_text(encoding="utf-8")
        self.assertIn("X-Upload-Offset", queue)
        self.assertIn("writeTailFile", queue)
        self.assertNotIn("uploadTask(withResumeData:", queue)
        sidecar = Path("ios/VideoUp/VideoUp/VisionSidecar.swift").read_text(encoding="utf-8")
        self.assertIn("vi-VN", sidecar)
        self.assertIn("en-US", sidecar)
        self.assertIn("VisionSidecar.swift", project)


def _video(db: Path) -> int:
    from control_plane.video_store import begin_upload, commit_upload

    created = begin_upload(db, name="clip.mp4", size_bytes=8, sha256=f"seed-{db}")
    video_id = int(created["id"])
    path = db.parent / f"{video_id}.mp4"
    path.write_bytes(b"12345678")
    commit_upload(db, video_id, str(path))
    return video_id


if __name__ == "__main__":
    unittest.main()
