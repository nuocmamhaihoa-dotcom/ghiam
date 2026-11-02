"""Window to drop a screenshot or photo, paint a mark, then save that point."""

from __future__ import annotations

import io
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog

try:
    from PIL import Image, ImageDraw, ImageGrab
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageGrab = None  # type: ignore[assignment]

from pc_macro.paint import PAINT_HEX, PAINT_RGB, paint_point
from pc_macro.screen import upsert_slot

DoneFn = Callable[[list[dict[str, Any]]], None]
CancelFn = Callable[[], None]
BRUSH = 16


class PaintPicker:
    """Paint magenta on a picture; Lưu vị trí stores the blob center."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        step: int,
        clicks: list[dict[str, Any]] | None = None,
        name: str = "",
        on_done: DoneFn,
        on_cancel: CancelFn,
    ) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._step = int(step)
        self._clicks = list(clicks or [])
        self._name = name
        self._source: Image.Image | None = None
        self.work: Image.Image | None = None
        self._photo: tk.PhotoImage | None = None
        self._scale = 1.0
        self._ox = 0
        self._oy = 0
        self.win = tk.Toplevel(root)
        self.win.title(f"Bôi màu nút {self._step + 1}")
        self.win.attributes("-topmost", True)
        width = max(720, int(root.winfo_screenwidth() * 0.86))
        height = max(520, int(root.winfo_screenheight() * 0.86))
        self.win.geometry(f"{width}x{height}+20+20")
        bar = tk.Frame(self.win, bg="#0b6bcb")
        bar.pack(side="top", fill="x")
        self.hint = tk.Label(
            bar,
            text=f"Bôi màu hồng lên nút {self._step + 1}, rồi Lưu vị trí. Máy bấm trái đúng chỗ tô.",
            fg="#ffffff",
            bg="#0b6bcb",
            font=("Segoe UI", 12, "bold"),
            wraplength=width - 40,
            justify="left",
        )
        self.hint.pack(side="left", padx=12, pady=10, fill="x", expand=True)
        actions = tk.Frame(self.win, bg="#e8edf2")
        actions.pack(side="bottom", fill="x")
        tk.Button(actions, text="Chụp màn", command=self.grab_screen, font=("Segoe UI", 12, "bold"), padx=12, pady=8).pack(
            side="left", padx=6, pady=8
        )
        tk.Button(actions, text="Thêm ảnh", command=self.open_image, font=("Segoe UI", 12, "bold"), padx=12, pady=8).pack(
            side="left", padx=4, pady=8
        )
        tk.Button(actions, text="Xóa màu", command=self.clear_paint, font=("Segoe UI", 12, "bold"), padx=12, pady=8).pack(
            side="left", padx=4, pady=8
        )
        tk.Button(
            actions,
            text="Lưu vị trí",
            command=self.save,
            font=("Segoe UI", 12, "bold"),
            bg="#ffd166",
            fg="#102030",
            padx=16,
            pady=8,
        ).pack(side="right", padx=8, pady=8)
        tk.Button(actions, text="Hủy", command=self.cancel, font=("Segoe UI", 12, "bold"), bg="#c2410c", fg="#ffffff", padx=12, pady=8).pack(
            side="right", padx=4, pady=8
        )
        self.canvas = tk.Canvas(self.win, bg="#101820", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._stroke)
        self.canvas.bind("<B1-Motion>", self._stroke)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: self._fit())
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<F8>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Configure>", lambda _e: self._fit())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()
        self.win.after(80, self.grab_screen)

    def grab_screen(self) -> None:
        try:
            image = ImageGrab.grab()
        except Exception:
            self.hint.configure(text="Không chụp được màn. Bấm Thêm ảnh và chọn ảnh bạn bôi màu.")
            return
        self._load(image)

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win,
            title="Thêm ảnh để bôi màu",
            filetypes=[("Ảnh", "*.png *.jpg *.jpeg *.bmp *.webp"), ("Tất cả", "*.*")],
        )
        if not path:
            return
        try:
            image = Image.open(path)
        except Exception:
            self.hint.configure(text="Không mở được ảnh. Chọn file PNG hoặc JPG.")
            return
        self._load(image)

    def clear_paint(self) -> None:
        if self._source is None:
            return
        self.work = self._source.copy()
        self._fit()
        self.hint.configure(text="Đã xóa màu. Bôi lại chỗ cần bấm, rồi Lưu vị trí.")

    def save(self) -> None:
        if self.work is None:
            self.hint.configure(text="Chụp màn hoặc Thêm ảnh, rồi bôi màu hồng lên nút cần bấm.")
            return
        hit = paint_point(self.work)
        if hit is None:
            self.hint.configure(text="Bôi màu hồng (#ff00aa) lên chỗ cần bấm, rồi Lưu vị trí.")
            return
        clicks = upsert_slot(
            self._clicks,
            self._step,
            hit["x"],
            hit["y"],
            hit["sw"],
            hit["sh"],
            self._name,
            via="paint",
        )
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

    def _load(self, image: Image.Image) -> None:
        if image.mode != "RGB":
            image = image.convert("RGB")
        self._source = image.copy()
        self.work = image.copy()
        self.hint.configure(
            text=f"Ảnh {image.size[0]}×{image.size[1]}. Bôi màu hồng nút {self._step + 1}, rồi Lưu vị trí."
        )
        self.win.after(40, self._fit)

    def _fit(self) -> None:
        if self.work is None or not self.canvas.winfo_exists():
            return
        canvas_w = max(int(self.canvas.winfo_width()), 40)
        canvas_h = max(int(self.canvas.winfo_height()), 40)
        width, height = self.work.size
        scale = min(canvas_w / width, canvas_h / height)
        preview_w = max(1, int(width * scale))
        preview_h = max(1, int(height * scale))
        self._scale = scale
        self._ox = (canvas_w - preview_w) // 2
        self._oy = (canvas_h - preview_h) // 2
        preview = self.work.resize((preview_w, preview_h), Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        preview.save(buf, format="PNG")
        self._photo = tk.PhotoImage(data=buf.getvalue())
        self.canvas.delete("all")
        self.canvas.create_image(self._ox, self._oy, image=self._photo, anchor="nw")

    def _to_image(self, x: int, y: int) -> tuple[int, int] | None:
        if self.work is None or self._scale <= 0:
            return None
        ix = int((x - self._ox) / self._scale)
        iy = int((y - self._oy) / self._scale)
        width, height = self.work.size
        if 0 <= ix < width and 0 <= iy < height:
            return ix, iy
        return None

    def _stroke(self, event: tk.Event) -> None:
        if self.work is None:
            return
        point = self._to_image(int(event.x), int(event.y))
        if point is None:
            return
        draw = ImageDraw.Draw(self.work)
        x, y = point
        draw.ellipse((x - BRUSH, y - BRUSH, x + BRUSH, y + BRUSH), fill=PAINT_RGB)
        radius = max(2, int(BRUSH * self._scale))
        self.canvas.create_oval(
            event.x - radius,
            event.y - radius,
            event.x + radius,
            event.y + radius,
            fill=PAINT_HEX,
            outline="",
        )


def show_paint_picker(
    root: tk.Misc,
    *,
    step: int,
    clicks: list[dict[str, Any]] | None = None,
    name: str = "",
    on_done: DoneFn,
    on_cancel: CancelFn,
) -> PaintPicker:
    return PaintPicker(root, step=step, clicks=clicks, name=name, on_done=on_done, on_cancel=on_cancel)
