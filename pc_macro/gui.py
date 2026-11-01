"""Simple window: type a number, Enter, add 1, save the number if the screen changes."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.hub import HubError, configured, describe, pull, push
from pc_macro.numbers import NumberQueue, apply_turn, digit_spans, parse_number_list
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
        self.root.geometry("400x620")
        self.root.minsize(360, 560)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value="Dán số. Bấm vào ô cần nhập trên máy. Bắt đầu: gõ số, Enter, cộng 1.")
        self.current_label = tk.StringVar(value="Số sẽ gõ: —")
        self.delay = tk.StringVar(value="2")
        self.step = tk.StringVar(value="1")
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.click: dict[str, Any] | None = None
        self.current_slug = "day-so-enter"
        self.queue = NumberQueue(bump=True, step=1, span=0)
        self.desktop = Desktop(self._on_desktop, _now_ms)
        self._play_thread: threading.Thread | None = None
        self._build()
        self._load_or_empty()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def run(self) -> None:
        self.desktop.start()
        self.root.mainloop()

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 5}
        ttk.Label(self.root, textvariable=self.status, wraplength=370).pack(anchor="w", **pad)

        ttk.Label(self.root, text="Dãy số — mỗi dòng một số. Máy gõ dòng đang dùng vào ô đang mở.").pack(anchor="w", padx=12)
        self.number_text = tk.Text(self.root, height=7, wrap="word", font=("Segoe UI", 14))
        self.number_text.pack(fill="x", padx=12, pady=4)
        self.number_text.bind("<FocusOut>", lambda _event: self._sync_queue())

        ttk.Label(self.root, textvariable=self.current_label, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=12, pady=6)

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts, textvariable=self.delay, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(opts, text="Sau Enter, cộng").pack(side="left")
        ttk.Entry(opts, textvariable=self.step, width=5).pack(side="left", padx=6)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Bắt đầu", command=self.start_play).pack(side="left", padx=2)
        ttk.Button(buttons, text="Dừng", command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(buttons, text="Giao diện đổi", command=self.flag_change).pack(side="left", padx=2)

        extra = ttk.Frame(self.root)
        extra.pack(fill="x", **pad)
        ttk.Button(extra, text="Ghi chỗ nhập", command=self.start_record).pack(side="left", padx=2)
        ttk.Button(extra, text="Lưu", command=self.save_current).pack(side="left", padx=2)
        ttk.Button(extra, text="Lên VPS", command=self.push_hub).pack(side="left", padx=2)
        ttk.Button(extra, text="Lấy VPS", command=self.pull_hub).pack(side="left", padx=2)

        ttk.Label(self.root, text="Số lúc giao diện đổi — F9 cũng lưu số đang dùng.").pack(anchor="w", padx=12, pady=(8, 2))
        self.flag_box = tk.Listbox(self.root, height=6, font=("Segoe UI", 12))
        self.flag_box.pack(fill="both", expand=True, padx=12, pady=4)
        ttk.Label(self.root, text="F8 dừng. F9 lưu số khi thấy màn hình đổi.").pack(anchor="w", padx=12, pady=(0, 8))

    def start_record(self) -> None:
        if self.recorder.on or self._playing():
            return
        self.recorder.start(_now_ms())
        self.status.set("Bấm một lần vào ô cần nhập trên máy, rồi Dừng hoặc F8.")

    def start_play(self) -> None:
        if self.recorder.on:
            self.status.set("Dừng ghi chỗ nhập rồi hãy bắt đầu.")
            return
        if self._playing():
            return
        self._sync_queue()
        if not self.queue.items:
            self.status.set("Dán ít nhất một số vào ô dãy số.")
            return
        try:
            delay = float(self.delay.get().strip() or "0")
        except ValueError:
            self.status.set("Chờ phải là số giây.")
            return
        if delay < 0:
            self.status.set("Chờ không được âm.")
            return
        sample = self.queue.peek()
        self.script, events = compile_script(self._job_steps(sample))
        self.spans = digit_spans(events)
        self.queue.span = 0 if self.spans else None
        self.player = Player(self.desktop.send, time.sleep)
        self.status.set("Chuẩn bị… chuyển sang ô cần nhập.")
        self._play_thread = threading.Thread(target=self._run_play, args=(events, delay), daemon=True)
        self._play_thread.start()

    def stop_all(self) -> None:
        if self.player is not None:
            self.player.cancel()
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

    def _run_play(self, events: list[dict[str, Any]], delay: float) -> None:
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
            batch, value = apply_turn(events, self.spans, self.queue)
            if value:
                self._set_status(f"Đang gõ {value} rồi Enter.")
            self.root.after(0, self._refresh_number_view)
            self.root.after(0, lambda: self._persist(silent=True))
            return batch

        result = self.player.play(events, times=0, prepare=prepare)
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
            if self.recorder.on:
                recorded = self.recorder.stop()
                self.root.after(0, lambda ev=recorded: self._after_record(ev))
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

    def _sync_queue(self) -> None:
        items = parse_number_list(self.number_text.get("1.0", tk.END))
        try:
            step = int(self.step.get().strip() or "1")
        except ValueError:
            step = 1
            self.step.set("1")
        flagged = list(self.queue.flagged)
        last_used = self.queue.last_used
        self.queue = NumberQueue(items, step=step, bump=True, span=0, flagged=flagged, last_used=last_used)
        if self.queue.items:
            self.queue.index = min(self.queue.index, len(self.queue.items) - 1)
        self._refresh_current()

    def _refresh_number_view(self) -> None:
        self._write_numbers(self.queue.items)
        self.step.set(str(self.queue.step))
        self._refresh_current()
        self._refresh_flags()

    def _refresh_current(self) -> None:
        nxt = self.queue.peek()
        last = self.queue.last_used
        if nxt and last:
            self.current_label.set(f"Số sẽ gõ: {nxt}   (vừa xong: {last})")
        elif nxt:
            self.current_label.set(f"Số sẽ gõ: {nxt}")
        else:
            self.current_label.set("Số sẽ gõ: —")

    def _refresh_flags(self) -> None:
        self.flag_box.delete(0, tk.END)
        for value in self.queue.flagged:
            self.flag_box.insert(tk.END, value)

    def _write_numbers(self, items: list[str]) -> None:
        text = "\n".join(items)
        current = self.number_text.get("1.0", "end-1c")
        if current == text:
            return
        self.number_text.delete("1.0", tk.END)
        self.number_text.insert("1.0", text)

    def _load_or_empty(self) -> None:
        item = self.store.get(self.current_slug)
        if item is None:
            for row in self.store.list():
                item = row
                self.current_slug = str(row.get("slug") or self.current_slug)
                break
        if item:
            self.queue = NumberQueue.from_dict(item.get("numbers"))
            self.queue.bump = True
            if self.queue.span is None:
                self.queue.span = 0
            self.click = _click_from_steps(item.get("steps") or [])
        self._refresh_number_view()
        if self.queue.items:
            self.status.set("Đã mở dãy số đã lưu. Bấm vào ô cần nhập, rồi Bắt đầu.")

    def _persist(self, *, silent: bool) -> None:
        self._sync_queue()
        if not self.queue.items and not self.queue.flagged and not self.queue.last_used:
            if not silent:
                self.status.set("Chưa có số để lưu.")
            return
        sample = self.queue.peek() or self.queue.last_used or "1"
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
            self.status.set(f"Đã lưu dãy số. Số tiếp theo: {self.queue.peek() or '—'}.")

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
        return self.recorder.on or self._playing()

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
