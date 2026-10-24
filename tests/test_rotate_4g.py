"""Xoay 4G kq2: nhận thành công thật, lịch 5 phút lệch nhau, không spam khi chờ."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fb_poller.config import Settings
from fb_poller.workers.rotate_4g import (
    RotateLoop,
    RotateResult,
    RotateTarget,
    load_rotate_targets,
    next_due_at,
    rotate_succeeded,
    stagger_gap_sec,
)


OK_BODY = json.dumps({"name": "Key 1", "status": 200, "message": "✅ Key 1: Đổi IP thành công"})
WAIT_BODY = json.dumps({"name": "Key 1", "status": 400, "message": "❌ Key 1: Thất bại (cần chờ thêm)"})


def _target(n: int) -> RotateTarget:
    return RotateTarget(
        index=n,
        endpoint=f"1.1.1.{n}:8000:u:p",
        rotate_url=f"https://example.test/xoay/{n}",
        name=f"Key {n}",
    )


class RotateSuccessTests(unittest.TestCase):
    def test_real_success_payload(self) -> None:
        ok, status, message = rotate_succeeded(200, OK_BODY)
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIn("thành công", message)

    def test_http_200_but_provider_says_wait(self) -> None:
        ok, status, message = rotate_succeeded(200, WAIT_BODY)
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        self.assertIn("chờ", message)

    def test_http_error(self) -> None:
        ok, status, _ = rotate_succeeded(502, "bad gateway")
        self.assertFalse(ok)
        self.assertEqual(status, 502)


class RotateScheduleTests(unittest.TestCase):
    def test_stagger_is_30s_for_10_keys(self) -> None:
        self.assertEqual(stagger_gap_sec(10, 300), 30)

    def test_first_press_after_5_minutes_then_offset(self) -> None:
        self.assertEqual(
            next_due_at(index=1, count=10, interval_sec=300, last_ok_at=0, started_at=1_000),
            1_300,
        )
        self.assertEqual(
            next_due_at(index=2, count=10, interval_sec=300, last_ok_at=0, started_at=1_000),
            1_330,
        )
        self.assertEqual(
            next_due_at(index=10, count=10, interval_sec=300, last_ok_at=0, started_at=1_000),
            1_570,
        )

    def test_next_cycle_is_5_minutes_after_success(self) -> None:
        self.assertEqual(
            next_due_at(index=1, count=10, interval_sec=300, last_ok_at=2_000, started_at=1_000),
            2_300,
        )


class RotateLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"
        self.now = 1_000.0
        self.calls: list[int] = []

        async def press(target: RotateTarget) -> RotateResult:
            self.calls.append(target.index)
            return RotateResult(
                ok=True,
                index=target.index,
                endpoint=target.endpoint,
                rotate_url=target.rotate_url,
                http_status=200,
                api_status=200,
                message="✅ Đổi IP thành công",
            )

        self.loop = RotateLoop(
            targets=[_target(i) for i in range(1, 11)],
            interval_sec=300,
            state_path=self.state_path,
            press=press,
            now=lambda: self.now,
            started_at=1_000.0,
        )

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_nothing_due_before_five_minutes(self) -> None:
        self.now = 1_299
        self.assertEqual(self.loop.due_targets(), [])
        self.assertEqual(await self.loop.run_due(), [])
        self.assertEqual(self.calls, [])

    async def test_presses_one_key_every_30s_after_5_min(self) -> None:
        self.now = 1_300
        due = self.loop.due_targets()
        self.assertEqual([t.index for t in due], [1])
        await self.loop.run_due()
        self.assertEqual(self.calls, [1])

        self.now = 1_330
        await self.loop.run_due()
        self.assertEqual(self.calls, [1, 2])

        self.now = 1_570
        await self.loop.run_due()
        self.assertEqual(self.calls, [1, 2, 3])
        for _ in range(7):
            await self.loop.run_due()
        self.assertEqual(self.calls, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

    async def test_success_waits_full_5_minutes(self) -> None:
        self.now = 1_300
        await self.loop.run_due()
        self.now = 1_599
        self.assertNotIn(1, [t.index for t in self.loop.due_targets()])
        self.now = 1_600
        self.assertIn(1, [t.index for t in self.loop.due_targets()])

    async def test_cooldown_does_not_spam(self) -> None:
        async def press_wait(target: RotateTarget) -> RotateResult:
            self.calls.append(target.index)
            return RotateResult(
                ok=False,
                index=target.index,
                endpoint=target.endpoint,
                rotate_url=target.rotate_url,
                http_status=200,
                api_status=400,
                message="❌ Key 1: Thất bại (cần chờ thêm)",
            )

        self.loop.press = press_wait
        self.now = 1_300
        await self.loop.run_due()
        self.assertEqual(self.calls, [1])
        self.now = 1_330
        self.assertNotIn(1, [t.index for t in self.loop.due_targets()])
        self.now = 1_599
        self.assertNotIn(1, [t.index for t in self.loop.due_targets()])
        self.now = 1_600
        self.assertIn(1, [t.index for t in self.loop.due_targets()])


class LoadTargetsTests(unittest.TestCase):
    def test_pairs_repo_files_in_order(self) -> None:
        settings = Settings()
        targets = load_rotate_targets(settings)
        self.assertEqual(len(targets), 10)
        self.assertEqual(targets[0].endpoint, "103.125.189.149:8624:9rnbo:6wohm")
        self.assertEqual(targets[0].rotate_url, "https://keymon.dknhanh00.workers.dev/xoay/1")
        self.assertEqual(targets[9].endpoint, "103.16.160.43:8203:qh7wy:8j0iy")
        self.assertEqual(targets[9].rotate_url, "https://keymon.dknhanh00.workers.dev/xoay/10")


if __name__ == "__main__":
    unittest.main()
