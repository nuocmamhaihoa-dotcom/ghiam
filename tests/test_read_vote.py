"""Ba hướng đọc một dòng, và năm lần đọc lại khi cả ba lệch."""

from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from control_plane import read_vote
from control_plane.read_vote import (
    RowMemo,
    five_variants,
    forget_scope,
    memo_for,
    needs_reread,
    row_signature,
    vote_line,
)


class ReadVoteTests(unittest.TestCase):
    def test_two_names_agree_and_keep_the_marks(self) -> None:
        text, agreed = vote_line("Lan Anh", "Lân Anh", None, [], kind="name")
        self.assertTrue(agreed)
        self.assertEqual(text, "Lan Anh")
        text, agreed = vote_line("Lan Anh", "Lân Anh", "Lân Anh", [], kind="name")
        self.assertTrue(agreed)
        self.assertEqual(text, "Lân Anh")
        self.assertFalse(needs_reread("Lan Anh", "Lân Anh", None, "name"))

    def test_handle_ignores_letter_case(self) -> None:
        text, agreed = vote_line("@User.name", "@user.name", "@user.name", [], kind="handle")
        self.assertTrue(agreed)
        self.assertEqual(text, "@User.name")

    def test_a_single_read_stays_unconfirmed(self) -> None:
        text, agreed = vote_line("Trần Tùng", "", "", [], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "Trần Tùng")
        self.assertFalse(needs_reread("Trần Tùng", "", None, "name"))

    def test_three_different_names_take_the_rerun_majority(self) -> None:
        self.assertTrue(needs_reread("An", "Bình", "Cường", "name"))
        text, agreed = vote_line(
            "An",
            "Bình",
            "Cường",
            ["Bình", "Binh", "Khác", "Bình", "Một"],
            kind="name",
        )
        self.assertTrue(agreed)
        self.assertEqual(text, "Bình")

    def test_a_tie_or_a_single_vote_is_dropped(self) -> None:
        text, agreed = vote_line("An", "Bình", "Cường", ["An", "Bình", "Cường", "Dũng", "Em"], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "")
        text, agreed = vote_line("An", "Bình", "Cường", ["An", "An", "Bình", "Bình", ""], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "")

    def test_five_variants_change_the_pixels(self) -> None:
        crop = Image.new("RGB", (12, 8), "white")
        variants = five_variants(crop)
        self.assertEqual(len(variants), 5)
        sizes = {(image.width, image.height) for image in variants}
        self.assertIn((12, 8), sizes)
        self.assertIn((24, 16), sizes)
        self.assertIn((36, 24), sizes)

    def _sample(self, tone: int = 255, marks: int = 6) -> tuple[Image.Image, tuple[int, int, int, int]]:
        image = Image.new("L", (400, 80), tone)
        pen = ImageDraw.Draw(image)
        for index in range(marks):
            pen.rectangle((20 + index * 40, 20, 40 + index * 40, 50), fill=30)
        return image, (10, 10, 300, 50)

    def test_a_row_settles_after_three_frames_with_the_same_text(self) -> None:
        memo = RowMemo()
        image, box = self._sample()
        mark = (box[0], box[2], row_signature(image, box))
        self.assertIsNone(memo.settled("name", "Lan Anh", *mark))
        self.assertFalse(memo.agree("name", "Lan Anh", *mark, "f1", "Lan Anh"))
        self.assertFalse(memo.agree("name", "Lan Anh", *mark, "f2", "Lan Anh"))
        self.assertIsNone(memo.settled("name", "Lan Anh", *mark))
        self.assertTrue(memo.agree("name", "Lan Anh", *mark, "f3", "Lan Anh"))
        self.assertEqual(memo.settled("name", "Lan Anh", *mark), "Lan Anh")

    def test_the_same_frame_counts_once(self) -> None:
        memo = RowMemo()
        image, box = self._sample()
        mark = (box[0], box[2], row_signature(image, box))
        for _ in range(5):
            memo.agree("name", "Lan Anh", *mark, "f1", "Lan Anh")
        self.assertIsNone(memo.settled("name", "Lan Anh", *mark))

    def test_a_different_text_or_a_failed_vote_restarts_the_count(self) -> None:
        memo = RowMemo()
        image, box = self._sample()
        mark = (box[0], box[2], row_signature(image, box))
        memo.agree("name", "Lan Anh", *mark, "f1", "Lan Anh")
        memo.agree("name", "Lan Anh", *mark, "f2", "Lân Anh")
        memo.agree("name", "Lan Anh", *mark, "f3", "Lân Anh")
        self.assertIsNone(memo.settled("name", "Lan Anh", *mark))
        memo.fail("name", "Lan Anh", *mark)
        memo.agree("name", "Lan Anh", *mark, "f4", "Lân Anh")
        memo.agree("name", "Lan Anh", *mark, "f5", "Lân Anh")
        self.assertIsNone(memo.settled("name", "Lan Anh", *mark))
        memo.agree("name", "Lan Anh", *mark, "f6", "Lân Anh")
        self.assertEqual(memo.settled("name", "Lan Anh", *mark), "Lân Anh")

    def test_another_seed_column_or_picture_is_not_reused(self) -> None:
        memo = RowMemo()
        image, box = self._sample()
        mark = (box[0], box[2], row_signature(image, box))
        for frame in ("f1", "f2", "f3"):
            memo.agree("name", "Lan Anh", *mark, frame, "Lan Anh")
        self.assertEqual(memo.settled("name", "Lan Anh", *mark), "Lan Anh")
        self.assertIsNone(memo.settled("name", "Lân Anh", *mark))
        self.assertIsNone(memo.settled("handle", "Lan Anh", *mark))
        self.assertIsNone(memo.settled("name", "Lan Anh", mark[0] + 30, mark[1], mark[2]))
        self.assertIsNone(memo.settled("name", "Lan Anh", mark[0], mark[1] + 40, mark[2]))
        other, other_box = self._sample(tone=20, marks=2)
        self.assertIsNone(memo.settled("name", "Lan Anh", other_box[0], other_box[2], row_signature(other, other_box)))

    def test_the_same_row_in_a_later_frame_with_small_noise_is_reused(self) -> None:
        memo = RowMemo()
        image, box = self._sample()
        mark = (box[0], box[2], row_signature(image, box))
        for frame in ("f1", "f2", "f3"):
            memo.agree("name", "Lan Anh", *mark, frame, "Lan Anh")
        noisy = image.point(lambda value: min(255, value + 6))
        shifted_box = (box[0] + 2, box[1] + 1, box[2] - 3, box[3])
        later = (shifted_box[0], shifted_box[2], row_signature(noisy, shifted_box))
        self.assertEqual(memo.settled("name", "Lan Anh", *later), "Lan Anh")

    def test_memos_belong_to_one_video(self) -> None:
        first = memo_for("video-a")
        self.assertIs(first, memo_for("video-a"))
        self.assertIsNot(first, memo_for("video-b"))
        forget_scope("video-a")
        self.assertIsNot(first, memo_for("video-a"))
        read_vote.clear_memos()

    def test_rapid_text_reads_recognition_only_and_detection_shapes(self) -> None:
        self.assertEqual(read_vote._rapid_text(([["Ngo Thi Linh", 0.97]], [0.01])), "Ngo Thi Linh")
        boxed = [[[[0, 0], [9, 0], [9, 9], [0, 9]], "Ly Quoc", 0.9], [[[0, 20], [9, 20], [9, 29], [0, 29]], "Trang", 0.8]]
        self.assertEqual(read_vote._rapid_text((boxed, [0.2])), "Ly Quoc Trang")
        self.assertEqual(read_vote._rapid_text((None, None)), "")

    def test_rapid_reads_a_line_without_detection_and_with_one_thread(self) -> None:
        if read_vote.np is None:
            self.skipTest("numpy is required")
        built: list[dict[str, int]] = []
        calls: list[dict[str, object]] = []

        class Engine:
            def __init__(self, **options: int) -> None:
                built.append(options)

            def __call__(self, pixels: object, **options: object) -> tuple[list[list[object]], list[float]]:
                calls.append(options)
                return [["Lan Anh", 0.9]], [0.01]

        saved = (read_vote.RapidOCR, read_vote._engine, read_vote._engine_failed)
        read_vote.RapidOCR = Engine  # type: ignore[misc, assignment]
        read_vote._engine = None
        read_vote._engine_failed = False
        try:
            self.assertTrue(read_vote.rapid_ready())
            self.assertEqual(read_vote.read_rapid(Image.new("L", (60, 20), 255)), "Lan Anh")
            self.assertEqual(read_vote.read_rapid(Image.new("L", (60, 20), 255)), "Lan Anh")
        finally:
            read_vote.RapidOCR, read_vote._engine, read_vote._engine_failed = saved  # type: ignore[misc, assignment]
        self.assertEqual(built, [{"intra_op_num_threads": 1, "inter_op_num_threads": 1}])
        self.assertEqual(calls[0], {"use_det": False, "use_cls": False, "use_rec": True})

if __name__ == "__main__":
    unittest.main()
