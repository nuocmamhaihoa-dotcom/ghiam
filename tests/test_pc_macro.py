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
from pc_macro.numbers import (
    NumberQueue,
    apply_turn,
    bump_number,
    digit_spans,
    make_code,
    normalize_digits,
    normalize_prefix,
    parse_number_list,
    pick_span,
)
from pc_macro.script import (
    compile_script,
    delete_step,
    events_to_steps,
    insert_steps,
    merge_recording,
    move_step,
    new_step,
    number_enter_steps,
    replace_step,
    script_captions,
    steps_to_events,
)
from PIL import Image

from pc_macro.paint import paint_centroid, paint_point
from pc_macro.screen import (
    append_screen_click,
    apply_click_names,
    change_percent,
    click_from_paint,
    click_landed,
    click_order_cards,
    click_order_labels,
    click_slots,
    make_grid,
    overlay_banner,
    play_named_clicks,
    recover_after_enter,
    sanitize_clicks,
    scale_clicks,
    should_recover,
    start_both_scripts_status,
    upsert_slot,
)
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

    def test_player_after_error_keeps_next_number(self) -> None:
        sent: list[str] = []
        turns = {"n": 0}

        def prepare(_loop: int) -> list:
            turns["n"] += 1
            if turns["n"] > 2:
                return []
            return [{"t": 0, "kind": "key", "action": "down", "key": str(turns["n"])}]

        def after(_loop: int) -> list:
            if turns["n"] == 1:
                raise RuntimeError("click failed")
            return []

        player = Player(lambda event: sent.append(str(event.get("key"))), lambda _s: None)
        result = player.play([], times=0, gap_ms=0, prepare=prepare, after=after)
        self.assertEqual(result["loops"], 2)
        self.assertFalse(result["stopped"])
        self.assertEqual(sent, ["1", "2"])

    def test_player_send_error_keeps_loop(self) -> None:
        sent: list[str] = []

        def send(event: dict) -> None:
            key = str(event.get("key") or "")
            if key == "x":
                raise RuntimeError("send failed")
            if event.get("action") == "down":
                sent.append(key)

        turns = {"n": 0}

        def prepare(_loop: int) -> list:
            turns["n"] += 1
            if turns["n"] == 1:
                return [
                    {"t": 0, "kind": "key", "action": "down", "key": "x"},
                    {"t": 1, "kind": "key", "action": "down", "key": "a"},
                ]
            if turns["n"] == 2:
                return [{"t": 0, "kind": "key", "action": "down", "key": "b"}]
            return []

        player = Player(send, lambda _s: None)
        result = player.play([], times=0, gap_ms=0, prepare=prepare)
        self.assertEqual(result["loops"], 2)
        self.assertFalse(result["stopped"])
        self.assertEqual(sent, ["a", "b"])

    def test_named_click_send_error_retries_then_continues(self) -> None:
        sent: list[str] = []
        blows = {"n": 0}

        def send(event: dict) -> None:
            if event.get("action") != "down":
                return
            if event.get("x") == 1 and blows["n"] < 1:
                blows["n"] += 1
                raise RuntimeError("click dropped")
            sent.append(str(event.get("x")))

        still = make_grid((10, 10, 10))
        results = play_named_clicks(
            [{"x": 1, "y": 2, "name": "Mở"}, {"x": 3, "y": 4, "name": "Đóng"}],
            send,
            lambda _s: None,
            capture=lambda _x, _y: still,
            compile_events=lambda step: [{"kind": "click", "action": "down", "x": step["x"], "y": step["y"]}],
        )
        self.assertEqual([item["name"] for item in results], ["Mở", "Đóng"])
        self.assertGreaterEqual(results[0]["attempts"], 2)
        self.assertIn("1", sent)
        self.assertIn("3", sent)

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
        self.assertEqual(queue.last_used, "1002")
        self.assertEqual(queue.flag("1042"), "1042")
        self.assertEqual(queue.flagged, ["1042"])
        again = NumberQueue.from_dict(queue.to_dict())
        self.assertEqual(again.flagged, ["1042"])
        self.assertEqual(again.last_used, "1002")

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


class PrefixRangeTests(unittest.TestCase):
    def test_prefix_and_five_digits_walk_start_to_end(self) -> None:
        self.assertEqual(normalize_prefix("spx-ab!!99"), "spxab")
        self.assertEqual(normalize_digits("7"), "00007")
        self.assertEqual(make_code("SPXAB", "1"), "SPXAB00001")
        queue = NumberQueue(range_mode=True, prefix="SPXAB", start="00001", end="00003", step=1)
        self.assertEqual(queue.peek(), "SPXAB00001")
        self.assertEqual(queue.take(), "SPXAB00001")
        self.assertEqual(queue.take(), "SPXAB00002")
        self.assertEqual(queue.take(), "SPXAB00003")
        self.assertEqual(queue.take(), "")
        self.assertTrue(queue.exhausted)
        self.assertEqual(queue.peek(), "")
        again = NumberQueue.from_dict(queue.to_dict())
        self.assertEqual(again.prefix, "SPXAB")
        self.assertEqual(again.start, "00001")
        self.assertEqual(again.end, "00003")
        self.assertTrue(again.exhausted)
        self.assertEqual(again.peek(), "")

    def test_short_prefix_does_not_start_and_prefix_can_change(self) -> None:
        queue = NumberQueue(range_mode=True, prefix="AB", start="00010", end="00012")
        self.assertEqual(queue.peek(), "")
        queue = NumberQueue(
            range_mode=True,
            prefix="XYZ12",
            start="00010",
            end="00012",
            current="00011",
            step=1,
        )
        self.assertEqual(queue.take(), "XYZ1200011")
        self.assertEqual(queue.peek(), "XYZ1200012")
        self.assertEqual(queue.flag(), "XYZ1200011")
        self.assertEqual(queue.flagged, ["XYZ1200011"])

    def test_wider_end_resumes_after_last_number(self) -> None:
        queue = NumberQueue(range_mode=True, prefix="SPXAB", start="00008", end="00008")
        self.assertEqual(queue.take(), "SPXAB00008")
        self.assertTrue(queue.exhausted)
        resumed = NumberQueue.from_dict({**queue.to_dict(), "end": "00010", "exhausted": False})
        self.assertEqual(resumed.peek(), "SPXAB00009")
        self.assertEqual(resumed.take(), "SPXAB00009")

    def test_player_types_full_code_then_enter(self) -> None:
        queue = NumberQueue(range_mode=True, prefix="SPXAB", start="00001", end="00002")
        sent: list[str] = []

        def prepare(_loop: int) -> list:
            value = queue.take()
            if not value:
                return []
            _script, batch = compile_script(number_enter_steps(value))
            return batch

        player = Player(
            lambda event: sent.append(str(event["key"])) if event.get("action") == "down" else None,
            lambda _s: None,
        )
        result = player.play([], times=0, gap_ms=0, prepare=prepare)
        self.assertEqual(result["loops"], 2)
        self.assertEqual(sent[:11], ["S", "P", "X", "A", "B", "0", "0", "0", "0", "1", "enter"])
        self.assertEqual(sent[11:22], ["S", "P", "X", "A", "B", "0", "0", "0", "0", "2", "enter"])

    def test_screen_change_and_four_clicks_then_continue(self) -> None:
        same = make_grid((10, 10, 10))
        self.assertEqual(change_percent(same, same), 0.0)
        changed = make_grid((10, 10, 10), flip=int(0.85 * 48 * 27))
        hit, percent = should_recover(same, changed, 80)
        self.assertTrue(hit)
        self.assertGreaterEqual(percent, 80)
        miss, _percent = should_recover(same, make_grid((10, 10, 10), flip=10), 80)
        self.assertFalse(miss)
        self.assertEqual(len(sanitize_clicks([{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}, {"x": 7, "y": 8}, {"x": 9, "y": 10}])), 4)
        self.assertEqual(
            click_order_labels([{"x": 10, "y": 20}]),
            [
                "Nút 1  →  chuột trái  ·  10 , 20",
                "Nút 2  →  chưa ghi",
                "Nút 3  →  chưa ghi",
                "Nút 4  →  chưa ghi",
            ],
        )
        cards = click_order_cards([{"x": 10, "y": 20}, {"x": 30, "y": 40}])
        self.assertEqual(len(cards), 4)
        self.assertEqual(cards[0]["title"], "Bước 1")
        self.assertIn("10 , 20", cards[0]["detail"])
        self.assertEqual(cards[3]["detail"], "chưa ghi")
        first = append_screen_click([], 11, 22, 1920, 1080)
        second = append_screen_click(first, 33, 44, 1920, 1080)
        self.assertEqual([item["x"] for item in second], [11, 33])
        self.assertEqual(second[0]["sw"], 1920)
        self.assertAlmostEqual(second[0]["rx"], 11 / 1920)
        self.assertEqual(overlay_banner(0, recording=True), "Bấm trái nút 1/4 trên màn hình. Esc hủy.")
        self.assertEqual(overlay_banner(3, recording=True), "Bấm trái nút 4/4 trên màn hình. Esc hủy.")
        self.assertIn("Lưu", overlay_banner(4, recording=False, awaiting_save=True))
        self.assertIn("1 → 2 → 3 → 4", overlay_banner(4, recording=False))
        scaled = scale_clicks(
            [{"x": 960, "y": 540, "sw": 1920, "sh": 1080, "rx": 0.5, "ry": 0.5}],
            1280,
            720,
        )
        self.assertEqual(len(scaled), 1)
        self.assertEqual(scaled[0]["x"], 640)
        self.assertEqual(scaled[0]["y"], 360)
        self.assertEqual(scaled[0]["sw"], 1280)
        keep = scale_clicks([{"x": 11, "y": 22}], 1920, 1080)
        self.assertEqual((keep[0]["x"], keep[0]["y"]), (11, 22))
        again_ratio = NumberQueue.from_dict(
            NumberQueue(
                range_mode=True,
                prefix="SPXAB",
                start="00001",
                end="00002",
                recover_clicks=[{"x": 960, "y": 540, "sw": 1920, "sh": 1080, "name": "Nút OK"}],
            ).to_dict()
        )
        self.assertAlmostEqual(again_ratio.recover_clicks[0]["rx"], 0.5)
        self.assertEqual(again_ratio.recover_clicks[0]["name"], "Nút OK")
        named = apply_click_names([{"x": 10, "y": 20}], ["Xác nhận"])
        self.assertEqual(named[0]["name"], "Xác nhận")
        same_patch = make_grid((10, 10, 10))
        changed_patch = make_grid((10, 10, 10), flip=80)
        self.assertFalse(click_landed(same_patch, same_patch)[0])
        self.assertTrue(click_landed(same_patch, changed_patch)[0])
        patches = [same_patch, same_patch, same_patch, changed_patch]
        sent: list[str] = []

        def capture(_x: int, _y: int):
            return patches.pop(0)

        def send(event):
            if event.get("action") == "down":
                sent.append(str(event.get("x")))

        results = play_named_clicks(
            [{"x": 11, "y": 22, "name": "Đóng"}],
            send,
            lambda _s: None,
            capture=capture,
            compile_events=lambda step: [{"kind": "click", "action": "down", "x": step["x"], "y": step["y"]}],
        )
        self.assertEqual(results[0]["name"], "Đóng")
        self.assertEqual(results[0]["attempts"], 2)
        self.assertTrue(results[0]["landed"])
        self.assertEqual(sent, ["11", "11"])
        queue = NumberQueue(
            range_mode=True,
            prefix="SPXAB",
            start="00001",
            end="00003",
            recover_clicks=[{"x": 11, "y": 22}, {"x": 33, "y": 44}, {"x": 55, "y": 66}, {"x": 77, "y": 88}],
            change_threshold=80,
        )
        again = NumberQueue.from_dict(queue.to_dict())
        self.assertEqual(len(again.recover_clicks), 4)
        self.assertEqual(again.recover_clicks[0]["x"], 11)
        self.assertEqual(again.change_threshold, 80)
        sent: list[str] = []
        after_calls = {"n": 0}

        def prepare(_loop: int) -> list:
            value = queue.take()
            if not value:
                return []
            _script, batch = compile_script(number_enter_steps(value))
            return batch

        def after(_loop: int) -> list:
            after_calls["n"] += 1
            if after_calls["n"] != 1:
                return []
            queue.flag(queue.last_used)
            _script, extra = compile_script(list(queue.recover_clicks))
            return extra

        player = Player(
            lambda event: sent.append(f"{event.get('kind')}:{event.get('key') or event.get('x')}")
            if event.get("action") == "down"
            else None,
            lambda _s: None,
        )
        result = player.play([], times=0, gap_ms=0, prepare=prepare, after=after)
        self.assertEqual(result["loops"], 3)
        self.assertEqual(queue.flagged, ["SPXAB00001"])
        self.assertIn("click:11", sent)
        self.assertIn("click:77", sent)
        self.assertLess(sent.index("key:enter"), sent.index("click:11"))
        same = make_grid((10, 10, 10))
        flipped = make_grid((10, 10, 10), flip=int(0.85 * 48 * 27))
        four = [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}, {"x": 7, "y": 8}]
        hit, percent, clicks = recover_after_enter(same, flipped, 80, four)
        self.assertTrue(hit)
        self.assertGreaterEqual(percent, 80)
        self.assertEqual(len(clicks), 4)
        miss, _percent, none = recover_after_enter(same, same, 80, four)
        self.assertFalse(miss)
        self.assertEqual(none, [])
        forced, _percent, clicks = recover_after_enter(same, same, 80, four, forced=True)
        self.assertTrue(forced)
        self.assertEqual(len(clicks), 4)
        changed, _percent, empty = recover_after_enter(same, flipped, 80, [{"x": 1, "y": 2}])
        self.assertTrue(changed)
        self.assertEqual(empty, [])
        self.assertIn("cả hai kịch bản", start_both_scripts_status(four, 80))
        self.assertIn("Chưa đủ 4 nút", start_both_scripts_status([], 80))

    def test_paint_centroid_and_upsert_keep_step_order(self) -> None:
        image = Image.new("RGB", (100, 80), (12, 12, 12))
        for x in range(40, 51):
            for y in range(20, 31):
                image.putpixel((x, y), (255, 0, 170))
        x, y, count = paint_centroid(image)
        self.assertGreaterEqual(count, 8)
        self.assertEqual((x, y), (45, 25))
        self.assertIsNone(paint_centroid(Image.new("RGB", (20, 20), (12, 12, 12))))
        hit = paint_point(image)
        self.assertEqual(hit["sw"], 100)
        self.assertEqual(hit["sh"], 80)
        painted = click_from_paint(image, name="Nút OK", step=2)
        self.assertEqual(painted["via"], "paint")
        self.assertEqual(painted["step"], 2)
        self.assertEqual(painted["name"], "Nút OK")
        only_third = upsert_slot([], 2, painted["x"], painted["y"], 100, 80, "Nút OK", via="paint")
        self.assertEqual(len(only_third), 1)
        self.assertEqual(only_third[0]["step"], 2)
        slots = click_slots(only_third)
        self.assertIsNone(slots[0])
        self.assertIsNone(slots[1])
        self.assertEqual(slots[2]["x"], painted["x"])
        cards = click_order_cards(only_third)
        self.assertEqual(cards[0]["detail"], "chưa ghi")
        self.assertIn("bôi màu", cards[2]["detail"])
        first = upsert_slot(only_third, 0, 10, 12, 100, 80, via="point")
        self.assertEqual([item["step"] for item in first], [0, 2])
        named = apply_click_names(first, ["Một", "", "Ba", ""])
        self.assertEqual(named[0]["name"], "Một")
        self.assertEqual(named[1]["name"], "Ba")


class ScriptStepTests(unittest.TestCase):
    def test_number_then_enter_is_the_default_job(self) -> None:
        steps, events = compile_script(number_enter_steps("1001"))
        self.assertEqual([step["kind"] for step in steps], ["number", "key"])
        self.assertEqual(steps[0]["value"], "1001")
        self.assertEqual(steps[1]["key"], "enter")
        keys = [event["key"] for event in events if event.get("action") == "down"]
        self.assertEqual(keys, ["1", "0", "0", "1", "enter"])
        coded, typed = compile_script(number_enter_steps("SPXAB00001"))
        self.assertEqual([step["kind"] for step in coded], ["type", "key"])
        self.assertEqual(coded[0]["value"], "SPXAB00001")
        typed_keys = [event["key"] for event in typed if event.get("action") == "down"]
        self.assertEqual(typed_keys, ["S", "P", "X", "A", "B", "0", "0", "0", "0", "1", "enter"])

    def test_collapses_and_compiles_back(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "h"},
                {"t": 5, "kind": "key", "action": "up", "key": "h"},
                {"t": 10, "kind": "key", "action": "down", "key": "i"},
                {"t": 15, "kind": "key", "action": "up", "key": "i"},
                {"t": 40, "kind": "key", "action": "down", "key": "Enter"},
                {"t": 50, "kind": "click", "action": "down", "button": "left", "x": 8, "y": 9},
                {"t": 60, "kind": "click", "action": "up", "button": "left", "x": 8, "y": 9},
            ]
        )
        steps = events_to_steps(events)
        self.assertEqual([step["kind"] for step in steps], ["type", "key", "click"])
        self.assertEqual(steps[0]["value"], "hi")
        self.assertEqual(script_captions(steps), ["Gõ hi", "Phím enter", "Bấm trái tại 8,9"])
        replayed = steps_to_events(steps)
        downs = [event["key"] for event in replayed if event.get("kind") == "key" and event.get("action") == "down"]
        self.assertEqual(downs, ["h", "i", "enter"])
        clicks = [event for event in replayed if event.get("kind") == "click" and event.get("action") == "up"]
        self.assertEqual(clicks[0]["x"], 8)

    def test_edit_reorder_and_number_after_compile(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "1"},
                {"t": 1, "kind": "key", "action": "up", "key": "1"},
                {"t": 2, "kind": "key", "action": "down", "key": "0"},
                {"t": 3, "kind": "key", "action": "up", "key": "0"},
                {"t": 4, "kind": "key", "action": "down", "key": "0"},
                {"t": 5, "kind": "key", "action": "up", "key": "0"},
                {"t": 6, "kind": "key", "action": "down", "key": "1"},
                {"t": 7, "kind": "key", "action": "up", "key": "1"},
                {"t": 20, "kind": "key", "action": "down", "key": "Enter"},
            ]
        )
        steps = events_to_steps(events)
        self.assertEqual(steps[0]["kind"], "number")
        self.assertEqual(steps[0]["value"], "1001")
        steps = replace_step(steps, 0, {"kind": "number", "value": "2050", "wait": 0})
        self.assertEqual(steps[0]["value"], "2050")
        extra = new_step("wait", wait=400)
        assert extra is not None
        steps = insert_steps(steps, 1, [extra])
        steps = move_step(steps, 2, -1)
        self.assertEqual([step["kind"] for step in steps], ["number", "key", "wait"])
        steps = delete_step(steps, 2)
        self.assertEqual([step["kind"] for step in steps], ["number", "key"])
        compiled, replayed = compile_script(steps)
        self.assertEqual(len(compiled), 2)
        spans = digit_spans(replayed)
        self.assertEqual([span["value"] for span in spans], ["2050"])
        queue = NumberQueue(["3300"], bump=True, step=1, span=0)
        batch, used = apply_turn(replayed, spans, queue)
        self.assertEqual(used, "3300")
        self.assertEqual([span["value"] for span in digit_spans(batch)], ["3300"])
        self.assertEqual(queue.items, ["3301"])

    def test_wait_shifts_clock_and_rejects_junk(self) -> None:
        events = steps_to_events(
            [
                {"kind": "wait", "wait": 500},
                {"kind": "key", "key": "a"},
                {"kind": "explode"},
                {"kind": "type", "value": ""},
            ]
        )
        self.assertEqual(events[0]["t"], 500)
        self.assertEqual(events[0]["key"], "a")
        self.assertIsNone(new_step("nope"))
        self.assertEqual(move_step([{"kind": "key", "key": "a"}], 0, -1)[0]["key"], "a")

    def test_merge_recording_appends_only_the_new_clip(self) -> None:
        first = sanitize_events(
            [
                {"t": 0, "kind": "key", "action": "down", "key": "1"},
                {"t": 1, "kind": "key", "action": "up", "key": "1"},
                {"t": 2, "kind": "key", "action": "down", "key": "0"},
                {"t": 3, "kind": "key", "action": "up", "key": "0"},
                {"t": 4, "kind": "key", "action": "down", "key": "0"},
                {"t": 5, "kind": "key", "action": "up", "key": "0"},
                {"t": 6, "kind": "key", "action": "down", "key": "1"},
                {"t": 7, "kind": "key", "action": "up", "key": "1"},
                {"t": 20, "kind": "key", "action": "down", "key": "Enter"},
            ]
        )
        extra = sanitize_events(
            [
                {"t": 0, "kind": "click", "action": "down", "button": "left", "x": 40, "y": 80},
                {"t": 10, "kind": "click", "action": "up", "button": "left", "x": 40, "y": 80},
            ]
        )
        script = events_to_steps(first)
        # Ghi mới replaces. Ghi thêm / F8 must use only the just-stopped clip.
        self.assertEqual([step["kind"] for step in merge_recording(script, extra, appending=False)], ["click"])
        appended = merge_recording(script, extra, appending=True)
        self.assertEqual([step["kind"] for step in appended], ["number", "key", "click"])
        self.assertEqual(appended[0]["value"], "1001")
        self.assertEqual(appended[2]["x"], 40)
        # Passing the old script events as "recorded" would duplicate — that was the F8 bug.
        duplicated = merge_recording(script, first, appending=True)
        self.assertEqual([step["kind"] for step in duplicated], ["number", "key", "number", "key"])
        self.assertEqual(merge_recording(script, [], appending=True), script)
        inserted = merge_recording(script, extra, appending=True, at=0)
        self.assertEqual([step["kind"] for step in inserted], ["number", "click", "key"])

    def test_record_edit_number_then_play(self) -> None:
        recorder = Recorder()
        recorder.start(1000)
        for offset, key, action in (
            (0, "1", "down"),
            (5, "1", "up"),
            (10, "0", "down"),
            (15, "0", "up"),
            (20, "0", "down"),
            (25, "0", "up"),
            (30, "1", "down"),
            (35, "1", "up"),
            (50, "Enter", "down"),
            (55, "Enter", "up"),
            (80, "f8", "down"),
        ):
            keep = recorder.add(1000 + offset, {"kind": "key", "action": action, "key": key})
            if key.lower() == "f8":
                self.assertFalse(keep)
        self.assertFalse(recorder.on)
        script = merge_recording([], recorder.events, appending=False)
        self.assertEqual([step["kind"] for step in script], ["number", "key"])
        self.assertNotIn("f8", [step.get("key") for step in script])

        extra = Recorder()
        extra.start(2000)
        extra.add(2050, {"kind": "click", "action": "down", "button": "left", "x": 12, "y": 24})
        extra.add(2080, {"kind": "click", "action": "up", "button": "left", "x": 12, "y": 24})
        clip = extra.stop()
        script = merge_recording(script, clip, appending=True)
        script = replace_step(script, 0, {"kind": "number", "value": "2050", "wait": 0})
        compiled, replayed = compile_script(script)
        self.assertEqual([step["kind"] for step in compiled], ["number", "key", "click"])
        spans = digit_spans(replayed)
        self.assertEqual([span["value"] for span in spans], ["2050"])
        queue = NumberQueue(["3300", "4400"], bump=True, step=1, span=0)
        sent: list[str] = []

        def prepare(_loop: int) -> list:
            batch, _used = apply_turn(replayed, spans, queue)
            return batch

        def send(event: dict) -> None:
            kind = event.get("kind")
            if kind == "key" and event.get("action") == "down":
                sent.append(str(event["key"]))
            elif kind == "click" and event.get("action") == "down":
                sent.append(str(event.get("button") or "left"))

        player = Player(send, lambda _s: None)
        result = player.play(replayed, times=2, gap_ms=0, prepare=prepare)
        self.assertEqual(sent[:6], ["3", "3", "0", "0", "enter", "left"])
        self.assertEqual(sent[6:12], ["4", "4", "0", "0", "enter", "left"])
        self.assertEqual(result["loops"], 2)
        self.assertEqual(queue.items, ["3301", "4401"])


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
            scripted = store.save(
                "Kịch bản",
                [],
                steps=[{"kind": "number", "value": "42"}, {"kind": "key", "key": "enter", "wait": 200}],
            )
            loaded = store.get(scripted["slug"])
            assert loaded is not None
            self.assertEqual([step["kind"] for step in loaded["steps"]], ["number", "key"])
            self.assertEqual(loaded["steps"][0]["value"], "42")
            self.assertEqual(loaded["step_count"], 2)


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
                self.assertIn("1. Gõ a", shown.getvalue())
                os.environ.pop("PC_MACRO_HUB", None)
                missing = io.StringIO()
                with redirect_stdout(missing):
                    self.assertEqual(main(["pull"]), 1)
                self.assertIn("PC_MACRO_HUB", missing.getvalue())
                hub = io.StringIO()
                with redirect_stdout(hub):
                    self.assertEqual(main(["hub"]), 0)
                self.assertIn("PC_MACRO_HUB", hub.getvalue())
            finally:
                os.environ.pop("PC_MACRO_DIR", None)


if __name__ == "__main__":
    unittest.main()
