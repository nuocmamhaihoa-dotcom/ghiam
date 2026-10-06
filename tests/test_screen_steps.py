"""Screen-recording steps from visible words."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane import layout_learn
from control_plane import screen_steps
from control_plane.screen_steps import (
    _FRAME_READ_SECONDS,
    _MAX_FRAMES,
    _read_one,
    _work_dir,
    _segment_count,
    _segment_ranges,
    _segment_threads,
    ReadProgress,
    _changed_frames,
    _earlier_frames,
    _extract_frames,
    _ffmpeg_extract_command,
    _media_env,
    _media_seconds,
    _read_frames,
    _read_label,
    _read_span,
    _sample_previews,
    _sample_rate,
    _unread_windows,
    _saved_frames,
    analyze_screen_video,
    clean_ocr,
    faststart_video,
    ocr_workers,
    read_screen_video,
    same_caption,
    seen_line,
    steps_from_text,
    visible_steps,
)


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

    def test_visible_steps_keep_each_row(self) -> None:
        frames = [
            (index / 8, [f"Danh bạ · Danh {index:03d} · Nguoi {index:03d} Xx"])
            for index in range(30)
        ]
        steps = visible_steps(frames)
        self.assertEqual(len(steps), 30)
        self.assertIn("Nguoi 029 Xx", steps[-1]["caption"])


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

    def test_segment_count_respects_env_and_cpu(self) -> None:
        previous = os.environ.get("CONTROL_FFMPEG_SEGMENTS")
        os.environ["CONTROL_FFMPEG_SEGMENTS"] = "5"
        try:
            self.assertEqual(_segment_count(), 5)
        finally:
            if previous is None:
                os.environ.pop("CONTROL_FFMPEG_SEGMENTS", None)
            else:
                os.environ["CONTROL_FFMPEG_SEGMENTS"] = previous
        self.assertGreaterEqual(_segment_count(), 4)

    def test_ocr_workers_use_the_remaining_cores(self) -> None:
        self.assertEqual(ocr_workers(100, 12, reserve=1), 11)
        self.assertEqual(ocr_workers(4, 12, reserve=1), 4)
        self.assertEqual(ocr_workers(10, 1, reserve=1), 1)
        self.assertEqual(ocr_workers(100, 16, reserve=0), 16)

    def test_frame_extract_is_not_limited_to_one_thread(self) -> None:
        previous = os.environ.get("OMP_THREAD_LIMIT")
        os.environ["OMP_THREAD_LIMIT"] = "1"
        try:
            self.assertNotIn("OMP_THREAD_LIMIT", _media_env())
        finally:
            if previous is None:
                os.environ.pop("OMP_THREAD_LIMIT", None)
            else:
                os.environ["OMP_THREAD_LIMIT"] = previous

    def test_frame_extract_uses_every_core_without_enlarging(self) -> None:
        argv = _ffmpeg_extract_command(Path("clip.mp4"), Path("f-%05d.png"), 8)
        self.assertEqual(argv[argv.index("-threads") + 1], "0")
        self.assertLess(argv.index("-threads"), argv.index("-i"))
        self.assertNotIn("-q:v", argv)
        self.assertNotIn("mjpeg", argv)
        self.assertEqual(argv[argv.index("-compression_level") + 1], "1")
        scale = next(item for item in argv if item.startswith("fps="))
        self.assertIn("fps=8", scale)
        self.assertIn(r"scale=min(720\,iw):-2", scale)
        self.assertNotIn("yuv420p", scale)
        jpeg = _ffmpeg_extract_command(Path("clip.mp4"), Path("f-%05d.jpg"), 8)
        self.assertNotIn("-compression_level", jpeg)
        self.assertEqual(jpeg[jpeg.index("-q:v") + 1], "1")
        self.assertEqual(jpeg[jpeg.index("-c:v") + 1], "mjpeg")

    def test_preview_keeps_the_first_middle_and_last_frame(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            chosen = []
            for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))):
                path = folder / f"{index}.jpg"
                Image.new("RGB", (800, 200), color).save(path, format="JPEG")
                chosen.append((float(index), path))
            previews = _sample_previews(chosen)
        self.assertEqual(len(previews), 3)
        self.assertTrue(all(item.startswith(b"\xff\xd8") and len(item) <= 150_000 for item in previews))

    def test_frame_extract_honors_a_pc_thread_budget(self) -> None:
        previous = os.environ.get("CONTROL_FFMPEG_THREADS")
        os.environ["CONTROL_FFMPEG_THREADS"] = "18"
        try:
            argv = _ffmpeg_extract_command(Path("clip.mp4"), Path("f-%05d.jpg"), 4)
            self.assertEqual(argv[argv.index("-threads") + 1], "18")
        finally:
            if previous is None:
                os.environ.pop("CONTROL_FFMPEG_THREADS", None)
            else:
                os.environ["CONTROL_FFMPEG_THREADS"] = previous

    def test_media_seconds_are_microseconds(self) -> None:
        self.assertEqual(_media_seconds("960000"), 0.96)
        self.assertIsNone(_media_seconds("N/A"))

    def test_every_changed_frame_is_read(self) -> None:
        class Sink:
            def __init__(self) -> None:
                self.problems: list[str] = []

            def report(self, _percent: int, _task: str) -> None:
                return None

            def problem(self, text: str) -> None:
                self.problems.append(text)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            images: list[tuple[float, Path]] = []
            for index in range(1001):
                path = root / f"{index:04d}.png"
                color = (0, 0, 0) if index % 2 == 0 else (255, 255, 255)
                Image.new("RGB", (40, 40), color).save(path)
                images.append((index / 8, path))
            sink = Sink()
            chosen = _changed_frames(images, sink)
        self.assertEqual(len(chosen), 1001)
        self.assertFalse(any("1000" in text for text in sink.problems))

    def test_a_video_past_three_hours_is_spread_across_the_frame_cap(self) -> None:
        duration = 4 * 60 * 60
        rate = _sample_rate(duration)
        self.assertLess(rate, 4.0)
        self.assertAlmostEqual(rate * duration, _MAX_FRAMES, places=3)

    def test_read_span_follows_the_whole_video(self) -> None:
        early = [(0.0, Path("a.png")), (10.0, Path("b.png"))]
        late = [(1800.0, Path("c.png")), (3590.0, Path("d.png"))]
        _early_lo, early_hi = _read_span(early, 3600.0)
        late_lo, _late_hi = _read_span(late, 3600.0)
        self.assertLess(early_hi, late_lo)
        self.assertEqual(_read_label(late, 3600.0), "Đọc chữ, phút 59/60")
        self.assertTrue(_read_label(late, 3600.0, continued=True).startswith("Đọc tiếp"))

    def test_a_full_video_keeps_four_frames_a_second(self) -> None:
        self.assertEqual(_sample_rate(20 * 60), 4.0)
        self.assertEqual(_sample_rate(30 * 60), 4.0)
        self.assertEqual(_sample_rate(40 * 60), 4.0)
        self.assertEqual(_sample_rate(60 * 60), 4.0)
        self.assertEqual(_sample_rate(3 * 60 * 60), 4.0)

    def test_unread_windows_merge_misses_and_keep_a_named_frame_out(self) -> None:
        person = {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"}
        readings = [
            (0.0, [], []),
            (0.5, [], []),
            (2.0, [], [person]),
            (3.0, [], []),
            (3.4, [], []),
        ]
        # 0.0 và 0.5 cách tên hơn một giây nên bỏ. 3.0 đúng một giây sau tên nên giữ.
        # 3.4 cách 1.4 giây nên bỏ. Khung giữ được nới một giây hai đầu.
        self.assertEqual(_unread_windows(readings), [(2.0, 4.0)])
        self.assertEqual(_unread_windows([(1.0, [], [person])]), [])

    def test_words_without_a_person_still_open_a_reread(self) -> None:
        self.assertEqual(_unread_windows([(0.0, ["Trần"], [])]), [(0.0, 1.0)])
        stray = {"kind": "profile", "name": "Trần Tùng", "username": ""}
        self.assertEqual(_unread_windows([(2.0, [], [stray])]), [(1.0, 3.0)])

    def test_a_long_blank_run_is_not_reread(self) -> None:
        person = {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"}
        readings: list[tuple[float, list[str], list[dict[str, str]]]] = [
            (float(second), [], []) for second in (0, 2, 4, 6, 8)
        ]
        readings.append((20.0, [], [person]))
        self.assertEqual(_unread_windows(readings), [])

    def test_a_blank_just_before_a_person_is_reread(self) -> None:
        person = {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"}
        readings = [
            (0.0, [], []),
            (1.5, [], []),
            (2.0, [], [person]),
            (9.0, [], []),
        ]
        self.assertEqual(_unread_windows(readings), [(0.5, 2.5)])
        self.assertEqual(
            _unread_windows([(5.5, [], []), (9.0, [], []), (5.0, ["Trần"], [])]),
            [(4.0, 6.5)],
        )

    def test_a_frame_unread_after_thirty_seconds_is_skipped(self) -> None:
        self.assertEqual(_FRAME_READ_SECONDS, 30.0)
        clock = {"now": 0.0}
        calls: list[str] = []

        def now() -> float:
            return clock["now"]

        def no_gpu(_image: Path) -> None:
            return None

        def slow_read(_path: Path, _loaded: object = None, *, seconds: float | None = None) -> str:
            self.assertIsNotNone(seconds)
            assert seconds is not None
            self.assertLessEqual(seconds, 30.0)
            clock["now"] += 31
            calls.append("fast")
            return ""

        def standard_read(*_args: object, **_kwargs: object) -> str:
            calls.append("standard")
            return ""

        def tighten(*_args: object, **_kwargs: object) -> tuple[list[object], list[dict[str, str]]]:
            calls.append("tighten")
            return [], []

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "khung.png"
            Image.new("RGB", (40, 40), "white").save(image)
            original = (
                screen_steps.read_lines,
                screen_steps.read_frame_tsv,
                screen_steps.read_frame_tsv_standard,
                screen_steps.tighten_frame_reading,
            )
            screen_steps.read_lines = no_gpu
            screen_steps.read_frame_tsv = slow_read
            screen_steps.read_frame_tsv_standard = standard_read
            screen_steps.tighten_frame_reading = tighten
            try:
                read = _read_one((0.0, image), clock=now)
            finally:
                (
                    screen_steps.read_lines,
                    screen_steps.read_frame_tsv,
                    screen_steps.read_frame_tsv_standard,
                    screen_steps.tighten_frame_reading,
                ) = original
        self.assertTrue(read.timed_out)
        self.assertEqual(read.captions, [])
        self.assertEqual(calls, ["fast"])

    def test_a_frame_inside_thirty_seconds_still_gets_a_second_read(self) -> None:
        clock = {"now": 0.0}
        calls: list[str] = []

        def now() -> float:
            return clock["now"]

        def no_gpu(_image: Path) -> None:
            return None

        def fast_read(_path: Path, _loaded: object = None, *, seconds: float | None = None) -> str:
            clock["now"] += 1
            calls.append("fast")
            return ""

        def standard_read(*_args: object, **_kwargs: object) -> str:
            calls.append("standard")
            return ""

        def tighten(*_args: object, **_kwargs: object) -> tuple[list[object], list[dict[str, str]]]:
            calls.append("tighten")
            return [], []

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "khung.png"
            Image.new("RGB", (40, 40), "white").save(image)
            original = (
                screen_steps.read_lines,
                screen_steps.read_frame_tsv,
                screen_steps.read_frame_tsv_standard,
                screen_steps.tighten_frame_reading,
            )
            screen_steps.read_lines = no_gpu
            screen_steps.read_frame_tsv = fast_read
            screen_steps.read_frame_tsv_standard = standard_read
            screen_steps.tighten_frame_reading = tighten
            try:
                read = _read_one((1.0, image), clock=now)
            finally:
                (
                    screen_steps.read_lines,
                    screen_steps.read_frame_tsv,
                    screen_steps.read_frame_tsv_standard,
                    screen_steps.tighten_frame_reading,
                ) = original
        self.assertFalse(read.timed_out)
        self.assertEqual(calls, ["fast", "standard", "tighten"])

    def test_a_frame_that_hangs_is_left_behind_within_the_budget(self) -> None:
        release = threading.Event()

        def no_gpu(_image: Path) -> None:
            return None

        def hang(_path: Path, _loaded: object = None, *, seconds: float | None = None) -> str:
            release.wait(3)
            return ""

        def unused(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("không đọc thêm sau khi hết giờ")

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "khung.png"
            Image.new("RGB", (40, 40), "white").save(image)
            original = (
                screen_steps._FRAME_READ_SECONDS,
                screen_steps.read_lines,
                screen_steps.read_frame_tsv,
                screen_steps.read_frame_tsv_standard,
                screen_steps.tighten_frame_reading,
            )
            screen_steps._FRAME_READ_SECONDS = 0.25
            screen_steps.read_lines = no_gpu
            screen_steps.read_frame_tsv = hang
            screen_steps.read_frame_tsv_standard = unused
            screen_steps.tighten_frame_reading = unused
            started = time.monotonic()
            try:
                read = _read_one((0.0, image))
            finally:
                release.set()
                (
                    screen_steps._FRAME_READ_SECONDS,
                    screen_steps.read_lines,
                    screen_steps.read_frame_tsv,
                    screen_steps.read_frame_tsv_standard,
                    screen_steps.tighten_frame_reading,
                ) = original
        self.assertTrue(read.timed_out)
        self.assertEqual(read.captions, [])
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_skipped_frame_does_not_stop_the_next_frame(self) -> None:
        class Sink(ReadProgress):
            def __init__(self) -> None:
                self.problems: list[str] = []

            def problem(self, text: str) -> None:
                self.problems.append(text)

        def fake_read(item: tuple[float, Path], *_args: object, **_kwargs: object) -> object:
            from control_plane.screen_steps import _FrameRead

            if item[0] == 0.0:
                return _FrameRead(item[0], [], [], 0, 0, [], 0, False, True)
            return _FrameRead(item[0], ["Trần Tùng"], [], 1, 1, [], 10, False, False)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            chosen = []
            for index, name in enumerate(("cham.png", "kip.png")):
                path = root / name
                Image.new("RGB", (40, 40), "white").save(path)
                chosen.append((index / 4, path))
            sink = Sink()
            original_read = screen_steps._read_one
            screen_steps._read_one = fake_read
            try:
                rows = _read_frames(chosen, sink)
            finally:
                screen_steps._read_one = original_read
        self.assertTrue(any("1 khung quá 30 giây, bỏ qua." == text for text in sink.problems))
        self.assertTrue(any(captions == ["Trần Tùng"] for _stamp, captions, _found in rows))
        self.assertTrue(any(captions == [] for _stamp, captions, _found in rows))

    def test_a_long_extract_is_split_into_four_ranges(self) -> None:
        ranges = _segment_ranges(96, 0)
        self.assertEqual(ranges, [(1, 24), (25, 48), (49, 72), (73, 96)])
        self.assertEqual(_segment_ranges(96, 30), [(31, 48), (49, 72), (73, 96)])
        self.assertEqual(_segment_ranges(40, 0), [(1, 40)])
        self.assertEqual(_segment_threads("16", 4), "4")
        self.assertIsNone(_segment_threads(None, 4))

    def test_four_decoders_cover_the_video(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clip = root / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=12",
                    "-pix_fmt", "yuv420p", str(clip),
                ],
                check=True,
                timeout=60,
            )
            work = root / "frames"
            work.mkdir()
            frames = _extract_frames(clip, work, 8.0, 12.0, ReadProgress())
            self.assertGreaterEqual(len(frames), 12 * 8 - 4)
            self.assertTrue((work / "f-00001.png").is_file())
            numbers = sorted(int(path.stem.split("-")[1]) for _seconds, path in frames)
            self.assertEqual(numbers, list(range(1, numbers[-1] + 1)))

    def test_a_continued_extract_starts_at_the_next_frame(self) -> None:
        argv = _ffmpeg_extract_command(Path("clip.mp4"), Path("f-%05d.jpg"), 4.0, first=41)
        self.assertEqual(argv[argv.index("-ss") + 1], "10.000")
        self.assertLess(argv.index("-ss"), argv.index("-i"))
        self.assertEqual(argv[argv.index("-start_number") + 1], "41")
        self.assertEqual(argv[argv.index("-frames:v") + 1], str(_MAX_FRAMES - 40))
        plain = _ffmpeg_extract_command(Path("clip.mp4"), Path("f-%05d.jpg"), 4.0)
        self.assertNotIn("-ss", plain)
        self.assertNotIn("-start_number", plain)

    def test_a_growing_video_is_extracted_once(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clip = root / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=320x480:r=30:d=6",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(clip),
                ],
                check=True,
                timeout=60,
            )
            data = clip.read_bytes()
            grow = root / "grow.mp4"
            grow.write_bytes(data[: len(data) // 2])
            work = root / "work"
            work.mkdir()
            sink = ReadProgress()
            first = _extract_frames(grow, work, 4.0, 6.0, sink, partial=True, have=0)
            self.assertTrue(first)
            self.assertTrue((work / "extract.partial").is_file())
            stamp = (work / "f-00001.png").stat().st_mtime_ns
            have = _earlier_frames(work, 4.0)
            self.assertEqual(have, len(first))
            grow.write_bytes(data)
            whole = _extract_frames(grow, work, 4.0, 6.0, sink, partial=False, have=have)
            self.assertEqual((work / "f-00001.png").stat().st_mtime_ns, stamp)
            self.assertFalse((work / "extract.partial").exists())
            once = root / "once"
            once.mkdir()
            single = _extract_frames(clip, once, 4.0, 6.0, sink, partial=False, have=0)
            self.assertEqual(len(whole), len(single))

    def test_frames_from_another_rate_are_extracted_again(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "f-00001.jpg").write_bytes(b"jpg")
            (work / "f-00002.jpg").write_bytes(b"jpg")
            (work / "extract.partial").write_text("2.000000", encoding="utf-8")
            self.assertEqual(_earlier_frames(work, 2.0), 2)
            self.assertEqual(_earlier_frames(work, 4.0), 0)
            self.assertFalse(any(work.glob("f-*")))

    def test_progress_names_the_work(self) -> None:
        if shutil.which("ffmpeg") is None or shutil.which("tesseract") is None:
            self.skipTest("ffmpeg and tesseract are required")

        class Capture(ReadProgress):
            def __init__(self) -> None:
                self.events: list[tuple[int, str]] = []
                self.problems: list[str] = []

            def report(self, percent: int, task: str) -> None:
                self.events.append((int(percent), task))

            def problem(self, text: str) -> None:
                self.problems.append(text)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=320x480:d=1",
                    str(path),
                ],
                check=True,
                timeout=30,
            )
            capture = Capture()
            _steps, people = analyze_screen_video(path, capture)
        self.assertEqual(people, [])
        tasks = [task for _percent, task in capture.events]
        self.assertTrue(any(task == "Tách khung hình" for task in tasks))
        self.assertTrue(any(task.startswith("Đọc chữ") for task in tasks))
        percents = [percent for percent, _task in capture.events]
        self.assertEqual(percents, sorted(percents))
        self.assertGreaterEqual(percents[-1], 90)
        self.assertTrue(capture.problems)

    def test_the_video_reports_what_it_learned_apart_from_problems(self) -> None:
        if shutil.which("ffmpeg") is None or shutil.which("tesseract") is None:
            self.skipTest("ffmpeg and tesseract are required")

        class Capture(ReadProgress):
            def __init__(self) -> None:
                self.learned: list[str] = []
                self.problems: list[str] = []

            def problem(self, text: str) -> None:
                self.problems.append(text)

            def note_learned(self, text: str) -> None:
                self.learned.append(text)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=320x480:d=1",
                    str(path),
                ],
                check=True,
                timeout=30,
            )
            layout_learn.clear_learners()
            learner = layout_learn.learner_for(str(_work_dir(path)))
            for _ in range(3):
                learner.learn_handle(430, 459)
            capture = Capture()
            analyze_screen_video(path, capture)
            layout_learn.clear_learners()
        self.assertEqual(len(capture.learned), 1)
        self.assertIn("430 đến 459", capture.learned[0])
        self.assertFalse(any("Quy luật" in text for text in capture.problems))

    def test_a_saved_frame_is_not_read_again(self) -> None:
        class Memory(ReadProgress):
            def __init__(self) -> None:
                self.frames = {
                    "0.125": (["Tran Tung"], [{"kind": "profile", "name": "Tran Tung", "contactName": "", "username": "@trn.tng751"}])
                }
                self.read_again: list[float] = []

            def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
                return self.frames

            def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
                self.read_again.append(seconds)
                self.frames[f"{float(seconds):.3f}"] = (captions, sightings)

        sink = Memory()
        readings = _read_frames(
            [(0.125, Path("/tmp/khong-co-khung-a.png")), (0.250, Path("/tmp/khong-co-khung-b.png"))],
            sink,
        )
        self.assertEqual(readings[0][1], ["Tran Tung"])
        self.assertEqual(readings[0][2][0]["username"], "@trn.tng751")
        self.assertNotIn(0.125, sink.read_again)
        self.assertIn(0.250, sink.read_again)

    def test_saved_extract_is_reused_at_the_same_rate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            image = work / "f-00001.png"
            image.write_bytes(b"png")
            (work / "extract.done").write_text("8.000000", encoding="utf-8")
            saved = _saved_frames(work, 8.0)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved[0][0], 0.0)
            self.assertEqual(saved[0][1], image)
            self.assertIsNone(_saved_frames(work, 4.0))

    def test_staged_people_skip_the_video(self) -> None:
        class Staged(ReadProgress):
            def staged_people(self) -> list[dict[str, str]] | None:
                return [{"name": "Tran Tung", "contactName": "A Tung", "username": "@trn.tng751"}]

        _steps, people = analyze_screen_video(Path("/tmp/khong-co-video.mp4"), Staged())
        self.assertEqual(people, [{"name": "Tran Tung", "contactName": "A Tung", "username": "@trn.tng751"}])

    def test_a_small_shift_is_not_read_again(self) -> None:
        class Sink:
            def report(self, _percent: int, _task: str) -> None:
                return None

            def problem(self, _text: str) -> None:
                return None

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "a.png"
            second = root / "b.png"
            third = root / "c.png"
            Image.new("RGB", (200, 400), (255, 255, 255)).save(first)
            nudged = Image.new("RGB", (200, 400), (255, 255, 255))
            nudged.putpixel((100, 200), (0, 0, 0))
            nudged.save(second)
            Image.new("RGB", (200, 400), (0, 0, 0)).save(third)
            chosen = _changed_frames([(0.0, first), (0.5, second), (1.0, third)], Sink())
        self.assertEqual([item[1].name for item in chosen], ["a.png", "c.png"])

    def test_a_new_name_on_the_same_layout_is_read(self) -> None:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if not Path(font_path).is_file():
            self.skipTest("font missing")
        font = ImageFont.truetype(font_path, 40)

        class Sink:
            def report(self, _percent: int, _task: str) -> None:
                return None

            def problem(self, _text: str) -> None:
                return None

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = []
            for index, (name, handle) in enumerate(
                (("Tuan Tran Shop", "@tuan.tran11"), ("Tuan Tran Shop", "@tuan.tran11"), ("Mai Tran Shop", "@mai.tran10"))
            ):
                image = Image.new("RGB", (720, 1560), (255, 255, 255))
                pen = ImageDraw.Draw(image)
                pen.ellipse((260, 200, 460, 400), fill=(90, 120, 200))
                pen.text((120, 460), name, font=font, fill=(0, 0, 0))
                pen.text((120, 520), handle, font=font, fill=(40, 40, 40))
                path = root / f"{index}.jpg"
                image.save(path, format="JPEG", quality=90 - index)
                paths.append((float(index), path))
            chosen = _changed_frames(paths, Sink())
        self.assertEqual([item[1].name for item in chosen], ["0.jpg", "2.jpg"])

    def test_faststart_keeps_a_file_that_is_not_a_video(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(b"not-a-video")
            ready = faststart_video(path)
            self.assertEqual(ready, path)
            self.assertEqual(path.read_bytes(), b"not-a-video")
