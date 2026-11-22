"""Forced password-change after login is a hit: save it, click back, keep going."""

from __future__ import annotations

import unittest
from typing import Any

from pc_macro.login_page import (
    GRID_H,
    GRID_W,
    OUTCOME_CHANGED,
    OUTCOME_OK,
    OUTCOME_PASSWORD_WAIT,
    PASSWORD_WAIT_LABEL,
    back_click_for,
    board_label,
    find_back_arrow,
    looks_like_password_change,
    recover_login_after_password,
    resolve_enter_outcome,
    turn_outcome,
    view_box,
)
from pc_macro.numbers import FourLaneQueue
from pc_macro.script import compile_script, one_code_type_steps
from pc_macro.session import Player


def _cell(color: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    return [color] * (GRID_W * GRID_H)


def _put(cells: list[tuple[int, int, int]], x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < GRID_W and 0 <= y < GRID_H:
        cells[y * GRID_W + x] = color


def _fill_row(cells: list[tuple[int, int, int]], y: int, color: tuple[int, int, int], *, inset: int = 4) -> None:
    for x in range(inset, GRID_W - inset):
        _put(cells, x, y, color)


def password_change_grid() -> list[tuple[int, int, int]]:
    """Synthetic Evoshop ĐỔI MẬT KHẨU page: light bg, 3 fields, teal button, back chevron."""
    cells = _cell((245, 245, 245))
    for y in range(18, 23):
        _fill_row(cells, y, (248, 248, 250), inset=6)
    for y in range(28, 33):
        _fill_row(cells, y, (248, 248, 250), inset=6)
    for y in range(38, 43):
        _fill_row(cells, y, (248, 248, 250), inset=6)
    for y in range(56, 62):
        _fill_row(cells, y, (0, 191, 165), inset=5)
    # Left chevron on a white header.
    for y in range(0, 10):
        _fill_row(cells, y, (255, 255, 255), inset=0)
    for x, y in ((4, 2), (3, 3), (2, 4), (3, 5), (4, 6)):
        _put(cells, x, y, (32, 32, 32))
    return cells


def login_form_grid() -> list[tuple[int, int, int]]:
    """Login: two fields + teal Đăng nhập, no in-page back arrow."""
    cells = _cell((245, 245, 245))
    for y in range(2, 10):
        _fill_row(cells, y, (0, 160, 150), inset=12)
    for y in range(24, 30):
        _fill_row(cells, y, (248, 248, 250), inset=6)
    for y in range(34, 40):
        _fill_row(cells, y, (248, 248, 250), inset=6)
    for y in range(56, 62):
        _fill_row(cells, y, (0, 191, 165), inset=5)
    return cells


def dashboard_grid() -> list[tuple[int, int, int]]:
    cells = _cell((32, 48, 70))
    for y in range(0, GRID_H, 3):
        _fill_row(cells, y, (240, 80, 70), inset=2)
    return cells


class LoginPageTests(unittest.TestCase):
    def test_password_change_counts_as_login_and_labeled_wait(self) -> None:
        pixels = password_change_grid()
        self.assertTrue(looks_like_password_change(pixels, GRID_W, GRID_H))
        self.assertEqual(
            resolve_enter_outcome(changed=True, pixels=pixels, width=GRID_W, height=GRID_H),
            OUTCOME_PASSWORD_WAIT,
        )
        self.assertEqual(turn_outcome(True, "password_wait"), OUTCOME_PASSWORD_WAIT)
        self.assertEqual(board_label(OUTCOME_PASSWORD_WAIT), PASSWORD_WAIT_LABEL)
        arrow = find_back_arrow(pixels, GRID_W, GRID_H)
        self.assertIsNotNone(arrow)
        assert arrow is not None
        self.assertLess(arrow[0], 12)
        self.assertLess(arrow[1], 16)

    def test_login_and_dashboard_are_not_password_wait(self) -> None:
        login = login_form_grid()
        dash = dashboard_grid()
        self.assertFalse(looks_like_password_change(login, GRID_W, GRID_H))
        self.assertFalse(looks_like_password_change(dash, GRID_W, GRID_H))
        self.assertEqual(
            resolve_enter_outcome(changed=False, pixels=login, width=GRID_W, height=GRID_H),
            OUTCOME_OK,
        )
        self.assertEqual(
            resolve_enter_outcome(changed=True, pixels=dash, width=GRID_W, height=GRID_H),
            OUTCOME_CHANGED,
        )

    def test_back_click_uses_arrow_then_saved_name(self) -> None:
        view = {
            "pixels": password_change_grid(),
            "width": GRID_W,
            "height": GRID_H,
            "left": 100,
            "top": 80,
            "box_w": GRID_W,
            "box_h": GRID_H,
        }
        bound = {"x": 240, "y": 400, "wx": 120, "wy": 90}
        auto = back_click_for(bound, view)
        self.assertIsNotNone(auto)
        assert auto is not None
        self.assertGreaterEqual(auto["x"], 100)
        self.assertLess(auto["x"], 100 + 16)
        saved = back_click_for(bound, view, saved_back={"x": 111, "y": 99, "name": "quay lại"})
        self.assertEqual((saved["x"], saved["y"]), (111, 99))

    def test_view_box_prefers_hwnd_rect(self) -> None:
        box = view_box({"x": 200, "y": 300}, hwnd_rect=(10, 20, 400, 700))
        self.assertEqual(box, (10, 20, 400, 700))
        around = view_box({"x": 200, "y": 300, "wx": 40, "wy": 50})
        self.assertIsNotNone(around)
        assert around is not None
        self.assertLessEqual(around[0], 40)
        self.assertGreater(around[2], 200)

    def test_board_saves_password_wait_and_keeps_going(self) -> None:
        queue = FourLaneQueue(prefix="SPXAB", start="00007", end="00009", step=1)
        queue.mark_running("SPXAB00007")
        entry = queue.note_enter("SPXAB00007", True, outcome="password_wait")
        self.assertEqual(entry["outcome"], "password_wait")
        self.assertTrue(entry["changed"])
        queue.take_one()
        self.assertEqual(queue.peek_one(), "SPXAB00008")
        self.assertEqual(queue.password_digits(), ["00007"])
        rows = {item["digits"]: item["state"] for item in queue.board_rows()}
        self.assertEqual(rows["00007"], "password_wait")
        again = FourLaneQueue.from_dict(queue.to_dict())
        self.assertEqual(again.password_digits(), ["00007"])
        self.assertEqual(again.turns[0]["outcome"], "password_wait")

    def test_click_back_then_type_next_code(self) -> None:
        queue = FourLaneQueue(prefix="SPXAB", start="00001", end="00002", step=1)
        bound = {"x": 80, "y": 120, "wx": 20, "wy": 30}
        field = {"text": "", "keys": []}
        sent: list[str] = []
        screens = {"page": "password"}

        def send(event: dict[str, Any]) -> None:
            if event.get("kind") == "click" and event.get("action") == "down":
                screens["page"] = "login"
                sent.append(f"click:{event['x']},{event['y']}")
                return
            if event.get("kind") != "key" or event.get("action") != "down":
                return
            key = str(event.get("key") or "")
            sent.append(key)
            if len(key) == 1:
                field["text"] += key

        view = {
            "pixels": password_change_grid(),
            "width": GRID_W,
            "height": GRID_H,
            "left": 20,
            "top": 30,
            "box_w": GRID_W,
            "box_h": GRID_H,
        }
        code = queue.peek_one()
        outcome = resolve_enter_outcome(changed=True, pixels=view["pixels"], width=GRID_W, height=GRID_H)
        self.assertEqual(outcome, OUTCOME_PASSWORD_WAIT)
        result = recover_login_after_password(
            send,
            lambda _s: None,
            bound,
            view,
            recapture=lambda: None if screens["page"] == "login" else view,
        )
        self.assertTrue(result["clicked"])
        self.assertTrue(any(item.startswith("click:") for item in sent))
        queue.note_enter(code, True, outcome=outcome)
        queue.take_one()
        nxt = queue.peek_one()
        self.assertEqual(nxt, "SPXAB00002")
        player = Player(send, lambda _s: None)
        _script, batch = compile_script(one_code_type_steps(nxt, bound))
        player.emit(batch)
        self.assertIn("SPXAB00002", field["text"])

    def test_f9_changed_does_not_erase_password_wait(self) -> None:
        queue = FourLaneQueue(prefix="SPXAB", start="00003", end="00004")
        queue.note_enter("SPXAB00003", True, outcome="password_wait")
        queue.note_enter("SPXAB00003", False)
        self.assertEqual(queue.turns[-1]["outcome"], "password_wait")
        self.assertTrue(queue.turns[-1]["changed"])


if __name__ == "__main__":
    unittest.main()
