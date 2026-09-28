"""Screen-recording steps from visible words."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from control_plane.screen_steps import clean_ocr, read_screen_video, same_caption, seen_line, steps_from_text


class ScreenStepTextTests(unittest.TestCase):
    def test_clean_ocr_keeps_readable_lines(self) -> None:
        text = clean_ocr("  \nOpen Notes\n...\nSave Note\nSave Note\n")
        self.assertEqual(text, "Open Notes · Save Note")

    def test_same_caption_ignores_spacing(self) -> None:
        self.assertTrue(same_caption("Open Notes", "open   notes"))
        self.assertFalse(same_caption("Open Notes", "Save Note"))

    def test_steps_drop_repeated_screens(self) -> None:
        steps = steps_from_text([(0, "Open Notes"), (1, "Open Notes"), (2, "Save Note")])
        self.assertEqual([step["caption"] for step in steps], ["0:00 — Open Notes", "0:02 — Save Note"])

    def test_noise_lines_are_dropped(self) -> None:
        self.assertEqual(clean_ocr("aQo* ở 7.06 c7a8O Selag"), "")

    def test_seen_line_keeps_place_and_account(self) -> None:
        self.assertEqual(seen_line("TikTok"), "TikTok")
        self.assertNotIn("Mở", seen_line("TikTok"))
        line = seen_line("Da follow @monaco.daily6")
        self.assertIn("@monaco.daily6", line)
        self.assertIn("Đã follow", line)
        self.assertEqual(clean_ocr("topcv beko ecord"), "")
        self.assertEqual(clean_ocr("panh ban dang foal"), "")
        steps = steps_from_text(
            [
                (0, "aQo* o 7.06 c7a8O Selag"),
                (1, "topcv beko ecord"),
                (2, "Danh ba\nBa Thanh Xuan Trung"),
                (7, "TikTok"),
                (12, "Da follow\n@monaco.daily6"),
                (13, "Da follow @monaco.daily6"),
            ]
        )
        captions = [step["caption"] for step in steps]
        self.assertTrue(any("Danh bạ" in caption and "Thanh Xuan Trung" in caption for caption in captions))
        self.assertTrue(any(caption.endswith("TikTok") or "— TikTok" in caption for caption in captions))
        self.assertEqual(sum("monaco.daily6" in caption for caption in captions), 1)
        self.assertFalse(any("Mở " in caption for caption in captions))
        self.assertFalse(any("topcv" in caption or "panh" in caption for caption in captions))


class ScreenVideoTests(unittest.TestCase):
    def test_video_lists_words_that_appear(self) -> None:
        if shutil.which("ffmpeg") is None or shutil.which("tesseract") is None:
            self.skipTest("ffmpeg and tesseract are required")
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            draw = (
                "drawtext=fontfile=%s:text='Thanh Xuan':fontsize=42:fontcolor=black:x=30:y=180:enable='lt(t,1.2)',"
                "drawtext=fontfile=%s:text='@monaco.daily6':fontsize=36:fontcolor=black:x=30:y=180:enable='gte(t,1.2)'"
            ) % (font, font)
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=480x800:d=3",
                    "-vf", draw,
                    str(path),
                ],
                check=True,
                timeout=30,
            )
            steps = read_screen_video(path)
        captions = " ".join(step["caption"] for step in steps)
        self.assertIn("Thanh", captions)
        self.assertIn("monaco.daily6", captions)
