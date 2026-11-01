"""Small always-on-top window: Ghi, Dừng, Làm lại."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.events import default_title, script_lines
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
        self.root.title("Ghi nhớ thao tác")
        self.root.geometry("380x520")
        self.root.minsize(340, 460)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value="Chưa ghi. Bấm Ghi, làm thao tác trên máy, rồi Dừng.")
        self.name = tk.StringVar(value="")
        self.times = tk.StringVar(value="1")
        self.delay = tk.StringVar(value="2")
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.current: list[dict[str, Any]] = []
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
        ttk.Label(self.root, textvariable=self.status, wraplength=350).pack(anchor="w", **pad)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        self.btn_record = ttk.Button(buttons, text="Ghi", command=self.start_record)
        self.btn_stop = ttk.Button(buttons, text="Dừng", command=self.stop_all)
        self.btn_play = ttk.Button(buttons, text="Làm lại", command=self.start_play)
        self.btn_record.pack(side="left", padx=2)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_play.pack(side="left", padx=2)

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Lặp").pack(side="left")
        ttk.Entry(opts, textvariable=self.times, width=5).pack(side="left", padx=(4, 12))
        ttk.Label(opts, text="Chờ (giây)").pack(side="left")
        ttk.Entry(opts, textvariable=self.delay, width=5).pack(side="left", padx=4)
        ttk.Label(self.root, text="0 lần lặp = làm mãi đến khi bấm Dừng hoặc F8.").pack(anchor="w", **pad)

        save = ttk.Frame(self.root)
        save.pack(fill="x", **pad)
        ttk.Entry(save, textvariable=self.name).pack(side="left", fill="x", expand=True)
        ttk.Button(save, text="Lưu", command=self.save_current).pack(side="left", padx=4)

        ttk.Label(self.root, text="Bản đã lưu — bấm để chọn, rồi Làm lại.").pack(anchor="w", **pad)
        self.listbox = tk.Listbox(self.root, height=8)
        self.listbox.pack(fill="both", expand=True, padx=10, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._pick)

        row = ttk.Frame(self.root)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Xoá bản đang chọn", command=self.delete_selected).pack(side="left")
        ttk.Label(self.root, text="F8 cũng dừng ghi hoặc dừng làm lại.").pack(anchor="w", padx=10, pady=(0, 8))

        self.steps = tk.Text(self.root, height=8, wrap="word", state="disabled")
        self.steps.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def start_record(self) -> None:
        if self.recorder.on or self._playing():
            return
        self.current = []
        self.recorder.start(_now_ms())
        self.status.set("Đang ghi. Làm thao tác trên máy. Bấm Dừng hoặc F8 khi xong.")
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
        try:
            saved = self.store.save(self.name.get(), self.current)
        except ValueError:
            self.status.set("Không có thao tác để lưu.")
            return
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
        if self.name.get() == item["name"]:
            self.current = []
            self.name.set("")
            self._set_steps([])
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
        self._set_status("Đang làm lại…")
        result = self.player.play(events, times=times)
        if result["stopped"]:
            self._set_status(f"Đã dừng sau {result['loops']} lần.")
        else:
            self._set_status(f"Đã làm xong {result['loops']} lần.")
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
            return
        if not self.name.get().strip():
            self.name.set(default_title(events))
        self.status.set(f"Đã ghi {len(events)} thao tác. Bấm Làm lại hoặc Lưu.")
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
        self.name.set(item["name"])
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


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
