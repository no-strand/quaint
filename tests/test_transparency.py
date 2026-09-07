from PIL import Image
from app.image_effects import apply_image_effects


def test_effects_preserve_alpha_channel():
    img = Image.new('RGBA', (2, 2), (255, 0, 0, 0))
    img.putpixel((1, 1), (0, 255, 0, 123))
    out = apply_image_effects(img)
    assert out.mode == 'RGBA'
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((1, 1))[3] == 123
