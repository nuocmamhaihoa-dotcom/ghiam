"""Bộ đọc giữ trong bộ nhớ phải ra cùng các từ với lệnh tesseract."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane.screen_people import _tesseract_cli, lines_from_tsv
from control_plane.tesseract_keep import read_tsv


def _words(tsv: str) -> list[str]:
    words: list[str] = []
    for raw in tsv.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if word:
            words.append(word)
    return words


def _draw(lines: list[str]) -> Image.Image:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
    image = Image.new("L", (720, 480), 255)
    pen = ImageDraw.Draw(image)
    top = 48
    for text in lines:
        pen.text((40, top), text, font=font, fill=0)
        top += 90
    return image


class TesseractKeepTests(unittest.TestCase):
    def test_kept_engine_matches_cli_words_including_black_pixels(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is required")
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if not font_path.is_file():
            self.skipTest("font missing")

        def check(image: Image.Image) -> None:
            self.assertIn(0, image.tobytes())
            kept = read_tsv(image)
            if kept is None:
                self.skipTest("in-process tesseract is unavailable")
            with tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / "frame.png"
                image.save(path)
                cli = _tesseract_cli(path)
            self.assertEqual(_words(kept), _words(cli))
            self.assertEqual(
                [line.text for line in lines_from_tsv(kept)],
                [line.text for line in lines_from_tsv(cli)],
            )

        check(_draw(["Tran Tung", "Danh ba"]))
        check(_draw(["Ba soi", "@b.soi22"]))
