"""Số lõi theo lúc máy rảnh, báo cấu hình PC, và đồng hồ từng bước."""

from __future__ import annotations

import os
import unittest

from control_plane import stage_timing
from control_plane.video_helpers import HelperBook, clean_hardware, clean_timing, slots_for
from pc_agent import pc_hardware, pc_power
from pc_agent.video_worker import hardware_line, idle_budget, worker_budget


class PowerBudgetTests(unittest.TestCase):
    def test_a_rested_pc_uses_all_cores_but_one(self) -> None:
        self.assertEqual(worker_budget(20, 65277 * 1024 * 1024), (16, 4))
        self.assertEqual(idle_budget(20, 65277 * 1024 * 1024), 19)
        self.assertEqual(idle_budget(1, None), 1)
        self.assertEqual(idle_budget(2, None), 1)
        self.assertEqual(idle_budget(8, None), 7)

    def test_ram_still_caps_the_rested_budget(self) -> None:
        self.assertEqual(idle_budget(20, 2 * 1024 * 1024 * 1024), 6)
        self.assertEqual(idle_budget(20, 100 * 1024 * 1024), 1)

    def test_budget_rises_after_the_idle_wait_and_drops_at_once(self) -> None:
        budget = pc_power.PowerBudget(16, 19, idle_after=120)
        self.assertEqual(budget.workers, 16)
        self.assertFalse(budget.update(30.0))
        self.assertFalse(budget.update(None))
        self.assertTrue(budget.update(121.0))
        self.assertEqual(budget.workers, 19)
        self.assertTrue(budget.is_resting)
        self.assertFalse(budget.update(500.0))
        self.assertTrue(budget.update(2.0))
        self.assertEqual(budget.workers, 16)
        self.assertFalse(budget.is_resting)

    def test_a_pc_with_no_spare_core_never_changes(self) -> None:
        budget = pc_power.PowerBudget(1, 1)
        self.assertFalse(budget.update(9999.0))
        self.assertFalse(budget.is_resting)

    def test_system_calls_are_safe_when_the_machine_is_not_windows(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows")
        self.assertIsNone(pc_power.idle_seconds())
        self.assertFalse(pc_power.keep_full_speed())
        self.assertFalse(pc_power.raise_this_thread())


class HelperSlotTests(unittest.TestCase):
    def test_a_gpu_pc_holds_four_and_a_cpu_pc_holds_two(self) -> None:
        self.assertEqual(slots_for(False), 2)
        self.assertEqual(slots_for(True), 4)
        book = HelperBook(None)
        book.beat("cpu", "cpu", 8, gpu=False)
        book.beat("gpu", "gpu", 8, gpu=True, gpu_name="RTX")
        self.assertTrue(book.try_hold("cpu"))
        self.assertTrue(book.try_hold("cpu"))
        self.assertFalse(book.try_hold("cpu"))
        for _ in range(4):
            self.assertTrue(book.try_hold("gpu"))
        self.assertFalse(book.try_hold("gpu"))


class HardwareReportTests(unittest.TestCase):
    def test_collect_returns_small_plain_values(self) -> None:
        found = pc_hardware.collect(20, 65277 * 1024 * 1024)
        self.assertEqual(found["logical"], 20)
        self.assertEqual(found["ramMb"], 65277)
        self.assertIsInstance(found["cpu"], str)
        self.assertIsInstance(found["gpus"], list)
        self.assertGreaterEqual(int(found["physical"]), 0)
        self.assertGreaterEqual(int(found["tempFreeMb"]), 0)

    def test_the_console_line_names_the_cores_and_card(self) -> None:
        text = hardware_line(
            {"cpu": "Intel Core i7-12700K", "physical": 12, "logical": 20, "gpus": ["Intel UHD 770"], "tempFreeMb": 204800}
        )
        self.assertIn("Intel Core i7-12700K", text)
        self.assertIn("12 lõi vật lý, 20 luồng", text)
        self.assertIn("Intel UHD 770", text)
        self.assertIn("200 GB", text)
        self.assertEqual(hardware_line({}), "")

    def test_the_hub_keeps_only_known_clean_fields(self) -> None:
        hardware = clean_hardware(
            {
                "cpu": "  Intel   Core\ti7 " + "x" * 200,
                "physical": 12,
                "logical": True,
                "ramMb": 65277,
                "gpus": ["NVIDIA RTX", "", "AMD " + "y" * 200, "a", "b", "c"],
                "evil": "<script>",
            }
        )
        self.assertNotIn("evil", hardware)
        self.assertNotIn("logical", hardware)
        self.assertEqual(hardware["physical"], 12)
        self.assertLessEqual(len(str(hardware["cpu"])), 80)
        self.assertNotIn("\t", str(hardware["cpu"]))
        self.assertEqual(len(hardware["gpus"]), 4 - 1)  # type: ignore[arg-type]
        self.assertEqual(clean_hardware("x"), {})
        timing = clean_timing(
            {"frames": 240, "readMs": 1001.7, "voteMs": -5, "extra": 9, "third": "7", "rereads": False, "skipped": 175, "rescued": 43}
        )
        self.assertEqual(timing, {"frames": 240, "readMs": 1001, "voteMs": 0, "skipped": 175, "rescued": 43})

    def test_the_hub_remembers_hardware_and_timing_of_the_best_pc(self) -> None:
        book = HelperBook()
        book.beat(
            "pc-1",
            "DESKTOP",
            20,
            workers=19,
            build=19,
            hardware={"cpu": "Intel Core i7-12700K", "physical": 12, "logical": 20},
            timing={"frames": 240, "readMs": 260, "voteMs": 400},
        )
        shown = book.public()
        self.assertEqual(shown["hardware"]["cpu"], "Intel Core i7-12700K")  # type: ignore[index]
        self.assertEqual(shown["timing"]["voteMs"], 400)  # type: ignore[index]
        book.beat("pc-1", "DESKTOP", 20, workers=19, build=19)
        shown = book.public()
        self.assertEqual(shown["hardware"]["physical"], 12)  # type: ignore[index]


class StageTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        stage_timing.reset()

    def tearDown(self) -> None:
        stage_timing.reset()

    def test_summary_gives_mean_milliseconds_per_frame(self) -> None:
        for _ in range(4):
            stage_timing.add("read", 0.25)
            stage_timing.add("vote", 0.5)
            stage_timing.add("thumb", 0.01)
        stage_timing.add("ffmpeg", 7.6)
        for _ in range(10):
            stage_timing.bump("vote.lines")
        stage_timing.bump("vote.reused", 6)
        stage_timing.bump("vote.rereads", 2)
        stage_timing.bump("vote.skipped", 3)
        stage_timing.bump("zone.rescued", 5)
        found = stage_timing.summary(stage_timing.snapshot())
        self.assertEqual(found["frames"], 4)
        self.assertEqual(found["readMs"], 250)
        self.assertEqual(found["voteMs"], 500)
        self.assertEqual(found["thumbMs"], 10)
        self.assertEqual(found["ffmpegSec"], 8)
        self.assertEqual((found["voteLines"], found["reused"], found["rereads"]), (10, 6, 2))
        self.assertEqual((found["skipped"], found["rescued"]), (3, 5))
        text = stage_timing.describe(stage_timing.snapshot())
        self.assertIn("4 khung", text)
        self.assertIn("dùng lại 6", text)
        self.assertIn("bỏ ô sai hình 3", text)
        self.assertIn("Cứu 5 trang hồ sơ", text)

    def test_take_clears_the_clock_and_an_empty_clock_says_nothing(self) -> None:
        stage_timing.add("read", 1.0)
        self.assertTrue(stage_timing.take())
        self.assertEqual(stage_timing.take(), {})
        self.assertEqual(stage_timing.describe({}), "")
        with stage_timing.timed("read"):
            pass
        self.assertEqual(stage_timing.snapshot()["read"][1], 1)


if __name__ == "__main__":
    unittest.main()
