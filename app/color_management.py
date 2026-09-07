"""Gerenciamento de cor simples e seguro via ICC incorporado -> sRGB."""
from io import BytesIO
from PIL import Image, ImageCms

def convert_embedded_profile_to_srgb(image: Image.Image) -> Image.Image:
    icc = image.info.get('icc_profile')
    if not icc:
        return image
    try:
        src = ImageCms.ImageCmsProfile(BytesIO(icc))
        dst = ImageCms.createProfile('sRGB')
        mode = 'RGBA' if 'A' in image.getbands() else 'RGB'
        alpha = image.getchannel('A').copy() if mode == 'RGBA' else None
        rgb = image.convert('RGB')
        out = ImageCms.profileToProfile(rgb, src, dst, outputMode='RGB')
        if alpha is not None:
            out=out.convert('RGBA'); out.putalpha(alpha)
        return out
    except Exception:
        return image
