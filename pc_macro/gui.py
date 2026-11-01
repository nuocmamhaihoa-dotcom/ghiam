"""Small always-on-top window: Ghi, Dừng, Làm lại, dãy số, kịch bản từng bước."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.events import default_title
from pc_macro.numbers import NumberQueue, apply_turn, digit_spans, parse_number_list, pick_span
from pc_macro.script import (
    compile_script,
    delete_step,
    events_to_steps,
    insert_steps,
    merge_recording,
    move_step,
    new_step,
    replace_step,
    step_caption,
)
from pc_macro.session import Player, Recorder, ignore_window
from pc_macro.store import MacroStore

_NONE = "Không thay số"
_KIND_LABELS = {
    "type": "Gõ chữ",
    "number": "Gõ số",
    "key": "Phím",
    "click": "Bấm chuột",
    "scroll": "Cuộn",
    "wait": "Chờ",
}
_LABEL_KINDS = {label: kind for kind, label in _KIND_LABELS.items()}
_BUTTON_LABELS = {"left": "trái", "right": "phải", "middle": "giữa"}
_LABEL_BUTTONS = {label: name for name, label in _BUTTON_LABELS.items()}


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
        self.root.geometry("440x820")
        self.root.minsize(380, 680)
        self.root.attributes("-topmost", True)
        self.status = tk.StringVar(value="Chưa ghi. Bấm Ghi, làm thao tác trên máy, rồi Dừng.")
        self.name = tk.StringVar(value="")
        self.times = tk.StringVar(value="1")
        self.delay = tk.StringVar(value="2")
        self.span_choice = tk.StringVar(value=_NONE)
        self.bump = tk.BooleanVar(value=True)
        self.step = tk.StringVar(value="1")
        self.next_number = tk.StringVar(value="Chưa có số tiếp theo.")
        self.edit_kind = tk.StringVar(value=_KIND_LABELS["type"])
        self.edit_value = tk.StringVar(value="")
        self.edit_key = tk.StringVar(value="")
        self.edit_x = tk.StringVar(value="200")
        self.edit_y = tk.StringVar(value="200")
        self.edit_button = tk.StringVar(value="trái")
        self.edit_dx = tk.StringVar(value="0")
        self.edit_dy = tk.StringVar(value="-1")
        self.edit_wait = tk.StringVar(value="0")
        self.recorder = Recorder(ignore_point=ignore_window(self._window_box))
        self.player: Player | None = None
        self.current: list[dict[str, Any]] = []
        self.script: list[dict[str, Any]] = []
        self.current_slug = ""
        self.appending = False
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
        ttk.Label(self.root, textvariable=self.status, wraplength=410).pack(anchor="w", **pad)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Ghi", command=self.start_record).pack(side="left", padx=2)
        ttk.Button(buttons, text="Dừng", command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(buttons, text="Làm lại", command=self.start_play).pack(side="left", padx=2)
        ttk.Button(buttons, text="Ghi thêm", command=self.start_append).pack(side="left", padx=2)

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
        numbers.pack(fill="x", padx=10, pady=6)
        ttk.Label(
            numbers,
            text="Dán mỗi dòng một số. Máy tự chọn dãy số dài nhất trong kịch bản — đổi ở ô dưới nếu sai.",
            wraplength=400,
        ).pack(anchor="w", padx=8, pady=(6, 2))
        self.number_text = tk.Text(numbers, height=4, wrap="word")
        self.number_text.pack(fill="x", padx=8, pady=4)
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

        ttk.Label(self.root, text="Bản đã lưu — nháy đúp hoặc bấm Mở. Chèn để ghép vào kịch bản đang sửa.").pack(
            anchor="w", **pad
        )
        self.listbox = tk.Listbox(self.root, height=3)
        self.listbox.pack(fill="x", padx=10, pady=4)
        self.listbox.bind("<Double-Button-1>", self._pick)

        row = ttk.Frame(self.root)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Mở", command=self._pick).pack(side="left")
        ttk.Button(row, text="Chèn vào kịch bản", command=self.insert_saved).pack(side="left", padx=6)
        ttk.Button(row, text="Xoá bản", command=self.delete_selected).pack(side="left")
        ttk.Label(self.root, text="F8 dừng ghi / làm lại. Lặp 0 = làm mãi.").pack(anchor="w", padx=10)

        script = ttk.LabelFrame(self.root, text="Kịch bản — từng bước, sửa lần lượt")
        script.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        ttk.Label(
            script,
            text="Ghi xong sẽ tách thành bước. Sửa / xếp lại / thêm, rồi Làm lại đúng kịch bản.",
            wraplength=400,
        ).pack(anchor="w", padx=8, pady=(6, 2))
        self.script_box = tk.Listbox(script, height=8)
        self.script_box.pack(fill="both", expand=True, padx=8, pady=4)
        self.script_box.bind("<<ListboxSelect>>", self._pick_step)

        actions = ttk.Frame(script)
        actions.pack(fill="x", padx=8, pady=2)
        ttk.Button(actions, text="Lên", command=self.move_up).pack(side="left", padx=2)
        ttk.Button(actions, text="Xuống", command=self.move_down).pack(side="left", padx=2)
        ttk.Button(actions, text="Sửa", command=self.apply_step).pack(side="left", padx=2)
        ttk.Button(actions, text="Xóa", command=self.remove_step).pack(side="left", padx=2)
        ttk.Button(actions, text="Thêm", command=self.add_step).pack(side="left", padx=2)

        form = ttk.Frame(script)
        form.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(form, text="Loại").grid(row=0, column=0, sticky="w", pady=2)
        self.kind_box = ttk.Combobox(
            form,
            textvariable=self.edit_kind,
            state="readonly",
            width=12,
            values=tuple(_KIND_LABELS.values()),
        )
        self.kind_box.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Chờ ms").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.edit_wait, width=7).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(form, text="Gõ / số").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.edit_value, width=18).grid(row=1, column=1, sticky="we", padx=4)
        ttk.Label(form, text="Phím").grid(row=1, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.edit_key, width=8).grid(row=1, column=3, sticky="w", padx=4)

        ttk.Label(form, text="X, Y").grid(row=2, column=0, sticky="w", pady=2)
        xy = ttk.Frame(form)
        xy.grid(row=2, column=1, sticky="w", padx=4)
        ttk.Entry(xy, textvariable=self.edit_x, width=6).pack(side="left")
        ttk.Entry(xy, textvariable=self.edit_y, width=6).pack(side="left", padx=4)
        ttk.Label(form, text="Nút").grid(row=2, column=2, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.edit_button,
            state="readonly",
            width=7,
            values=tuple(_BUTTON_LABELS.values()),
        ).grid(row=2, column=3, sticky="w", padx=4)

        ttk.Label(form, text="Cuộn dx, dy").grid(row=3, column=0, sticky="w", pady=2)
        delta = ttk.Frame(form)
        delta.grid(row=3, column=1, sticky="w", padx=4)
        ttk.Entry(delta, textvariable=self.edit_dx, width=6).pack(side="left")
        ttk.Entry(delta, textvariable=self.edit_dy, width=6).pack(side="left", padx=4)
        form.columnconfigure(1, weight=1)

    def start_record(self) -> None:
        if self.recorder.on or self._playing():
            return
        self.appending = False
        self.current = []
        self.script = []
        self.current_slug = ""
        self.recorder.start(_now_ms())
        self.status.set("Đang ghi. Làm lần lượt trên máy. Dừng hoặc F8 khi xong — máy tách thành bước.")
        self._refresh_script_box()

    def start_append(self) -> None:
        if self.recorder.on or self._playing():
            return
        if not self.script:
            self.start_record()
            return
        self.appending = True
        self.recorder.start(_now_ms())
        self.status.set("Đang ghi thêm vào kịch bản. Dừng hoặc F8 khi xong.")

    def stop_all(self) -> None:
        if self.player is not None:
            self.player.cancel()
        if not self.recorder.on:
            return
        recorded = self.recorder.stop()
        self._after_record(recorded)

    def start_play(self) -> None:
        if self.recorder.on or self._playing():
            return
        self._apply_script()
        events = self.current
        if not events:
            self.status.set("Chưa có bước. Bấm Ghi rồi làm trên máy, hoặc Thêm bước.")
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
            self.status.set("Kịch bản chưa có dãy số. Thêm bước Gõ số, hoặc ghi lúc đang gõ số.")
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
        if self.recorder.on:
            self.status.set("Dừng ghi rồi hãy lưu.")
            return
        self._apply_script()
        if not self.script:
            self.status.set("Chưa có bước để lưu.")
            return
        self._sync_queue()
        try:
            saved = self.store.save(
                self.name.get(),
                self.current,
                slug=self.current_slug or None,
                numbers=self.queue.to_dict(),
                steps=self.script,
            )
        except ValueError:
            self.status.set("Không có thao tác để lưu.")
            return
        self.current_slug = str(saved["slug"])
        self.name.set(saved["name"])
        self.status.set(f"Đã lưu kịch bản «{saved['name']}» — {saved.get('step_count') or len(self.script)} bước.")
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
            self.script = []
            self.current_slug = ""
            self.name.set("")
            self._refresh_script_box()
            self._set_spans([])
        self.status.set(f"Đã xoá «{item['name']}».")
        self._refresh_list()

    def insert_saved(self) -> None:
        if self._busy():
            return
        item = self._selected()
        if item is None:
            self.status.set("Chọn một bản đã lưu rồi bấm Chèn vào kịch bản.")
            return
        incoming = item.get("steps") or events_to_steps(item.get("events") or [])
        if not incoming:
            self.status.set("Bản đó chưa có bước để chèn.")
            return
        at = self._selected_script_index()
        point = len(self.script) if at is None else at + 1
        self.script = insert_steps(self.script, point, incoming)
        self._apply_script(select=min(point, len(self.script) - 1), prefer_longest=self.queue.span is None)
        self.status.set(f"Đã chèn {len(incoming)} bước từ «{item['name']}».")

    def move_up(self) -> None:
        self._move_selected(-1)

    def move_down(self) -> None:
        self._move_selected(1)

    def apply_step(self) -> None:
        if self._busy():
            return
        index = self._selected_script_index()
        if index is None:
            self.status.set("Chọn một bước, sửa ô bên dưới, rồi bấm Sửa.")
            return
        incoming = self._step_from_form()
        if incoming is None:
            self.status.set("Ô bước chưa đủ — kiểm tra loại, nội dung, tọa độ.")
            return
        self.script = replace_step(self.script, index, incoming)
        self._apply_script(select=index)
        self.status.set(f"Đã sửa bước {index + 1}: {step_caption(self.script[index])}.")

    def remove_step(self) -> None:
        if self._busy():
            return
        index = self._selected_script_index()
        if index is None:
            self.status.set("Chọn bước cần xóa.")
            return
        self.script = delete_step(self.script, index)
        nxt = min(index, len(self.script) - 1) if self.script else None
        self._apply_script(select=nxt, prefer_longest=self.queue.span is None)
        self.status.set("Đã xóa bước.")

    def add_step(self) -> None:
        if self._busy():
            return
        incoming = self._step_from_form()
        if incoming is None:
            incoming = new_step(_LABEL_KINDS.get(self.edit_kind.get()) or "wait")
        if incoming is None:
            self.status.set("Không tạo được bước. Chọn loại rồi điền ô.")
            return
        at = self._selected_script_index()
        point = len(self.script) if at is None else at + 1
        self.script = insert_steps(self.script, point, [incoming])
        self._apply_script(select=point)
        self.status.set(f"Đã thêm bước {point + 1}.")

    def _run_play(self, events: list[dict[str, Any]], times: int, delay: float) -> None:
        remaining = delay
        while remaining > 0 and self.player is not None and not self.player.cancelled:
            self._set_status(f"Bắt đầu làm lại sau {int(remaining)} giây…")
            time.sleep(min(1.0, remaining))
            remaining -= 1
        if self.player is None or self.player.cancelled:
            self._set_status("Đã dừng.")
            self.player = None
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
            if self.player is not None:
                self.player.cancel()
            if self.recorder.on:
                # Stop on this listener thread so keys after F8 are not kept.
                recorded = self.recorder.stop()
                self.root.after(0, lambda ev=recorded: self._after_record(ev))
            return
        if not self.recorder.on:
            return
        keep = self.recorder.add(now_ms, raw)
        count = len(self.recorder.events)
        self.root.after(0, lambda: self.status.set(f"Đang ghi {count} thao tác…"))
        if not keep:
            self.root.after(0, lambda ev=list(self.recorder.events): self._after_record(ev))

    def _after_record(self, recorded: list[dict[str, Any]] | None = None) -> None:
        events = recorded if recorded is not None else self.recorder.events
        appending = self.appending
        at = self._selected_script_index() if appending else None
        old_len = len(self.script)
        self.script = merge_recording(self.script, events, appending=appending, at=at)
        self.appending = False
        if not self.script:
            self.current = []
            self.status.set("Không có phím hay bấm để lưu. Bấm Ghi rồi làm trên máy.")
            self._refresh_script_box()
            self._set_spans([])
            return
        if appending:
            point = old_len if at is None else at + 1
            select = min(point, len(self.script) - 1)
        else:
            select = 0
        self._apply_script(select=select, prefer_longest=not (appending and self.queue.span is not None))
        if not self.name.get().strip() and self.current:
            self.name.set(default_title(self.current))
        self.status.set(f"Kịch bản {len(self.script)} bước. Sửa lần lượt, rồi Làm lại hoặc Lưu.")

    def _apply_script(self, select: int | None = None, prefer_longest: bool = False) -> None:
        self.script, self.current = compile_script(self.script)
        self._refresh_script_box(select)
        self._set_spans(digit_spans(self.current), prefer_longest=prefer_longest)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for item in self.store.list():
            count = item.get("step_count") or item.get("event_count")
            self.listbox.insert(tk.END, f"{item['name']}  ({count} bước)")

    def _pick(self, _event: object | None = None) -> None:
        item = self._selected()
        if item is None or self._busy():
            return
        self.script = list(item.get("steps") or events_to_steps(item["events"]))
        self.current_slug = str(item.get("slug") or "")
        self.name.set(item["name"])
        self.queue = NumberQueue.from_dict(item.get("numbers"))
        self._load_queue_into_form()
        self._apply_script(select=0 if self.script else None, prefer_longest=self.queue.span is None)
        if self.queue.span is not None:
            self._select_span(self.queue.span)
        self.status.set(f"Đã mở kịch bản «{item['name']}» — {len(self.script)} bước. Sửa rồi Làm lại.")

    def _pick_step(self, _event: object | None = None) -> None:
        index = self._selected_script_index()
        if index is None:
            return
        self._load_step_into_form(self.script[index])

    def _selected(self) -> dict[str, Any] | None:
        choice = self.listbox.curselection()
        if not choice:
            return None
        items = self.store.list()
        index = int(choice[0])
        if index < 0 or index >= len(items):
            return None
        return items[index]

    def _selected_script_index(self) -> int | None:
        choice = self.script_box.curselection()
        if not choice:
            return None
        index = int(choice[0])
        if index < 0 or index >= len(self.script):
            return None
        return index

    def _refresh_script_box(self, select: int | None = None) -> None:
        current = self._selected_script_index() if select is None else select
        self.script_box.delete(0, tk.END)
        for index, step in enumerate(self.script):
            self.script_box.insert(tk.END, f"{index + 1}. {step.get('caption') or step_caption(step)}")
        if current is not None and 0 <= current < len(self.script):
            self.script_box.selection_set(current)
            self.script_box.see(current)
            self._load_step_into_form(self.script[current])

    def _move_selected(self, delta: int) -> None:
        if self._busy():
            return
        index = self._selected_script_index()
        if index is None:
            self.status.set("Chọn bước cần xếp lại.")
            return
        dest = index + delta
        self.script = move_step(self.script, index, delta)
        if dest < 0 or dest >= len(self.script):
            self._refresh_script_box(index)
            return
        self._apply_script(select=dest)
        self.status.set(f"Đã chuyển bước lên vị trí {dest + 1}." if delta < 0 else f"Đã chuyển bước xuống vị trí {dest + 1}.")

    def _load_step_into_form(self, step: dict[str, Any]) -> None:
        kind = str(step.get("kind") or "type")
        self.edit_kind.set(_KIND_LABELS.get(kind, _KIND_LABELS["type"]))
        self.edit_value.set(str(step.get("value") or ""))
        self.edit_key.set(str(step.get("key") or ""))
        self.edit_x.set(str(step.get("x") if step.get("x") is not None else "200"))
        self.edit_y.set(str(step.get("y") if step.get("y") is not None else "200"))
        self.edit_button.set(_BUTTON_LABELS.get(str(step.get("button") or "left"), "trái"))
        self.edit_dx.set(str(step.get("dx") if step.get("dx") is not None else "0"))
        self.edit_dy.set(str(step.get("dy") if step.get("dy") is not None else "-1"))
        self.edit_wait.set(str(int(step.get("wait") or 0)))

    def _step_from_form(self) -> dict[str, Any] | None:
        kind = _LABEL_KINDS.get(self.edit_kind.get())
        if kind is None:
            return None
        wait = _as_int(self.edit_wait.get(), 0)
        if kind in {"type", "number"}:
            return new_step(kind, value=self.edit_value.get(), wait=wait)
        if kind == "key":
            return new_step(kind, key=self.edit_key.get(), wait=wait)
        if kind == "click":
            return new_step(
                kind,
                x=_as_int(self.edit_x.get(), None),
                y=_as_int(self.edit_y.get(), None),
                button=_LABEL_BUTTONS.get(self.edit_button.get(), "left"),
                wait=wait,
            )
        if kind == "scroll":
            return new_step(
                kind,
                x=_as_int(self.edit_x.get(), None),
                y=_as_int(self.edit_y.get(), None),
                dx=_as_int(self.edit_dx.get(), 0),
                dy=_as_int(self.edit_dy.get(), 0),
                wait=wait,
            )
        if kind == "wait":
            return new_step(kind, wait=wait)
        return None  # unknown kind — combobox only offers known labels

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
            self.next_number.set("Không thay số — làm lại đúng kịch bản.")
            return
        fallback = str(self.spans[self.queue.span]["value"]) if self.spans and self.queue.span < len(self.spans) else ""
        nxt = self.queue.peek(fallback)
        if not nxt:
            self.next_number.set("Chưa có số. Dán danh sách hoặc thêm bước Gõ số.")
            return
        self.next_number.set(f"Số tiếp theo: {nxt}")

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
        self.desktop.stop()
        self.root.destroy()


def _span_label(span: dict[str, Any], index: int) -> str:
    return f"{index + 1}. {span.get('value')}"


def _as_int(value: str, default: int | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
