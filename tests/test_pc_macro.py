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
