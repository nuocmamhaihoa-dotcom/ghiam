"""Fullscreen marks so the user can point at four left-clicks in order."""

from __future__ import annotations

from typing import Any, Callable

import tkinter as tk

from pc_macro.screen import (
    RECOVER_CLICKS,
    append_screen_click,
    click_order_labels,
    click_slots,
    overlay_banner,
    sanitize_clicks,
    upsert_slot,
)

_FILL = "#0b6bcb"
_EDGE = "#ffffff"
_TEXT = "#ffffff"
_BG = "#101820"
DoneFn = Callable[[list[dict[str, Any]]], None]
CancelFn = Callable[[], None]


class ClickOverlay:
    """Cover the screen, catch four left clicks, then wait for Lưu."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        on_done: DoneFn,
        on_cancel: CancelFn,
        clicks: list[dict[str, Any]] | None = None,
        recording: bool = True,
        step: int | None = None,
    ) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._recording = recording
        self._awaiting_save = False
        self._step = step if step is not None and 0 <= int(step) < RECOVER_CLICKS else None
        self.screen_w = max(1, int(root.winfo_screenwidth()))
        self.screen_h = max(1, int(root.winfo_screenheight()))
        self.clicks = sanitize_clicks(clicks or [])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        self.win.configure(bg=_BG)
        try:
            self.win.attributes("-alpha", 0.46 if recording else 0.36)
        except tk.TclError:
            pass
        self.bar = tk.Frame(self.win, bg="#0b6bcb")
        self.bar.pack(side="bottom", fill="x")
        self.hint = tk.Label(self.bar, text="", fg=_TEXT, bg="#0b6bcb", font=("Segoe UI", 12, "bold"))
        self.hint.pack(side="left", padx=12, pady=10)
        self.btn_save = tk.Button(
            self.bar,
            text="Lưu vị trí" if self._step is not None else "Lưu 4 nút",
            command=self._save,
            font=("Segoe UI", 12, "bold"),
            bg="#ffd166",
            fg="#102030",
            activebackground="#ffe08a",
            relief="flat",
            padx=16,
            pady=8,
        )
        self.btn_redo = tk.Button(
            self.bar,
            text="Ghi lại",
            command=self._redo,
            font=("Segoe UI", 12, "bold"),
            bg="#e8edf2",
            fg="#17202a",
            relief="flat",
            padx=12,
            pady=8,
        )
        self.btn_cancel = tk.Button(
            self.bar,
            text="Hủy",
            command=self.cancel,
            font=("Segoe UI", 12, "bold"),
            bg="#c2410c",
            fg=_TEXT,
            relief="flat",
            padx=12,
            pady=8,
        )
        self.btn_cancel.pack(side="right", padx=8, pady=8)
        self.btn_redo.pack(side="right", padx=4, pady=8)
        self.btn_save.pack(side="right", padx=4, pady=8)
        self.canvas = tk.Canvas(self.win, bg=_BG, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<F8>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self._save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()
        if recording and self._step is None and len(self.clicks) >= RECOVER_CLICKS:
            self._recording = False
            self._awaiting_save = True
        self._sync_bar()
        self._redraw()
        if not recording:
            self.btn_save.pack_forget()
            self.btn_redo.pack_forget()
            self.hint.configure(text="Xem 4 nút đã lưu. Bấm màn hoặc đợi để đóng.")
            self.win.after(4500, self.close)

    def _on_click(self, event: tk.Event) -> None:
        if not self._recording:
            if not self._awaiting_save:
                self.close()
            return
        x = int(event.x_root)
        y = int(event.y_root)
        if x < 0 or y < 0:
            return
        if self._step is not None:
            self.clicks = upsert_slot(self.clicks, self._step, x, y, self.screen_w, self.screen_h, via="point")
            self._recording = False
            self._awaiting_save = True
        else:
            self.clicks = append_screen_click(self.clicks, x, y, self.screen_w, self.screen_h)
            if len(self.clicks) >= RECOVER_CLICKS:
                self._recording = False
                self._awaiting_save = True
        self._sync_bar()
        self._redraw()

    def _save(self) -> None:
        if not self._awaiting_save:
            return
        if self._step is not None:
            if click_slots(self.clicks)[self._step] is None:
                return
        elif len(self.clicks) < RECOVER_CLICKS:
            return
        clicks = list(self.clicks)
        self.close()
        self._on_done(clicks)

    def _redo(self) -> None:
        if not self._recording and not self._awaiting_save:
            return
        if self._step is not None:
            slots = click_slots(self.clicks)
            slots[self._step] = None
            self.clicks = [item for item in slots if item is not None]
        else:
            self.clicks = []
        self._recording = True
        self._awaiting_save = False
        self._sync_bar()
        self._redraw()

    def _finish(self, clicks: list[dict[str, Any]]) -> None:
        self.close()
        self._on_done(clicks)

    def cancel(self) -> None:
        self.close()
        self._on_cancel()

    def close(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        if self.win.winfo_exists():
            self.win.destroy()

    def _sync_bar(self) -> None:
        if self._awaiting_save:
            self.btn_save.configure(state="normal")
            if self._step is not None:
                self.hint.configure(text=f"Đã chọn nút {self._step + 1}. Bấm Lưu vị trí, hoặc Ghi lại.")
            else:
                self.hint.configure(text="Đã chọn 4 nút. Bấm Lưu để giữ, hoặc Ghi lại.")
            return
        self.btn_save.configure(state="disabled")
        if self._recording and self._step is not None:
            self.hint.configure(text=f"Bấm trái trên màn hình · nút {self._step + 1}")
        elif self._recording:
            self.hint.configure(text=f"Bấm trái trên màn hình · nút {len(self.clicks) + 1}/{RECOVER_CLICKS}")
        else:
            self.hint.configure(text="")

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = max(int(self.canvas.winfo_width() or self.screen_w), 320)
        banner = overlay_banner(
            len(self.clicks),
            recording=self._recording,
            awaiting_save=self._awaiting_save,
            step=self._step,
        )
        self.canvas.create_rectangle(0, 0, width, 72, fill="#0b6bcb", outline="")
        self.canvas.create_text(20, 24, text=banner, fill=_TEXT, font=("Segoe UI", 16, "bold"), anchor="w")
        self.canvas.create_text(
            20,
            52,
            text=f"Thứ tự bấm:  1  →  2  →  3  →  4     Màn {self.screen_w}×{self.screen_h}",
            fill="#ffd166",
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        )
        for index in range(RECOVER_CLICKS):
            cx = width - 28 - (RECOVER_CLICKS - 1 - index) * 40
            filled = index < len(self.clicks)
            self.canvas.create_oval(cx - 14, 22, cx + 14, 50, fill="#ffd166" if filled else "#7aa7d9", outline=_EDGE, width=2)
            self.canvas.create_text(cx, 36, text=str(index + 1), fill="#102030" if filled else _TEXT, font=("Segoe UI", 13, "bold"))
        prev: tuple[int, int] | None = None
        for index, click in enumerate(self.clicks, start=1):
            x = int(click["x"])
            y = int(click["y"])
            if prev is not None:
                self.canvas.create_line(prev[0], prev[1], x, y, fill="#ffd166", width=5, arrow=tk.LAST)
            r = 28
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=_FILL, outline=_EDGE, width=4)
            self.canvas.create_text(x, y, text=str(index), fill=_TEXT, font=("Segoe UI", 20, "bold"))
            self.canvas.create_text(x, y + 44, text=f"Nút {index} / 4", fill=_EDGE, font=("Segoe UI", 13, "bold"))
            prev = (x, y)


def show_click_overlay(
    root: tk.Misc,
    *,
    on_done: DoneFn,
    on_cancel: CancelFn,
    clicks: list[dict[str, Any]] | None = None,
    recording: bool = True,
    step: int | None = None,
) -> ClickOverlay:
    return ClickOverlay(
        root,
        on_done=on_done,
        on_cancel=on_cancel,
        clicks=clicks,
        recording=recording,
        step=step,
    )


def preview_labels(clicks: list[dict[str, Any]] | None) -> list[str]:
    return click_order_labels(clicks or [])
