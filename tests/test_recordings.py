"""Keyboard and touch recordings stay replayable and never keep passwords."""

from __future__ import annotations

import unittest

from control_plane.recordings import default_title, sanitize_events, script_lines


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


if __name__ == "__main__":
    unittest.main()
