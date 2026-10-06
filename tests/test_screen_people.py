"""Ghép danh bạ và hồ sơ đọc từ vị trí chữ trên khung hình."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane import screen_people as people
from control_plane.people import clean_username, fold_name
from control_plane.read_vote import RowMemo, clear_memos
from control_plane.screen_people import (
    TextLine,
    _line_box,
    _strip_button,
    _tesseract_command,
    choose_tsv,
    handle_from_tsv,
    lines_from_tsv,
    locate_tesseract,
    name_min_conf,
    prepare_tesseract,
    propose_rows,
    reading_counts,
    sightings_from_image,
    sightings_from_lines,
    tsv_word_counts,
)


def _line(text: str, top: int, height: int = 28, left: int = 70) -> TextLine:
    return TextLine(text, left, top, top + height)


class ScreenPeopleTests(unittest.TestCase):
    def test_keeps_words_from_confidence_30(self) -> None:
        rows = [
            "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t30\tHong",
            "5\t1\t1\t1\t1\t2\t60\t10\t40\t20\t29\tBo",
            "5\t1\t1\t1\t1\t3\t110\t10\t40\t20\t40\tNam",
            "5\t1\t1\t1\t1\t4\t160\t10\t40\t20\t-1\tNope",
        ]
        tsv = "\n".join(rows)
        self.assertEqual(tsv_word_counts(tsv), (3, 2))
        self.assertEqual([line.text for line in lines_from_tsv(tsv)], ["Hong Nam"])
        kept = rows[0]
        low = rows[1]
        cli = "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t90\tNam"
        self.assertEqual(choose_tsv(kept, cli), kept)
        self.assertEqual(choose_tsv(low, cli), cli)
        self.assertEqual(choose_tsv(None, cli), cli)
        self.assertEqual(choose_tsv("", ""), "")
        self.assertEqual(choose_tsv(None, ""), "")

    def test_handle_tsv_keeps_only_sure_accounts(self) -> None:
        strong = "\n".join(
            [
                "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t85\t@trn.tng751",
            ]
        )
        weak = "\n".join(
            [
                "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t40\t@trn.tng751",
            ]
        )
        junk = "\n".join(
            [
                "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t90\t@trn!tng",
            ]
        )
        self.assertEqual(handle_from_tsv(strong)[0], "@trn.tng751")
        self.assertEqual(handle_from_tsv(weak)[0], "")
        self.assertEqual(handle_from_tsv(junk)[0], "")
        mixed = "\n".join(
            [
                "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t80\tTrần",
                "5\t1\t1\t1\t1\t2\t60\t10\t40\t20\t40\tTùng",
            ]
        )
        self.assertEqual(name_min_conf(mixed, "Trần Tùng"), 40.0)
        self.assertIsNone(name_min_conf(mixed, "Bà soi"))

    def test_pairs_contact_rows_and_profiles_into_three_columns(self) -> None:
        contacts = sightings_from_lines(
            [
                _line("Danh bạ", 40, 30),
                _line("Hong@1978", 100),
                _line("Dịu 93", 180),
                _line("user7457244303237", 214, 20),
                _line("A Tùng Bán Gạch", 300),
                _line("Trần Tùng", 334, 20),
                _line("Chị Soi Xuân Trung", 430),
                _line("Bà soi", 464, 20),
                _line("Follow", 310, 24, left=520),
            ]
        )
        profiles = sightings_from_lines(
            [
                _line("Trần Tùng", 80, 48),
                _line("@trn.tng751", 140, 28),
                _line("Đã follow", 210, 24),
            ]
        ) + sightings_from_lines(
            [
                _line("Bà soi", 80, 48),
                _line("@b.soi22", 140, 28),
            ]
        )
        rows = propose_rows(contacts + contacts + profiles + profiles)
        self.assertEqual(
            rows,
            [
                {"name": "Trần Tùng", "contactName": "A Tùng Bán Gạch", "username": "@trn.tng751"},
                {"name": "Bà soi", "contactName": "Chị Soi Xuân Trung", "username": "@b.soi22"},
            ],
        )

    def test_ocr_without_diacritics_keeps_the_spelled_name(self) -> None:
        seen = [
            {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
            {"kind": "profile", "name": "Tran Tung", "username": "@trn.tng751"},
        ]
        rows = propose_rows(seen + seen)
        self.assertEqual(rows[0]["name"], "Trần Tùng")
        self.assertEqual(rows[0]["username"], "@trn.tng751")

    def test_same_contact_name_keeps_the_spelling_with_marks(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Ba soi", "contactName": "Chi Soi Xuan Trung"},
                {"kind": "contact", "name": "Bà soi", "contactName": "Chị Soi Xuân Trung"},
                {"kind": "profile", "name": "Bà soi", "username": "@b.soi22"},
                {"kind": "profile", "name": "Bà soi", "username": "@b.soi22"},
            ]
        )
        self.assertEqual(rows[0]["name"], "Bà soi")
        self.assertEqual(rows[0]["contactName"], "Chị Soi Xuân Trung")

    def test_repeated_handle_wins_over_one_character_drift(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Dịu 93", "contactName": "user7457244303237"},
                {"kind": "contact", "name": "Dịu 93", "contactName": "user7457244303237"},
                {"kind": "profile", "name": "Dịu 93", "username": "@daodiu100693"},
                {"kind": "profile", "name": "Dịu 93", "username": "@daodiu100693"},
                {"kind": "profile", "name": "Diu 93", "username": "@daodiu10069s"},
            ]
        )
        self.assertEqual(rows[0]["username"], "@daodiu100693")
        self.assertEqual(rows[0]["name"], "Dịu 93")

    def test_two_different_handles_are_not_proposed(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Dịu 93", "contactName": "user7457244303237"},
                {"kind": "profile", "name": "Dịu 93", "username": "@daodiu100693"},
                {"kind": "profile", "name": "Dịu 93", "username": "@other.handle1"},
            ]
        )
        self.assertEqual(rows, [])

    def test_contact_seen_three_times_beats_one_different_name(self) -> None:
        contacts = [
            {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"}
            for _index in range(3)
        ]
        contacts.append({"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"})
        profile = {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"}
        rows = propose_rows(contacts + [profile, profile])
        self.assertEqual(rows[0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(rows[0]["username"], "@trn.tng751")

    def test_contact_seen_twice_beats_one_different_name(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
                {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
                {"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"},
                {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
            ]
        )
        self.assertEqual(rows[0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(rows[0]["username"], "@trn.tng751")

    def test_two_contact_names_are_not_proposed(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
                {"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"},
                {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
            ]
        )
        self.assertEqual(rows, [])

    def test_profile_screen_does_not_invent_a_contact(self) -> None:
        found = sightings_from_lines(
            [
                _line("Trần Tùng", 70, 52),
                _line("@trn.tng751", 140, 30),
                _line("238", 200, 28),
                _line("Đã follow", 240, 24),
                _line("Tin nhắn", 300, 28),
            ]
        )
        self.assertEqual(found, [{"kind": "profile", "name": "Trần Tùng", "contactName": "", "username": "@trn.tng751"}])
        self.assertEqual(propose_rows(found), [])

    def test_at_sign_and_sentence_are_not_names(self) -> None:
        found = sightings_from_lines(
            [
                _line("Hồng Hàn Xì Cơ Khí", 100),
                _line("Hong@1978", 128, 22),
                _line("Bạn Dũng Xin Việc", 220),
                _line("Tháo niềng thì đổi", 248, 22),
                _line("A Trình Xây Nhà", 360),
                _line("vịt", 388, 20),
            ]
        )
        self.assertEqual(
            found,
            [{"kind": "contact", "name": "vịt", "contactName": "A Trình Xây Nhà", "username": ""}],
        )

    def test_drawn_frames_match_the_shared_name(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is required")
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if not font_path.is_file():
            self.skipTest("font missing")
        font = ImageFont.truetype(str(font_path), 32)
        small = ImageFont.truetype(str(font_path), 26)

        def save(folder: Path, index: int, lines: list[tuple[str, int, ImageFont.ImageFont]]) -> Path:
            image = Image.new("RGB", (480, 900), "white")
            pen = ImageDraw.Draw(image)
            for text, top, face in lines:
                pen.text((36, top), text, font=face, fill="black")
            path = folder / f"{index}.png"
            image.save(path)
            return path

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            contacts = save(
                folder,
                1,
                [
                    ("Danh ba", 36, font),
                    ("A Tung Ban Gach", 140, font),
                    ("Tran Tung", 184, small),
                    ("Chi Soi Xuan Trung", 300, font),
                    ("Ba soi", 344, small),
                ],
            )
            profile_tung = save(folder, 2, [("Tran Tung", 80, font), ("@trn.tng751", 150, small)])
            profile_soi = save(folder, 3, [("Ba soi", 80, font), ("@b.soi22", 150, small)])
            seen = (
                sightings_from_image(contacts)
                + sightings_from_image(profile_tung)
                + sightings_from_image(profile_soi)
            )
            rows = propose_rows(seen + seen)
        # Bộ chữ chuẩn đôi khi đọc chấm trên chữ i thành dấu hỏi. Bài này kiểm cách ghép, nên so tên danh bạ theo chữ gốc.
        self.assertEqual(
            [(row["name"], fold_name(row["contactName"]), row["username"]) for row in rows],
            [
                ("Tran Tung", "a tung ban gach", "@trn.tng751"),
                ("Ba soi", "chi soi xuan trung", "@b.soi22"),
            ],
        )

    def test_every_consistent_person_is_kept(self) -> None:
        contacts = []
        profiles = []
        for index in range(201):
            name = f"Ten {index:04d}"
            contact = {"kind": "contact", "name": name, "contactName": f"Danh ba {index:04d}"}
            profile = {"kind": "profile", "name": name, "username": f"user{index:04d}x"}
            contacts.extend((contact, contact))
            profiles.extend((profile, profile))
        rows = propose_rows(contacts + profiles)
        self.assertEqual(len(rows), 201)
        self.assertEqual(rows[0]["username"], "@user0000x")
        self.assertEqual(rows[-1]["username"], "@user0200x")

    def test_one_reading_is_saved(self) -> None:
        once = [
            {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
            {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
        ]
        self.assertEqual(
            propose_rows(once),
            [{"name": "Trần Tùng", "contactName": "A Tùng Bán Gạch", "username": "@trn.tng751"}],
        )

    def test_reading_counts_separate_the_list_from_accounts(self) -> None:
        sightings = [
            {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
            {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
            {"kind": "contact", "name": "Bà soi", "contactName": "Chị Soi Xuân Trung"},
            {"kind": "profile", "name": "Dịu 93", "username": "@daodiu100693"},
            {"kind": "contact", "name": "Bấm nút ba lần để dừng", "contactName": "Cấu hình không hợp lệ"},
            {"kind": "profile", "name": "Bấm nút ba lần để dừng", "username": "@kol"},
        ]
        self.assertEqual(reading_counts(sightings), {"contacts": 2, "accounts": 2, "saved": 1})
        self.assertEqual(len(propose_rows(sightings)), 1)

    def test_instruction_text_and_short_handle_are_not_saved(self) -> None:
        found = sightings_from_lines(
            [
                _line("Bấm nút ba lần để dừng", 80),
                _line("Cấu hình không hợp lệ", 120),
                _line("@kol", 170),
            ]
        )
        self.assertEqual(found, [])
        self.assertEqual(clean_username("@kol"), "")
        junk = [
            {"kind": "contact", "name": "Bấm nút ba lần để dừng", "contactName": "Cấu hình không hợp lệ"},
            {"kind": "profile", "name": "Bấm nút ba lần để dừng", "username": "@kol"},
        ]
        self.assertEqual(propose_rows(junk + junk), [])

    def test_dung_is_a_name(self) -> None:
        pair = [
            {"kind": "contact", "name": "Dũng", "contactName": "Bạn Dũng Xin Việc"},
            {"kind": "profile", "name": "Dũng", "username": "@dung.ok1"},
        ]
        rows = propose_rows(pair + pair)
        self.assertEqual(rows[0]["name"], "Dũng")
        self.assertEqual(rows[0]["contactName"], "Bạn Dũng Xin Việc")
        self.assertEqual(rows[0]["username"], "@dung.ok1")

    def test_locate_tesseract_uses_the_install_folder(self) -> None:
        previous = {
            "CONTROL_TESSERACT": os.environ.get("CONTROL_TESSERACT"),
            "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX"),
            "PATH": os.environ.get("PATH"),
        }
        os.environ.pop("CONTROL_TESSERACT", None)
        os.environ.pop("TESSDATA_PREFIX", None)
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                exe = root / "Tesseract-OCR" / "tesseract.exe"
                exe.parent.mkdir()
                exe.write_bytes(b"MZ")
                data = root / "tessdata"
                data.mkdir()
                (data / "vie.traineddata").write_bytes(b"trained")
                found_exe, found_data = locate_tesseract([root])
                self.assertEqual(found_exe, exe)
                self.assertEqual(found_data, data)
                self.assertEqual(prepare_tesseract([root]), "")
                self.assertEqual(_tesseract_command(), str(exe))
                self.assertTrue(os.environ["TESSDATA_PREFIX"].rstrip("\\/").endswith("tessdata"))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _word(index: int, text: str, left: int, top: int, width: int, height: int, conf: int = 90) -> str:
    return f"5\t1\t1\t1\t1\t{index}\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"


class LineBoxTests(unittest.TestCase):
    def test_the_name_box_stops_before_the_follow_button_and_a_junk_blob(self) -> None:
        tsv = "\n".join(
            [
                _word(1, "Hoàng", 138, 300, 90, 34),
                _word(2, "Thanh", 238, 300, 92, 34),
                _word(3, "Tùng", 340, 300, 80, 34),
                _word(4, "ey", 540, 280, 140, 80),
                _word(5, "Follow", 560, 300, 110, 34),
            ]
        )
        line = TextLine("Hoàng Thanh Tùng", 138, 280, 360)
        left, top, width, height = _line_box(line, tsv, 720, "name")
        self.assertEqual(left, 138)
        self.assertEqual(left + width, 420)
        self.assertEqual((top, height), (300, 34))

    def test_the_name_box_drops_a_second_line_that_sits_close_below(self) -> None:
        tsv = "\n".join(
            [
                _word(1, "Lý", 138, 300, 40, 34),
                _word(2, "Gia", 190, 300, 60, 34),
                _word(3, "Hương", 262, 346, 100, 30),
            ]
        )
        line = TextLine("Lý Gia", 138, 300, 376)
        left, top, width, height = _line_box(line, tsv, 720, "name")
        self.assertEqual((left, top, height), (138, 300, 34))
        self.assertEqual(left + width, 250)

    def test_the_handle_box_starts_at_the_at_sign_and_names_leave_the_handle_out(self) -> None:
        tsv = "\n".join(
            [
                _word(1, "Ba", 138, 300, 50, 34),
                _word(2, "soi", 198, 300, 60, 34),
                _word(3, "@b.soi22", 300, 304, 190, 30),
            ]
        )
        line = TextLine("Ba soi @b.soi22", 138, 300, 334)
        handle = _line_box(line, tsv, 720, "handle")
        name = _line_box(line, tsv, 720, "name")
        self.assertEqual(handle[0], 300)
        self.assertEqual(handle[0] + handle[2], 490)
        self.assertEqual(name[0] + name[2], 258)

    def test_without_word_boxes_the_box_is_a_guess_from_the_line(self) -> None:
        line = TextLine("Lan Anh", 70, 120, 150)
        left, top, width, height = _line_box(line, "", 720, "name")
        self.assertEqual((left, top, height), (70, 120, 30))
        self.assertGreater(width, 48)

    def test_follow_with_punctuation_is_a_button(self) -> None:
        self.assertEqual(_strip_button("Hong Follow,"), "Hong")
        self.assertEqual(_strip_button("Lan Anh Thích."), "Lan Anh")
        self.assertEqual(_strip_button("Follow"), "")
        self.assertEqual(_strip_button("Tuấn Anh"), "Tuấn Anh")

    def test_a_loaded_picture_is_read_without_opening_the_file(self) -> None:
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if not font_path.is_file():
            self.skipTest("font missing")
        image = Image.new("L", (720, 200), 255)
        ImageDraw.Draw(image).text((40, 60), "Tran Tung", font=ImageFont.truetype(str(font_path), 42), fill=0)
        missing = Path("/nonexistent/frame.png")
        from control_plane.tesseract_keep import reader_mode

        if reader_mode() != "api":
            self.skipTest("in-process tesseract is unavailable")
        self.assertIn("Tran", people.read_frame_tsv(missing, image))


class VoteSpeedTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_memos()

    def tearDown(self) -> None:
        clear_memos()

    def test_vote_box_reuses_a_row_settled_in_three_frames(self) -> None:
        image = Image.new("RGB", (200, 80), "white")
        ImageDraw.Draw(image).rectangle((20, 20, 120, 50), fill="black")
        calls = {"n": 0}

        def fake_read(picture: Image.Image, dest: Path, kind: str) -> str:
            del picture, dest, kind
            calls["n"] += 1
            return "Lan Anh"

        memo = RowMemo()
        original = people._read_prepared
        people._read_prepared = fake_read
        try:
            for frame in ("f1", "f2", "f3"):
                text, agreed = people._vote_box(image, (10, 10, 140, 50), Path("x.png"), "name", "Lan Anh", memo, frame)
                self.assertTrue(agreed)
                self.assertEqual(text, "Lan Anh")
            self.assertEqual(calls["n"], 3)
            again, agreed = people._vote_box(image, (10, 10, 140, 50), Path("x.png"), "name", "Lan Anh", memo, "f4")
            self.assertTrue(agreed)
            self.assertEqual(again, "Lan Anh")
            self.assertEqual(calls["n"], 3)
            people._vote_box(image, (10, 10, 140, 50), Path("x.png"), "name", "Bình An", memo, "f5")
            self.assertGreater(calls["n"], 3)
        finally:
            people._read_prepared = original

    def test_a_line_that_never_agrees_is_read_in_every_frame(self) -> None:
        image = Image.new("RGB", (200, 80), "white")
        calls = {"n": 0}

        def fake_read(picture: Image.Image, dest: Path, kind: str) -> str:
            del picture, dest, kind
            calls["n"] += 1
            return "Khác hẳn"

        original_rapid = people.read_rapid
        memo = RowMemo()
        original = people._read_prepared
        people._read_prepared = fake_read
        people.read_rapid = lambda picture: "Lạ lùng"
        try:
            for frame in range(6):
                people._vote_box(image, (10, 10, 140, 50), Path("x.png"), "name", "An Nhiên", memo, f"f{frame}")
        finally:
            people._read_prepared = original
            people.read_rapid = original_rapid
        self.assertGreaterEqual(calls["n"], 6)

    def test_rewrite_votes_lines_together(self) -> None:
        dests: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(3) if people.reader_limit() >= 3 else None

        def fake_vote(
            image: Image.Image,
            box: tuple[int, int, int, int],
            dest: Path,
            kind: str,
            seed: str,
            memo: RowMemo | None = None,
            frame_id: str = "",
            on_settle: object = None,
        ) -> tuple[str, bool]:
            del image, box, kind, memo, frame_id, on_settle
            if start is not None:
                start.wait(timeout=2)
            with lock:
                dests.append(dest.name)
            return seed.strip(), True

        original = people._vote_box
        people._vote_box = fake_vote
        try:
            image = Image.new("RGB", (240, 160), "white")
            lines = [
                TextLine("An", 10, 10, 30),
                TextLine("Binh", 10, 50, 70),
                TextLine("Cuong", 10, 90, 110),
            ]
            updated, names, _handles = people._rewrite_with_votes(Path("frame.png"), image, lines, "")
        finally:
            people._vote_box = original
        self.assertEqual([row.text for row in updated], ["An", "Binh", "Cuong"])
        self.assertEqual(names, {"An", "Binh", "Cuong"})
        self.assertEqual(len(set(dests)), 3)

    def test_tesseract_cli_shares_one_budget_and_stops(self) -> None:
        calls: list[float] = []

        def fake_run(_argv: list[str], **kwargs: object) -> object:
            calls.append(float(kwargs["timeout"]))

            class Result:
                returncode = 1
                stdout = ""

            return Result()

        original = people.subprocess.run
        people.subprocess.run = fake_run
        try:
            self.assertEqual(people._tesseract_cli_run(Path("x.png"), timeout=0), "")
            self.assertEqual(calls, [])
            self.assertEqual(people._tesseract_cli_run(Path("x.png"), timeout=4), "")
            self.assertEqual(len(calls), 2)
            self.assertGreater(calls[0], 0)
            self.assertLessEqual(calls[0], 4)
            self.assertGreater(calls[1], 0)
            self.assertLessEqual(calls[1], calls[0])
            calls.clear()

            def expired(_argv: list[str], **_kwargs: object) -> object:
                calls.append(1)
                raise subprocess.TimeoutExpired(cmd="tesseract", timeout=1)

            people.subprocess.run = expired
            self.assertEqual(people._tesseract_cli_run(Path("x.png"), timeout=4), "")
            self.assertEqual(calls, [1])
        finally:
            people.subprocess.run = original
