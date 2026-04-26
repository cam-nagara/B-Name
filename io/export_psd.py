"""PSD レイヤー保存ヘルパ."""

from __future__ import annotations

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


def has_psd_tools() -> bool:
    return _HAS_PSD


def _psd_blend_mode(mode: str):
    if BlendMode is None:
        return None
    mode = (mode or "normal").lower()
    mapping = {
        "normal": BlendMode.NORMAL,
        "multiply": BlendMode.MULTIPLY,
        "screen": BlendMode.SCREEN,
        "overlay": BlendMode.OVERLAY,
        "add": BlendMode.LINEAR_DODGE,
        "linear_dodge": BlendMode.LINEAR_DODGE,
    }
    return mapping.get(mode, BlendMode.NORMAL)


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


def save_layers_as_psd(
    layers: Sequence[Any],
    size: tuple[int, int],
    out_path: Path,
    group_masks: dict[tuple[str, ...], Any] | None = None,
) -> bool:
    if not _HAS_PSD:
        return False
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
        _logger.exception("layered psd save failed: %s", exc)
        return False


def save_flat_image_as_psd(img, out_path: Path) -> bool:
    if not _HAS_PSD:
        return False
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
