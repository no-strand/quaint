from PIL import Image
from app.image_effects import FILTERS, apply_image_effects, apply_image_filter


def test_filter_catalog_has_requested_non_destructive_filters():
    assert {"none", "grayscale", "sepia", "invert", "vintage", "cool", "sharpen", "soften"}.issubset(set(FILTERS))


def test_filter_preserves_alpha_and_dimensions():
    src = Image.new("RGBA", (8, 6), (20, 90, 180, 77))
    for name in FILTERS:
        out = apply_image_filter(src, name)
        assert out.size == src.size
        assert "A" in out.getbands()
        assert out.getpixel((0, 0))[3] == 77


def test_filter_is_part_of_display_effect_pipeline():
    src = Image.new("RGB", (4, 4), (10, 40, 200))
    normal = apply_image_effects(src, filter_name="none")
    inverted = apply_image_effects(src, filter_name="invert")
    assert normal.getpixel((0, 0)) != inverted.getpixel((0, 0))
