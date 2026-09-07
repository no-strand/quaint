from PIL import Image
from app.image_effects import flip_image


def test_flip_horizontal_and_vertical_preserve_alpha():
    img = Image.new('RGBA', (2, 2), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 10))
    h = flip_image(img, horizontal=True)
    assert h.getpixel((1, 0)) == (255, 0, 0, 10)
    v = flip_image(img, vertical=True)
    assert v.getpixel((0, 1)) == (255, 0, 0, 10)
