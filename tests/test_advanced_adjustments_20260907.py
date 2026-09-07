from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from app.image_effects import (
    ADJUSTMENT_KEYS,
    DEFAULT_ADJUSTMENTS,
    apply_image_effects,
    normalize_adjustments,
)

ROOT = Path(__file__).resolve().parents[1]
DIALOG = (ROOT / "app" / "adjustments_dialog.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _different(a, b):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


def _sample():
    # Gradiente com cromaticidade e faixa tonal incompleta para exercitar todos
    # os controles, inclusive níveis automáticos.
    x = np.linspace(35, 215, 96, dtype=np.uint8)
    y = np.linspace(0, 40, 64, dtype=np.uint8)[:, None]
    r = np.clip(x[None, :] + y, 0, 255)
    g = np.clip(60 + x[None, :] // 2 + y // 3, 0, 255)
    b = np.clip(210 - x[None, :] // 2 + y // 4, 0, 255)
    arr = np.stack([r, g, b], axis=-1).astype(np.uint8)
    # Pequena região com textura/bordas para que nitidez tenha algo real a reforçar.
    arr[18:46, 34:62] = (90, 150, 210)
    arr[24:40, 40:56] = (185, 70, 55)
    return Image.fromarray(arr, "RGB")


def test_new_adjustment_catalog_and_defaults():
    for key in ("gamma", "hue", "whites", "blacks", "sharpness", "auto_levels"):
        assert key in ADJUSTMENT_KEYS
        assert key in DEFAULT_ADJUSTMENTS
    assert DEFAULT_ADJUSTMENTS["gamma"] == 1.0
    assert DEFAULT_ADJUSTMENTS["hue"] == 0.0
    assert DEFAULT_ADJUSTMENTS["sharpness"] == 0.0
    assert DEFAULT_ADJUSTMENTS["auto_levels"] == 0.0


def test_previous_nine_value_adjustments_are_normalized_safely():
    previous = (1.1, 0.9, 1.2, 0.1, 0.2, -0.1, 0.3, 0.2, -0.2)
    normalized = normalize_adjustments(previous)
    assert normalized[:9] == previous
    assert normalized[9:] == tuple(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS[9:])


def test_each_new_manual_adjustment_changes_pixels():
    src = _sample()
    cases = {
        "gamma": 1.8,
        "hue": 0.35,
        "whites": 0.8,
        "blacks": -0.8,
        "sharpness": 1.5,
        "auto_levels": 1.0,
    }
    for key, value in cases.items():
        settings = dict(DEFAULT_ADJUSTMENTS)
        settings[key] = value
        out = apply_image_effects(src, settings)
        assert out.size == src.size
        assert _different(src, out), f"{key} deve alterar a imagem"


def test_advanced_adjustments_preserve_alpha():
    src = _sample().convert("RGBA")
    src.putalpha(Image.new("L", src.size, 93))
    settings = dict(DEFAULT_ADJUSTMENTS)
    settings.update({
        "gamma": 1.4,
        "hue": 0.2,
        "whites": 0.5,
        "blacks": -0.4,
        "sharpness": 1.0,
        "auto_levels": 1.0,
    })
    out = apply_image_effects(src, settings)
    assert out.mode == "RGBA"
    assert out.getchannel("A").getextrema() == (93, 93)


def test_adjustments_dialog_exposes_all_requested_controls():
    for token in (
        'tr("adjustments.gamma")',
        'tr("adjustments.hue")',
        'tr("adjustments.whites")',
        'tr("adjustments.blacks")',
        'tr("adjustments.sharpness")',
        'tr("adjustments.auto_levels")',
        'self.btn_auto_levels.setCheckable(True)',
    ):
        assert token in DIALOG
    assert "gama" in README.lower()
    assert "níveis automáticos" in README.lower()
