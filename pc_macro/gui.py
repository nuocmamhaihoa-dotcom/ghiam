"""Simple window: type a number, Enter, add 1, save the number if the screen changes."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.hub import HubError, configured, describe, get_run, pull, push, set_run
from pc_macro.numbers import (
    FourLaneQueue,
    LANE_COUNT,
    NumberQueue,
    code_digits,
    make_code,
    normalize_digits,
    normalize_prefix,
)
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
    click_slots,
    current_screen_size,
    customer_target,
    frames_from_clicks,
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
    enter_key_steps,
    four_lane_enter_steps,
    number_enter_steps,
    one_code_type_steps,
)
from pc_macro.session import Player, Recorder, ignore_window
from pc_macro.store import MacroStore
from pc_macro.updater import consume_just_updated
from pc_macro.version import PC_MACRO_BUILD
from pc_macro.vpn import KIND_LABELS, VpnClient, status_label
from pc_macro.win_focus import (
    bring_window_at,
    find_window_by_title,
    focus_handle,
    is_hub_title,
    is_window_alive,
    lower_hub_windows,
    window_handle_at,
    window_title,
)


def main() -> None:
    if not available():
        print(install_hint())
        print("Giải nén zip, rồi bấm đúp CHAY-O-DAY.cmd")
        raise SystemExit(1)
    App().run()


class App:
    def __init__(self) -> None:
        self.store = MacroStore()
        self.root = tk.Tk()
        self.root.title("Gõ số rồi Enter")
        self.root.geometry("380x620")
        self.root.minsize(360, 560)
        self.root.configure(bg="#102033")
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value=_startup_status())
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
        self.delay2 = tk.StringVar(value="0")
        self.step2 = tk.StringVar(value="1")
        self.threshold2 = tk.StringVar(value=str(LANE_PAUSE_THRESHOLD))
        self.lanes_label = tk.StringVar(value="Hướng 2 — 1 tiến trình: —")
        self.frame_label = tk.StringVar(value=frames_label(None))
        self.fields_label = tk.StringVar(value="Ctrl+F7 hoặc CHẠY: tự chọn ô, rồi dãy số tăng và Enter từng lần")
        self.now_code = tk.StringVar(value="—")
        self.changed_label = tk.StringVar(value="Chưa có số đổi giao diện")
        self.lane_card_vars = [
            tk.StringVar(value="Trình duyệt web khách  —\nChưa gắn\nChưa lưu số khi đổi")
            for _index in range(LANE_COUNT)
        ]
        self.run2 = tk.BooleanVar(value=False)
        self.vpn = VpnClient()
        self.vpn_kind = tk.StringVar(value=KIND_LABELS["free"])
        self.vpn_name = tk.StringVar(value="")
        self.vpn_config = tk.StringVar(value="")
        self.vpn_user = tk.StringVar(value="")
        self.vpn_pass = tk.StringVar(value="")
        self.vpn_disconnect = tk.StringVar(value="")
        self.vpn_enabled = tk.BooleanVar(value=False)
        self.vpn_exit = tk.BooleanVar(value=True)
        self.vpn_privacy = tk.BooleanVar(value=False)
        self.vpn_ipv6 = tk.BooleanVar(value=False)
        self.vpn_label = tk.StringVar(value=status_label(self.vpn.profile))
        self._filling_vpn = False
        self._vpn_busy = False
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
        self._picking_lane_index: int | None = None
        self._picking_frame_index: int | None = None
        self._run_after_pick = False
        self._mods = {"ctrl": False, "shift": False, "alt": False, "cmd": False}
        self._build()
        self._fill_vpn()
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
        self.root.after(200, self._vpn_autostart)
        self.root.after(900, self._poll_run_cmd)
        self.root.mainloop()

    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        wrap = tk.Frame(parent, bg="#ffffff", highlightbackground="#d5dbe3", highlightthickness=1)
        wrap.pack(fill="x", padx=12, pady=6)
        tk.Label(wrap, text=title, font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#17202a", anchor="w").pack(
            fill="x", padx=12, pady=(10, 4)
        )
        return wrap

    def _action_btn(self, parent: tk.Misc, text: str, command: Any, *, fill: str, fg: str = "#ffffff") -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 13, "bold"),
            bg=fill,
            fg=fg,
            activebackground=fill,
            activeforeground=fg,
            relief="flat",
            padx=16,
            pady=10,
            cursor="hand2",
        )
        return btn

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 5}
        bg = "#102033"
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabel", background=bg, foreground="#d6e8ff")
        style.configure("TFrame", background=bg)
        style.configure("TCheckbutton", background=bg, foreground="#d6e8ff")
        style.configure("TEntry", fieldbackground="#ffffff")

        tk.Label(
            self.root,
            textvariable=self.now_code,
            font=("Segoe UI", 28, "bold"),
            bg=bg,
            fg="#7dd3fc",
            anchor="center",
        ).pack(fill="x", padx=16, pady=(18, 2))
        tk.Label(
            self.root,
            textvariable=self.fields_label,
            font=("Segoe UI", 10),
            bg=bg,
            fg="#93c5fd",
            wraplength=340,
            justify="center",
        ).pack(fill="x", padx=16)
        tk.Label(
            self.root,
            textvariable=self.status,
            font=("Segoe UI", 9),
            bg=bg,
            fg="#cbd5e1",
            wraplength=340,
            justify="center",
        ).pack(fill="x", padx=16, pady=(2, 8))

        fields = tk.Frame(self.root, bg=bg)
        fields.pack(fill="x", padx=16, pady=4)
        ttk.Entry(fields, textvariable=self.prefix2, width=7, font=("Segoe UI", 18), justify="center").pack(
            side="left", expand=True, fill="x", padx=(0, 6)
        )
        ttk.Entry(fields, textvariable=self.start2, width=6, font=("Segoe UI", 18), justify="center").pack(
            side="left", expand=True, fill="x", padx=3
        )
        ttk.Entry(fields, textvariable=self.end2, width=6, font=("Segoe UI", 18), justify="center").pack(
            side="left", expand=True, fill="x", padx=(6, 0)
        )

        acts = tk.Frame(self.root, bg=bg)
        acts.pack(fill="x", padx=16, pady=(14, 6))
        self._btn_bind = self._action_btn(acts, "GẮN", self.pick_lane_fields, fill="#334155")
        self._btn_bind.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._btn_start2 = self._action_btn(acts, "CHẠY · Ctrl+F7", self._on_run_tap, fill="#16a34a")
        self._btn_start2.pack(side="left", fill="x", expand=True)
        self._btn_stop2 = self._btn_start2
        self.root.bind("<Control-F7>", lambda _event: self._on_hotkey_start())
        self.root.bind("<F8>", lambda _event: self._on_stop_hotkey())

        board = tk.Frame(self.root, bg="#16324f", highlightbackground="#1e4970", highlightthickness=1)
        board.pack(fill="both", expand=True, padx=16, pady=(10, 4))
        tk.Label(
            board,
            text="5 số đang chạy",
            font=("Segoe UI", 9, "bold"),
            bg="#16324f",
            fg="#93c5fd",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 0))
        head = tk.Frame(board, bg="#1e4970")
        head.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(head, text="5 số", width=8, font=("Segoe UI", 9, "bold"), bg="#1e4970", fg="#e2e8f0").pack(
            side="left", padx=4, pady=4
        )
        tk.Label(head, text="Sau Enter", font=("Segoe UI", 9, "bold"), bg="#1e4970", fg="#e2e8f0", anchor="w").pack(
            side="left", fill="x", expand=True, padx=4, pady=4
        )
        self._board_body = tk.Frame(board, bg="#16324f")
        self._board_body.pack(fill="both", expand=True, padx=8, pady=4)
        tk.Label(
            board,
            textvariable=self.changed_label,
            font=("Segoe UI", 9, "bold"),
            bg="#16324f",
            fg="#fbbf24",
            wraplength=320,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 8))

        foot = tk.Frame(self.root, bg=bg)
        foot.pack(fill="x", padx=16, pady=(8, 4))
        ttk.Checkbutton(foot, text="VPN", variable=self.vpn_enabled, command=self._on_vpn_toggle).pack(side="left")
        tk.Label(foot, textvariable=self.vpn_label, bg=bg, fg="#64748b", font=("Segoe UI", 8)).pack(
            side="left", padx=8
        )

        hidden = tk.Frame(self.root)
        ttk.Entry(hidden, textvariable=self.delay2)
        ttk.Entry(hidden, textvariable=self.step2)
        ttk.Entry(hidden, textvariable=self.threshold2)
        ttk.Combobox(hidden, textvariable=self.vpn_kind, values=list(KIND_LABELS.values()))
        ttk.Entry(hidden, textvariable=self.vpn_name)
        self.flag_box2 = tk.Listbox(hidden, height=1)
        self.lanes_label.set("")
        self.frame_label.set("")
        for index in range(LANE_COUNT):
            tk.Label(hidden, textvariable=self.lane_card_vars[index])

        self._dir1 = ttk.Frame(self.root)
        ttk.Label(self._dir1, text="5 ký tự cố định (đứng trước, đổi được)").pack(anchor="w", padx=12)
        ttk.Entry(self._dir1, textvariable=self.prefix, width=8, font=("Segoe UI", 18)).pack(anchor="w", padx=12, pady=4)

        row = ttk.Frame(self._dir1)
        row.pack(fill="x", **pad)
        left = ttk.Frame(row)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Số bắt đầu (5 số)").pack(anchor="w")
        ttk.Entry(left, textvariable=self.start, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)
        right = ttk.Frame(row)
        right.pack(side="left", fill="x", expand=True, padx=(12, 0))
        ttk.Label(right, text="Số kết thúc (5 số)").pack(anchor="w")
        ttk.Entry(right, textvariable=self.end, width=8, font=("Segoe UI", 16)).pack(anchor="w", pady=2)

        ttk.Label(self._dir1, textvariable=self.current_label, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=12, pady=8)

        opts = ttk.Frame(self._dir1)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts, textvariable=self.delay, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(opts, text="Mỗi lần cộng").pack(side="left")
        ttk.Entry(opts, textvariable=self.step, width=5).pack(side="left", padx=6)
        ttk.Label(opts, text="Đổi màn ≥ %").pack(side="left", padx=(14, 0))
        ttk.Entry(opts, textvariable=self.threshold, width=4).pack(side="left", padx=6)

        buttons = ttk.Frame(self._dir1)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Bắt đầu", command=self.start_play).pack(side="left", padx=2)
        ttk.Button(buttons, text="Dừng", command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(buttons, text="Giao diện đổi", command=self.flag_change).pack(side="left", padx=2)

        extra = ttk.Frame(self._dir1)
        extra.pack(fill="x", **pad)
        ttk.Button(extra, text="Ghi chỗ nhập", command=self.start_record).pack(side="left", padx=2)
        ttk.Button(extra, text="Ghi 4 nút", command=self.start_record_recover).pack(side="left", padx=2)
        ttk.Button(extra, text="Lưu", command=self.save_current).pack(side="left", padx=2)
        ttk.Button(extra, text="Lên VPS", command=self.push_hub).pack(side="left", padx=2)
        ttk.Button(extra, text="Lấy VPS", command=self.pull_hub).pack(side="left", padx=2)

        ttk.Label(self._dir1, text="4 nút chuột trái — tên, chọn vị trí hoặc bôi màu trên ảnh").pack(anchor="w", padx=12, pady=(8, 2))
        self._name_vars: list[tk.StringVar] = []
        self._mark_cards: list[tuple[tk.Frame, tk.Label, tk.Label, tk.Frame]] = []
        self._loading_names = False
        for index in range(RECOVER_CLICKS):
            card = tk.Frame(self._dir1, bd=1, relief="solid", bg="#f3f8fd")
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
        marks = ttk.Frame(self._dir1)
        marks.pack(fill="x", **pad)
        ttk.Button(marks, text="Lưu 4 nút", command=self.save_recover).pack(side="left", padx=2)
        ttk.Button(marks, text="Hiện 4 nút trên màn", command=self.preview_recover).pack(side="left", padx=2)
        ttk.Button(marks, text="Xóa 4 nút", command=self.clear_recover).pack(side="left", padx=2)
        ttk.Label(self._dir1, textvariable=self.recover_label, wraplength=390).pack(anchor="w", padx=12, pady=(2, 6))
        ttk.Label(self._dir1, text="Số lúc giao diện đổi ≥ ngưỡng — F9 cũng lưu số vừa Enter.").pack(anchor="w", padx=12, pady=(8, 2))
        self.flag_box = tk.Listbox(self._dir1, height=4, font=("Segoe UI", 12))
        self.flag_box.pack(fill="both", expand=True, padx=12, pady=4)
        ttk.Label(
            self._dir1,
            text="Bắt đầu = gõ Enter + nếu màn đổi ≥80% tự bấm 4 nút. F8 dừng. Bôi màu / Chọn vị trí từng bước.",
        ).pack(anchor="w", padx=12, pady=(0, 8))
        self._paint_run_buttons()

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
        self._persist_vpn()
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
        if self._playing2() or self.lanes.running or self._pending2 or self.lanes.last_used:
            self._sync_lanes()
            code = ""
            if self._pending2:
                code = self._pending2[0][1]
            code = code or self.lanes.running or (self.lanes.last_used[0] if self.lanes.last_used else "") or self.lanes.peek_one()
            if code:
                self.lanes.note_enter(code, True)
                self._refresh_lanes()
                self._persist_lanes(silent=True)
                self._post_run({"status": "play" if self._playing2() else "idle"})
                self.status.set(f"Đã ghi {code_digits(code)} vì giao diện đổi.")
                return
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
        if not self._ensure_vpn():
            if self.player is not None:
                self.player.cancel()
            self.player = None
            return
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
            leak = self.vpn.check_leak()
            if leak:
                self._set_status(leak)
                if self.player is not None:
                    self.player.cancel()
                return []
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
        if raw.get("kind") == "key" and key in self._mods:
            self._mods[key] = raw.get("action") == "down"
            return
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f9":
            self.root.after(0, self.flag_change)
            return
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f7" and (
            self._mods.get("ctrl") or self._mods.get("cmd")
        ):
            self.root.after(0, self._on_hotkey_start)
            return
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f8":
            self.root.after(0, self._on_stop_hotkey)
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
            index = self._picking_lane_index
            self._apply_lane_clicks(clicks, index=index)
            self._deiconify()
            self._refresh_lanes()
            self._persist_lanes(silent=True)
            self._picking_lane_index = None
            if self._run_after_pick:
                self._run_after_pick = False
                self.status.set("Đã chọn ô. Gõ mã, Enter một lần, rồi tăng 1.")
                self.root.after(80, self.start_play2)
                return
            self.status.set(
                "Đã gắn ô. Ctrl+F7 hoặc CHẠY: gõ mã → Enter một lần → tăng 1."
            )
            return
        self._recover_draft = sanitize_clicks(clicks)
        self._finish_recover(keep=True)

    def _overlay_cancel(self) -> None:
        self._overlay = None
        armed = self._run_after_pick
        self._run_after_pick = False
        if self._picking_lanes:
            self._picking_lanes = False
            self._deiconify()
            self._picking_lane_index = None
            self.status.set("Đã hủy chọn ô." if armed else "Đã hủy gắn trình / ô.")
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

    def _toggle_run2(self) -> None:
        self._on_run_tap()

    def _on_hotkey_start(self) -> None:
        """Ctrl+F7: pick the field if needed, then type → Enter → +1."""
        if self._picking_lanes or self._overlay is not None or self._run_after_pick:
            return
        if self._playing2() or self.run2.get():
            return
        if self.lanes.waiting:
            self.continue_play2()
            return
        self.start_play2()

    def _on_stop_hotkey(self) -> None:
        if self.player is not None:
            self.player.cancel()
        if self.player2 is not None:
            self.player2.cancel()
        if self._record_recover:
            self._finish_recover(keep=False)
        overlay = self._overlay
        if overlay is not None:
            try:
                overlay.cancel()
            except tk.TclError:
                self._overlay_cancel()
        elif self._picking_lanes:
            self._overlay_cancel()
        if self._painter is not None:
            self._paint_cancel()
        if self._framer is not None:
            self._frame_cancel()
        if self.recorder.on:
            recorded = self.recorder.stop()
            self._after_record(recorded)
        if self.run2.get() and not self._playing2():
            self.stop_play2()

    def _on_run_tap(self) -> None:
        if self._playing2() or self.run2.get():
            self.stop_play2()
            return
        if self.lanes.waiting:
            self.continue_play2()
            return
        self.start_play2()

    def _paint_run_buttons(self) -> None:
        playing = self._playing2() or bool(self.run2.get())
        start = getattr(self, "_btn_start2", None)
        if start is not None:
            if playing:
                start.configure(text="TẮT · F8", bg="#dc2626", activebackground="#dc2626")
            elif self.lanes.waiting:
                start.configure(text="TIẾP · Ctrl+F7", bg="#0b6bcb", activebackground="#0b6bcb")
            else:
                start.configure(text="CHẠY · Ctrl+F7", bg="#16a34a", activebackground="#16a34a")
        bind = getattr(self, "_btn_bind", None)
        if bind is not None:
            ready = customer_target(self.lanes.lane_clicks) is not None
            bind.configure(
                text="ĐÃ GẮN" if ready else "GẮN",
                bg="#0f766e" if ready else "#334155",
                activebackground="#0f766e" if ready else "#334155",
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
            self.run2.set(False)
            return
        if delay < 0:
            self.status.set("Hướng 2: chờ không được âm.")
            self.run2.set(False)
            return
        if customer_target(self.lanes.lane_clicks) is None:
            self._run_after_pick = True
            self.status.set("Bấm 1 lần vào ô muốn gõ dãy số. Mỗi số Enter một lần rồi tăng 1.")
            self.pick_one_lane(0)
            return
        self._persist_vpn()
        self.player2 = Player(self.desktop.send, time.sleep)
        self.run2.set(True)
        bar = self.lanes.change_threshold
        self.status.set(
            f"Gõ mã vào ô đã chọn, Enter một lần, tăng 1. F8 tắt. Khung ≥{bar}% thì F9 lưu mã."
        )
        self._post_run({"play_cmd": "run", "status": "play", "pc_bound": True})
        self._paint_run_buttons()
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
            self.status.set(f"Tiếp tục trình duyệt khách đã dừng: {nxt}.")
            self.run2.set(True)
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
        if customer_target(self.lanes.lane_clicks) is None:
            self.status.set("Chưa gắn Evoshop. Bấm Gắn trình khách rồi Tiếp tục.")
            self.run2.set(False)
            return
        self._persist_vpn()
        self.player2 = Player(self.desktop.send, time.sleep)
        self.status.set(f"Tiếp tục hướng 2 từ {nxt or self._codes_text(self.lanes.peek())}.")
        self.run2.set(True)
        self._post_run({"play_cmd": "run", "status": "play", "pc_bound": True})
        self._paint_run_buttons()
        self._play_thread2 = threading.Thread(target=self._run_play2, args=(0,), daemon=True)
        self._play_thread2.start()

    def stop_play2(self) -> None:
        if self.player2 is not None:
            self.player2.cancel()
        self.run2.set(False)
        nxt = self._codes_text(self.lanes.paused_codes, self.lanes.paused)
        if nxt == "—":
            nxt = self._codes_text(self.lanes.peek())
        self._post_run({"play_cmd": "", "status": "idle"})
        self._paint_run_buttons()
        try:
            self.root.deiconify()
        except tk.TclError:
            pass
        self.status.set(f"Đã tắt. {'Chờ Tiếp tục tại ' if self.lanes.waiting else 'Tiếp theo: '}{nxt}.")

    def save_lanes(self) -> None:
        self._persist_lanes(silent=False)

    def _apply_lane_clicks(self, clicks: list[dict[str, Any]], index: int | None = None) -> None:
        self.lanes.lane_clicks = sanitize_clicks(clicks)
        frames = frames_from_clicks(self.lanes.lane_clicks)
        self.lanes.watch_frames = frames
        self.lanes.watch_frame = frames[0] if frames else None
        if index is not None and 0 <= index < LANE_COUNT and not self.lanes.lane_titles[index]:
            self.lanes.lane_titles[index] = "Evoshop — trình duyệt web khách"

    def pick_one_lane(self, index: int) -> None:
        if self._busy():
            return
        if not (0 <= index < LANE_COUNT):
            return
        self._picking_lanes = True
        self._picking_lane_index = index
        self.status.set("Bấm 1 lần vào ô muốn cho dãy số chạy tăng. Esc hoặc F8 hủy.")
        self.root.withdraw()
        try:
            self._overlay = show_click_overlay(
                self.root,
                on_done=self._overlay_done,
                on_cancel=self._overlay_cancel,
                clicks=self.lanes.lane_clicks,
                recording=True,
                step=index,
                noun="ô",
                instant=True,
            )
        except tk.TclError:
            self._picking_lanes = False
            self._picking_lane_index = None
            self._run_after_pick = False
            self.root.deiconify()
            self.status.set("Không mở được lớp phủ để gắn trình duyệt.")

    def continue_lane(self, index: int) -> None:
        if self._playing():
            self.status.set("Đang chạy hướng 1. Bấm Dừng rồi hãy tiếp tục hướng 2.")
            return
        if not (0 <= index < LANE_COUNT):
            return
        self._sync_lanes(write_back=True)
        if not self.lanes.waiting_lanes[index]:
            self.status.set(f"Trình {index + 1} không đang dừng.")
            return
        nxt = self.lanes.resume_lane(index)
        self._refresh_lanes()
        self._persist_lanes(silent=True)
        if self._playing2():
            self.status.set(f"Tiếp tục trình duyệt khách: {nxt or '—'}.")
            self.run2.set(True)
            return
        if not nxt and not self.lanes.peek():
            self.status.set("Trình duyệt khách: không còn mã để tiếp tục.")
            return
        if customer_target(self.lanes.lane_clicks) is None:
            self.status.set("Chưa gắn Evoshop. Bấm Gắn trình khách rồi Tiếp tục.")
            self.run2.set(False)
            return
        self._persist_vpn()
        self.player2 = Player(self.desktop.send, time.sleep)
        shown = nxt or self._codes_text(self.lanes.peek())
        self.status.set(f"Tiếp tục Evoshop từ {shown}.")
        self.run2.set(True)
        self._post_run({"play_cmd": "run", "status": "play", "pc_bound": True})
        self._paint_run_buttons()
        self._play_thread2 = threading.Thread(target=self._run_play2, args=(0,), daemon=True)
        self._play_thread2.start()

    def pick_lane_fields(self) -> None:
        self.pick_one_lane(0)

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

    def _lane_focus_clicks(self) -> list[dict[str, Any] | None]:
        sw, sh = self._screen_size()
        mapped: list[dict[str, Any] | None] = []
        for slot in click_slots(self.lanes.lane_clicks):
            if slot is None:
                mapped.append(None)
                continue
            scaled = scale_clicks([slot], sw, sh)
            mapped.append(scaled[0] if scaled else None)
        return mapped

    def _lane_clicks_for(self, active: list[tuple[int, str]]) -> list[dict[str, Any] | None]:
        clicks = self._lane_focus_clicks()
        return [clicks[index] if index < len(clicks) else None for index, _code in active]

    def _lane_frame(self, index: int) -> dict[str, Any] | None:
        return lane_watch_frame(self.lanes.watch_frames, self._lane_focus_clicks(), index)

    def _raise_customer(self) -> dict[str, Any] | None:
        target = customer_target(self._lane_focus_clicks()) or customer_target(self.lanes.lane_clicks)
        if target is None:
            return None
        lower_hub_windows()
        handle = 0
        try:
            handle = int(target.get("hwnd") or 0)
        except (TypeError, ValueError):
            handle = 0
        title = str(target.get("wtitle") or "")
        if handle and not is_window_alive(handle):
            handle = 0
        if not handle and title:
            handle = find_window_by_title(title)
        if handle and focus_handle(handle):
            return target
        wx = int(target["wx"])
        wy = int(target["wy"])
        hit = window_handle_at(wx, wy)
        if hit and is_hub_title(window_title(hit)):
            return None
        if hit and focus_handle(hit):
            return target
        if bring_window_at(wx, wy):
            hit = window_handle_at(wx, wy)
            if hit and is_hub_title(window_title(hit)):
                return None
            return target
        return None

    def _post_run(self, payload: dict[str, Any]) -> None:
        if not configured():
            return

        def work() -> None:
            body = dict(payload)
            peek = self.lanes.peek()
            body.setdefault("current", peek[0] if peek else "")
            body.setdefault("digits", self.lanes.running_digits())
            body.setdefault("changed", list(self.lanes.changed_digits()))
            body.setdefault("turns", [dict(item) for item in self.lanes.board_rows(40)])
            body.setdefault("pc_bound", customer_target(self.lanes.lane_clicks) is not None)
            try:
                set_run(body)
            except HubError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _poll_run_cmd(self) -> None:
        try:
            self.root.after(1200, self._poll_run_cmd)
        except tk.TclError:
            return
        if self._picking_lanes or self.recorder.on:
            return
        threading.Thread(target=self._poll_run_worker, daemon=True).start()

    def _poll_run_worker(self) -> None:
        if not configured():
            return
        try:
            body = get_run()
        except HubError:
            return
        self.root.after(0, lambda: self._apply_run_cmd(body))
        peek = self.lanes.peek()
        try:
            set_run(
                {
                    "pc_seen": int(time.time()),
                    "pc_bound": customer_target(self.lanes.lane_clicks) is not None,
                    "current": peek[0] if peek else "",
                    "digits": self.lanes.running_digits(),
                    "changed": list(self.lanes.changed_digits()),
                    "turns": [dict(item) for item in self.lanes.board_rows(40)],
                    "status": "play" if self._playing2() else "idle",
                }
            )
        except HubError:
            return

    def _apply_run_cmd(self, body: dict[str, Any]) -> None:
        cmd = str((body or {}).get("play_cmd") or "")
        if cmd == "run" and not self._playing2():
            self.start_play2()
            return
        if cmd == "stop" and self._playing2():
            self.stop_play2()

    def _run_play2(self, delay: float) -> None:
        if not self._ensure_vpn():
            self._set_status("VPN chưa nối — hướng 2 vẫn gõ vào Evoshop đã gắn.")
        remaining = delay
        while remaining > 0 and self.player2 is not None and not self.player2.cancelled:
            self._set_status(f"Hướng 2 bắt đầu sau {int(remaining)} giây…")
            time.sleep(min(1.0, remaining))
            remaining -= 1
        if self.player2 is None or self.player2.cancelled:
            self._set_status("Đã dừng hướng 2.")
            self.player2 = None
            return
        self.root.after(0, self.root.iconify)
        if self._raise_customer() is None:
            self._set_status("Mất gắn Evoshop. Bấm Gắn trình khách rồi bật chạy lại.")
            self.player2 = None
            self.root.after(0, lambda: self.run2.set(False))
            self.root.after(0, self._paint_run_buttons)
            self.root.after(0, self.root.deiconify)
            self._post_run({"play_cmd": "", "status": "need_bind", "pc_bound": False})
            return
        time.sleep(0.28)

        def prepare(_loop: int) -> list[dict[str, Any]]:
            leak = self.vpn.check_leak()
            if leak:
                self._set_status(f"{leak} Vẫn gõ số rồi Enter.")
            if self._raise_customer() is None:
                return []
            time.sleep(0.2)
            active = self.lanes.peek_active()
            if not active:
                return []
            self._pending2 = list(active)
            clicks = self._lane_clicks_for(active)
            shown = self._codes_text([code for _index, code in active])
            self.lanes.mark_running(active[0][1])
            self._set_status(f"Đang gõ 5 số {code_digits(active[0][1])} · {shown}. Rồi Enter một lần.")
            self.root.after(0, self._refresh_lanes)
            self._post_run({"status": "play"})
            steps: list[dict[str, Any]] = []
            for (_index, code), click in zip(active, clicks):
                # Xóa mã cũ rồi gõ mã hiện tại. Enter và +1 chỉ chạy sau bước này.
                steps.extend(one_code_type_steps(code, click))
            _script, batch = compile_script(steps)
            return batch

        def after(_loop: int) -> list[dict[str, Any]]:
            if self.player2 is None or self.player2.cancelled:
                return []
            active = list(self._pending2)
            if not active:
                return []
            befores: dict[int, Any] = {}
            for index, _code in active:
                try:
                    befores[index] = capture_watch(self._lane_frame(index))
                except Exception:
                    befores[index] = None
            _script, burst = compile_script(enter_key_steps())
            try:
                self.player2.emit(burst)
            except Exception as exc:
                self._set_status(f"Lỗi Enter: {exc}.")
                return []
            time.sleep(0.55)
            if self.player2 is None or self.player2.cancelled:
                return []
            kept: list[str] = []
            hits: list[str] = []
            for index, code in active:
                try:
                    changed, _percent = watch_changed(
                        befores.get(index),
                        capture_watch(self._lane_frame(index)),
                        self.lanes.change_threshold,
                    )
                except Exception:
                    changed = False
                self.lanes.note_enter(code, changed)
                self.lanes.take_lane(index)
                kept.append(code)
                if changed:
                    hits.append(code_digits(code))
            shown = " · ".join(code_digits(item) for item in kept) or "—"
            nxt = code_digits(self.lanes.peek_one()) or "—"
            if hits:
                self._set_status(f"Đã Enter {shown}. Đổi giao diện: {' · '.join(hits)}. Tiếp theo: {nxt}.")
            else:
                self._set_status(f"Đã Enter {shown}. Tăng 1 → tiếp theo: {nxt}.")
            if not self.lanes.peek() and self.player2 is not None:
                self.player2.cancel()
            self.root.after(0, self._refresh_lanes)
            self.root.after(0, lambda: self._persist_lanes(silent=True))
            self._post_run({"status": "play"})
            return []

        try:
            result = self.player2.play([], times=0, prepare=prepare, after=after)
        except Exception as exc:
            result = {"loops": 0, "played": 0, "stopped": True}
            self._set_status(f"Hướng 2 dừng vì lỗi: {exc}.")
        finally:
            self.player2 = None
            self.root.after(0, lambda: self.run2.set(False))
            self.root.after(0, self._paint_run_buttons)
            self.root.after(0, self.root.deiconify)
            self._post_run({"play_cmd": "", "status": "idle"})
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
        turns = list(self.lanes.turns)
        running = self.lanes.running
        if start != self.lanes.start and not self._playing2():
            currents = []
            exhausted = False
            waiting = False
            waiting_lanes = None
            paused = ""
            paused_codes = []
            paused_lanes = []
            turns = []
            running = ""
        self.lanes = FourLaneQueue(
            prefix=prefix,
            start=start,
            end=end,
            currents=currents,
            step=step,
            exhausted=exhausted,
            last_used=self.lanes.last_used,
            flagged=self.lanes.flagged,
            flagged_lanes=self.lanes.flagged_lanes,
            lane_titles=self.lanes.lane_titles,
            paused=paused,
            paused_codes=paused_codes,
            paused_lanes=paused_lanes,
            waiting=waiting,
            waiting_lanes=waiting_lanes,
            watch_frame=self.lanes.watch_frame,
            watch_frames=watch_frames,
            lane_clicks=self.lanes.lane_clicks,
            change_threshold=normalize_threshold(self.threshold2.get(), LANE_PAUSE_THRESHOLD),
            turns=turns,
            running=running,
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
        self.now_code.set(self.lanes.running_digits() or "—")
        self._paint_board()
        if self.lanes.waiting and paused != "—" and nxt:
            self.lanes_label.set("Đã lưu " + paused + " — tiếp theo: " + " · ".join(nxt))
        elif self.lanes.waiting and paused != "—":
            self.lanes_label.set("Đã lưu " + paused + " — bấm Tiếp tục")
        elif nxt and last:
            self.lanes_label.set("Tiếp theo: " + " · ".join(nxt) + "   (vừa xong: " + " · ".join(last) + ")")
        elif nxt:
            self.lanes_label.set("Sẵn sàng gõ vào trình duyệt đã gắn")
        else:
            self.lanes_label.set("Nhập 5 ký tự cố định, số bắt đầu và số kết thúc")
        self.frame_label.set(frames_label(self.lanes.watch_frames))
        ready = customer_target(self.lanes.lane_clicks) is not None
        if ready:
            self.fields_label.set("Đã gắn ô. Ctrl+F7 / CHẠY: gõ mã → Enter → +1. F8 tắt.")
        else:
            self.fields_label.set("Ctrl+F7 hoặc CHẠY: tự chọn ô, rồi dãy số tăng và Enter từng lần")
        self._paint_run_buttons()
        slots = click_slots(self.lanes.lane_clicks)
        active = {index: code for index, code in self.lanes.peek_active()}
        for index in range(LANE_COUNT):
            waiting = index < len(self.lanes.waiting_lanes) and self.lanes.waiting_lanes[index]
            paused = self.lanes.paused_lanes[index] if index < len(self.lanes.paused_lanes) else ""
            last = self.lanes.last_used[index] if index < len(self.lanes.last_used) else ""
            code = paused if waiting else (active.get(index) or last)
            slot = slots[index] if index < len(slots) else None
            try:
                window_x = int(slot.get("wx")) if slot else -1
            except (TypeError, ValueError, AttributeError):
                window_x = -1
            if slot and window_x >= 0:
                bind = "Đã gắn trình khách + dòng mã KH / SĐT"
            elif slot:
                bind = "Đã gắn dòng mã KH / SĐT"
            else:
                bind = "Chưa gắn"
            mark = " · dừng" if waiting else ""
            title = self.lanes.lane_titles[index] or "Trình duyệt web khách"
            saved = self.lanes.flagged_lanes[index] if index < len(self.lanes.flagged_lanes) else []
            saved_txt = ("Lưu: " + " · ".join(saved[-4:])) if saved else "Chưa lưu số khi đổi"
            self.lane_card_vars[index].set(f"{title}  {code or '—'}\n{bind}{mark}\n{saved_txt}")
        self.flag_box2.delete(0, tk.END)
        for value in self.lanes.flagged:
            self.flag_box2.insert(tk.END, value)

    def _paint_board(self) -> None:
        body = getattr(self, "_board_body", None)
        if body is None:
            return
        for child in body.winfo_children():
            child.destroy()
        labels = {"run": "Đang chạy", "changed": "Đổi giao diện", "ok": "Giữ nguyên"}
        colors = {"run": "#7dd3fc", "changed": "#fbbf24", "ok": "#94a3b8"}
        rows = self.lanes.board_rows(12)
        if not rows:
            tk.Label(body, text="Chưa gõ số nào.", font=("Segoe UI", 10), bg="#16324f", fg="#64748b").pack(
                anchor="w", padx=4, pady=6
            )
        for item in rows:
            state = str(item.get("state") or "ok")
            if state not in labels:
                state = "ok"
            row = tk.Frame(body, bg="#16324f")
            row.pack(fill="x", pady=1)
            tk.Label(
                row,
                text=str(item.get("digits") or "—"),
                width=8,
                font=("Segoe UI", 12, "bold"),
                bg="#16324f",
                fg=colors[state],
            ).pack(side="left", padx=4)
            tk.Label(
                row,
                text=labels[state],
                font=("Segoe UI", 10, "bold"),
                bg="#16324f",
                fg=colors[state],
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=4)
        changed = self.lanes.changed_digits()
        if changed:
            self.changed_label.set("Đổi giao diện: " + " · ".join(changed))
        else:
            self.changed_label.set("Chưa có số đổi giao diện")

    def _persist_lanes(self, *, silent: bool) -> None:
        self._sync_lanes()
        if not self.lanes.prefix and not self.lanes.last_used and not self.lanes.flagged:
            if not silent:
                self.status.set("Hướng 2: nhập 5 ký tự cố định rồi lưu.")
            return
        sample = " ".join(self.lanes.peek() or self.lanes.last_used or ["1"])
        try:
            saved = self.store.save(
                "Hướng 2 — 1 tiến trình Enter",
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
        try:
            self.vpn.save(self._vpn_form())
            self.vpn.shutdown()
        except Exception:
            pass
        self.desktop.stop()
        self.root.destroy()

    def _vpn_form(self) -> dict[str, Any]:
        label = self.vpn_kind.get()
        kind = next((key for key, text in KIND_LABELS.items() if text == label), "free")
        on = bool(self.vpn_enabled.get())
        return {
            "enabled": on,
            "disconnect_on_exit": on,
            "kind": kind,
            "name": self.vpn_name.get(),
            "config": self.vpn_config.get(),
            "username": self.vpn_user.get(),
            "password": self.vpn_pass.get(),
            "connect_cmd": self.vpn_config.get() if kind == "command" else "",
            "disconnect_cmd": self.vpn_disconnect.get(),
            "connected": bool(self.vpn.profile.get("connected")),
            "last_ip": self.vpn.profile.get("last_ip") or "",
            "baseline_ip": self.vpn.profile.get("baseline_ip") or "",
            "last_source": self.vpn.profile.get("last_source") or "",
            "privacy_lock": on,
            "block_ipv6": on,
            "kill_switch": on,
        }

    def _fill_vpn(self) -> None:
        item = self.vpn.profile
        empty = (
            item["kind"] == "windows"
            and not item["name"]
            and not item["config"]
            and not item["enabled"]
            and not item["connected"]
        )
        self._filling_vpn = True
        try:
            self.vpn_kind.set(KIND_LABELS["free"] if empty else KIND_LABELS.get(item["kind"], KIND_LABELS["free"]))
            self.vpn_name.set(item["name"])
            shown = item["config"] or item["connect_cmd"]
            self.vpn_config.set(shown)
            self.vpn_user.set(item["username"])
            self.vpn_pass.set(item["password"])
            self.vpn_disconnect.set(item["disconnect_cmd"])
            on = bool(item["enabled"])
            self.vpn_enabled.set(on)
            self.vpn_exit.set(on)
            self.vpn_privacy.set(on)
            self.vpn_ipv6.set(on)
            self.vpn_label.set(status_label(item))
        finally:
            self._filling_vpn = False
        if empty:
            self._persist_vpn()

    def _persist_vpn(self) -> None:
        try:
            self.vpn.save(self._vpn_form())
        except OSError:
            return

    def _on_vpn_toggle(self) -> None:
        if self._filling_vpn or self._vpn_busy:
            return
        if self.vpn_enabled.get():
            if self.vpn_kind.get() == KIND_LABELS["windows"] and not self.vpn_name.get().strip() and not self.vpn_config.get().strip():
                self.vpn_kind.set(KIND_LABELS["free"])
            self.vpn.save(self._vpn_form())
            self.vpn_label.set("Đang bật VPN và mọi bước bảo mật…")
            self._vpn_busy = True
            threading.Thread(target=self._vpn_on_worker, daemon=True).start()
            return
        self.vpn_label.set("Đang tắt VPN…")
        self._vpn_busy = True
        threading.Thread(target=self._vpn_off_worker, daemon=True).start()

    def refresh_vpn_ip(self) -> None:
        self.vpn_label.set("Đang lấy IP công cộng…")
        threading.Thread(target=self._vpn_ip_worker, daemon=True).start()

    def _vpn_autostart(self) -> None:
        if not self.vpn.profile.get("enabled"):
            return
        self.vpn_label.set("Đang bật VPN và mọi bước bảo mật…")
        self._vpn_busy = True
        threading.Thread(target=self._vpn_on_worker, daemon=True).start()

    def _vpn_on_worker(self) -> None:
        result = self.vpn.turn_on()
        self.root.after(0, lambda r=result: self._vpn_finished(r, "nối"))

    def _vpn_off_worker(self) -> None:
        result = self.vpn.turn_off()
        self.root.after(0, lambda r=result: self._vpn_finished(r, "ngắt"))

    def _vpn_ip_worker(self) -> None:
        ip = self.vpn.refresh_ip()

        def done() -> None:
            self._fill_vpn()
            self.vpn_label.set(f"IP công cộng: {ip}" if ip else "Không lấy được IP công cộng.")

        self.root.after(0, done)

    def _vpn_finished(self, result: dict[str, Any], action: str) -> None:
        self._vpn_busy = False
        self._fill_vpn()
        if result.get("ok"):
            ip = result.get("ip") or self.vpn.profile.get("last_ip") or ""
            extra = f" · IP {ip}" if ip else ""
            self.vpn_label.set(f"Đã {action} VPN{extra}.")
            return
        self.vpn_label.set(result.get("error") or f"Không {action} được VPN.")

    def _ensure_vpn(self) -> bool:
        self._persist_vpn()
        result = self.vpn.guard()
        self.root.after(0, self._fill_vpn)
        if result.get("ok"):
            ip = result.get("ip") or self.vpn.profile.get("last_ip") or ""
            if ip:
                self._set_status(f"VPN đã nối · IP {ip}.")
            return True
        self._set_status(result.get("error") or "Chế độ ẩn IP: chưa nối được — dừng, không chạy trên địa chỉ thật.")
        return False


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


def _startup_status() -> str:
    stamp = consume_just_updated()
    if stamp and stamp.get("build"):
        return f"Đã tự cập nhật lên bản {stamp['build']}. Ctrl+F7 bắt đầu, chọn ô, mỗi số Enter một lần."
    return f"Ctrl+F7 bắt đầu · chọn ô · gõ + Enter +1. Bản {PC_MACRO_BUILD}."


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
