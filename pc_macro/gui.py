"""Simple window: type a number, Enter, add 1, save the number if the screen changes."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.hub import HubError, configured, describe, pull, push
from pc_macro.numbers import FourLaneQueue, LANE_COUNT, NumberQueue, make_code, normalize_digits, normalize_prefix
from pc_macro.overlay import ClickOverlay, FrameOverlay, show_click_overlay, show_frame_overlay
from pc_macro.painter import PaintPicker, show_paint_picker
from pc_macro.screen import (
    LANE_PAUSE_THRESHOLD,
    RECOVER_CLICKS,
    append_screen_click,
    apply_click_names,
    capture_grid,
    capture_watch,
    click_order_cards,
    current_screen_size,
    frames_label,
    lane_watch_frame,
    normalize_click_name,
    normalize_threshold,
    play_named_clicks,
    recover_after_enter,
    sanitize_clicks,
    sanitize_frame,
    scale_clicks,
    start_both_scripts_status,
    watch_changed,
)
from pc_macro.script import (
    compile_script,
    four_lane_enter_burst_steps,
    four_lane_enter_steps,
    four_lane_type_steps,
    number_enter_steps,
)
from pc_macro.session import Player, Recorder, ignore_window
from pc_macro.store import MacroStore


def main() -> None:
    if not available():
        print(install_hint())
        raise SystemExit(1)
    App().run()


class App:
    def __init__(self) -> None:
        self.store = MacroStore()
        self.root = tk.Tk()
        self.root.title("Gõ số rồi Enter")
        self.root.geometry("430x1480")
        self.root.minsize(400, 1080)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(
            value="Bắt đầu gồm cả hai kịch bản: gõ mã + Enter; màn đổi ≥80% thì tự bấm 4 nút trái."
        )
        self.current_label = tk.StringVar(value="Sẽ gõ: —")
        self.recover_label = tk.StringVar(value="4 nút: chưa ghi")
        self.prefix = tk.StringVar(value="")
        self.start = tk.StringVar(value="00001")
        self.end = tk.StringVar(value="00010")
        self.delay = tk.StringVar(value="2")
        self.step = tk.StringVar(value="1")
        self.threshold = tk.StringVar(value="80")
        self.prefix2 = tk.StringVar(value="")
        self.start2 = tk.StringVar(value="00001")
        self.end2 = tk.StringVar(value="00010")
        self.delay2 = tk.StringVar(value="2")
        self.step2 = tk.StringVar(value="1")
        self.threshold2 = tk.StringVar(value=str(LANE_PAUSE_THRESHOLD))
        self.lanes_label = tk.StringVar(value="Hướng 2 — 4 làn: —")
        self.frame_label = tk.StringVar(value=frames_label(None))
        self.fields_label = tk.StringVar(value="4 ô nhập: chưa ghi — chọn 4 ô, mỗi ô một trình duyệt.")
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.player2: Player | None = None
        self.click: dict[str, Any] | None = None
        self.current_slug = "day-so-enter"
        self.slug2 = "day-so-enter-2"
        self.queue = NumberQueue(range_mode=True, prefix="", start="00001", end="00010", step=1)
        self.lanes = FourLaneQueue(prefix="", start="00001", end="00010", step=1)
        self.desktop = Desktop(self._on_desktop, _now_ms)
        self._play_thread: threading.Thread | None = None
        self._play_thread2: threading.Thread | None = None
        self._loading_lanes = False
        self._record_recover = False
        self._recover_draft: list[dict[str, Any]] = []
        self._force_recover = False
        self._before_grid = None
        self._capture_warned = False
        self._overlay: ClickOverlay | None = None
        self._framer: FrameOverlay | None = None
        self._painter: PaintPicker | None = None
        self._pending2: list[tuple[int, str]] = []
        self._picking_lanes = False
        self._picking_frame_index: int | None = None
        self._build()
        self._load_or_empty()
        self.prefix.trace_add("write", self._on_fields)
        self.start.trace_add("write", self._on_fields)
        self.end.trace_add("write", self._on_fields)
        self.step.trace_add("write", self._on_fields)
        self.prefix2.trace_add("write", self._on_lanes)
        self.start2.trace_add("write", self._on_lanes)
        self.end2.trace_add("write", self._on_lanes)
        self.step2.trace_add("write", self._on_lanes)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def run(self) -> None:
        self.desktop.start()
        self.root.mainloop()

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 5}
        ttk.Label(self.root, textvariable=self.status, wraplength=370).pack(anchor="w", **pad)

        ttk.Label(self.root, text="5 ký tự cố định (đứng trước, đổi được)").pack(anchor="w", padx=12)
        ttk.Entry(self.root, textvariable=self.prefix, width=8, font=("Segoe UI", 18)).pack(anchor="w", padx=12, pady=4)

        row = ttk.Frame(self.root)
        row.pack(fill="x", **pad)
        left = ttk.Frame(row)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Số bắt đầu (5 số)").pack(anchor="w")
        ttk.Entry(left, textvariable=self.start, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)
        right = ttk.Frame(row)
        right.pack(side="left", fill="x", expand=True, padx=(12, 0))
        ttk.Label(right, text="Số kết thúc (5 số)").pack(anchor="w")
        ttk.Entry(right, textvariable=self.end, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)

        ttk.Label(self.root, textvariable=self.current_label, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=12, pady=8)

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts, textvariable=self.delay, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(opts, text="Mỗi lần cộng").pack(side="left")
        ttk.Entry(opts, textvariable=self.step, width=5).pack(side="left", padx=6)
        ttk.Label(opts, text="Đổi màn ≥ %").pack(side="left", padx=(14, 0))
        ttk.Entry(opts, textvariable=self.threshold, width=4).pack(side="left", padx=6)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Bắt đầu", command=self.start_play).pack(side="left", padx=2)
        ttk.Button(buttons, text="Dừng", command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(buttons, text="Giao diện đổi", command=self.flag_change).pack(side="left", padx=2)

        extra = ttk.Frame(self.root)
        extra.pack(fill="x", **pad)
        ttk.Button(extra, text="Ghi chỗ nhập", command=self.start_record).pack(side="left", padx=2)
        ttk.Button(extra, text="Ghi 4 nút", command=self.start_record_recover).pack(side="left", padx=2)
        ttk.Button(extra, text="Lưu", command=self.save_current).pack(side="left", padx=2)
        ttk.Button(extra, text="Lên VPS", command=self.push_hub).pack(side="left", padx=2)
        ttk.Button(extra, text="Lấy VPS", command=self.pull_hub).pack(side="left", padx=2)

        ttk.Label(self.root, text="4 nút chuột trái — tên, chọn vị trí hoặc bôi màu trên ảnh").pack(anchor="w", padx=12, pady=(8, 2))
        self._name_vars: list[tk.StringVar] = []
        self._mark_cards: list[tuple[tk.Frame, tk.Label, tk.Label, tk.Frame]] = []
        self._loading_names = False
        for index in range(RECOVER_CLICKS):
            card = tk.Frame(self.root, bd=1, relief="solid", bg="#f3f8fd")
            card.pack(fill="x", padx=12, pady=3)
            head = tk.Frame(card, bg="#f3f8fd")
            head.pack(fill="x")
            num = tk.Label(head, text=str(index + 1), font=("Segoe UI", 18, "bold"), fg="#0b6bcb", bg="#f3f8fd", width=2)
            num.pack(side="left", padx=4)
            name = tk.StringVar()
            ttk.Entry(head, textvariable=name).pack(side="left", fill="x", expand=True, padx=4, pady=4)
            detail = tk.Label(card, text="chưa ghi", font=("Segoe UI", 10), bg="#f3f8fd", anchor="w")
            detail.pack(fill="x", padx=8)
            actions = tk.Frame(card, bg="#f3f8fd")
            actions.pack(fill="x", padx=8, pady=(2, 6))
            ttk.Button(actions, text="Bôi màu", command=lambda at=index: self.paint_step(at)).pack(side="left", padx=2)
            ttk.Button(actions, text="Chọn vị trí", command=lambda at=index: self.pick_step(at)).pack(side="left", padx=2)
            name.trace_add("write", self._on_click_name)
            self._name_vars.append(name)
            self._mark_cards.append((card, num, detail, actions))
        marks = ttk.Frame(self.root)
        marks.pack(fill="x", **pad)
        ttk.Button(marks, text="Lưu 4 nút", command=self.save_recover).pack(side="left", padx=2)
        ttk.Button(marks, text="Hiện 4 nút trên màn", command=self.preview_recover).pack(side="left", padx=2)
        ttk.Button(marks, text="Xóa 4 nút", command=self.clear_recover).pack(side="left", padx=2)
        ttk.Label(self.root, textvariable=self.recover_label, wraplength=390).pack(anchor="w", padx=12, pady=(2, 6))
        ttk.Label(self.root, text="Số lúc giao diện đổi ≥ ngưỡng — F9 cũng lưu số vừa Enter.").pack(anchor="w", padx=12, pady=(8, 2))
        self.flag_box = tk.Listbox(self.root, height=4, font=("Segoe UI", 12))
        self.flag_box.pack(fill="both", expand=True, padx=12, pady=4)
        ttk.Label(
            self.root,
            text="Bắt đầu = gõ Enter + nếu màn đổi ≥80% tự bấm 4 nút. F8 dừng. Bôi màu / Chọn vị trí từng bước.",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ttk.Separator(self.root).pack(fill="x", padx=12, pady=8)
        ttk.Label(self.root, text="Hướng 2 — 4 tiến trình Enter đồng loạt (không đụng hướng 1)", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=12
        )
        ttk.Label(
            self.root,
            text="Mỗi dãy số chạy một trình duyệt. Gõ 4 mã rồi Enter đồng loạt. Chỉ lưu mã của trình duyệt có màn hình đổi ≥20%; các mã khác vẫn chạy. Tiếp tục chỉ mở lại trình duyệt đã dừng.",
            wraplength=390,
        ).pack(anchor="w", padx=12, pady=(2, 4))
        ttk.Label(self.root, text="5 ký tự cố định hướng 2").pack(anchor="w", padx=12)
        ttk.Entry(self.root, textvariable=self.prefix2, width=8, font=("Segoe UI", 18)).pack(anchor="w", padx=12, pady=4)
        row2 = ttk.Frame(self.root)
        row2.pack(fill="x", **pad)
        left2 = ttk.Frame(row2)
        left2.pack(side="left", fill="x", expand=True)
        ttk.Label(left2, text="Số bắt đầu").pack(anchor="w")
        ttk.Entry(left2, textvariable=self.start2, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)
        right2 = ttk.Frame(row2)
        right2.pack(side="left", fill="x", expand=True, padx=(12, 0))
        ttk.Label(right2, text="Số kết thúc").pack(anchor="w")
        ttk.Entry(right2, textvariable=self.end2, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)
        ttk.Label(self.root, textvariable=self.lanes_label, wraplength=390, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=12, pady=6
        )
        opts2 = ttk.Frame(self.root)
        opts2.pack(fill="x", **pad)
        ttk.Label(opts2, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts2, textvariable=self.delay2, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(opts2, text="Mỗi lần cộng").pack(side="left")
        ttk.Entry(opts2, textvariable=self.step2, width=5).pack(side="left", padx=6)
        ttk.Label(opts2, text="Khung đổi ≥ %").pack(side="left", padx=(14, 0))
        ttk.Entry(opts2, textvariable=self.threshold2, width=4).pack(side="left", padx=6)
        ttk.Label(self.root, textvariable=self.frame_label, wraplength=390).pack(anchor="w", padx=12, pady=(2, 2))
        ttk.Label(self.root, textvariable=self.fields_label, wraplength=390).pack(anchor="w", padx=12, pady=(0, 2))
        buttons2 = ttk.Frame(self.root)
        buttons2.pack(fill="x", **pad)
        ttk.Button(buttons2, text="Bắt đầu 2", command=self.start_play2).pack(side="left", padx=2)
        ttk.Button(buttons2, text="Dừng 2", command=self.stop_play2).pack(side="left", padx=2)
        ttk.Button(buttons2, text="Tiếp tục", command=self.continue_play2).pack(side="left", padx=2)
        extra2 = ttk.Frame(self.root)
        extra2.pack(fill="x", **pad)
        ttk.Button(extra2, text="Chọn 4 ô nhập", command=self.pick_lane_fields).pack(side="left", padx=2)
        ttk.Button(extra2, text="Chọn 4 khung", command=self.pick_watch_frame).pack(side="left", padx=2)
        ttk.Button(extra2, text="Lưu hướng 2", command=self.save_lanes).pack(side="left", padx=2)
        ttk.Label(self.root, text="Mã dừng khi khung 5 số đổi ≥ ngưỡng (đủ 5 ký tự + 5 số)").pack(
            anchor="w", padx=12, pady=(6, 2)
        )
        self.flag_box2 = tk.Listbox(self.root, height=3, font=("Segoe UI", 12))
        self.flag_box2.pack(fill="x", padx=12, pady=4)

    def start_record(self) -> None:
        if self.recorder.on or self._playing() or self._record_recover:
            return
        self.recorder.start(_now_ms())
        self.status.set("Bấm một lần vào ô cần nhập trên máy, rồi Dừng hoặc F8.")

    def start_record_recover(self) -> None:
        if self.recorder.on or self._playing() or self._record_recover:
            return
        self._recover_draft = []
        self._record_recover = True
        self._refresh_recover()
        self.status.set("Màn hình phủ lên — bấm trái 4 chỗ, rồi bấm Lưu 4 nút.")
        self.root.withdraw()
        try:
            self._overlay = show_click_overlay(
                self.root,
                on_done=self._overlay_done,
                on_cancel=self._overlay_cancel,
                recording=True,
            )
        except tk.TclError:
            self._record_recover = False
            self.root.deiconify()
            self.status.set("Không mở được lớp phủ màn hình.")

    def paint_step(self, index: int) -> None:
        if self._busy() or not 0 <= index < RECOVER_CLICKS:
            return
        self.status.set(f"Bôi màu hồng lên nút {index + 1} trên ảnh, rồi Lưu vị trí.")
        self.root.withdraw()
        try:
            self._painter = show_paint_picker(
                self.root,
                step=index,
                clicks=self.queue.recover_clicks,
                name=normalize_click_name(self._name_vars[index].get()),
                on_done=self._paint_done,
                on_cancel=self._paint_cancel,
            )
        except tk.TclError:
            self._painter = None
            self.root.deiconify()
            self.status.set("Không mở được cửa sổ bôi màu.")

    def pick_step(self, index: int) -> None:
        if self._busy() or not 0 <= index < RECOVER_CLICKS:
            return
        self._record_recover = True
        self._recover_draft = list(self.queue.recover_clicks)
        self.status.set(f"Bấm trái trên màn hình cho nút {index + 1}, rồi Lưu vị trí.")
        self.root.withdraw()
        try:
            self._overlay = show_click_overlay(
                self.root,
                on_done=self._overlay_done,
                on_cancel=self._overlay_cancel,
                clicks=self.queue.recover_clicks,
                recording=True,
                step=index,
            )
        except tk.TclError:
            self._record_recover = False
            self.root.deiconify()
            self.status.set("Không mở được lớp phủ màn hình.")

    def preview_recover(self) -> None:
        if self._busy():
            return
        clicks = self.queue.recover_clicks
        if len(clicks) < RECOVER_CLICKS:
            self.status.set("Chưa đủ 4 nút. Bấm Ghi 4 nút rồi chỉ trên màn hình.")
            return
        self._overlay = show_click_overlay(
            self.root,
            on_done=lambda _clicks: None,
            on_cancel=lambda: None,
            clicks=clicks,
            recording=False,
        )
        self.status.set("Đang hiện 4 nút trên màn hình, đúng thứ tự 1 → 2 → 3 → 4.")

    def save_recover(self) -> None:
        if self._busy():
            return
        clicks = self._named_clicks(self.queue.recover_clicks)
        if len(clicks) < RECOVER_CLICKS:
            self.status.set("Chưa đủ 4 nút. Bấm Ghi 4 nút, chọn 4 chỗ trên màn, nhập tên, rồi Lưu.")
            return
        self.queue.recover_clicks = clicks
        self._persist(silent=True)
        self._refresh_recover()
        self.status.set("Đã lưu 4 nút trái. Đổi cỡ màn hình máy sẽ quy tỷ lệ khi bấm lại.")

    def clear_recover(self) -> None:
        if self._busy():
            return
        self.queue.recover_clicks = []
        self._recover_draft = []
        self._refresh_recover()
        self._persist(silent=True)
        self.status.set("Đã xóa 4 nút.")

    def start_play(self) -> None:
        if self.recorder.on:
            self.status.set("Dừng ghi chỗ nhập rồi hãy bắt đầu.")
            return
        if self._playing2():
            self.status.set("Đang chạy hướng 2. Bấm Dừng 2 rồi hãy bắt đầu hướng 1.")
            return
        if self._playing():
            return
        self._sync_queue(write_back=True)
        if not self.queue.peek():
            self.status.set("Nhập đủ 5 ký tự cố định, rồi số bắt đầu và số kết thúc.")
            return
        try:
            delay = float(self.delay.get().strip() or "0")
        except ValueError:
            self.status.set("Chờ phải là số giây.")
            return
        if delay < 0:
            self.status.set("Chờ không được âm.")
            return
        self.player = Player(self.desktop.send, time.sleep)
        self.status.set(start_both_scripts_status(self.queue.recover_clicks, self.queue.change_threshold))
        self._play_thread = threading.Thread(target=self._run_play, args=(delay,), daemon=True)
        self._play_thread.start()

    def stop_all(self) -> None:
        if self.player is not None:
            self.player.cancel()
        if self.player2 is not None:
            self.player2.cancel()
        if self._record_recover or self._overlay is not None:
            self._finish_recover(keep=False)
        if self._painter is not None:
            self._close_painter()
        if self._framer is not None:
            self._close_framer()
        if not self.recorder.on:
            return
        recorded = self.recorder.stop()
        self._after_record(recorded)

    def flag_change(self) -> None:
        self._sync_queue()
        value = self.queue.flag()
        if not value:
            self.status.set("Chưa có số để lưu.")
            return
        if self._playing():
            self._force_recover = True
        self._refresh_flags()
        self._persist(silent=True)
        self.status.set(f"Đã lưu số {value} vì giao diện đổi.")

    def save_current(self) -> None:
        self._persist(silent=False)

    def push_hub(self) -> None:
        if self._busy():
            return
        if not configured():
            self.status.set("Chưa đặt PC_MACRO_HUB. Tải gói từ VPS rồi chạy Cai-dat.ps1.")
            return
        self._persist(silent=True)
        threading.Thread(target=self._push_hub_worker, daemon=True).start()

    def pull_hub(self) -> None:
        if self._busy():
            return
        if not configured():
            self.status.set("Chưa đặt PC_MACRO_HUB. Tải gói từ VPS rồi chạy Cai-dat.ps1.")
            return
        threading.Thread(target=self._pull_hub_worker, daemon=True).start()

    def _push_hub_worker(self) -> None:
        try:
            result = push(self.store, self.current_slug)
        except (HubError, ValueError) as exc:
            self._set_status(str(exc))
            return
        self._set_status(f"Đã đưa {result['count']} bản lên {describe()}.")

    def _pull_hub_worker(self) -> None:
        try:
            result = pull(self.store, self.current_slug)
        except (HubError, ValueError) as exc:
            self._set_status(str(exc))
            return
        self.root.after(0, self._load_or_empty)
        self._set_status(f"Đã lấy {result['count']} bản từ {describe()}.")

    def _run_play(self, delay: float) -> None:
        remaining = delay
        while remaining > 0 and self.player is not None and not self.player.cancelled:
            self._set_status(f"Bắt đầu sau {int(remaining)} giây… bấm vào dòng cố định.")
            time.sleep(min(1.0, remaining))
            remaining -= 1
        if self.player is None or self.player.cancelled:
            self._set_status("Đã dừng.")
            self.player = None
            return

        def prepare(_loop: int) -> list[dict[str, Any]]:
            pending = []
            if self._force_recover:
                pending = self._consume_recover("F9")
            self._before_grid = capture_grid()
            if self._before_grid is None and not self._capture_warned:
                self._capture_warned = True
                self._set_status("Không chụp được màn hình — dùng F9 khi thấy đổi.")
            value = self.queue.take()
            if not value:
                return []
            _script, batch = compile_script(self._job_steps(value))
            self._set_status(f"Đang gõ {value} rồi Enter.")
            self.root.after(0, self._refresh_current)
            self.root.after(0, lambda: self._persist(silent=True))
            return pending + batch

        def after(_loop: int) -> list[dict[str, Any]]:
            if self.player is None or self.player.cancelled:
                return []
            time.sleep(0.5)
            if self.player is None or self.player.cancelled:
                return []
            try:
                forced = self._force_recover
                changed, percent, _clicks = recover_after_enter(
                    self._before_grid,
                    capture_grid(),
                    self.queue.change_threshold,
                    self.queue.recover_clicks,
                    forced=forced,
                )
                if not changed:
                    self._set_status(f"Đã Enter {self.queue.last_used}. Đổi màn {percent:.0f}% — tiếp tục gõ số.")
                    return []
                return self._consume_recover("F9" if forced else f"{percent:.0f}%")
            except Exception as exc:
                self._set_status(f"Lỗi khi bấm 4 nút: {exc}. Tiếp tục gõ số.")
                self._force_recover = False
                return []

        try:
            result = self.player.play([], times=0, prepare=prepare, after=after)
        except Exception as exc:
            result = {"loops": 0, "played": 0, "stopped": True}
            self._set_status(f"Đã dừng vì lỗi: {exc}.")
        finally:
            self.player = None
        self.root.after(0, self._refresh_number_view)
        self.root.after(0, lambda: self._persist(silent=True))
        if result["stopped"]:
            self._set_status(f"Đã dừng sau {result['loops']} lần. Số tiếp theo: {self.queue.peek() or '—'}.")
        else:
            self._set_status(f"Đã xong {result['loops']} lần.")

    def _on_desktop(self, now_ms: int, raw: dict[str, Any]) -> None:
        key = str(raw.get("key") or "").lower()
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f9":
            self.root.after(0, self.flag_change)
            return
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f8":
            if self.player is not None:
                self.player.cancel()
            if self.player2 is not None:
                self.player2.cancel()
            if self._record_recover:
                self.root.after(0, lambda: self._finish_recover(keep=False))
            if self._picking_lanes:
                self.root.after(0, self._overlay_cancel)
            if self._painter is not None:
                self.root.after(0, self._paint_cancel)
            if self._framer is not None:
                self.root.after(0, self._frame_cancel)
            if self.recorder.on:
                recorded = self.recorder.stop()
                self.root.after(0, lambda ev=recorded: self._after_record(ev))
            return
        if self._record_recover:
            return
        if not self.recorder.on:
            return
        keep = self.recorder.add(now_ms, raw)
        if raw.get("kind") == "click" and raw.get("action") == "up":
            recorded = self.recorder.stop()
            self.root.after(0, lambda ev=recorded: self._after_record(ev))
            return
        if not keep:
            self.root.after(0, lambda ev=list(self.recorder.events): self._after_record(ev))

    def _after_record(self, recorded: list[dict[str, Any]] | None = None) -> None:
        events = recorded if recorded is not None else self.recorder.events
        click = None
        sw, sh = self._screen_size()
        for event in events:
            if event.get("kind") == "click":
                saved = append_screen_click([], event.get("x"), event.get("y"), sw, sh)
                if saved:
                    click = saved[0]
                    click["wait"] = 80
        self.click = click
        if click:
            self.status.set(f"Đã nhớ chỗ nhập tại {click['x']},{click['y']}. Bắt đầu sẽ bấm đó rồi gõ số.")
        else:
            self.status.set("Chưa bắt được chỗ bấm. Đặt chuột vào ô rồi Ghi chỗ nhập, hoặc tự bấm trong lúc chờ.")
        self._persist(silent=True)

    def _job_steps(self, sample: str) -> list[dict[str, Any]]:
        steps = []
        if self.click:
            steps.append(self.click)
        steps.extend(number_enter_steps(sample))
        return steps

    def _overlay_done(self, clicks: list[dict[str, Any]]) -> None:
        self._overlay = None
        if self._picking_lanes:
            self._picking_lanes = False
            self.lanes.lane_clicks = sanitize_clicks(clicks)
            self._deiconify()
            self._refresh_lanes()
            self._persist_lanes(silent=True)
            self.status.set("Đã nhớ 4 ô nhập. Mỗi ô một trình duyệt — chỉ dừng mã của cửa sổ đổi.")
            return
        self._recover_draft = sanitize_clicks(clicks)
        self._finish_recover(keep=True)

    def _overlay_cancel(self) -> None:
        self._overlay = None
        if self._picking_lanes:
            self._picking_lanes = False
            self._deiconify()
            self.status.set("Đã hủy chọn 4 ô nhập.")
            return
        self._finish_recover(keep=False)

    def _close_overlay(self) -> None:
        overlay = self._overlay
        self._overlay = None
        if overlay is not None:
            try:
                overlay.close()
            except tk.TclError:
                pass
        try:
            if str(self.root.state()) == "withdrawn":
                self.root.deiconify()
                self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _paint_done(self, clicks: list[dict[str, Any]]) -> None:
        self._painter = None
        self._recover_draft = sanitize_clicks(clicks)
        self._finish_recover(keep=True)
        self._deiconify()

    def _paint_cancel(self) -> None:
        self._painter = None
        self._deiconify()
        self.status.set("Đã hủy bôi màu.")

    def _deiconify(self) -> None:
        try:
            if str(self.root.state()) == "withdrawn":
                self.root.deiconify()
                self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _close_painter(self) -> None:
        painter = self._painter
        self._painter = None
        if painter is not None:
            try:
                painter.close()
            except tk.TclError:
                pass
        self._deiconify()

    def _finish_recover(self, *, keep: bool) -> None:
        self._record_recover = False
        self._close_overlay()
        if keep:
            clicks = self._named_clicks(self._recover_draft)
            self.queue.recover_clicks = clicks
            self._persist(silent=True)
            filled = len(sanitize_clicks(clicks))
            if filled >= RECOVER_CLICKS:
                self.status.set("Đã lưu 4 nút. Máy bấm trái đúng chỗ bôi màu / chọn vị trí.")
            else:
                self.status.set(f"Đã ghi {filled}/4. Bôi màu hoặc chọn vị trí bước còn thiếu, rồi Lưu.")
        else:
            self.status.set("Đã hủy ghi vị trí.")
        self._recover_draft = []
        self._refresh_recover()

    def _consume_recover(self, reason: str) -> list[dict[str, Any]]:
        self._force_recover = False
        value = self.queue.flag(self.queue.last_used)
        self.root.after(0, self._refresh_flags)
        self.root.after(0, lambda: self._persist(silent=True))
        if not value:
            return []
        if len(self.queue.recover_clicks) < RECOVER_CLICKS:
            self._set_status(f"Đã lưu {value} ({reason}). Chưa đủ 4 nút — tiếp tục gõ số.")
            return []
        self._set_status(f"Đã lưu {value} ({reason}). Đang bấm 4 nút (bấm lại nếu chưa nhận).")
        self._play_named_clicks()
        return []

    def _screen_size(self) -> tuple[int, int]:
        return current_screen_size(fallback=(int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight())))

    def _named_clicks(self, clicks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        raw = clicks if clicks is not None else self.queue.recover_clicks
        typed = [normalize_click_name(item.get()) for item in self._name_vars]
        if any(typed):
            return apply_click_names(raw, typed)
        return sanitize_clicks(raw)

    def _play_named_clicks(self) -> None:
        sw, sh = self._screen_size()
        clicks = scale_clicks(self._named_clicks(), sw, sh)
        if not clicks:
            return

        def cancelled() -> bool:
            return self.player is None or self.player.cancelled

        try:
            results = play_named_clicks(clicks, self.desktop.send, time.sleep, cancelled=cancelled)
            if self.click and not cancelled():
                extra = scale_clicks([self.click], sw, sh)
                if extra:
                    play_named_clicks(extra, self.desktop.send, time.sleep, cancelled=cancelled, retries=0)
        except Exception as exc:
            self._set_status(f"Lỗi khi bấm 4 nút: {exc}. Tiếp tục gõ số.")
            return
        retried = [item for item in results if item["attempts"] > 1]
        if retried:
            names = ", ".join(item["name"] for item in retried)
            self._set_status(f"Đã bấm lại: {names}.")

    def _on_click_name(self, *_args: Any) -> None:
        if self._loading_names:
            return
        cleaned = [normalize_click_name(item.get()) for item in self._name_vars]
        changed = False
        for index, value in enumerate(cleaned):
            if value != self._name_vars[index].get():
                self._name_vars[index].set(value)
                changed = True
        if changed:
            return
        if self.queue.recover_clicks:
            self.queue.recover_clicks = self._named_clicks()

    def _refresh_recover(self, *, load_names: bool = False) -> None:
        clicks = self._recover_draft if self._record_recover else self.queue.recover_clicks
        cards = click_order_cards(clicks)
        if load_names:
            self._loading_names = True
            for index, var in enumerate(self._name_vars):
                name = cards[index].get("name") or ""
                if var.get() != name:
                    var.set(name)
            self._loading_names = False
        for index, (card, num, detail, actions) in enumerate(self._mark_cards):
            item = cards[index]
            detail.configure(text=item["detail"])
            empty = bool(item["empty"])
            bg = "#f7f9fb" if empty else "#e8f3ff"
            fg = "#5c6770" if empty else "#0b6bcb"
            card.configure(bg=bg)
            num.configure(fg=fg, bg=bg)
            detail.configure(bg=bg)
            actions.configure(bg=bg)
        done = sum(1 for item in sanitize_clicks(clicks))
        if done >= RECOVER_CLICKS:
            self.recover_label.set("Đã chọn 4 nút. Bôi màu hoặc chọn vị trí từng bước. Chưa nhận thì bấm lại đúng tên.")
        elif done:
            self.recover_label.set(f"Đã ghi {done}/4. Bôi màu / chọn vị trí bước còn thiếu, rồi Lưu.")
        else:
            self.recover_label.set("Chưa ghi. Từng bước: Bôi màu trên ảnh hoặc Chọn vị trí trên màn.")

    def _on_fields(self, *_args: Any) -> None:
        cleaned = normalize_prefix(self.prefix.get())
        if cleaned != self.prefix.get():
            self.prefix.set(cleaned)
            return
        self._sync_queue(write_back=False)

    def _sync_queue(self, *, write_back: bool = False) -> None:
        try:
            step = int(self.step.get().strip() or "1")
        except ValueError:
            step = 1
            self.step.set("1")
        if step < 1:
            step = 1
            self.step.set("1")
        prefix = normalize_prefix(self.prefix.get())
        start = normalize_digits(self.start.get() or "00001")
        end = normalize_digits(self.end.get() or start)
        current = self.queue.current if self.queue.range_mode else start
        exhausted = self.queue.exhausted if self.queue.range_mode else False
        if self.queue.range_mode and start != self.queue.start and not self._playing():
            current = start
            exhausted = False
        self.queue = NumberQueue(
            range_mode=True,
            prefix=prefix,
            start=start,
            end=end,
            current=current,
            step=step,
            flagged=list(self.queue.flagged),
            last_used=self.queue.last_used,
            exhausted=exhausted,
            recover_clicks=self._named_clicks(),
            change_threshold=normalize_threshold(self.threshold.get()),
        )
        if write_back:
            self._write_fields()
        self._refresh_current()

    def _write_fields(self) -> None:
        if self.prefix.get() != self.queue.prefix:
            self.prefix.set(self.queue.prefix)
        if self.start.get() != self.queue.start:
            self.start.set(self.queue.start)
        if self.end.get() != self.queue.end:
            self.end.set(self.queue.end)
        if self.step.get() != str(self.queue.step):
            self.step.set(str(self.queue.step))
        if self.threshold.get() != str(self.queue.change_threshold):
            self.threshold.set(str(self.queue.change_threshold))

    def _refresh_number_view(self) -> None:
        self._write_fields()
        self._refresh_current()
        self._refresh_flags()
        self._refresh_recover(load_names=True)

    def _refresh_current(self) -> None:
        nxt = self.queue.peek()
        last = self.queue.last_used
        if nxt and last:
            self.current_label.set(f"Sẽ gõ: {nxt}   (vừa xong: {last})")
        elif nxt:
            self.current_label.set(f"Sẽ gõ: {nxt}")
        else:
            self.current_label.set("Sẽ gõ: —")

    def _refresh_flags(self) -> None:
        self.flag_box.delete(0, tk.END)
        for value in self.queue.flagged:
            self.flag_box.insert(tk.END, value)

    def _load_or_empty(self) -> None:
        item = self.store.get(self.current_slug)
        if item is None:
            for row in self.store.list():
                item = row
                self.current_slug = str(row.get("slug") or self.current_slug)
                break
        if item:
            loaded = NumberQueue.from_dict(item.get("numbers"))
            if loaded.range_mode or loaded.prefix or loaded.start:
                self.queue = loaded
            self.queue.range_mode = True
            self.queue.bump = True
            self.click = _click_from_steps(item.get("steps") or [])
        self._refresh_number_view()
        self._load_lanes()
        if self.queue.prefix or self.queue.last_used:
            self.status.set("Đã mở dãy đã lưu. Bấm vào ô cần nhập, rồi Bắt đầu.")

    def _persist(self, *, silent: bool) -> None:
        self._sync_queue()
        self.queue.recover_clicks = self._named_clicks()
        if not self.queue.prefix and not self.queue.flagged and not self.queue.last_used and not self.queue.recover_clicks:
            if not silent:
                self.status.set("Nhập 5 ký tự cố định rồi lưu.")
            return
        sample = self.queue.peek() or self.queue.last_used or make_code(self.queue.prefix, self.queue.current) or "1"
        try:
            saved = self.store.save(
                "Dãy số rồi Enter",
                [],
                slug=self.current_slug,
                numbers=self.queue.to_dict(),
                steps=self._job_steps(sample),
            )
        except ValueError:
            if not silent:
                self.status.set("Chưa có số để lưu.")
            return
        self.current_slug = str(saved["slug"])
        if not silent:
            self.status.set(f"Đã lưu dãy. Số tiếp theo: {self.queue.peek() or '—'}.")

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status.set(text))

    def _window_box(self) -> tuple[float, float, float, float] | None:
        try:
            self.root.update_idletasks()
            left = float(self.root.winfo_rootx())
            top = float(self.root.winfo_rooty())
            right = left + float(self.root.winfo_width())
            bottom = top + float(self.root.winfo_height())
        except tk.TclError:
            return None
        return left, top, right, bottom

    def _playing(self) -> bool:
        if self.player is not None and not self.player.cancelled:
            return True
        return self._play_thread is not None and self._play_thread.is_alive()

    def _playing2(self) -> bool:
        if self.player2 is not None and not self.player2.cancelled:
            return True
        return self._play_thread2 is not None and self._play_thread2.is_alive()

    def _busy(self) -> bool:
        return (
            self.recorder.on
            or self._playing()
            or self._playing2()
            or self._record_recover
            or self._painter is not None
            or self._framer is not None
            or self._picking_lanes
        )

    def start_play2(self) -> None:
        if self.recorder.on:
            self.status.set("Dừng ghi chỗ nhập rồi hãy bắt đầu hướng 2.")
            return
        if self._playing():
            self.status.set("Đang chạy hướng 1. Bấm Dừng rồi hãy bắt đầu hướng 2.")
            return
        if self._playing2():
            return
        self._sync_lanes(write_back=True)
        if not self.lanes.peek():
            if self.lanes.waiting:
                self.status.set(
                    f"Hướng 2 đã lưu {self._codes_text(self.lanes.paused_codes, self.lanes.paused)}. Bấm Tiếp tục cho trình duyệt đã dừng."
                )
                return
            self.status.set("Hướng 2: nhập đủ 5 ký tự cố định, số bắt đầu và số kết thúc.")
            return
        try:
            delay = float(self.delay2.get().strip() or "0")
        except ValueError:
            self.status.set("Hướng 2: chờ phải là số giây.")
            return
        if delay < 0:
            self.status.set("Hướng 2: chờ không được âm.")
            return
        self.player2 = Player(self.desktop.send, time.sleep)
        bar = self.lanes.change_threshold
        ready = len(sanitize_clicks(self.lanes.lane_clicks))
        if ready < RECOVER_CLICKS:
            self.status.set(
                f"Hướng 2: chưa đủ 4 ô nhập — chỉ cửa sổ đang focus nhận phím. Khung ≥{bar}% thì chỉ lưu mã của trình duyệt đổi."
            )
        else:
            self.status.set(
                f"Hướng 2: gõ 4 mã, Enter đồng loạt. Khung ≥{bar}% thì chỉ lưu mã của trình duyệt đổi; các mã khác vẫn chạy."
            )
        self._play_thread2 = threading.Thread(target=self._run_play2, args=(delay,), daemon=True)
        self._play_thread2.start()

    def continue_play2(self) -> None:
        if self._playing():
            self.status.set("Đang chạy hướng 1. Bấm Dừng rồi hãy tiếp tục hướng 2.")
            return
        self._sync_lanes(write_back=True)
        if self._playing2():
            if not self.lanes.waiting:
                self.status.set("Hướng 2 đang chạy. Không có trình duyệt nào đang dừng.")
                return
            nxt = self.lanes.resume()
            self._refresh_lanes()
            self._persist_lanes(silent=True)
            self.status.set(f"Tiếp tục trình duyệt đã dừng: {nxt}. Các mã khác vẫn chạy.")
            return
        if not self.lanes.waiting and not self.lanes.paused:
            self.start_play2()
            return
        nxt = self.lanes.resume()
        if not nxt and not self.lanes.peek():
            self.status.set("Hướng 2: không còn mã để tiếp tục.")
            return
        self._refresh_lanes()
        self._persist_lanes(silent=True)
        self.player2 = Player(self.desktop.send, time.sleep)
        self.status.set(f"Tiếp tục hướng 2 từ {nxt or self._codes_text(self.lanes.peek())}.")
        self._play_thread2 = threading.Thread(target=self._run_play2, args=(0,), daemon=True)
        self._play_thread2.start()

    def stop_play2(self) -> None:
        if self.player2 is not None:
            self.player2.cancel()
        nxt = self._codes_text(self.lanes.paused_codes, self.lanes.paused)
        if nxt == "—":
            nxt = self._codes_text(self.lanes.peek())
        self.status.set(f"Đã dừng hướng 2. {'Chờ Tiếp tục tại ' if self.lanes.waiting else 'Tiếp theo: '}{nxt}.")

    def save_lanes(self) -> None:
        self._persist_lanes(silent=False)

    def pick_lane_fields(self) -> None:
        if self._busy():
            return
        self._picking_lanes = True
        self.status.set("Bấm trái 4 ô nhập của 4 tiến trình, theo thứ tự 1 → 4, rồi Lưu.")
        self.root.withdraw()
        try:
            self._overlay = show_click_overlay(
                self.root,
                on_done=self._overlay_done,
                on_cancel=self._overlay_cancel,
                clicks=self.lanes.lane_clicks,
                recording=True,
            )
        except tk.TclError:
            self._picking_lanes = False
            self.root.deiconify()
            self.status.set("Không mở được lớp phủ để chọn 4 ô nhập.")

    def pick_watch_frame(self) -> None:
        if self._busy():
            return
        self._picking_frame_index = 0
        self._open_frame_picker()

    def _open_frame_picker(self) -> None:
        index = self._picking_frame_index if self._picking_frame_index is not None else 0
        frames = list(self.lanes.watch_frames)
        self.status.set(f"Kéo khung quanh màn hình trình duyệt {index + 1}/{LANE_COUNT}, rồi Lưu.")
        self.root.withdraw()
        try:
            self._framer = show_frame_overlay(
                self.root,
                on_done=self._frame_done,
                on_cancel=self._frame_cancel,
                frame=frames[index] if index < len(frames) else None,
                title=f"Trình duyệt {index + 1}/{LANE_COUNT} — kéo khung màn hình website này.",
            )
        except tk.TclError:
            self._framer = None
            self._picking_frame_index = None
            self.root.deiconify()
            self.status.set("Không mở được lớp phủ để chọn khung trình duyệt.")

    def _frame_done(self, frame: dict[str, Any]) -> None:
        self._framer = None
        box = sanitize_frame(frame)
        index = self._picking_frame_index if self._picking_frame_index is not None else 0
        if box is None:
            self._picking_frame_index = None
            self.root.deiconify()
            self.status.set("Khung quá nhỏ. Kéo lại rộng hơn quanh trình duyệt đó.")
            return
        frames = list(self.lanes.watch_frames)
        while len(frames) < LANE_COUNT:
            frames.append(None)
        frames[index] = box
        self.lanes.watch_frames = frames
        self.lanes.watch_frame = frames[0]
        if index + 1 < LANE_COUNT:
            self._picking_frame_index = index + 1
            self._open_frame_picker()
            return
        self._picking_frame_index = None
        self.root.deiconify()
        self.frame_label.set(frames_label(self.lanes.watch_frames))
        self._persist_lanes(silent=True)
        self.status.set("Đã lưu 4 khung. Chỉ dừng mã của trình duyệt có màn hình đổi.")

    def _frame_cancel(self) -> None:
        self._framer = None
        self._picking_frame_index = None
        self.root.deiconify()
        self.status.set("Đã hủy chọn khung trình duyệt.")

    def _close_framer(self) -> None:
        picker = self._framer
        self._framer = None
        if picker is not None:
            try:
                picker.close()
            except tk.TclError:
                pass

    def _codes_text(self, codes: list[str] | None = None, fallback: str = "") -> str:
        items = [item for item in (codes or []) if item]
        if items:
            return " · ".join(items)
        return fallback or "—"

    def _lane_focus_clicks(self) -> list[dict[str, Any]]:
        sw, sh = self._screen_size()
        return scale_clicks(self.lanes.lane_clicks, sw, sh)

    def _lane_clicks_for(self, active: list[tuple[int, str]]) -> list[dict[str, Any]]:
        clicks = self._lane_focus_clicks()
        return [clicks[index] for index, _code in active if index < len(clicks)]

    def _lane_frame(self, index: int) -> dict[str, Any] | None:
        return lane_watch_frame(self.lanes.watch_frames, self._lane_focus_clicks(), index)

    def _run_play2(self, delay: float) -> None:
        remaining = delay
        while remaining > 0 and self.player2 is not None and not self.player2.cancelled:
            self._set_status(f"Hướng 2 bắt đầu sau {int(remaining)} giây…")
            time.sleep(min(1.0, remaining))
            remaining -= 1
        if self.player2 is None or self.player2.cancelled:
            self._set_status("Đã dừng hướng 2.")
            self.player2 = None
            return

        def prepare(_loop: int) -> list[dict[str, Any]]:
            active = self.lanes.peek_active()
            if not active:
                return []
            codes = [code for _index, code in active]
            self._pending2 = list(active)
            shown = self._codes_text(codes)
            bar = self.lanes.change_threshold
            paused = self._codes_text(self.lanes.paused_codes)
            extra = f" Đã lưu {paused}." if paused != "—" else ""
            self._set_status(
                f"Hướng 2 đang gõ {shown} rồi Enter đồng loạt. Chỉ dừng trình duyệt đổi ≥{bar}%.{extra}"
            )
            self.root.after(0, self._refresh_lanes)
            _script, batch = compile_script(four_lane_type_steps(codes, self._lane_clicks_for(active)))
            return batch

        def after(_loop: int) -> list[dict[str, Any]]:
            if self.player2 is None or self.player2.cancelled:
                return []
            active = list(self._pending2)
            if not active:
                return []
            clicks = self._lane_clicks_for(active)
            befores: dict[int, Any] = {}
            for index, _code in active:
                try:
                    befores[index] = capture_watch(self._lane_frame(index))
                except Exception:
                    befores[index] = None
            _script, burst = compile_script(four_lane_enter_burst_steps(clicks, count=len(active)))
            try:
                for event in burst:
                    if self.player2 is None or self.player2.cancelled:
                        return []
                    self.desktop.send(event)
            except Exception as exc:
                self._set_status(f"Hướng 2 lỗi Enter: {exc}.")
                return []
            time.sleep(0.5)
            if self.player2 is None or self.player2.cancelled:
                return []
            paused: list[str] = []
            kept: list[str] = []
            for index, code in active:
                try:
                    changed, percent = watch_changed(
                        befores.get(index),
                        capture_watch(self._lane_frame(index)),
                        self.lanes.change_threshold,
                    )
                except Exception:
                    self.lanes.take_lane(index)
                    kept.append(code)
                    continue
                if changed:
                    self.lanes.pause_lane(index, code)
                    paused.append(code)
                else:
                    self.lanes.take_lane(index)
                    kept.append(code)
            shown_paused = self._codes_text(paused)
            shown_kept = self._codes_text(kept)
            if paused and kept:
                self._set_status(
                    f"Đã lưu {shown_paused} vì màn hình trình duyệt đổi. Các mã khác vẫn chạy: {shown_kept}."
                )
            elif paused:
                self._set_status(
                    f"Đã lưu {shown_paused} vì màn hình trình duyệt đổi. Bấm Tiếp tục khi xong thao tác tay."
                )
            else:
                self._set_status(f"Đã Enter {shown_kept}. Không cửa sổ nào đổi — tiếp các trình duyệt còn lại.")
            if not self.lanes.peek() and self.player2 is not None:
                self.player2.cancel()
            self.root.after(0, self._refresh_lanes)
            self.root.after(0, lambda: self._persist_lanes(silent=True))
            return []

        try:
            result = self.player2.play([], times=0, prepare=prepare, after=after)
        except Exception as exc:
            result = {"loops": 0, "played": 0, "stopped": True}
            self._set_status(f"Hướng 2 dừng vì lỗi: {exc}.")
        finally:
            self.player2 = None
        self.root.after(0, self._refresh_lanes)
        self.root.after(0, lambda: self._persist_lanes(silent=True))
        if self.lanes.waiting and not self.lanes.peek():
            self._set_status(
                f"Hướng 2 đã lưu {self._codes_text(self.lanes.paused_codes, self.lanes.paused)}. Thao tác tay xong bấm Tiếp tục."
            )
        elif self.lanes.waiting:
            self._set_status(
                f"Hướng 2 đã lưu {self._codes_text(self.lanes.paused_codes, self.lanes.paused)}. Các trình duyệt khác đã hết dãy hoặc đã dừng."
            )
        elif result["stopped"]:
            self._set_status(
                f"Đã dừng hướng 2 sau {result['loops']} nhịp. Tiếp theo: {self._codes_text(self.lanes.peek())}."
            )
        else:
            self._set_status(f"Hướng 2 xong {result['loops']} nhịp.")

    def _on_lanes(self, *_args: Any) -> None:
        if self._loading_lanes:
            return
        cleaned = normalize_prefix(self.prefix2.get())
        if cleaned != self.prefix2.get():
            self.prefix2.set(cleaned)
            return
        self._sync_lanes(write_back=False)

    def _sync_lanes(self, *, write_back: bool = False) -> None:
        try:
            step = int(self.step2.get().strip() or "1")
        except ValueError:
            step = 1
            self.step2.set("1")
        if step < 1:
            step = 1
            self.step2.set("1")
        prefix = normalize_prefix(self.prefix2.get())
        start = normalize_digits(self.start2.get() or "00001")
        end = normalize_digits(self.end2.get() or start)
        currents = list(self.lanes.currents)
        exhausted = self.lanes.exhausted
        waiting = self.lanes.waiting
        waiting_lanes = list(self.lanes.waiting_lanes)
        paused = self.lanes.paused
        paused_codes = list(self.lanes.paused_codes)
        paused_lanes = list(self.lanes.paused_lanes)
        watch_frames = list(self.lanes.watch_frames)
        if start != self.lanes.start and not self._playing2():
            currents = []
            exhausted = False
            waiting = False
            waiting_lanes = None
            paused = ""
            paused_codes = []
            paused_lanes = []
        self.lanes = FourLaneQueue(
            prefix=prefix,
            start=start,
            end=end,
            currents=currents,
            step=step,
            exhausted=exhausted,
            last_used=self.lanes.last_used,
            flagged=self.lanes.flagged,
            paused=paused,
            paused_codes=paused_codes,
            paused_lanes=paused_lanes,
            waiting=waiting,
            waiting_lanes=waiting_lanes,
            watch_frame=self.lanes.watch_frame,
            watch_frames=watch_frames,
            lane_clicks=self.lanes.lane_clicks,
            change_threshold=normalize_threshold(self.threshold2.get(), LANE_PAUSE_THRESHOLD),
        )
        if write_back:
            if self.prefix2.get() != self.lanes.prefix:
                self.prefix2.set(self.lanes.prefix)
            if self.start2.get() != self.lanes.start:
                self.start2.set(self.lanes.start)
            if self.end2.get() != self.lanes.end:
                self.end2.set(self.lanes.end)
            if self.step2.get() != str(self.lanes.step):
                self.step2.set(str(self.lanes.step))
            if self.threshold2.get() != str(self.lanes.change_threshold):
                self.threshold2.set(str(self.lanes.change_threshold))
        self._refresh_lanes()

    def _refresh_lanes(self) -> None:
        nxt = self.lanes.peek()
        last = self.lanes.last_used
        paused = self._codes_text(self.lanes.paused_codes, self.lanes.paused)
        if self.lanes.waiting and paused != "—" and nxt:
            self.lanes_label.set("Đã lưu " + paused + " — các trình duyệt khác: " + " · ".join(nxt))
        elif self.lanes.waiting and paused != "—":
            self.lanes_label.set("Hướng 2 đã lưu " + paused + " — bấm Tiếp tục")
        elif nxt and last:
            self.lanes_label.set("Hướng 2 sẽ gõ: " + " · ".join(nxt) + "   (vừa xong: " + " · ".join(last) + ")")
        elif nxt:
            self.lanes_label.set("Hướng 2 sẽ gõ: " + " · ".join(nxt))
        else:
            self.lanes_label.set("Hướng 2 — 4 làn: —")
        self.frame_label.set(frames_label(self.lanes.watch_frames))
        ready = len(sanitize_clicks(self.lanes.lane_clicks))
        if ready >= RECOVER_CLICKS:
            self.fields_label.set("4 ô nhập: đã nhớ — mỗi ô một trình duyệt, Enter đồng loạt.")
        elif ready:
            self.fields_label.set(f"4 ô nhập: đã nhớ {ready}/4 — chọn đủ 4 ô, mỗi ô một trình duyệt.")
        else:
            self.fields_label.set("4 ô nhập: chưa ghi — chọn 4 ô, mỗi ô một trình duyệt.")
        self.flag_box2.delete(0, tk.END)
        for value in self.lanes.flagged:
            self.flag_box2.insert(tk.END, value)

    def _persist_lanes(self, *, silent: bool) -> None:
        self._sync_lanes()
        if not self.lanes.prefix and not self.lanes.last_used and not self.lanes.flagged:
            if not silent:
                self.status.set("Hướng 2: nhập 5 ký tự cố định rồi lưu.")
            return
        sample = " ".join(self.lanes.peek() or self.lanes.last_used or ["1"])
        try:
            saved = self.store.save(
                "Hướng 2 — 4 làn Enter",
                [],
                slug=self.slug2,
                numbers=self.lanes.to_dict(),
                steps=four_lane_enter_steps(
                    self.lanes.peek() or self.lanes.last_used or [sample],
                    self.lanes.lane_clicks,
                ),
            )
        except ValueError:
            if not silent:
                self.status.set("Hướng 2: chưa có số để lưu.")
            return
        self.slug2 = str(saved["slug"])
        if not silent:
            if self.lanes.waiting:
                self.status.set(
                    "Đã lưu hướng 2. Đang chờ Tiếp tục tại "
                    + self._codes_text(self.lanes.paused_codes, self.lanes.paused)
                    + "."
                )
            else:
                nxt = self.lanes.peek()
                self.status.set("Đã lưu hướng 2. Tiếp theo: " + (" · ".join(nxt) if nxt else "—") + ".")

    def _load_lanes(self) -> None:
        item = self.store.get(self.slug2)
        if item is None:
            self._refresh_lanes()
            return
        loaded = FourLaneQueue.from_dict(item.get("numbers"))
        self.lanes = loaded
        self._loading_lanes = True
        self.prefix2.set(self.lanes.prefix)
        self.start2.set(self.lanes.start)
        self.end2.set(self.lanes.end)
        self.step2.set(str(self.lanes.step))
        self.threshold2.set(str(self.lanes.change_threshold))
        self._loading_lanes = False
        self._refresh_lanes()

    def _close(self) -> None:
        self.stop_all()
        self._close_overlay()
        self._close_framer()
        self._close_painter()
        self._persist(silent=True)
        self._persist_lanes(silent=True)
        self.desktop.stop()
        self.root.destroy()


def _click_from_steps(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in steps:
        if step.get("kind") == "click":
            return {
                "kind": "click",
                "x": step.get("x"),
                "y": step.get("y"),
                "button": step.get("button") or "left",
                "wait": int(step.get("wait") or 80),
            }
    return None


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
