from PIL import Image, ImageCms
from app.color_management import convert_embedded_profile_to_srgb


def test_embedded_srgb_profile_is_interpreted_without_changing_size_or_alpha():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    icc = profile.tobytes()
    image = Image.new("RGBA", (5, 7), (20, 30, 40, 91))
    image.info["icc_profile"] = icc
    out = convert_embedded_profile_to_srgb(image)
    assert out.size == (5, 7)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 91
