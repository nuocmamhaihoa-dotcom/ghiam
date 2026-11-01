"""Find the spot the user painted; that is where the left-click goes."""

from __future__ import annotations

from typing import Any

PAINT_RGB = (255, 0, 170)
PAINT_HEX = "#ff00aa"
PAINT_TOLERANCE = 70
MIN_PAINT_PIXELS = 8


def is_paint_color(
    red: int,
    green: int,
    blue: int,
    *,
    target: tuple[int, int, int] = PAINT_RGB,
    tolerance: int = PAINT_TOLERANCE,
) -> bool:
    """True when this pixel is the paint the user brushed on."""
    return abs(red - target[0]) + abs(green - target[1]) + abs(blue - target[2]) <= tolerance


def paint_centroid(
    image: Any,
    *,
    target: tuple[int, int, int] = PAINT_RGB,
    tolerance: int = PAINT_TOLERANCE,
    min_pixels: int = MIN_PAINT_PIXELS,
) -> tuple[int, int, int] | None:
    """Center of the painted blob: (x, y, pixel count), or None if too small."""
    if image is None or not hasattr(image, "size") or not hasattr(image, "getdata"):
        return None
    width, height = int(image.size[0]), int(image.size[1])
    if width < 2 or height < 2:
        return None
    source = image.convert("RGB") if getattr(image, "mode", "RGB") != "RGB" else image
    pixels = source.load()
    total_x = 0
    total_y = 0
    count = 0
    for y in range(height):
        for x in range(width):
            pixel = pixels[x, y]
            if not is_paint_color(int(pixel[0]), int(pixel[1]), int(pixel[2]), target=target, tolerance=tolerance):
                continue
            total_x += x
            total_y += y
            count += 1
    if count < max(1, int(min_pixels)):
        return None
    return int(round(total_x / count)), int(round(total_y / count)), count


def paint_point(image: Any) -> dict[str, int] | None:
    """Image-space click from the painted mark. Does not keep the picture."""
    hit = paint_centroid(image)
    if hit is None:
        return None
    x, y, count = hit
    width, height = int(image.size[0]), int(image.size[1])
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return {"x": x, "y": y, "sw": width, "sh": height, "count": count}
