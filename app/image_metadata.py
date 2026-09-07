"""Extração leve de metadados da página/imagem atual."""
from __future__ import annotations

import io
from pathlib import Path
from PIL import Image, ExifTags

try:
    from PIL import ImageCms
except ImportError:  # pragma: no cover
    ImageCms = None


def _fraction_text(value):
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 < num < 1:
        inv = round(1 / num)
        if inv > 1 and abs(num - 1 / inv) < 0.002:
            return f"1/{inv} s"
    return f"{num:g} s"


def _fnumber_text(value):
    if value is None:
        return None
    try:
        return f"f/{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _orientation_text(value):
    names = {
        1: "normal", 2: "mirror_h", 3: "rotate_180", 4: "mirror_v",
        5: "transpose", 6: "rotate_90", 7: "transverse", 8: "rotate_270",
    }
    try:
        return names.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def _depth_text(img):
    bits = img.info.get("bits")
    if bits:
        try:
            return f"{int(bits)} bit"
        except (TypeError, ValueError):
            pass
    # Pillow normalmente entrega 8 bits por canal para imagens raster usuais.
    band_count = len(img.getbands())
    per_channel = 1 if img.mode == "1" else 8
    return f"{per_channel * band_count} bit ({img.mode})"


def _icc_text(raw):
    if not raw:
        return None
    description = None
    if ImageCms is not None:
        try:
            profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
            description = ImageCms.getProfileDescription(profile).strip()
        except Exception:
            description = None
    size_kb = len(raw) / 1024.0
    if description:
        return f"{description} ({size_kb:.1f} KB)"
    return f"{size_kb:.1f} KB"


def _open_source_image(archive, index):
    """Abre a fonte original quando possível para não perder EXIF/ICC no decode."""
    suffix = archive.page_suffix(index) if hasattr(archive, "page_suffix") else ""
    if suffix in (".webm", ".svg", ".svgz"):
        return archive.load_image(index, max_dim=None)
    if archive.kind == "image":
        return Image.open(archive.path)
    if archive.kind == "dir":
        return Image.open(archive.path / archive.pages[index])
    if archive.kind == "pdf":
        return archive.load_image(index, max_dim=None)
    return Image.open(io.BytesIO(archive.read_bytes(index)))


def extract_image_metadata(archive, index=0):
    """Retorna lista de pares ``(chave_de_tradução, valor)`` da página atual."""
    if archive is None or archive.is_text_page(index):
        return []
    try:
        img = _open_source_image(archive, index)
    except Exception:
        return []

    should_close = hasattr(img, "close")
    try:
        result = [
            ("image_meta.dimensions", f"{img.width} × {img.height} px"),
            ("image_meta.mode", str(img.mode)),
            ("image_meta.depth", _depth_text(img)),
        ]
        dpi = img.info.get("dpi")
        if dpi and isinstance(dpi, (tuple, list)) and len(dpi) >= 2:
            try:
                result.append(("image_meta.dpi", f"{float(dpi[0]):g} × {float(dpi[1]):g}"))
            except (TypeError, ValueError):
                pass
        fmt = getattr(img, "format", None)
        if fmt:
            result.append(("image_meta.format", str(fmt)))

        icc = _icc_text(img.info.get("icc_profile"))
        if icc:
            result.append(("image_meta.icc", icc))

        try:
            exif = img.getexif()
        except Exception:
            exif = {}
        named = {ExifTags.TAGS.get(tag, tag): value for tag, value in exif.items()}

        fields = [
            ("image_meta.date", "DateTimeOriginal", None),
            ("image_meta.camera_make", "Make", None),
            ("image_meta.camera_model", "Model", None),
            ("image_meta.lens", "LensModel", None),
            ("image_meta.iso", "ISOSpeedRatings", None),
            ("image_meta.aperture", "FNumber", _fnumber_text),
            ("image_meta.exposure", "ExposureTime", _fraction_text),
            ("image_meta.orientation", "Orientation", _orientation_text),
        ]
        for tr_key, exif_key, formatter in fields:
            value = named.get(exif_key)
            if value in (None, ""):
                continue
            if formatter:
                value = formatter(value)
            result.append((tr_key, str(value)))
        return result
    finally:
        if should_close:
            try:
                img.close()
            except Exception:
                pass
