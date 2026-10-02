"""Quy luật học từ lần đọc thành công: dải @ và tên của trang hồ sơ, chiều cao dòng tên, và việc cứu trang hồ sơ."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from control_plane import layout_learn, screen_people as people, screen_steps
from control_plane.layout_learn import LayoutLearner, Zone
from control_plane.read_vote import clear_memos
from control_plane.screen_people import TextLine
from control_plane.tesseract_keep import reader_mode

_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


class LayoutLearnerTests(unittest.TestCase):
    def test_a_zone_needs_three_consistent_successes(self) -> None:
        learner = LayoutLearner()
        learner.learn_handle(430, 459)
        learner.learn_handle(431, 459)
        self.assertIsNone(learner.handle_zone())
        learner.learn_handle(430, 460)
        self.assertEqual(learner.handle_zone(), Zone(430, 459))

    def test_scattered_successes_do_not_make_a_zone(self) -> None:
        learner = LayoutLearner()
        for top in (100, 300, 520, 700):
            learner.learn_name_zone(top, top + 30)
        self.assertIsNone(learner.name_zone())

    def test_outliers_do_not_move_the_zone(self) -> None:
        learner = LayoutLearner()
        for top, bottom in ((368, 409), (365, 410), (368, 409), (383, 396), (368, 431), (900, 940)):
            learner.learn_name_zone(top, bottom)
        zone = learner.name_zone()
        assert zone is not None
        self.assertEqual(zone.top, 368)
        self.assertLessEqual(zone.bottom, 431)

    def test_height_band_opens_only_after_enough_settled_lines(self) -> None:
        learner = LayoutLearner()
        for _ in range(19):
            learner.learn_height(28)
        self.assertIsNone(learner.height_band())
        learner.learn_height(34)
        band = learner.height_band()
        assert band is not None
        self.assertLessEqual(band[0], 21)
        self.assertGreaterEqual(band[1], 40)

    def test_edge_touching_boxes_never_fit_and_the_band_cuts_odd_heights(self) -> None:
        learner = LayoutLearner()
        self.assertTrue(learner.fits_name_box((138, 200, 240, 30), 1371))
        self.assertFalse(learner.fits_name_box((138, 0, 240, 30), 1371))
        self.assertFalse(learner.fits_name_box((138, 1345, 240, 30), 1371))
        for height in [26, 27, 28, 28, 30, 34] * 4:
            learner.learn_height(height)
        self.assertTrue(learner.fits_name_box((138, 200, 240, 28), 1371))
        self.assertFalse(learner.fits_name_box((138, 200, 240, 14), 1371))
        self.assertFalse(learner.fits_name_box((138, 200, 240, 80), 1371))

    def test_a_frame_is_tried_once(self) -> None:
        learner = LayoutLearner()
        self.assertTrue(learner.first_try("frames/f-00001.png"))
        self.assertFalse(learner.first_try("frames/f-00001.png"))
        self.assertTrue(learner.first_try("frames/f-00002.png"))

    def test_the_summary_names_what_was_learned(self) -> None:
        learner = LayoutLearner()
        self.assertEqual(learner.describe(), "")
        for _ in range(3):
            learner.learn_handle(430, 459)
            learner.learn_name_zone(368, 409)
        learner.note("skipped", 7)
        learner.note("rescued", 2)
        text = learner.describe()
        self.assertIn("Quy luật học được", text)
        self.assertIn("430 đến 459", text)
        self.assertIn("368 đến 409", text)
        self.assertIn("bỏ 7 ô dòng sai hình dạng", text)
        self.assertIn("cứu 2 trang hồ sơ", text)

    def test_each_video_has_its_own_learner(self) -> None:
        layout_learn.clear_learners()
        first = layout_learn.learner_for("video-a")
        self.assertIs(first, layout_learn.learner_for("video-a"))
        self.assertIsNot(first, layout_learn.learner_for("video-b"))
        layout_learn.forget_scope("video-a")
        self.assertIsNot(first, layout_learn.learner_for("video-a"))
        layout_learn.clear_learners()


def _profile_page(name: str, handle: str, *, name_top: int = 368, handle_top: int = 430) -> Image.Image:
    image = Image.new("L", (720, 1371), 255)
    pen = ImageDraw.Draw(image)
    pen.ellipse((210, 90, 510, 340), fill=120)
    big = ImageFont.truetype(str(_FONT), 40)
    small = ImageFont.truetype(str(_FONT), 28)
    pen.text(((720 - pen.textlength(name, font=big)) / 2, name_top - 4), name, font=big, fill=10)
    pen.text(((720 - pen.textlength(handle, font=small)) / 2, handle_top - 2), handle, font=small, fill=90)
    return image


class ProfileBandTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_memos()
        layout_learn.clear_learners()
        if not _FONT.is_file() or shutil.which("tesseract") is None or reader_mode() != "api":
            self.skipTest("needs the dejavu font and in-process tesseract")

    def tearDown(self) -> None:
        clear_memos()
        layout_learn.clear_learners()

    def _learner(self) -> LayoutLearner:
        learner = LayoutLearner()
        for _ in range(3):
            learner.learn_handle(430, 458)
            learner.learn_name_zone(368, 410)
        return learner

    def test_ink_box_takes_the_main_line_and_ignores_a_sliver_of_the_neighbour(self) -> None:
        image = Image.new("L", (720, 200), 255)
        pen = ImageDraw.Draw(image)
        pen.rectangle((200, 60, 520, 100), fill=30)
        pen.line((300, 0, 420, 0), fill=30)
        box = people._ink_box(image, (0, 0, 720, 200))
        self.assertEqual(box, (200, 60, 321, 41))
        self.assertIsNone(people._ink_box(Image.new("L", (720, 100), 255), (0, 0, 720, 100)))
        self.assertIsNone(people._ink_box(image, (0, 120, 720, 60)))

    def test_band_read_gets_the_handle_and_the_name_where_they_were_learned(self) -> None:
        page = _profile_page("Tran Tung", "@trn.tng751")
        learner = self._learner()
        handle = people._band_read(page, learner.handle_zone(), "handle")  # type: ignore[arg-type]
        name = people._band_read(page, learner.name_zone(), "name")  # type: ignore[arg-type]
        assert handle is not None and name is not None
        self.assertEqual(handle[0].casefold(), "@trn.tng751")
        self.assertEqual(name[0], "Tran Tung")
        self.assertIsNone(people._band_read(Image.new("L", (720, 1371), 255), learner.handle_zone(), "handle"))  # type: ignore[arg-type]

    def test_a_profile_page_the_whole_frame_read_missed_is_read_from_the_bands(self) -> None:
        page = _profile_page("Tran Tung", "@trn.tng751")
        learner = self._learner()
        lines, tsv, rescued = people._rescue_profile(page, learner, [], "", "v/f-00001.png")
        self.assertTrue(rescued)
        texts = [line.text for line in lines]
        self.assertIn("Tran Tung", texts)
        self.assertTrue(any("@trn.tng751" in text.casefold() for text in texts))
        self.assertIn("\t40\tTran Tung", tsv)
        self.assertEqual(people._profile_sighting(lines) is not None, True)
        again = people._rescue_profile(page, learner, [], "", "v/f-00001.png")
        self.assertFalse(again[2])

    def test_junk_in_the_learned_bands_is_replaced_and_other_words_stay(self) -> None:
        page = _profile_page("Tran Tung", "@trn.tng751")
        learner = self._learner()
        junk = "\n".join(
            [
                "5\t1\t1\t1\t1\t1\t300\t372\t40\t30\t55\tOd",
                "5\t1\t1\t1\t2\t1\t320\t436\t60\t20\t49\tWQSG",
                "5\t1\t2\t1\t1\t1\t100\t700\t90\t30\t95\tFollower",
            ]
        )
        lines, tsv, rescued = people._rescue_profile(page, learner, [], junk, "v/f-00002.png")
        self.assertTrue(rescued)
        texts = [line.text for line in lines]
        self.assertNotIn("Od", texts)
        self.assertNotIn("WQSG", texts)
        self.assertIn("Follower", texts)

    def test_no_rescue_on_a_list_page_or_when_the_handle_was_already_seen_without_a_name_zone(self) -> None:
        page = _profile_page("Tran Tung", "@trn.tng751")
        learner = self._learner()
        list_lines = [TextLine("Danh bạ", 60, 40, 70)]
        self.assertFalse(people._rescue_profile(page, learner, list_lines, "", "v/f-00003.png")[2])
        fresh = LayoutLearner()
        for _ in range(3):
            fresh.learn_handle(430, 458)
        seen = [TextLine("@trn.tng751", 240, 430, 458)]
        self.assertFalse(people._rescue_profile(page, fresh, seen, "", "v/f-00004.png")[2])
        self.assertFalse(people._rescue_profile(page, LayoutLearner(), [], "", "v/f-00005.png")[2])

    def test_a_voted_profile_frame_is_kept_and_teaches_the_zones(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "video.mp4-frames"
            folder.mkdir()
            learner = layout_learn.learner_for(str(folder))
            for _ in range(3):
                learner.learn_handle(430, 458)
                learner.learn_name_zone(368, 410)
            page = _profile_page("Tran Tung", "@trn.tng751")
            path = folder / "f-00001.png"
            page.save(path)
            _lines, found = people.tighten_frame_reading(path, [], [], tsv="", loaded=page)
        self.assertEqual([item["kind"] for item in found], ["profile"])
        self.assertEqual(found[0]["username"].casefold(), "@trn.tng751")
        self.assertEqual(found[0]["name"], "Tran Tung")
        self.assertEqual(learner.counts["rescued"], 1)


class RescuePassTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_memos()
        layout_learn.clear_learners()
        if not _FONT.is_file() or shutil.which("tesseract") is None or reader_mode() != "api":
            self.skipTest("needs the dejavu font and in-process tesseract")

    def tearDown(self) -> None:
        clear_memos()
        layout_learn.clear_learners()

    def test_an_empty_early_frame_is_read_again_once_the_video_taught_the_bands(self) -> None:
        class Sink:
            def __init__(self) -> None:
                self.saved: list[tuple[float, list[str], list[dict[str, str]]]] = []

            def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
                self.saved.append((seconds, captions, sightings))

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "video.mp4-frames"
            folder.mkdir()
            early = folder / "f-00001.png"
            late = folder / "f-00002.png"
            # prepare_frame_image bỏ 4% phía trên của khung 1371 điểm ảnh, tức 54 điểm. Vẽ lùi xuống đúng chừng đó.
            _profile_page("Tran Tung", "@trn.tng751", name_top=368 + 54, handle_top=430 + 54).save(early)
            _profile_page("Ba Soi", "@b.soi22", name_top=368 + 54, handle_top=430 + 54).save(late)
            chosen = [(0.0, early), (0.125, late)]
            list_line = {"kind": "contact", "name": "An", "contactName": "Bao", "username": ""}
            results: list[tuple[float, list[str], list[dict[str, str]]] | None] = [(0.0, [], []), (0.125, ["Danh bạ"], [list_line])]
            sink = Sink()
            self.assertEqual(screen_steps._rescue_empty(chosen, results, sink), (0, 0))
            learner = layout_learn.learner_for(str(folder))
            for _ in range(3):
                learner.learn_handle(430, 458)
                learner.learn_name_zone(368, 410)
            rescued, unblanked = screen_steps._rescue_empty(chosen, results, sink)
        self.assertEqual((rescued, unblanked), (1, 1))
        first = results[0]
        assert first is not None
        self.assertEqual(first[2][0]["name"], "Tran Tung")
        self.assertEqual(results[1], (0.125, ["Danh bạ"], [list_line]))
        self.assertEqual(len(sink.saved), 1)


class HandleRereadTests(unittest.TestCase):
    def test_handles_are_reread_with_rapidocr_and_names_with_the_standard_model(self) -> None:
        saved = (people.read_rapid, people._read_prepared)
        people.read_rapid = lambda picture: "Ngo Thi Linh" if picture.width > 100 else "@abc.def12"  # type: ignore[assignment]
        people._read_prepared = lambda picture, dest, kind: "Ngô Thị Linh"  # type: ignore[assignment]
        try:
            self.assertEqual(people._reread(Image.new("L", (50, 20)), Path("x.png"), "handle"), "@abc.def12")
            self.assertEqual(people._reread(Image.new("L", (300, 20)), Path("x.png"), "name"), "Ngô Thị Linh")
            people.read_rapid = lambda picture: None  # type: ignore[assignment]
            people._read_prepared = lambda picture, dest, kind: "@fallback.1"  # type: ignore[assignment]
            self.assertEqual(people._reread(Image.new("L", (50, 20)), Path("x.png"), "handle"), "@fallback.1")
        finally:
            people.read_rapid, people._read_prepared = saved  # type: ignore[assignment]


class NameGateTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_memos()

    def _votes(self, learner: LayoutLearner, line: TextLine, tsv: str, *, list_frame: bool) -> list[str]:
        asked: list[str] = []

        def fake_vote(image, box, dest, kind, seed, memo=None, frame_id="", on_settle=None):  # type: ignore[no-untyped-def]
            del image, box, dest, memo, frame_id, on_settle
            asked.append(f"{kind}:{seed}")
            return seed, True

        original = people._vote_box
        people._vote_box = fake_vote
        try:
            image = Image.new("L", (720, 400), 255)
            people._apply_line_votes(line, image, tsv, Path("x.png"), None, "f", learner, list_frame)
        finally:
            people._vote_box = original
        return asked

    def test_a_clipped_line_on_a_contact_list_is_not_voted(self) -> None:
        learner = LayoutLearner()
        tsv = "5\t1\t1\t1\t1\t1\t138\t380\t200\t19\t90\tLan"
        line = TextLine("Lan Anh", 138, 380, 399)
        self.assertEqual(self._votes(learner, line, tsv, list_frame=True), [])
        self.assertEqual(learner.counts["skipped"], 1)

    def test_the_same_line_on_a_profile_page_is_voted(self) -> None:
        learner = LayoutLearner()
        tsv = "5\t1\t1\t1\t1\t1\t138\t380\t200\t19\t90\tLan"
        line = TextLine("Lan Anh", 138, 380, 399)
        self.assertEqual(self._votes(learner, line, tsv, list_frame=False), ["name:Lan Anh"])
        self.assertEqual(learner.counts["skipped"], 0)

    def test_a_whole_line_inside_the_learned_band_is_voted(self) -> None:
        learner = LayoutLearner()
        for height in [26, 27, 28, 28, 30, 34] * 4:
            learner.learn_height(height)
        tsv = "5\t1\t1\t1\t1\t1\t138\t200\t200\t28\t90\tLan"
        line = TextLine("Lan Anh", 138, 200, 228)
        self.assertEqual(self._votes(learner, line, tsv, list_frame=True), ["name:Lan Anh"])
        tall = "5\t1\t1\t1\t1\t1\t138\t200\t540\t80\t90\tLan"
        self.assertEqual(self._votes(learner, TextLine("Lan Anh", 138, 200, 280), tall, list_frame=True), [])


if __name__ == "__main__":
    unittest.main()
