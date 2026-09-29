"""Ghép danh bạ và hồ sơ đọc từ vị trí chữ trên khung hình."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane.people import clean_username
from control_plane.screen_people import TextLine, propose_rows, sightings_from_image, sightings_from_lines


def _line(text: str, top: int, height: int = 28, left: int = 70) -> TextLine:
    return TextLine(text, left, top, top + height)


class ScreenPeopleTests(unittest.TestCase):
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
        self.assertEqual(
            rows,
            [
                {"name": "Tran Tung", "contactName": "A Tung Ban Gach", "username": "@trn.tng751"},
                {"name": "Ba soi", "contactName": "Chi Soi Xuan Trung", "username": "@b.soi22"},
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

    def test_one_reading_is_not_saved(self) -> None:
        once = [
            {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
            {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
        ]
        self.assertEqual(propose_rows(once), [])
        self.assertEqual(
            propose_rows(once + once),
            [{"name": "Trần Tùng", "contactName": "A Tùng Bán Gạch", "username": "@trn.tng751"}],
        )

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
