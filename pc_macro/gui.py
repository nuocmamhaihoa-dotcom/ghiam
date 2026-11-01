"""Simple window: type a number, Enter, add 1, save the number if the screen changes."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.hub import HubError, configured, describe, pull, push
from pc_macro.numbers import NumberQueue, make_code, normalize_digits, normalize_prefix
from pc_macro.overlay import ClickOverlay, show_click_overlay
from pc_macro.painter import PaintPicker, show_paint_picker
from pc_macro.screen import (
    RECOVER_CLICKS,
    append_screen_click,
    apply_click_names,
    capture_grid,
    click_order_cards,
    current_screen_size,
    normalize_click_name,
    normalize_threshold,
    play_named_clicks,
    recover_after_enter,
    sanitize_clicks,
    scale_clicks,
    start_both_scripts_status,
)
from pc_macro.script import compile_script, number_enter_steps
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
        self.root.geometry("430x1080")
        self.root.minsize(400, 900)
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
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.click: dict[str, Any] | None = None
        self.current_slug = "day-so-enter"
        self.queue = NumberQueue(range_mode=True, prefix="", start="00001", end="00010", step=1)
        self.desktop = Desktop(self._on_desktop, _now_ms)
        self._play_thread: threading.Thread | None = None
        self._record_recover = False
        self._recover_draft: list[dict[str, Any]] = []
        self._force_recover = False
        self._before_grid = None
        self._capture_warned = False
        self._overlay: ClickOverlay | None = None
        self._painter: PaintPicker | None = None
        self._build()
        self._load_or_empty()
        self.prefix.trace_add("write", self._on_fields)
        self.start.trace_add("write", self._on_fields)
        self.end.trace_add("write", self._on_fields)
        self.step.trace_add("write", self._on_fields)
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
        if self._record_recover or self._overlay is not None:
            self._finish_recover(keep=False)
        if self._painter is not None:
            self._close_painter()
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

        result = self.player.play([], times=0, prepare=prepare, after=after)
        self.root.after(0, self._refresh_number_view)
        self.root.after(0, lambda: self._persist(silent=True))
        if result["stopped"]:
            self._set_status(f"Đã dừng sau {result['loops']} lần. Số tiếp theo: {self.queue.peek() or '—'}.")
        else:
            self._set_status(f"Đã xong {result['loops']} lần.")
        self.player = None

    def _on_desktop(self, now_ms: int, raw: dict[str, Any]) -> None:
        key = str(raw.get("key") or "").lower()
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f9":
            self.root.after(0, self.flag_change)
            return
        if raw.get("kind") == "key" and raw.get("action") == "down" and key == "f8":
            if self.player is not None:
                self.player.cancel()
            if self._record_recover:
                self.root.after(0, lambda: self._finish_recover(keep=False))
            if self._painter is not None:
                self.root.after(0, self._paint_cancel)
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
        self._recover_draft = sanitize_clicks(clicks)
        self._finish_recover(keep=True)

    def _overlay_cancel(self) -> None:
        self._overlay = None
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

        results = play_named_clicks(clicks, self.desktop.send, time.sleep, cancelled=cancelled)
        if self.click and not cancelled():
            extra = scale_clicks([self.click], sw, sh)
            if extra:
                play_named_clicks(extra, self.desktop.send, time.sleep, cancelled=cancelled, retries=0)
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
        return self._play_thread is not None and self._play_thread.is_alive()

    def _busy(self) -> bool:
        return self.recorder.on or self._playing() or self._record_recover or self._painter is not None

    def _close(self) -> None:
        self.stop_all()
        self._close_overlay()
        self._close_painter()
        self._persist(silent=True)
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
