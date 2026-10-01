"""Bộ đọc giữ trong bộ nhớ phải ra cùng các từ với lệnh tesseract."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane import tesseract_keep as keep
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


class FreshGateTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is required")
        if not Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").is_file():
            self.skipTest("font missing")
        self.saved = keep._gate
        keep._gate = keep._Gate()

    def tearDown(self) -> None:
        fresh = keep._gate
        keep._gate = self.saved
        lib = fresh.lib
        while lib is not None and not fresh.idle.empty():
            api = fresh.idle.get_nowait()
            lib.TessBaseAPIEnd(api)
            lib.TessBaseAPIDelete(api)

    def test_threads_that_start_together_all_read_in_memory(self) -> None:
        keep._gate.limit = 4
        image = _draw(["Tran Tung", "Danh ba"])
        start = threading.Barrier(8)

        def read(_index: int) -> str | None:
            start.wait()
            return read_tsv(image)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read, range(8)))
        if keep._gate.lib is None:
            self.skipTest("in-process tesseract is unavailable")
        self.assertTrue(all(result is not None and "Tran" in result for result in results))
        self.assertLessEqual(keep._gate.made, 4)

    def test_readers_are_opened_before_the_first_frame(self) -> None:
        keep._gate.limit = 2
        opened = keep.warm_readers()
        if keep._gate.lib is None:
            self.skipTest("in-process tesseract is unavailable")
        self.assertEqual(opened, 2)
        self.assertEqual(keep._gate.idle.qsize(), 2)
        self.assertEqual(keep.reader_mode(), "api")
        self.assertIn("Tran", read_tsv(_draw(["Tran Tung"])) or "")
        self.assertEqual(keep._gate.made, 2)
