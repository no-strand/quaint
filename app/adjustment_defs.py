"""Definições leves dos ajustes de imagem do Quaint.

Este módulo não importa Pillow nem NumPy. Ele existe para que preferências,
atalhos e a inicialização da interface possam consultar valores padrão sem
carregar o pipeline pesado de processamento de imagem antes de uma imagem ser
realmente exibida/alterada.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

ADJUSTMENT_KEYS = (
    "brightness",
    "contrast",
    "saturation",
    "highlights",
    "exposure",
    "shadows",
    "vignette",
    "warmth",
    "tint",
    "gamma",
    "hue",
    "whites",
    "blacks",
    "sharpness",
    "auto_levels",
    "vibrance",
    "clarity",
    "dehaze",
    "noise_reduction",
    "blur",
    "grain",
    "fade",
    "color_balance_shadows",
    "color_balance_midtones",
    "color_balance_highlights",
    "auto_white_balance",
    "auto_contrast",
    "auto_enhance",
    "whiten_background",
    "darken_lines",
    "moire_reduction",
    "scan_cleanup",
    "threshold_enabled",
    "threshold_level",
    "curve_shadows",
    "curve_midtones",
    "curve_highlights",
)

FILTERS = ("none", "grayscale", "sepia", "invert", "vintage", "cool", "sharpen", "soften")

DEFAULT_ADJUSTMENTS = {
    "brightness": 1.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "highlights": 0.0,
    "exposure": 0.0,
    "shadows": 0.0,
    "vignette": 0.0,
    "warmth": 0.0,
    "tint": 0.0,
    "gamma": 1.0,
    "hue": 0.0,
    "whites": 0.0,
    "blacks": 0.0,
    "sharpness": 0.0,
    "auto_levels": 0.0,
    "vibrance": 0.0,
    "clarity": 0.0,
    "dehaze": 0.0,
    "noise_reduction": 0.0,
    "blur": 0.0,
    "grain": 0.0,
    "fade": 0.0,
    "color_balance_shadows": 0.0,
    "color_balance_midtones": 0.0,
    "color_balance_highlights": 0.0,
    "auto_white_balance": 0.0,
    "auto_contrast": 0.0,
    "auto_enhance": 0.0,
    "whiten_background": 0.0,
    "darken_lines": 0.0,
    "moire_reduction": 0.0,
    "scan_cleanup": 0.0,
    "threshold_enabled": 0.0,
    "threshold_level": 0.5,
    "curve_shadows": 0.0,
    "curve_midtones": 0.0,
    "curve_highlights": 0.0,
}

NEUTRAL_ADJUSTMENTS = tuple(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS)


def normalize_adjustments(values=None) -> tuple[float, ...]:
    if values is None:
        return NEUTRAL_ADJUSTMENTS
    if isinstance(values, Mapping):
        return tuple(float(values.get(k, DEFAULT_ADJUSTMENTS[k])) for k in ADJUSTMENT_KEYS)
    seq = list(values) if isinstance(values, Sequence) else []
    if 0 < len(seq) < len(ADJUSTMENT_KEYS):
        seq.extend(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS[len(seq):])
    if len(seq) != len(ADJUSTMENT_KEYS):
        return NEUTRAL_ADJUSTMENTS
    return tuple(float(v) for v in seq)


def adjustments_dict(values=None) -> dict[str, float]:
    return dict(zip(ADJUSTMENT_KEYS, normalize_adjustments(values)))


def normalize_filter_name(name) -> str:
    value = str(name or "none").strip().lower()
    return value if value in FILTERS else "none"
