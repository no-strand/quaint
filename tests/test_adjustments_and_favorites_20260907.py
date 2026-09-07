from pathlib import Path

import numpy as np
from PIL import Image

from app.image_effects import ADJUSTMENT_KEYS, DEFAULT_ADJUSTMENTS, apply_image_effects

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
FAVORITES = (ROOT / "app" / "favorite_panel.py").read_text(encoding="utf-8")
DIALOG = (ROOT / "app" / "adjustments_dialog.py").read_text(encoding="utf-8")


def _sample_rgba():
    x = np.linspace(20, 235, 96, dtype=np.uint8)
    rgb = np.zeros((72, 96, 4), dtype=np.uint8)
    rgb[..., 0] = x[None, :]
    rgb[..., 1] = np.flip(x)[None, :]
    rgb[..., 2] = 110
    rgb[..., 3] = np.linspace(40, 255, 72, dtype=np.uint8)[:, None]
    return Image.fromarray(rgb, "RGBA")


def test_new_adjustment_keys_are_present_and_neutral():
    expected = {
        "vibrance", "clarity", "dehaze", "noise_reduction", "blur", "grain", "fade",
        "color_balance_shadows", "color_balance_midtones", "color_balance_highlights",
        "auto_white_balance", "auto_contrast", "auto_enhance", "whiten_background",
        "darken_lines", "moire_reduction", "scan_cleanup", "threshold_enabled",
        "threshold_level", "curve_shadows", "curve_midtones", "curve_highlights",
    }
    assert expected.issubset(ADJUSTMENT_KEYS)
    assert DEFAULT_ADJUSTMENTS["threshold_level"] == 0.5
    assert all(k in DEFAULT_ADJUSTMENTS for k in ADJUSTMENT_KEYS)


def test_each_major_new_effect_changes_pixels_and_preserves_alpha():
    src = _sample_rgba()
    base = np.asarray(src)
    cases = [
        {"vibrance": 0.8}, {"clarity": 0.8}, {"dehaze": 0.8},
        {"blur": 0.7}, {"grain": 0.5}, {"fade": 0.8},
        {"color_balance_midtones": 0.8}, {"auto_white_balance": 1.0},
        {"auto_contrast": 1.0}, {"auto_enhance": 1.0},
        {"whiten_background": 0.9}, {"darken_lines": 0.9},
        {"moire_reduction": 0.9}, {"scan_cleanup": 1.0},
        {"curve_midtones": 0.7}, {"threshold_enabled": 1.0, "threshold_level": 0.55},
    ]
    for values in cases:
        out = apply_image_effects(src, values)
        arr = np.asarray(out)
        assert arr.shape == base.shape
        assert np.array_equal(arr[..., 3], base[..., 3])
        assert not np.array_equal(arr[..., :3], base[..., :3]), values


def test_adjustments_dialog_is_grouped_and_exposes_scan_advanced_controls():
    for token in (
        'adjustments.group_light', 'adjustments.group_color', 'adjustments.group_details',
        'adjustments.group_effects', 'adjustments.group_scan', 'adjustments.group_advanced',
        'threshold_enabled', 'curve_midtones', 'auto_white_balance', 'auto_enhance',
    ):
        assert token in DIALOG


def test_favorites_are_root_items_separate_from_bookmarks():
    assert 'def _favorite_target_path' in READER
    assert 'self._opened_root_path' in READER
    assert 'def toggle_current_favorite' in READER
    assert 'FavoritePanel' in READER
    assert 'def favorites(self)' in SETTINGS
    assert 'def add_favorite' in SETTINGS
    assert 'def remove_favorite' in SETTINGS
    assert 'path_selected = Signal(str)' in FAVORITES
    assert 'remove_requested = Signal(str)' in FAVORITES
    assert 'favorite_preview_png' in FAVORITES


def test_noise_reduction_changes_a_noisy_image():
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, size=(48, 48, 4), dtype=np.uint8)
    arr[..., 3] = 180
    src = Image.fromarray(arr, "RGBA")
    out = np.asarray(apply_image_effects(src, {"noise_reduction": 1.0}))
    assert not np.array_equal(out[..., :3], arr[..., :3])
    assert np.array_equal(out[..., 3], arr[..., 3])
