"""Small always-on-top window: Ghi, Dừng, Làm lại, dãy số tăng dần."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.events import default_title, script_lines
from pc_macro.numbers import NumberQueue, apply_turn, digit_spans, parse_number_list, pick_span
from pc_macro.session import Player, Recorder, ignore_window
from pc_macro.store import MacroStore

_NONE = "Không thay số"


def main() -> None:
    if not available():
        print(install_hint())
        raise SystemExit(1)
    App().run()


class App:
    def __init__(self) -> None:
        self.store = MacroStore()
        self.root = tk.Tk()
        self.root.title("Ghi nhớ thao tác")
        self.root.geometry("400x700")
        self.root.minsize(360, 580)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value="Chưa ghi. Bấm Ghi, làm thao tác trên máy, rồi Dừng.")
        self.name = tk.StringVar(value="")
        self.times = tk.StringVar(value="1")
        self.delay = tk.StringVar(value="2")
        self.span_choice = tk.StringVar(value=_NONE)
        self.bump = tk.BooleanVar(value=True)
        self.step = tk.StringVar(value="1")
        self.next_number = tk.StringVar(value="Chưa có số tiếp theo.")
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.current: list[dict[str, Any]] = []
        self.current_slug = ""
        self.spans: list[dict[str, Any]] = []
        self.queue = NumberQueue(bump=True, step=1)
        self.desktop = Desktop(self._on_desktop, _now_ms)
        self._play_thread: threading.Thread | None = None
        self._build()
        self._refresh_list()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def run(self) -> None:
        self.desktop.start()
        self.root.mainloop()

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        ttk.Label(self.root, textvariable=self.status, wraplength=370).pack(anchor="w", **pad)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Ghi", command=self.start_record).pack(side="left", padx=2)
        ttk.Button(buttons, text="Dừng", command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(buttons, text="Làm lại", command=self.start_play).pack(side="left", padx=2)

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Lặp").pack(side="left")
        ttk.Entry(opts, textvariable=self.times, width=5).pack(side="left", padx=(4, 12))
        ttk.Label(opts, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts, textvariable=self.delay, width=5).pack(side="left", padx=4)

        save = ttk.Frame(self.root)
        save.pack(fill="x", **pad)
        ttk.Entry(save, textvariable=self.name).pack(side="left", fill="x", expand=True)
        ttk.Button(save, text="Lưu", command=self.save_current).pack(side="left", padx=4)

        numbers = ttk.LabelFrame(self.root, text="Dãy số")
        numbers.pack(fill="both", padx=10, pady=6)
        ttk.Label(
            numbers,
            text="Dán mỗi dòng một số. Máy tự chọn dãy số dài nhất trong bản ghi — đổi ở ô dưới nếu sai.",
            wraplength=360,
        ).pack(anchor="w", padx=8, pady=(6, 2))
        self.number_text = tk.Text(numbers, height=6, wrap="word")
        self.number_text.pack(fill="both", expand=True, padx=8, pady=4)
        self.number_text.bind("<FocusOut>", lambda _event: self._sync_queue())

        pick = ttk.Frame(numbers)
        pick.pack(fill="x", padx=8, pady=2)
        ttk.Label(pick, text="Số sẽ thay").pack(side="left")
        self.span_box = ttk.Combobox(
            pick,
            textvariable=self.span_choice,
            state="readonly",
            width=22,
            values=(_NONE,),
        )
        self.span_box.pack(side="left", padx=6, fill="x", expand=True)
        self.span_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_queue())

        bump = ttk.Frame(numbers)
        bump.pack(fill="x", padx=8, pady=2)
        ttk.Checkbutton(
            bump,
            text="Sau mỗi lần dùng, cộng",
            variable=self.bump,
            command=self._sync_queue,
        ).pack(side="left")
        ttk.Entry(bump, textvariable=self.step, width=5).pack(side="left", padx=4)
        ttk.Label(numbers, textvariable=self.next_number).pack(anchor="w", padx=8, pady=(2, 8))

        ttk.Label(self.root, text="Bản đã lưu — bấm để chọn.").pack(anchor="w", **pad)
        self.listbox = tk.Listbox(self.root, height=4)
        self.listbox.pack(fill="x", padx=10, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._pick)

        row = ttk.Frame(self.root)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Xoá bản đang chọn", command=self.delete_selected).pack(side="left")
        ttk.Label(self.root, text="F8 dừng ghi / làm lại. Lặp 0 = làm mãi.").pack(anchor="w", padx=10)

        self.steps = tk.Text(self.root, height=6, wrap="word", state="disabled")
        self.steps.pack(fill="both", expand=True, padx=10, pady=(4, 10))

    def start_record(self) -> None:
        if self.recorder.on or self._playing():
            return
        self.current = []
        self.current_slug = ""
        self.recorder.start(_now_ms())
        self.status.set("Đang ghi. Gõ dãy số mẫu nếu muốn lần sau thay số. Dừng hoặc F8 khi xong.")
        self._set_steps([])

    def stop_all(self) -> None:
        if self.player is not None:
            self.player.cancel()
        if self.recorder.on:
            self.current = self.recorder.stop()
            self._after_record()

    def start_play(self) -> None:
        if self.recorder.on or self._playing():
            return
        events = self.current
        if not events:
            self.status.set("Chưa có thao tác. Bấm Ghi rồi làm trên máy trước.")
            return
        try:
            times = int(self.times.get().strip() or "1")
            delay = float(self.delay.get().strip() or "0")
        except ValueError:
            self.status.set("Lần lặp và chờ phải là số.")
            return
        if times < 0 or delay < 0:
            self.status.set("Lần lặp và chờ không được âm.")
            return
        self._sync_queue()
        if self.queue.span is not None and not self.spans:
            self.status.set("Bản ghi chưa có dãy số. Ghi lại lúc đang gõ số, rồi chọn số sẽ thay.")
            return
        self.player = Player(self.desktop.send, time.sleep)
        self.status.set("Chuẩn bị làm lại… chuyển sang cửa sổ cần thao tác.")
        self._play_thread = threading.Thread(
            target=self._run_play,
            args=(events, times, delay),
            daemon=True,
        )
        self._play_thread.start()

    def save_current(self) -> None:
        if self.recorder.on or not self.current:
            self.status.set("Ghi xong một bản rồi hãy lưu.")
            return
        self._sync_queue()
        try:
            saved = self.store.save(
                self.name.get(),
                self.current,
                slug=self.current_slug or None,
                numbers=self.queue.to_dict(),
            )
        except ValueError:
            self.status.set("Không có thao tác để lưu.")
            return
        self.current_slug = str(saved["slug"])
        self.name.set(saved["name"])
        self.status.set(f"Đã lưu «{saved['name']}».")
        self._refresh_list()

    def delete_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        if not messagebox.askyesno("Xoá bản ghi", f"Xoá «{item['name']}»?"):
            return
        self.store.delete(item["slug"])
        if self.current_slug == item["slug"]:
            self.current = []
            self.current_slug = ""
            self.name.set("")
            self._set_steps([])
            self._set_spans([])
        self.status.set(f"Đã xoá «{item['name']}».")
        self._refresh_list()

    def _run_play(self, events: list[dict[str, Any]], times: int, delay: float) -> None:
        remaining = delay
        while remaining > 0 and self.player is not None and not self.player.cancelled:
            self._set_status(f"Bắt đầu làm lại sau {int(remaining)} giây…")
            time.sleep(min(1.0, remaining))
            remaining -= 1
        if self.player is None or self.player.cancelled:
            self._set_status("Đã dừng.")
            return
        used: list[str] = []

        def prepare(_loop: int) -> list[dict[str, Any]]:
            batch, value = apply_turn(events, self.spans, self.queue)
            if value:
                used.append(value)
            self.root.after(0, self._refresh_number_view)
            return batch

        self._set_status("Đang làm lại…")
        result = self.player.play(events, times=times, prepare=prepare)
        self.root.after(0, self._refresh_number_view)
        if result["stopped"]:
            note = f"Đã dừng sau {result['loops']} lần."
        else:
            note = f"Đã làm xong {result['loops']} lần."
        if used:
            note += " Số vừa dùng: " + ", ".join(used[-8:]) + "."
        self._set_status(note)
        self.player = None

    def _on_desktop(self, now_ms: int, raw: dict[str, Any]) -> None:
        key = str(raw.get("key") or "").lower()
        if raw.get("kind") == "key" and key in {"f8"} and raw.get("action") == "down":
            self.root.after(0, self.stop_all)
            if self.recorder.on:
                self.recorder.add(now_ms, raw)
            return
        if not self.recorder.on:
            return
        keep = self.recorder.add(now_ms, raw)
        count = len(self.recorder.events)
        self.root.after(0, lambda: self.status.set(f"Đang ghi {count} thao tác…"))
        if not keep:
            self.root.after(0, self._after_record)

    def _after_record(self) -> None:
        events = self.current or self.recorder.events
        self.current = events
        if not events:
            self.status.set("Không có phím hay bấm để lưu. Bấm Ghi rồi làm trên máy.")
            self._set_steps([])
            self._set_spans([])
            return
        if not self.name.get().strip():
            self.name.set(default_title(events))
        self._set_spans(digit_spans(events), prefer_longest=True)
        self.status.set(f"Đã ghi {len(events)} thao tác. Chọn số sẽ thay nếu cần, rồi Làm lại hoặc Lưu.")
        self._set_steps(script_lines(events))

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for item in self.store.list():
            self.listbox.insert(tk.END, f"{item['name']}  ({item['event_count']})")

    def _pick(self, _event: object | None = None) -> None:
        item = self._selected()
        if item is None:
            return
        self.current = item["events"]
        self.current_slug = str(item.get("slug") or "")
        self.name.set(item["name"])
        self.queue = NumberQueue.from_dict(item.get("numbers"))
        self._load_queue_into_form()
        self._set_spans(digit_spans(item["events"]), prefer_longest=self.queue.span is None)
        if self.queue.span is not None:
            self._select_span(self.queue.span)
        self.status.set(f"Đã chọn «{item['name']}». Bấm Làm lại.")
        self._set_steps(script_lines(item["events"]))

    def _selected(self) -> dict[str, Any] | None:
        choice = self.listbox.curselection()
        if not choice:
            return None
        items = self.store.list()
        index = int(choice[0])
        if index < 0 or index >= len(items):
            return None
        return items[index]

    def _set_spans(self, spans: list[dict[str, Any]], prefer_longest: bool = False) -> None:
        self.spans = spans
        labels = [_NONE, *[_span_label(span, index) for index, span in enumerate(spans)]]
        self.span_box["values"] = labels
        if prefer_longest:
            chosen = pick_span(spans)
            self._select_span(chosen)
        elif self.span_choice.get() not in labels:
            self._select_span(self.queue.span if spans else None)
        self._sync_queue()

    def _select_span(self, index: int | None) -> None:
        if index is None or index < 0 or index >= len(self.spans):
            self.span_choice.set(_NONE)
            return
        self.span_choice.set(_span_label(self.spans[index], index))

    def _sync_queue(self) -> None:
        items = parse_number_list(self.number_text.get("1.0", tk.END))
        try:
            step = int(self.step.get().strip() or "1")
        except ValueError:
            step = 1
            self.step.set("1")
        span = None
        label = self.span_choice.get()
        for index, item in enumerate(self.spans):
            if label == _span_label(item, index):
                span = index
                break
        if span is not None and not items:
            items = [str(self.spans[span]["value"])]
            self._write_numbers(items)
        self.queue.items = items
        if self.queue.items:
            self.queue.index = min(self.queue.index, len(self.queue.items) - 1)
        else:
            self.queue.index = 0
        self.queue.step = step
        self.queue.bump = bool(self.bump.get())
        self.queue.span = span
        self._refresh_next_label()

    def _load_queue_into_form(self) -> None:
        self._write_numbers(self.queue.items)
        self.step.set(str(self.queue.step))
        self.bump.set(bool(self.queue.bump))
        self._refresh_next_label()

    def _write_numbers(self, items: list[str]) -> None:
        current = self.number_text.get("1.0", "end-1c")
        text = "\n".join(items)
        if current == text:
            return
        self.number_text.delete("1.0", tk.END)
        self.number_text.insert("1.0", text)

    def _refresh_number_view(self) -> None:
        self._write_numbers(self.queue.items)
        self._refresh_next_label()

    def _refresh_next_label(self) -> None:
        if self.queue.span is None:
            self.next_number.set("Không thay số — làm lại đúng bản ghi.")
            return
        fallback = str(self.spans[self.queue.span]["value"]) if self.spans and self.queue.span < len(self.spans) else ""
        nxt = self.queue.peek(fallback)
        if not nxt:
            self.next_number.set("Chưa có số. Dán danh sách hoặc ghi lúc đang gõ số.")
            return
        self.next_number.set(f"Số tiếp theo: {nxt}")

    def _set_steps(self, lines: list[str]) -> None:
        self.steps.configure(state="normal")
        self.steps.delete("1.0", tk.END)
        self.steps.insert("1.0", "\n".join(lines))
        self.steps.configure(state="disabled")

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

    def _close(self) -> None:
        self.stop_all()
        self.desktop.stop()
        self.root.destroy()


def _span_label(span: dict[str, Any], index: int) -> str:
    return f"{index + 1}. {span.get('value')}"


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
