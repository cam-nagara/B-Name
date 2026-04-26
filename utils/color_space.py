"""Color-space conversion helpers for Blender COLOR properties."""

from __future__ import annotations


def srgb_to_linear_value(value: float) -> float:
    """Convert one sRGB channel to Blender's scene-linear channel."""
    v = max(0.0, min(1.0, float(value)))
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def linear_to_srgb_value(value: float) -> float:
    """Convert one scene-linear channel to sRGB for UI/display semantics."""
    v = max(0.0, min(1.0, float(value)))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def srgb_to_linear_rgb(rgb) -> tuple[float, float, float]:
    return tuple(srgb_to_linear_value(float(rgb[i])) for i in range(3))


def linear_to_srgb_rgb(rgb) -> tuple[float, float, float]:
    return tuple(linear_to_srgb_value(float(rgb[i])) for i in range(3))
