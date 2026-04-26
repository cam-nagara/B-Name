"""コマ背景の透明扱いとプレビュー画像の背景透過ヘルパ."""

from __future__ import annotations

from collections import deque


def panel_background_is_transparent(entry, *, epsilon: float = 1e-4) -> bool:
    bg = getattr(entry, "background_color", None)
    if bg is None or len(bg) < 4:
        return False
    try:
        return float(bg[3]) <= epsilon
    except (TypeError, ValueError):
        return False


def make_background_transparent(image, *, threshold: int = 18):
    """PIL Image の周辺背景色に近いピクセルを alpha=0 にする."""
    if image is None:
        return image
    try:
        rgba = image.convert("RGBA")
        width, height = rgba.size
    except Exception:  # noqa: BLE001
        return image
    if width <= 0 or height <= 0:
        return rgba
    try:
        alpha_min, _alpha_max = rgba.getchannel("A").getextrema()
    except Exception:  # noqa: BLE001
        alpha_min = 255
    if alpha_min < 255:
        return rgba

    bg = _estimate_background_rgb(rgba)
    if bg is None:
        return rgba

    limit = int(threshold) * int(threshold) * 3
    pixels = list(rgba.getdata())
    transparent = _edge_connected_background_pixels(pixels, width, height, bg, limit)
    if not any(transparent):
        return rgba
    out = []
    for idx, (r, g, b, a) in enumerate(pixels):
        out.append((r, g, b, 0) if transparent[idx] else (r, g, b, a))
    rgba.putdata(out)
    return rgba


def _estimate_background_rgb(image) -> tuple[int, int, int] | None:
    width, height = image.size
    coords = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    samples: list[tuple[int, int, int]] = []
    for coord in coords:
        try:
            r, g, b, a = image.getpixel(coord)
        except Exception:  # noqa: BLE001
            continue
        if a == 0:
            continue
        samples.append((int(r), int(g), int(b)))
    if not samples:
        return None
    return max(set(samples), key=samples.count)


def _edge_connected_background_pixels(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    bg: tuple[int, int, int],
    limit: int,
) -> bytearray:
    visited = bytearray(width * height)
    queue: deque[int] = deque()

    def is_background(idx: int) -> bool:
        r, g, b, a = pixels[idx]
        if a == 0:
            return True
        diff = (r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2
        return diff <= limit

    def add_if_background(x: int, y: int) -> None:
        idx = y * width + x
        if visited[idx] or not is_background(idx):
            return
        visited[idx] = 1
        queue.append(idx)

    for x in range(width):
        add_if_background(x, 0)
        add_if_background(x, height - 1)
    for y in range(1, height - 1):
        add_if_background(0, y)
        add_if_background(width - 1, y)

    while queue:
        idx = queue.popleft()
        x = idx % width
        y = idx // width
        if x > 0:
            add_if_background(x - 1, y)
        if x < width - 1:
            add_if_background(x + 1, y)
        if y > 0:
            add_if_background(x, y - 1)
        if y < height - 1:
            add_if_background(x, y + 1)
    return visited
