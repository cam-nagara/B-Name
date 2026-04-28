"""PSD レイヤー保存ヘルパ."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from pathlib import Path
from typing import Any, Sequence

from ..utils import log

_logger = log.get_logger(__name__)

try:
    from psd_tools import PSDImage  # type: ignore
    from psd_tools.api.layers import Group, PixelLayer  # type: ignore
    from psd_tools.constants import BlendMode  # type: ignore

    _HAS_PSD = True
except ImportError:  # pragma: no cover
    PSDImage = None  # type: ignore
    Group = None  # type: ignore
    PixelLayer = None  # type: ignore
    BlendMode = None  # type: ignore
    _HAS_PSD = False


@dataclass(frozen=True)
class _PsdLayer:
    name: str
    image: Any
    left: int
    top: int
    visible: bool
    opacity: int
    blend_mode: str

    @property
    def right(self) -> int:
        return self.left + self.image.width

    @property
    def bottom(self) -> int:
        return self.top + self.image.height


def has_psd_tools() -> bool:
    return _HAS_PSD


def can_write_layered_psd() -> bool:
    return True


def _psd_blend_mode(mode: str):
    if BlendMode is None:
        return None
    mode = (mode or "normal").lower()
    mapping = {
        "normal": BlendMode.NORMAL,
        "multiply": BlendMode.MULTIPLY,
        "screen": BlendMode.SCREEN,
        "lighten": getattr(BlendMode, "LIGHTEN", BlendMode.NORMAL),
        "overlay": BlendMode.OVERLAY,
        "add": BlendMode.LINEAR_DODGE,
        "linear_dodge": BlendMode.LINEAR_DODGE,
    }
    return mapping.get(mode, BlendMode.NORMAL)


def _fallback_blend_key(mode: str) -> bytes:
    mode = (mode or "normal").lower()
    mapping = {
        "normal": b"norm",
        "multiply": b"mul ",
        "screen": b"scrn",
        "lighten": b"lite",
        "overlay": b"over",
        "add": b"lddg",
        "linear_dodge": b"lddg",
    }
    return mapping.get(mode, b"norm")


def _ensure_psd_group(parent, name: str, cache: dict[tuple[str, ...], Any], path: tuple[str, ...]):
    if path in cache:
        return cache[path]
    if hasattr(parent, "create_group"):
        group = parent.create_group(name=name)
    elif Group is not None:
        group = Group.new(parent, name, True)
        parent.append(group)
    else:  # pragma: no cover - psd-tools API unavailable
        raise RuntimeError("psd-tools group API unavailable")
    cache[path] = group
    return group


def _p16(value: int) -> bytes:
    return struct.pack(">H", int(value) & 0xFFFF)


def _p32(value: int) -> bytes:
    return struct.pack(">I", int(value) & 0xFFFFFFFF)


def _i16(value: int) -> bytes:
    return struct.pack(">h", int(value))


def _i32(value: int) -> bytes:
    return struct.pack(">i", int(value))


def _pad_even(data: bytes) -> bytes:
    return data if len(data) % 2 == 0 else data + b"\0"


def _pad4(data: bytes) -> bytes:
    padding = (-len(data)) % 4
    return data + (b"\0" * padding)


def _pascal_name(name: str) -> bytes:
    raw = str(name or "Layer").encode("macroman", errors="replace")[:255]
    return _pad4(bytes([len(raw)]) + raw)


def _unicode_name_block(name: str) -> bytes:
    raw = str(name or "Layer").encode("utf-16be")
    payload = _p32(len(raw) // 2) + raw
    return b"8BIM" + b"luni" + _p32(len(payload)) + _pad_even(payload)


def _layer_extra_data(name: str) -> bytes:
    data = b""
    data += _p32(0)  # layer mask data
    data += _p32(0)  # layer blending ranges
    data += _pascal_name(name)
    data += _unicode_name_block(name)
    return data


def _clip_image_to_canvas(image, left: int, top: int, size: tuple[int, int]):
    width, height = size
    right = left + image.width
    bottom = top + image.height
    clip_left = max(0, left)
    clip_top = max(0, top)
    clip_right = min(width, right)
    clip_bottom = min(height, bottom)
    if clip_right <= clip_left or clip_bottom <= clip_top:
        return None, 0, 0
    crop = image.crop((clip_left - left, clip_top - top, clip_right - left, clip_bottom - top))
    return crop, clip_left, clip_top


def _group_mask_for_layer(layer: Any, group_masks: dict[tuple[str, ...], Any] | None) -> list[Any]:
    if not group_masks:
        return []
    layer_path = tuple(getattr(layer, "group_path", ()) or ())
    matched: list[tuple[tuple[str, ...], Any]] = []
    for path, mask in group_masks.items():
        if len(path) <= len(layer_path) and tuple(layer_path[: len(path)]) == tuple(path):
            matched.append((tuple(path), mask))
    matched.sort(key=lambda item: len(item[0]))
    return [mask for _path, mask in matched]


def _apply_mask(image, left: int, top: int, mask: Any):
    from PIL import Image, ImageChops  # type: ignore

    mask_img = mask.image.convert("L")
    layer_mask = Image.new("L", image.size, 0)
    mask_left = int(getattr(mask, "left", 0))
    mask_top = int(getattr(mask, "top", 0))
    overlap_left = max(left, mask_left)
    overlap_top = max(top, mask_top)
    overlap_right = min(left + image.width, mask_left + mask_img.width)
    overlap_bottom = min(top + image.height, mask_top + mask_img.height)
    if overlap_right > overlap_left and overlap_bottom > overlap_top:
        crop = mask_img.crop(
            (
                overlap_left - mask_left,
                overlap_top - mask_top,
                overlap_right - mask_left,
                overlap_bottom - mask_top,
            )
        )
        layer_mask.paste(crop, (overlap_left - left, overlap_top - top))
    result = image.copy()
    alpha = ImageChops.multiply(result.getchannel("A"), layer_mask)
    result.putalpha(alpha)
    return result


def _prepare_fallback_layers(
    layers: Sequence[Any],
    size: tuple[int, int],
    group_masks: dict[tuple[str, ...], Any] | None,
) -> list[_PsdLayer]:
    prepared: list[_PsdLayer] = []
    for index, layer in enumerate(layers):
        image = layer.image.convert("RGBA")
        left = int(getattr(layer, "left", 0))
        top = int(getattr(layer, "top", 0))
        for mask in _group_mask_for_layer(layer, group_masks):
            image = _apply_mask(image, left, top, mask)
        image, left, top = _clip_image_to_canvas(image, left, top, size)
        if image is None or image.width <= 0 or image.height <= 0:
            continue
        raw_name = str(getattr(layer, "name", "") or f"Layer {index + 1}")
        group_path = tuple(getattr(layer, "group_path", ()) or ())
        name = "/".join((*group_path, raw_name)) if group_path else raw_name
        prepared.append(
            _PsdLayer(
                name=name,
                image=image,
                left=left,
                top=top,
                visible=bool(getattr(layer, "visible", True)),
                opacity=int(max(0, min(255, getattr(layer, "opacity", 255)))),
                blend_mode=str(getattr(layer, "blend_mode", "normal") or "normal"),
            )
        )
    return prepared


def _scale_alpha(image, opacity: int):
    if opacity >= 255:
        return image
    result = image.copy()
    alpha = result.getchannel("A").point(lambda value: int(value * opacity / 255))
    result.putalpha(alpha)
    return result


def _flatten_fallback_layers(layers: Sequence[_PsdLayer], size: tuple[int, int]):
    from PIL import Image, ImageChops  # type: ignore

    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    for layer in layers:
        if not layer.visible or layer.opacity <= 0:
            continue
        src = _scale_alpha(layer.image.convert("RGBA"), layer.opacity)
        if layer.blend_mode in {"multiply", "screen", "lighten", "overlay", "add", "linear_dodge"}:
            region = canvas.crop((layer.left, layer.top, layer.right, layer.bottom))
            base_rgb = region.convert("RGB")
            src_rgb = src.convert("RGB")
            mode = layer.blend_mode
            if mode == "multiply":
                blended = ImageChops.multiply(base_rgb, src_rgb)
            elif mode == "screen":
                blended = ImageChops.screen(base_rgb, src_rgb)
            elif mode == "lighten":
                blended = ImageChops.lighter(base_rgb, src_rgb)
            elif mode == "overlay" and hasattr(ImageChops, "overlay"):
                blended = ImageChops.overlay(base_rgb, src_rgb)
            elif mode in {"add", "linear_dodge"}:
                blended = ImageChops.add(base_rgb, src_rgb, scale=1.0)
            else:
                blended = src_rgb
            mixed = Image.composite(blended, base_rgb, src.getchannel("A"))
            composed = Image.merge("RGBA", (*mixed.split(), region.getchannel("A")))
            composed.alpha_composite(src)
            canvas.paste(composed, (layer.left, layer.top))
        else:
            canvas.alpha_composite(src, dest=(layer.left, layer.top))
    return canvas


def _packbits_row(row: bytes) -> bytes:
    out = bytearray()
    length = len(row)
    index = 0
    while index < length:
        run = 1
        while index + run < length and run < 128 and row[index + run] == row[index]:
            run += 1
        if run >= 3:
            out.append(257 - run)
            out.append(row[index])
            index += run
            continue
        literal_start = index
        index += run
        while index < length:
            run = 1
            while index + run < length and run < 128 and row[index + run] == row[index]:
                run += 1
            if run >= 3 or index - literal_start >= 128:
                break
            index += run
        literal = row[literal_start:index]
        out.append(len(literal) - 1)
        out.extend(literal)
    return bytes(out)


def _rle_channel_chunks(channel) -> tuple[bytes, bytes] | None:
    width, height = channel.size
    raw = channel.tobytes()
    counts = bytearray()
    payload = bytearray()
    for row_index in range(height):
        start = row_index * width
        packed = _packbits_row(raw[start : start + width])
        if len(packed) > 0xFFFF:
            return None
        counts.extend(_p16(len(packed)))
        payload.extend(packed)
    return bytes(counts), bytes(payload)


def _encoded_channel_data(channel) -> bytes:
    raw_data = b"\0\0" + channel.tobytes()
    rle = _rle_channel_chunks(channel)
    if rle is None:
        return raw_data
    counts, payload = rle
    encoded = b"\0\1" + counts + payload
    return encoded if len(encoded) < len(raw_data) else raw_data


def _channel_data(layer: _PsdLayer) -> list[tuple[int, bytes]]:
    image = layer.image.convert("RGBA")
    channels = image.split()
    return [
        (0, _encoded_channel_data(channels[0])),
        (1, _encoded_channel_data(channels[1])),
        (2, _encoded_channel_data(channels[2])),
        (-1, _encoded_channel_data(channels[3])),
    ]


def _layer_record(layer: _PsdLayer, channels: list[tuple[int, bytes]]) -> bytes:
    record = b""
    record += _i32(layer.top)
    record += _i32(layer.left)
    record += _i32(layer.bottom)
    record += _i32(layer.right)
    record += _p16(len(channels))
    for channel_id, data in channels:
        record += _i16(channel_id)
        record += _p32(len(data))
    record += b"8BIM"
    record += _fallback_blend_key(layer.blend_mode)
    record += bytes([layer.opacity])
    record += b"\0"  # clipping
    record += bytes([0 if layer.visible else 2])
    record += b"\0"  # filler
    extra = _layer_extra_data(layer.name)
    record += _p32(len(extra))
    record += extra
    return record


def _composite_image_data(layers: Sequence[_PsdLayer], size: tuple[int, int]) -> bytes:
    merged = _flatten_fallback_layers(layers, size).convert("RGBA")
    channels = merged.split()
    raw_data = b"\0\0" + b"".join(channel.tobytes() for channel in channels)
    counts = bytearray()
    payload = bytearray()
    for channel in channels:
        rle = _rle_channel_chunks(channel)
        if rle is None:
            return raw_data
        channel_counts, channel_payload = rle
        counts.extend(channel_counts)
        payload.extend(channel_payload)
    encoded = b"\0\1" + bytes(counts) + bytes(payload)
    return encoded if len(encoded) < len(raw_data) else raw_data


def _empty_psd_layer(size: tuple[int, int]) -> _PsdLayer:
    from PIL import Image  # type: ignore

    return _PsdLayer(
        name="empty",
        image=Image.new("RGBA", size, (0, 0, 0, 0)),
        left=0,
        top=0,
        visible=True,
        opacity=255,
        blend_mode="normal",
    )


def _layer_info_data(psd_layers: Sequence[_PsdLayer]) -> bytes:
    layer_records = b""
    layer_image_data = b""
    psd_stack = list(reversed(psd_layers))
    for layer in psd_stack:
        channels = _channel_data(layer)
        layer_records += _layer_record(layer, channels)
        layer_image_data += b"".join(data for _channel_id, data in channels)
    layer_info = _i16(len(psd_stack)) + layer_records + layer_image_data
    return _pad_even(layer_info)


def _psd_header(size: tuple[int, int]) -> bytes:
    width, height = size
    return (
        b"8BPS"
        + _p16(1)
        + (b"\0" * 6)
        + _p16(4)
        + _p32(height)
        + _p32(width)
        + _p16(8)
        + _p16(3)
    )


def _psd_body(psd_layers: Sequence[_PsdLayer], size: tuple[int, int]) -> bytes:
    layer_info = _layer_info_data(psd_layers)
    layer_and_mask = _p32(len(layer_info)) + layer_info + _p32(0)
    return (
        _psd_header(size)
        + _p32(0)
        + _p32(0)
        + _p32(len(layer_and_mask))
        + layer_and_mask
        + _composite_image_data(psd_layers, size)
    )


def _save_layers_with_builtin_writer(
    layers: Sequence[Any],
    size: tuple[int, int],
    out_path: Path,
    group_masks: dict[tuple[str, ...], Any] | None,
) -> bool:
    try:
        psd_layers = _prepare_fallback_layers(layers, size, group_masks)
        if not psd_layers:
            psd_layers = [_empty_psd_layer(size)]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_psd_body(psd_layers, size))
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("built-in layered psd save failed: %s", exc)
        return False


def save_layers_as_psd(
    layers: Sequence[Any],
    size: tuple[int, int],
    out_path: Path,
    group_masks: dict[tuple[str, ...], Any] | None = None,
) -> bool:
    if _HAS_PSD:
        try:
            psd = PSDImage.new(mode="RGB", size=size)
            group_cache: dict[tuple[str, ...], Any] = {(): psd}
            for layer in layers:
                parent = psd
                path_accum: list[str] = []
                for part in layer.group_path:
                    path_accum.append(part)
                    parent = _ensure_psd_group(parent, part, group_cache, tuple(path_accum))
                kwargs = {
                    "name": layer.name,
                    "top": int(layer.top),
                    "left": int(layer.left),
                    "opacity": int(max(0, min(255, layer.opacity))),
                }
                blend_mode = _psd_blend_mode(layer.blend_mode)
                if blend_mode is not None:
                    kwargs["blend_mode"] = blend_mode
                pixel_layer = PixelLayer.frompil(layer.image.convert("RGBA"), parent=parent, **kwargs)
                pixel_layer.visible = bool(layer.visible)
            if group_masks:
                for path, mask in group_masks.items():
                    group = group_cache.get(path)
                    if group is None:
                        parent = psd
                        path_accum = []
                        for part in path:
                            path_accum.append(part)
                            parent = _ensure_psd_group(parent, part, group_cache, tuple(path_accum))
                        group = group_cache.get(path)
                    if group is None or not hasattr(group, "create_mask"):
                        continue
                    if hasattr(group, "has_mask") and group.has_mask():
                        continue
                    group.create_mask(mask.image, top=int(mask.top), left=int(mask.left))
            psd.save(str(out_path))
            return True
        except Exception as exc:  # noqa: BLE001
            _logger.exception("psd-tools layered save failed, fallback to built-in writer: %s", exc)
    return _save_layers_with_builtin_writer(layers, size, out_path, group_masks)


def save_flat_image_as_psd(img, out_path: Path) -> bool:
    from types import SimpleNamespace

    layer = SimpleNamespace(
        name="B-Name",
        image=img.convert("RGBA"),
        left=0,
        top=0,
        group_path=(),
        visible=True,
        opacity=255,
        blend_mode="normal",
    )
    return save_layers_as_psd([layer], img.size, out_path)
