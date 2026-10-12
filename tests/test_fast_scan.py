"""Giải mã và OCR chồng lên nhau, không đổi khung được chọn."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from control_plane.screen_table import FrameObs
from control_plane.video_scan import (
    SCAN_FPS,
    VideoPlan,
    _EarlierScreens,
    _decode_ocr_budget,
    _plan_ocr_jobs,
    _scan_video,
    extract_frames,
    use_ffmpeg_threads,
)


def _jpeg(seed: int) -> bytes:
    image = Image.new("L", (32, 40), 0)
    pixels = image.load()
    for y in range(40):
        for x in range(32):
            pixels[x, y] = (seed * 17 + x * 13 + y * 7) % 256
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class BudgetTests(unittest.TestCase):
    def test_split_keeps_room_for_decode(self) -> None:
        self.assertEqual(_decode_ocr_budget(16, 2, 16), (8, 8))
        self.assertEqual(_decode_ocr_budget(16, 1, 16), (16, 12))
        self.assertEqual(_decode_ocr_budget(8, 2, 16), (4, 2))
        self.assertEqual(_decode_ocr_budget(2, 2, 4), (2, 2))


class AliasDirectionTests(unittest.TestCase):
    def test_only_an_earlier_frame_can_be_reused(self) -> None:
        screens = _EarlierScreens()
        digest = (1, 2)
        screens.remember(5.0, digest, 7)
        self.assertIsNone(screens.match(1.0, digest))
        self.assertIsNone(screens.match(8.1, digest))
        self.assertEqual(screens.match(6.0, digest), 7)
        self.assertEqual(screens.match(5.0, digest), 7)

    def test_job_order_matches_the_sequential_scan(self) -> None:
        plan = VideoPlan(reads=[0, 1, 10], taps=[[8, 9, 10]], transitions=[10], count=30)
        jobs = _plan_ocr_jobs(plan, [], SCAN_FPS)
        self.assertEqual(
            [(round(job.at, 5), job.kind, job.source, job.number) for job in jobs],
            [
                (0.0, 0, "main", 0),
                (round(1 / 30, 5), 0, "main", 1),
                (round(8 / 30, 5), 0, "main", 8),
                (round(9 / 30, 5), 0, "main", 9),
                (round(10 / 30, 5), 0, "main", 10),
                (round(10 / 30, 5), 1, "tap", 10),
            ],
        )


class SequentialSignatureTests(unittest.TestCase):
    def test_without_a_pool_extract_keeps_the_old_arguments(self) -> None:
        calls: list[str] = []

        def fake_extract(path, folder, numbers, fps, on_progress=None, prefix="f"):
            calls.append(prefix)
            self.assertEqual(list(numbers), [])
            return {}

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch("control_plane.video_scan.plan_video", return_value=VideoPlan(count=3)),
                patch("control_plane.video_scan.extract_frames", fake_extract),
            ):
                observations, learned = _scan_video(
                    Path("clip.mp4"),
                    Path(folder),
                    SCAN_FPS,
                    None,
                )
        self.assertEqual(calls, ["f"])
        self.assertEqual(observations, [])
        self.assertIsNone(learned)


class OverlapTests(unittest.TestCase):
    def test_later_duplicate_reuses_the_earlier_read(self) -> None:
        plan = VideoPlan(reads=[0, 1], count=5)
        blob = _jpeg(3)
        reads: list[float] = []
        extracts: list[tuple[str, int | None]] = []

        def fake_extract(path, folder, numbers, fps, on_progress=None, prefix="f", threads=None, on_ready=None, on_decoded=None):
            extracts.append((prefix, threads))
            for number in numbers:
                file = Path(folder) / f"{prefix}_{number}.jpg"
                file.write_bytes(blob)
                if on_ready is not None:
                    on_ready(number, file)
            if on_decoded is not None:
                on_decoded()
            return {}

        def fake_read(path, at, layout=None):
            reads.append(at)
            return FrameObs("list", at=at)

        use_ffmpeg_threads(8)
        with tempfile.TemporaryDirectory() as folder:
            with ThreadPoolExecutor(max_workers=4) as pool:
                with (
                    patch("control_plane.video_scan.plan_video", return_value=plan),
                    patch("control_plane.video_scan.extract_frames", fake_extract),
                    patch("control_plane.video_scan.read_frame_at", fake_read),
                    patch("control_plane.video_scan.read_frame_aggressive_at", side_effect=AssertionError("vòng 2")),
                ):
                    observations, _learned = _scan_video(
                        Path("clip.mp4"),
                        Path(folder),
                        SCAN_FPS,
                        pool.submit,
                    )
        self.assertEqual(extracts, [("f", 8)])
        self.assertEqual(reads, [0.0])
        self.assertEqual([obs.at for _path, obs in observations], [0.0, 1 / SCAN_FPS])
        self.assertTrue(all(obs.kind == "list" for _path, obs in observations))

    def test_a_frame_that_arrives_late_is_not_aliased_to_a_later_one(self) -> None:
        reads_at = list(range(24)) + [30, 24]
        plan = VideoPlan(reads=reads_at, count=40)
        same = _jpeg(9)
        other = _jpeg(2)
        seen: list[float] = []

        def fake_extract(path, folder, numbers, fps, on_progress=None, prefix="f", threads=None, on_ready=None, on_decoded=None):
            order = [number for number in numbers if number != 24]
            if 24 in numbers:
                order.append(24)
            for number in order:
                file = Path(folder) / f"{prefix}_{number}.jpg"
                file.write_bytes(same if number in (24, 30) else other)
                if on_ready is not None:
                    on_ready(number, file)
            if on_decoded is not None:
                on_decoded()
            return {}

        def fake_read(path, at, layout=None):
            seen.append(at)
            return FrameObs("list", at=at)

        use_ffmpeg_threads(8)
        with tempfile.TemporaryDirectory() as folder:
            with ThreadPoolExecutor(max_workers=4) as pool:
                with (
                    patch("control_plane.video_scan.plan_video", return_value=plan),
                    patch("control_plane.video_scan.extract_frames", fake_extract),
                    patch("control_plane.video_scan.read_frame_at", fake_read),
                    patch("control_plane.video_scan.read_frame_aggressive_at", side_effect=AssertionError("vòng 2")),
                ):
                    observations, _learned = _scan_video(
                        Path("clip.mp4"),
                        Path(folder),
                        SCAN_FPS,
                        pool.submit,
                    )
        self.assertIn(24 / SCAN_FPS, seen)
        self.assertIn(30 / SCAN_FPS, seen)
        self.assertEqual(
            [obs.at for _path, obs in observations],
            [number / SCAN_FPS for number in sorted(plan.reads)],
        )

    def test_tap_waits_until_the_whole_window_is_on_disk(self) -> None:
        plan = VideoPlan(reads=[5], taps=[[3, 4, 5]], count=12)
        taps: list[list[str]] = []

        def fake_extract(path, folder, numbers, fps, on_progress=None, prefix="f", threads=None, on_ready=None, on_decoded=None):
            for number in numbers:
                file = Path(folder) / f"{prefix}_{number}.jpg"
                file.write_bytes(_jpeg(number + (0 if prefix == "f" else 100)))
                if on_ready is not None:
                    on_ready(number, file)
            if on_decoded is not None:
                on_decoded()
            return {}

        def fake_read(path, at, layout=None):
            return FrameObs("list", at=at)

        def fake_tap(paths, at):
            taps.append(list(paths))
            return FrameObs("tap", at=at)

        use_ffmpeg_threads(8)
        with tempfile.TemporaryDirectory() as folder:
            with ThreadPoolExecutor(max_workers=4) as pool:
                with (
                    patch("control_plane.video_scan.plan_video", return_value=plan),
                    patch("control_plane.video_scan.extract_frames", fake_extract),
                    patch("control_plane.video_scan.read_frame_at", fake_read),
                    patch("control_plane.video_scan.read_tap_at", fake_tap),
                    patch("control_plane.video_scan.read_frame_aggressive_at", return_value=FrameObs("list")),
                    patch("control_plane.video_scan.burst_frame_numbers", return_value=[]),
                ):
                    observations, _learned = _scan_video(
                        Path("clip.mp4"),
                        Path(folder),
                        SCAN_FPS,
                        pool.submit,
                    )
        self.assertEqual(len(taps), 1)
        self.assertEqual([Path(item).name for item in taps[0]], ["f_3.jpg", "f_4.jpg", "f_5.jpg"])
        self.assertTrue(any(obs.kind == "tap" for _path, obs in observations))


@unittest.skipUnless(shutil.which("ffmpeg"), "cần ffmpeg")
class StreamExtractTests(unittest.TestCase):
    def test_on_ready_sees_each_closed_jpeg_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "clip.mp4"
            frames = root / "frames"
            frames.mkdir()
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=160x120:rate=30",
                    "-t",
                    "0.4",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(video),
                ],
                check=True,
                timeout=30,
            )
            seen: list[int] = []
            decoded = {"n": 0}

            def on_ready(number: int, path: Path) -> None:
                data = path.read_bytes()
                self.assertGreater(len(data), 100)
                self.assertTrue(data.startswith(b"\xff\xd8"))
                self.assertTrue(data.endswith(b"\xff\xd9"))
                with Image.open(path) as image:
                    image.load()
                seen.append(number)

            def on_decoded() -> None:
                decoded["n"] += 1

            files = extract_frames(
                video,
                frames,
                [0, 2, 4],
                fps=30,
                prefix="f",
                threads=1,
                on_ready=on_ready,
                on_decoded=on_decoded,
            )
            self.assertEqual(decoded["n"], 1)
            self.assertEqual(seen, [0, 2, 4])
            self.assertEqual(sorted(files), [0, 2, 4])
            for number in (0, 2, 4):
                self.assertTrue(files[number].is_file())


if __name__ == "__main__":
    unittest.main()
