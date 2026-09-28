"""Keyboard and touch recordings stay replayable and never keep passwords."""

from __future__ import annotations

import unittest

from control_plane.recordings import (
    apply_fills,
    blanks_of,
    default_title,
    events_to_steps,
    pick_match,
    prepare_clip_steps,
    resolve_scenario,
    sanitize_events,
    sanitize_steps,
    script_lines,
    splice_steps,
    steps_to_events,
)


class RecordingSanitizeTests(unittest.TestCase):
    def test_strips_password_keys_and_values(self) -> None:
        events = sanitize_events(
            [
                {"t": 0, "kind": "key", "target": "#token", "key": "s", "code": "KeyS"},
                {"t": 10, "kind": "value", "target": "#token", "value": "super-secret"},
                {"t": 20, "kind": "key", "target": "#actionText", "key": "a", "code": "KeyA"},
                {"t": 30, "kind": "value", "target": "#actionText", "value": "import url"},
                {"t": 40, "kind": "pointer", "target": "#saveAction", "phase": "up", "pointerType": "touch", "click": True, "x": 12, "y": 40, "nx": 0.1, "ny": 0.2},
                {"t": 50, "kind": "pointer", "target": "javascript:alert(1)", "phase": "up", "click": True},
            ]
        )
        self.assertEqual(
            events,
            [
                {"t": 0, "kind": "key", "target": "#token", "redacted": True},
                {"t": 20, "kind": "key", "target": "#actionText", "key": "a", "code": "KeyA"},
                {"t": 30, "kind": "value", "target": "#actionText", "value": "import url"},
                {
                    "t": 40,
                    "kind": "pointer",
                    "target": "#saveAction",
                    "phase": "up",
                    "pointerType": "touch",
                    "click": True,
                    "x": 12.0,
                    "y": 40.0,
                    "nx": 0.1,
                    "ny": 0.2,
                },
            ],
        )
        blob = str(events)
        self.assertNotIn("super-secret", blob)
        self.assertNotIn("javascript", blob)

    def test_script_collapses_typing_and_names_touch(self) -> None:
        events = sanitize_events(
            [
                {"t": 1, "kind": "value", "target": "#actionText", "value": "im"},
                {"t": 2, "kind": "value", "target": "#actionText", "value": "import"},
                {"t": 3, "kind": "key", "target": "#actionText", "key": "Enter"},
                {"t": 4, "kind": "pointer", "target": "#recheck", "phase": "down", "pointerType": "touch"},
                {"t": 5, "kind": "pointer", "target": "#recheck", "phase": "up", "pointerType": "touch", "click": True},
            ]
        )
        self.assertEqual(
            script_lines(events),
            [
                "Gõ vào #actionText: import",
                "Phím Enter tại #actionText",
                "Chạm #recheck",
            ],
        )
        self.assertEqual(default_title(events), "1 lần chạm · 1 phím")

    def test_off_target_touch_keeps_the_intended_control(self) -> None:
        events = sanitize_events(
            [
                {
                    "t": 8,
                    "kind": "pointer",
                    "target": "#refresh",
                    "phase": "up",
                    "pointerType": "touch",
                    "click": True,
                    "intent": "tap",
                    "label": "  Tải\n lại  ",
                    "snapped": True,
                    "x": 4,
                    "y": 9,
                },
                {
                    "t": 12,
                    "kind": "pointer",
                    "target": "#actionText",
                    "phase": "up",
                    "pointerType": "touch",
                    "intent": "focus",
                    "label": "Mình vừa làm gì?",
                    "click": True,
                },
                {
                    "t": 20,
                    "kind": "pointer",
                    "target": "body",
                    "phase": "up",
                    "pointerType": "touch",
                    "intent": "swipe",
                    "click": True,
                    "label": "không phải nút",
                },
                {
                    "t": 30,
                    "kind": "pointer",
                    "target": "#refresh",
                    "phase": "up",
                    "pointerType": "touch",
                    "intent": "explode",
                    "click": True,
                },
            ]
        )
        self.assertEqual(events[0]["label"], "Tải lại")
        self.assertTrue(events[0]["snapped"])
        self.assertEqual(events[0]["intent"], "tap")
        self.assertNotIn("label", events[2])
        self.assertFalse(events[2]["click"])
        self.assertNotIn("intent", events[3])
        self.assertEqual(
            script_lines(events),
            [
                "Chạm lệch, hiểu là Tải lại",
                "Chạm vào ô Mình vừa làm gì?",
                "Chạm #refresh",
            ],
        )

    def test_signature_is_kept_and_ranks_a_moved_control(self) -> None:
        events = sanitize_events(
            [
                {
                    "t": 4,
                    "kind": "pointer",
                    "target": "#refresh",
                    "phase": "up",
                    "pointerType": "touch",
                    "click": True,
                    "intent": "tap",
                    "label": "Tải lại",
                    "role": "button",
                    "hint": "  ",
                    "index": 1,
                    "snapped": True,
                }
            ]
        )
        self.assertEqual(events[0]["role"], "button")
        self.assertEqual(events[0]["index"], 1)
        self.assertNotIn("hint", events[0])
        wanted = events[0]
        picked = pick_match(
            wanted,
            [
                {"target": "#saveToken", "label": "Lưu token", "role": "button", "index": 0},
                {"target": "#refreshMoved", "label": "Tải lại", "role": "button", "index": 1},
                {"target": "#recheck", "label": "Check proxy ngay", "role": "button", "index": 2},
            ],
        )
        self.assertEqual(picked["choice"]["target"], "#refreshMoved")
        self.assertFalse(picked["ambiguous"])

    def test_close_scores_ask_the_user(self) -> None:
        picked = pick_match(
            {"target": "#gone", "label": "Lưu", "role": "button", "hint": "Nhật ký", "index": 0},
            [
                {"target": "#a", "label": "Lưu", "role": "button", "hint": "Nhật ký", "index": 0},
                {"target": "#b", "label": "Lưu", "role": "button", "hint": "Nhật ký", "index": 1},
            ],
        )
        self.assertEqual(picked["choice"]["target"], "#a")
        self.assertTrue(picked["ambiguous"])
        self.assertEqual(picked["alternatives"][0]["target"], "#b")


class ScriptStepTests(unittest.TestCase):
    def test_typing_collapses_and_compiles_back(self) -> None:
        events = sanitize_events(
            [
                {
                    "t": 1,
                    "kind": "pointer",
                    "target": "#actionText",
                    "phase": "up",
                    "pointerType": "touch",
                    "click": True,
                    "intent": "focus",
                    "label": "Mình vừa làm gì?",
                    "role": "textbox",
                    "index": 0,
                },
                {"t": 2, "kind": "value", "target": "#actionText", "value": "im"},
                {"t": 3, "kind": "value", "target": "#actionText", "value": "import"},
                {
                    "t": 4,
                    "kind": "pointer",
                    "target": "#refresh",
                    "phase": "up",
                    "pointerType": "touch",
                    "click": True,
                    "intent": "tap",
                    "label": "Tải lại",
                    "role": "button",
                    "index": 1,
                },
            ]
        )
        steps = events_to_steps(events)
        self.assertEqual([step["kind"] for step in steps], ["type", "tap"])
        self.assertEqual(steps[0]["value"], "import")
        self.assertEqual(steps[0]["label"], "Mình vừa làm gì?")
        self.assertEqual(steps[1]["caption"], "Chạm Tải lại")
        compiled = steps_to_events(steps)
        self.assertEqual([event["kind"] for event in compiled], ["pointer", "value", "pointer"])
        self.assertEqual(compiled[0]["intent"], "focus")
        self.assertEqual(compiled[1]["value"], "import")
        self.assertIn("Gõ vào #actionText: import", script_lines(compiled))

    def test_splice_deletes_a_range_and_drops_bad_targets(self) -> None:
        steps = sanitize_steps(
            [
                {"kind": "tap", "target": "#refresh", "label": "Tải lại"},
                {"kind": "tap", "target": "#recheck", "label": "Check"},
                {"kind": "tap", "target": "#saveAction", "label": "Ghi nhớ"},
            ]
        )
        deleted = splice_steps(steps, 0, 1, [])
        self.assertEqual([step["target"] for step in deleted], ["#saveAction"])
        with self.assertRaises(ValueError):
            splice_steps(steps, 2, 1, [])
        cleaned = sanitize_steps(
            [
                {"kind": "tap", "target": "javascript:alert(1)"},
                {"kind": "nope", "target": "#refresh"},
                {"kind": "type", "target": "#token", "value": "super-secret"},
            ]
        )
        self.assertEqual(len(cleaned), 1)
        self.assertTrue(cleaned[0]["redacted"])
        self.assertNotIn("super-secret", str(cleaned))


class NamedClipTests(unittest.TestCase):
    def test_blanks_fill_independently_and_stay_linked(self) -> None:
        steps = prepare_clip_steps(
            [
                {
                    "kind": "type",
                    "target": "#actionText",
                    "label": "Mình vừa làm gì?",
                    "value": "mẫu",
                    "valueBlank": "Nội dung",
                    "role": "textbox",
                },
                {
                    "kind": "tap",
                    "target": "#refresh",
                    "label": "Tải lại",
                    "role": "button",
                    "targetBlank": "Nút",
                },
            ]
        )
        self.assertEqual(steps[0]["caption"], "Gõ vào Mình vừa làm gì?: mẫu · chỗ trống chữ «Nội dung»")
        blanks = blanks_of(steps)
        self.assertEqual([blank["name"] for blank in blanks], ["Nội dung", "Nút"])
        filled = apply_fills(
            steps,
            {"Nội dung": "lan mot", "Nút": {"target": "#searchAction", "label": "Tìm", "role": "button"}},
        )
        self.assertEqual(filled[0]["value"], "lan mot")
        self.assertEqual(filled[1]["target"], "#searchAction")
        self.assertEqual(filled[1]["label"], "Tìm")
        self.assertNotIn("Tải lại", filled[1].get("label", ""))
        shared = apply_fills(steps, {"Nội dung": "chung"})
        self.assertEqual(shared[0]["value"], "chung")
        self.assertEqual(shared[1]["target"], "#refresh")
        with self.assertRaises(ValueError):
            prepare_clip_steps(
                [
                    {"kind": "type", "target": "#actionText", "value": "a", "valueBlank": "Ô"},
                    {"kind": "tap", "target": "#refresh", "targetBlank": "Ô"},
                ]
            )
        first = {"1": steps}
        resolved = resolve_scenario(
            {1: steps},
            [
                {"clipId": 1, "fills": {"Nội dung": "AAAA"}},
                {"clipId": 1, "fills": {"Nội dung": "BBBB"}},
            ],
        )
        self.assertEqual([step["value"] for step in resolved if step["kind"] == "type"], ["AAAA", "BBBB"])
        changed = [dict(step) for step in steps]
        changed.append({"kind": "tap", "target": "#recheck", "label": "Check proxy ngay"})
        changed = prepare_clip_steps(changed)
        again = resolve_scenario({1: changed}, [{"clipId": 1, "fills": {"Nội dung": "AAAA"}}])
        self.assertEqual(again[-1]["target"], "#recheck")
        self.assertEqual(again[0]["value"], "AAAA")
        self.assertTrue(first)
        with self.assertRaises(KeyError):
            resolve_scenario({}, [{"clipId": 9, "fills": {}}])


if __name__ == "__main__":
    unittest.main()
