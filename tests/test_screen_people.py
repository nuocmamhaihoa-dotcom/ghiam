"""Ghép danh bạ và hồ sơ đọc từ vị trí chữ trên khung hình."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
        rows = propose_rows(contacts + profiles)
        self.assertEqual(
            rows,
            [
                {"name": "Trần Tùng", "contactName": "A Tùng Bán Gạch", "username": "@trn.tng751"},
                {"name": "Bà soi", "contactName": "Chị Soi Xuân Trung", "username": "@b.soi22"},
            ],
        )

    def test_ocr_without_diacritics_keeps_the_spelled_name(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
                {"kind": "profile", "name": "Tran Tung", "username": "@trn.tng751"},
            ]
        )
        self.assertEqual(rows[0]["name"], "Trần Tùng")
        self.assertEqual(rows[0]["username"], "@trn.tng751")

    def test_same_contact_name_keeps_the_spelling_with_marks(self) -> None:
        rows = propose_rows(
            [
                {"kind": "contact", "name": "Ba soi", "contactName": "Chi Soi Xuan Trung"},
                {"kind": "contact", "name": "Bà soi", "contactName": "Chị Soi Xuân Trung"},
                {"kind": "profile", "name": "Bà soi", "username": "@b.soi22"},
            ]
        )
        self.assertEqual(rows[0]["name"], "Bà soi")
        self.assertEqual(rows[0]["contactName"], "Chị Soi Xuân Trung")

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
            rows = propose_rows(
                sightings_from_image(contacts) + sightings_from_image(profile_tung) + sightings_from_image(profile_soi)
            )
        self.assertEqual(
            rows,
            [
                {"name": "Tran Tung", "contactName": "A Tung Ban Gach", "username": "@trn.tng751"},
                {"name": "Ba soi", "contactName": "Chi Soi Xuan Trung", "username": "@b.soi22"},
            ],
        )
