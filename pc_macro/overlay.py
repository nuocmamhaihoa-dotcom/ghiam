"""Fullscreen marks so the user can point at four left-clicks in order."""

from __future__ import annotations

from typing import Any, Callable

import tkinter as tk

from pc_macro.screen import RECOVER_CLICKS, append_screen_click, click_order_labels, overlay_banner, sanitize_clicks

_FILL = "#0b6bcb"
_EDGE = "#ffffff"
_TEXT = "#ffffff"
_BG = "#101820"
DoneFn = Callable[[list[dict[str, Any]]], None]
CancelFn = Callable[[], None]


class ClickOverlay:
    """Cover the screen, catch four left clicks, draw 1–4 at those points."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        on_done: DoneFn,
        on_cancel: CancelFn,
        clicks: list[dict[str, Any]] | None = None,
        recording: bool = True,
    ) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._recording = recording
        self.clicks = sanitize_clicks(clicks or [])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        width = int(root.winfo_screenwidth())
        height = int(root.winfo_screenheight())
        self.win.geometry(f"{width}x{height}+0+0")
        self.win.configure(bg=_BG)
        try:
            self.win.attributes("-alpha", 0.46 if recording else 0.36)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.win, bg=_BG, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<F8>", lambda _e: self.cancel())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()
        self._redraw()
        if not recording:
            self.win.after(4500, self.close)

    def _on_click(self, event: tk.Event) -> None:
        if not self._recording:
            self.close()
            return
        x = int(event.x_root)
        y = int(event.y_root)
        if x < 0 or y < 0:
            return
        self.clicks = append_screen_click(self.clicks, x, y)
        self._redraw()
        if len(self.clicks) >= RECOVER_CLICKS:
            self._recording = False
            clicks = list(self.clicks)
            self.win.after(450, lambda: self._finish(clicks))

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

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = max(int(self.canvas.winfo_screenwidth()), 320)
        banner = overlay_banner(len(self.clicks), recording=self._recording)
        self.canvas.create_rectangle(0, 0, width, 72, fill="#0b6bcb", outline="")
        self.canvas.create_text(20, 24, text=banner, fill=_TEXT, font=("Segoe UI", 16, "bold"), anchor="w")
        self.canvas.create_text(
            20,
            52,
            text="Thứ tự bấm:  1  →  2  →  3  →  4",
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
) -> ClickOverlay:
    return ClickOverlay(root, on_done=on_done, on_cancel=on_cancel, clicks=clicks, recording=recording)


def preview_labels(clicks: list[dict[str, Any]] | None) -> list[str]:
    return click_order_labels(clicks or [])
