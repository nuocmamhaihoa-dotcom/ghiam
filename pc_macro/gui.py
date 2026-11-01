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
from pc_macro.screen import RECOVER_CLICKS, capture_grid, normalize_threshold, sanitize_clicks, should_recover
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
        self.root.geometry("420x760")
        self.root.minsize(380, 680)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value="Nhập 5 ký tự cố định, số bắt đầu / kết thúc, rồi ghi 4 nút nếu cần.")
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

        ttk.Label(self.root, textvariable=self.recover_label, wraplength=390).pack(anchor="w", padx=12, pady=(6, 2))
        ttk.Label(self.root, text="Số lúc giao diện đổi ≥ ngưỡng — F9 cũng lưu số vừa Enter.").pack(anchor="w", padx=12, pady=(8, 2))
        self.flag_box = tk.Listbox(self.root, height=5, font=("Segoe UI", 12))
        self.flag_box.pack(fill="both", expand=True, padx=12, pady=4)
        ttk.Label(self.root, text="F8 dừng. Sau Enter máy đo % đổi màn. Đủ ngưỡng: lưu số, bấm 4 nút, rồi gõ số tiếp.").pack(anchor="w", padx=12, pady=(0, 8))

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
        self.status.set("Bấm trái nút 1/4 trên màn hình (không bấm cửa sổ này).")
        self._refresh_recover()

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
        self.status.set("Chuẩn bị… chuyển sang ô cần nhập.")
        self._play_thread = threading.Thread(target=self._run_play, args=(delay,), daemon=True)
        self._play_thread.start()

    def stop_all(self) -> None:
        if self.player is not None:
            self.player.cancel()
        if self._record_recover:
            self._finish_recover(keep=False)
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
            if self._force_recover:
                return self._consume_recover("F9")
            changed, percent = should_recover(self._before_grid, capture_grid(), self.queue.change_threshold)
            if not changed:
                self._set_status(f"Đã Enter {self.queue.last_used}. Đổi màn {percent:.0f}% — tiếp tục.")
                return []
            return self._consume_recover(f"{percent:.0f}%")

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
            if self.recorder.on:
                recorded = self.recorder.stop()
                self.root.after(0, lambda ev=recorded: self._after_record(ev))
            return
        if self._record_recover:
            if raw.get("kind") == "click" and raw.get("action") == "up":
                self.root.after(0, lambda ev=dict(raw): self._add_recover_click(ev))
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
        for event in events:
            if event.get("kind") == "click":
                click = {"kind": "click", "x": event.get("x"), "y": event.get("y"), "button": event.get("button") or "left", "wait": 80}
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

    def _add_recover_click(self, raw: dict[str, Any]) -> None:
        if not self._record_recover:
            return
        try:
            x = float(raw.get("x"))
            y = float(raw.get("y"))
        except (TypeError, ValueError):
            return
        box = self._window_box()
        if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return
        clicks = sanitize_clicks([*self._recover_draft, {"x": x, "y": y}])
        if len(clicks) == len(self._recover_draft):
            return
        self._recover_draft = clicks
        if len(self._recover_draft) >= RECOVER_CLICKS:
            self._finish_recover(keep=True)
            return
        self.status.set(f"Đã ghi {len(self._recover_draft)}/4 nút. Bấm nút trái tiếp theo.")
        self._refresh_recover()

    def _finish_recover(self, *, keep: bool) -> None:
        self._record_recover = False
        if keep and len(self._recover_draft) >= RECOVER_CLICKS:
            self.queue.recover_clicks = sanitize_clicks(self._recover_draft)
            self._persist(silent=True)
            self.status.set("Đã nhớ 4 nút. Sau Enter, nếu màn đổi đủ ngưỡng máy sẽ bấm 4 nút rồi gõ số tiếp.")
        elif keep:
            self.status.set("Chưa đủ 4 nút. Bấm Ghi 4 nút rồi chỉ đúng 4 vị trí.")
        else:
            self.status.set("Đã hủy ghi 4 nút.")
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
        self._set_status(f"Đã lưu {value} ({reason}). Đang bấm 4 nút rồi gõ số tiếp.")
        return self._recover_events()

    def _recover_events(self) -> list[dict[str, Any]]:
        steps = list(self.queue.recover_clicks)
        if self.click:
            steps.append(self.click)
        _script, events = compile_script(steps)
        return events

    def _refresh_recover(self) -> None:
        clicks = self._recover_draft if self._record_recover else self.queue.recover_clicks
        if not clicks:
            self.recover_label.set("4 nút: chưa ghi — bấm Ghi 4 nút rồi chỉ 4 vị trí.")
            return
        points = " · ".join(f"{item['x']},{item['y']}" for item in clicks)
        self.recover_label.set(f"4 nút ({len(clicks)}/4): {points}")

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
            recover_clicks=list(self.queue.recover_clicks),
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
        self._refresh_recover()

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
        return self.recorder.on or self._playing() or self._record_recover

    def _close(self) -> None:
        self.stop_all()
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
