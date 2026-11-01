"""Desktop macros remember keyboard and mouse actions, then replay them."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pc_macro.desktop import key_name
from pc_macro.events import default_title, point_in_box, sanitize_events, schedule, script_lines
from pc_macro.numbers import NumberQueue, apply_turn, bump_number, digit_spans, parse_number_list, pick_span
from pc_macro.session import Player, Recorder
from pc_macro.store import MacroStore


class SanitizeTests(unittest.TestCase):
    def test_keeps_keys_clicks_and_drops_stop_hotkey(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "a"},
                {"t": 10, "kind": "key", "action": "up", "key": "a"},
                {"t": 20, "kind": "key", "action": "down", "key": "F8"},
                {"t": 30, "kind": "click", "action": "up", "button": "left", "x": 12.4, "y": 40.6},
                {"t": 40, "kind": "scroll", "x": 10, "y": 20, "dx": 0, "dy": -2},
                {"t": 50, "kind": "click", "action": "up", "button": "left", "x": -3, "y": 1},
                {"t": 60, "kind": "explode", "key": "a"},
            ]
        )
        self.assertEqual(
            events,
            [
                {"t": 0, "kind": "key", "action": "down", "key": "a"},
                {"t": 10, "kind": "key", "action": "up", "key": "a"},
                {"t": 30, "kind": "click", "action": "up", "button": "left", "x": 12, "y": 41},
                {"t": 40, "kind": "scroll", "x": 10, "y": 20, "dx": 0, "dy": -2},
            ],
        )

    def test_script_collapses_typing(self) -> None:
        events = sanitize_events(
            [
                {"t": 1, "kind": "key", "action": "down", "key": "h"},
                {"t": 2, "kind": "key", "action": "up", "key": "h"},
                {"t": 3, "kind": "key", "action": "down", "key": "i"},
                {"t": 4, "kind": "key", "action": "down", "key": "Enter"},
                {"t": 5, "kind": "click", "action": "down", "button": "left", "x": 8, "y": 9},
                {"t": 6, "kind": "click", "action": "up", "button": "left", "x": 8, "y": 9},
            ]
        )
        self.assertEqual(script_lines(events), ["Gõ hi", "Phím enter", "Bấm trái tại 8,9"])
        self.assertEqual(default_title(events), "3 phím · 1 lần bấm")

    def test_schedule_shortens_long_gaps(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "a"},
                {"t": 20_000, "kind": "key", "action": "down", "key": "b"},
            ]
        )
        waits = [wait for wait, _event in schedule(events)]
        self.assertEqual(waits[0], 0)
        self.assertEqual(waits[1], 2000)


class RecorderPlayerTests(unittest.TestCase):
    def test_recorder_skips_window_clicks_and_stops_on_f8(self) -> None:
        recorder = Recorder(ignore_point=lambda x, y: 10 <= x <= 50 and 10 <= y <= 50)
        recorder.start(1000)
        self.assertTrue(recorder.add(1100, {"kind": "key", "action": "down", "key": "a"}))
        self.assertTrue(recorder.add(1200, {"kind": "click", "action": "up", "button": "left", "x": 20, "y": 20}))
        self.assertTrue(recorder.add(1300, {"kind": "click", "action": "up", "button": "left", "x": 400, "y": 80}))
        self.assertFalse(recorder.add(1400, {"kind": "key", "action": "down", "key": "f8"}))
        self.assertFalse(recorder.on)
        kinds = [(event["kind"], event.get("key"), event.get("x")) for event in recorder.events]
        self.assertEqual(kinds, [("key", "a", None), ("click", None, 400)])

    def test_player_repeats_and_can_stop(self) -> None:
        sent: list[str] = []
        slept: list[float] = []

        def send(event: dict) -> None:
            sent.append(str(event.get("key")))
            if len(sent) == 3:
                player.cancel()

        player = Player(send, slept.append)
        events = [
            {"t": 0, "kind": "key", "action": "down", "key": "a"},
            {"t": 100, "kind": "key", "action": "down", "key": "b"},
        ]
        result = player.play(events, times=0, gap_ms=250)
        self.assertTrue(result["stopped"])
        self.assertEqual(sent, ["a", "b", "a"])
        self.assertEqual(result["loops"], 1)
        self.assertGreaterEqual(len(slept), 1)

    def test_player_runs_requested_times(self) -> None:
        sent: list[str] = []
        player = Player(lambda event: sent.append(str(event["key"])), lambda _s: None)
        events = [{"t": 0, "kind": "key", "action": "down", "key": "x"}]
        result = player.play(events, times=3, gap_ms=0)
        self.assertEqual(sent, ["x", "x", "x"])
        self.assertEqual(result, {"loops": 3, "played": 3, "stopped": False})


class NumberListTests(unittest.TestCase):
    def test_detects_and_replaces_the_longest_number(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "1"},
                {"t": 5, "kind": "key", "action": "up", "key": "1"},
                {"t": 10, "kind": "key", "action": "down", "key": "0"},
                {"t": 15, "kind": "key", "action": "up", "key": "0"},
                {"t": 20, "kind": "key", "action": "down", "key": "0"},
                {"t": 25, "kind": "key", "action": "up", "key": "0"},
                {"t": 30, "kind": "key", "action": "down", "key": "1"},
                {"t": 35, "kind": "key", "action": "up", "key": "1"},
                {"t": 40, "kind": "key", "action": "down", "key": "Enter"},
                {"t": 50, "kind": "key", "action": "down", "key": "8"},
                {"t": 55, "kind": "key", "action": "up", "key": "8"},
            ]
        )
        spans = digit_spans(events)
        self.assertEqual([span["value"] for span in spans], ["1001", "8"])
        self.assertEqual(pick_span(spans), 0)
        queue = NumberQueue(["2050", "3300"], step=1, bump=True, span=0)
        first, used = apply_turn(events, spans, queue)
        self.assertEqual(used, "2050")
        self.assertEqual([span["value"] for span in digit_spans(first)], ["2050", "8"])
        self.assertEqual(queue.items, ["2051", "3300"])
        second, used = apply_turn(events, spans, queue)
        self.assertEqual(used, "3300")
        self.assertEqual([span["value"] for span in digit_spans(second)], ["3300", "8"])
        self.assertEqual(queue.items, ["2051", "3301"])

    def test_parse_list_and_keep_leading_zeros(self) -> None:
        self.assertEqual(parse_number_list("1001, 2050\n3300 SPX"), ["1001", "2050", "3300"])
        self.assertEqual(bump_number("0010", 1), "0011")
        self.assertEqual(bump_number("999", 1), "1000")
        queue = NumberQueue([], bump=True, step=1, span=0)
        self.assertEqual(queue.take("1001"), "1001")
        self.assertEqual(queue.items, ["1002"])
        self.assertEqual(queue.take("1001"), "1002")

    def test_player_types_next_number_each_loop(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "1"},
                {"t": 1, "kind": "key", "action": "up", "key": "1"},
            ]
        )
        spans = digit_spans(events)
        queue = NumberQueue(["1", "2"], bump=False, span=0)
        sent: list[str] = []

        def prepare(_loop: int) -> list:
            batch, _used = apply_turn(events, spans, queue)
            return batch

        player = Player(
            lambda event: sent.append(str(event["key"])) if event.get("action") == "down" else None,
            lambda _s: None,
        )
        result = player.play(events, times=2, gap_ms=0, prepare=prepare)
        self.assertEqual(sent, ["1", "2"])
        self.assertEqual(result["loops"], 2)


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MacroStore(Path(tmp))
            events = [{"t": 0, "kind": "key", "action": "down", "key": "a"}]
            first = store.save("Mở notepad", events)
            second = store.save("Mở notepad", events)
            self.assertEqual(first["name"], "Mở notepad")
            self.assertNotEqual(first["slug"], second["slug"])
            listed = store.list()
            self.assertEqual(len(listed), 2)
            loaded = store.get(first["slug"])
            assert loaded is not None
            self.assertEqual(loaded["events"][0]["key"], "a")
            self.assertTrue(store.delete(first["slug"]))
            self.assertEqual(len(store.list()), 1)
            again = store.save(
                "Có số",
                [{"t": 0, "kind": "key", "action": "down", "key": "1"}],
                numbers={"items": ["1001"], "step": 2, "bump": True, "span": 0},
            )
            loaded = store.get(again["slug"])
            assert loaded is not None
            self.assertEqual(loaded["numbers"]["items"], ["1001"])
            self.assertEqual(loaded["numbers"]["step"], 2)
            store.save(
                "Có số",
                [{"t": 0, "kind": "key", "action": "down", "key": "1"}],
                slug=again["slug"],
                numbers={"items": ["1003"], "step": 2, "bump": True, "span": 0},
            )
            self.assertEqual(store.get(again["slug"])["numbers"]["items"], ["1003"])
            self.assertEqual(len(store.list()), 2)


class HelperTests(unittest.TestCase):
    def test_point_in_box_uses_padding(self) -> None:
        box = (100.0, 50.0, 200.0, 150.0)
        self.assertTrue(point_in_box(100, 50, box))
        self.assertTrue(point_in_box(96, 50, box, pad=8))
        self.assertFalse(point_in_box(80, 50, box, pad=8))

    def test_key_name_reads_pynput_style_objects(self) -> None:
        class _Char:
            char = "a"
            name = None

        class _Special:
            char = None
            name = "ctrl_l"

        class _Space:
            char = " "
            name = "space"

        self.assertEqual(key_name(_Char()), "a")
        self.assertEqual(key_name(_Special()), "ctrl")
        self.assertEqual(key_name(_Space()), "space")


class CliTests(unittest.TestCase):
    def test_list_and_show(self) -> None:
        from pc_macro.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PC_MACRO_DIR"] = tmp
            try:
                MacroStore(Path(tmp)).save(
                    "Gõ a",
                    [{"t": 0, "kind": "key", "action": "down", "key": "a"}],
                )
                listed = io.StringIO()
                with redirect_stdout(listed):
                    self.assertEqual(main(["list"]), 0)
                self.assertIn("Gõ a", listed.getvalue())
                shown = io.StringIO()
                with redirect_stdout(shown):
                    self.assertEqual(main(["show", "Gõ a"]), 0)
                self.assertIn("Gõ a", shown.getvalue())
            finally:
                os.environ.pop("PC_MACRO_DIR", None)


if __name__ == "__main__":
    unittest.main()
