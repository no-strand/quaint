"""Ajustes visuais puros aplicados às páginas/imagens do leitor.

Este módulo não depende do Qt para que a mesma transformação possa ser usada
na visualização, exportação e demais ferramentas do Quaint.
"""
from __future__ import annotations

class _LazyNumpy:
    """Importa NumPy somente quando um efeito realmente precisa dele."""
    __slots__ = ("_module",)

    def __init__(self):
        self._module = None

    def _load(self):
        module = self._module
        if module is None:
            import numpy as module
            self._module = module
        return module

    def __getattr__(self, name):
        return getattr(self._load(), name)

np = _LazyNumpy()
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

from app.adjustment_defs import (
    ADJUSTMENT_KEYS, FILTERS, DEFAULT_ADJUSTMENTS, NEUTRAL_ADJUSTMENTS,
    normalize_adjustments, adjustments_dict, normalize_filter_name,
)

def _clip01(arr):
    return np.clip(arr, 0.0, 1.0)


def _luma(arr):
    return arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722


def _from_float_rgb(arr) -> Image.Image:
    return Image.fromarray(np.rint(_clip01(arr) * 255.0).astype(np.uint8), mode="RGB")



def apply_image_filter(image: Image.Image, filter_name="none") -> Image.Image:
    """Aplica um filtro visual não destrutivo preservando o canal alfa."""
    name = normalize_filter_name(filter_name)
    if name == "none":
        return image.copy()
    alpha = image.getchannel("A").copy() if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    if name == "grayscale":
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    elif name == "sepia":
        # Matriz executada pelo Pillow em C: evita três arrays NumPy full-res.
        rgb = rgb.convert("RGB", (
            0.393, 0.769, 0.189, 0.0,
            0.349, 0.686, 0.168, 0.0,
            0.272, 0.534, 0.131, 0.0,
        ))
    elif name == "invert":
        rgb = ImageOps.invert(rgb)
    elif name == "vintage":
        rgb = ImageEnhance.Color(rgb).enhance(0.72)
        rgb = ImageEnhance.Contrast(rgb).enhance(0.92)
        r, g, b = rgb.split()
        r = r.point([min(255, i + 14) for i in range(256)])
        g = g.point([min(255, i + 6) for i in range(256)])
        b = b.point([max(0, i - 10) for i in range(256)])
        rgb = Image.merge("RGB", (r, g, b))
    elif name == "cool":
        r, g, b = rgb.split()
        r = r.point([max(0, i - 10) for i in range(256)])
        b = b.point([min(255, i + 16) for i in range(256)])
        rgb = Image.merge("RGB", (r, g, b))
    elif name == "sharpen":
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.4, percent=145, threshold=2))
    elif name == "soften":
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=1.1))
    if alpha is not None:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(alpha)
    return rgb


def _apply_hue_shift(img: Image.Image, hue_amount: float) -> Image.Image:
    amount = max(-1.0, min(1.0, float(hue_amount)))
    if abs(amount) <= 1e-6:
        return img
    hsv = img.convert("HSV")
    h, s, v = hsv.split()
    shift = int(round(amount * 128.0))
    h = h.point([((i + shift) & 255) for i in range(256)])
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def _gray_world_white_balance(img: Image.Image) -> Image.Image:
    # A média cromática não precisa examinar milhões de pixels. Uma amostra
    # BOX pequena é suficiente e evita um array float32 full-res só para obter
    # três números. A correção final usa LUTs do Pillow em C.
    rgb = img.convert("RGB")
    sample = rgb.copy()
    sample.thumbnail((160, 160), Image.Resampling.BOX)
    means = ImageStat.Stat(sample).mean[:3]
    target = sum(means) / 3.0
    scales = [max(0.65, min(1.55, target / max(mean, 0.025))) for mean in means]
    channels = rgb.split()
    corrected = []
    for channel, scale in zip(channels, scales):
        corrected.append(channel.point([min(255, int(round(i * scale))) for i in range(256)]))
    return Image.merge("RGB", tuple(corrected))


def _auto_contrast_luma(img: Image.Image, cutoff=0.5) -> Image.Image:
    ycbcr = img.convert("YCbCr")
    y, cb, cr = ycbcr.split()
    y = ImageOps.autocontrast(y, cutoff=cutoff)
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def _apply_vibrance(img: Image.Image, amount: float) -> Image.Image:
    """Saturação seletiva sem arrays full-res.

    Para valores positivos, a saturação HSV vira uma máscara: regiões já muito
    saturadas recebem menos reforço, preservando o comportamento de "vibrance"
    em vez de uma saturação global simples. Todo o trabalho por pixel fica nas
    rotinas nativas do Pillow, reduzindo bastante CPU/RAM em páginas grandes.
    """
    amount = max(-1.0, min(1.0, float(amount)))
    if abs(amount) <= 1e-6:
        return img
    if amount < 0:
        return ImageEnhance.Color(img).enhance(max(0.0, 1.0 + amount * 0.90))

    hsv = img.convert("HSV")
    saturation = hsv.getchannel("S")
    # Quanto menor a saturação original, maior o peso do reforço.
    mask_lut = [max(0, min(255, int(round((255 - i) * 0.92)))) for i in range(256)]
    mask = saturation.point(mask_lut)
    boosted = ImageEnhance.Color(img).enhance(1.0 + amount * 1.15)
    return Image.composite(boosted, img, mask)


def _apply_color_balance(img: Image.Image, shadows: float, midtones: float, highlights: float) -> Image.Image:
    values = [max(-1.0, min(1.0, float(v))) for v in (shadows, midtones, highlights)]
    if max(abs(v) for v in values) <= 1e-6:
        return img
    arr = np.asarray(img, dtype=np.float32) / 255.0
    lum = _luma(arr)
    shadow_mask = (1.0 - lum) ** 2.2
    highlight_mask = lum ** 2.2
    mid_mask = np.clip(1.0 - np.abs(lum - 0.5) * 2.0, 0.0, 1.0) ** 1.4
    delta = (values[0] * shadow_mask + values[1] * mid_mask + values[2] * highlight_mask) * 0.13
    arr[..., 0] += delta
    arr[..., 1] += delta * 0.025
    arr[..., 2] -= delta
    return _from_float_rgb(arr)


def _apply_tonal_curve(img: Image.Image, shadows: float, midtones: float, highlights: float) -> Image.Image:
    controls = [max(-1.0, min(1.0, float(v))) for v in (shadows, midtones, highlights)]
    if max(abs(v) for v in controls) <= 1e-6:
        return img
    xs = (0.0, 0.25, 0.50, 0.75, 1.0)
    ys = [
        0.0,
        0.25 + controls[0] * 0.18,
        0.50 + controls[1] * 0.22,
        0.75 + controls[2] * 0.18,
        1.0,
    ]
    ys = [max(0.0, min(1.0, value)) for value in ys]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1])
    ys[-1] = 1.0
    # Curva de 256 entradas: a operação full-res fica no Image.point() em C
    # em vez de np.interp sobre milhões de floats.
    lut = []
    segment = 0
    for raw in range(256):
        x = raw / 255.0
        while segment < 3 and x > xs[segment + 1]:
            segment += 1
        x0, x1 = xs[segment], xs[segment + 1]
        y0, y1 = ys[segment], ys[segment + 1]
        t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
        lut.append(max(0, min(255, int(round((y0 + (y1 - y0) * t) * 255.0)))))
    return img.point(lut * len(img.getbands()))


def _apply_clarity(img: Image.Image, amount: float) -> Image.Image:
    """Contraste local usando filtros nativos do Pillow.

    O caminho anterior criava dois arrays float32 full-res. UnsharpMask produz
    um reforço local muito semelhante para clareza positiva, enquanto valores
    negativos misturam um blur suave. Ambos permanecem em código nativo.
    """
    amount = max(-1.0, min(1.0, float(amount)))
    if abs(amount) <= 1e-6:
        return img
    if amount > 0:
        percent = max(1, int(round(amount * 115.0)))
        detailed = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=percent, threshold=2))
        # Um toque de contraste de meio-tom garante efeito também em gradientes
        # suaves, onde o UnsharpMask puro pode não alterar nenhum pixel.
        return ImageEnhance.Contrast(detailed).enhance(1.0 + amount * 0.10)
    strength = min(0.70, abs(amount) * 0.62)
    softened = img.filter(ImageFilter.GaussianBlur(radius=1.4 + abs(amount) * 1.6))
    return Image.blend(img, softened, strength)


def _apply_dehaze(img: Image.Image, amount: float) -> Image.Image:
    """Aproximação de dehaze inteiramente nas primitivas C do Pillow."""
    amount = max(-1.0, min(1.0, float(amount)))
    if abs(amount) <= 1e-6:
        return img
    out = ImageEnhance.Contrast(img).enhance(max(0.10, 1.0 + amount * 0.85))
    # Dehaze positivo abaixa levemente o ponto médio; negativo cria névoa.
    out = ImageEnhance.Brightness(out).enhance(max(0.20, 1.0 - amount * 0.035))
    out = ImageEnhance.Color(out).enhance(max(0.0, 1.0 + amount * 0.22))
    return out


def _apply_scan_cleanup(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    gray = ImageOps.autocontrast(gray, cutoff=1.0)
    # A transformação depende somente do tom de cinza, então uma LUT de 256
    # valores substitui arrays float32 e três canais temporários.
    lut = []
    for raw in range(256):
        value = raw / 255.0
        bright = max(0.0, min(1.0, (value - 0.52) / 0.48))
        dark = max(0.0, min(1.0, (0.48 - value) / 0.48))
        value += bright * (1.0 - value) * 0.32
        value -= dark * value * 0.24
        lut.append(max(0, min(255, int(round(value * 255.0)))))
    return gray.point(lut).convert("RGB")


def apply_image_effects(image: Image.Image, adjustments=None, filter_name="none") -> Image.Image:
    """Aplica filtros e ajustes não destrutivos preservando o canal alfa.

    O caminho neutro é intencionalmente O(cópia) e não importa NumPy. Isso
    importa para Crop/Compare/Salvar alterações quando o usuário ainda não
    mexeu em nenhum controle: não há motivo para converter milhões de pixels
    para float só para obter a mesma imagem.
    """
    normalized = normalize_adjustments(adjustments)
    normalized_filter = normalize_filter_name(filter_name)
    if normalized == NEUTRAL_ADJUSTMENTS and normalized_filter == "none":
        return image.copy()

    values = dict(zip(ADJUSTMENT_KEYS, normalized))
    # Sem filtro, não faça uma cópia full-res apenas para logo convertê-la em
    # RGB. apply_image_filter() continua não destrutivo para chamadas externas.
    if normalized_filter != "none":
        image = apply_image_filter(image, normalized_filter)
    alpha = image.getchannel("A").copy() if "A" in image.getbands() else None
    img = image.convert("RGB")

    # ------------------------------- Automáticos de base
    if values["auto_white_balance"] >= 0.5 or values["auto_enhance"] >= 0.5:
        img = _gray_world_white_balance(img)
    if values["auto_levels"] >= 0.5:
        # Auto levels atua por canal.
        img = ImageOps.autocontrast(img, cutoff=0.5)
    if values["auto_contrast"] >= 0.5 or values["auto_enhance"] >= 0.5:
        # Auto contrast atua só na luminância e preserva melhor a cor.
        img = _auto_contrast_luma(img, cutoff=0.5)
    if values["auto_enhance"] >= 0.5:
        img = _apply_vibrance(img, 0.16)
        img = _apply_clarity(img, 0.12)

    # ------------------------------- Luz / faixa tonal
    gamma = max(0.20, min(3.00, values["gamma"]))
    if abs(gamma - 1.0) > 1e-6:
        inv_gamma = 1.0 / gamma
        lut = [max(0, min(255, int(round(((i / 255.0) ** inv_gamma) * 255.0)))) for i in range(256)]
        img = img.point(lut * len(img.getbands()))

    exposure = max(-1.0, min(1.0, values["exposure"]))
    if abs(exposure) > 1e-6:
        img = ImageEnhance.Brightness(img).enhance(2.0 ** exposure)

    brightness = max(0.0, values["brightness"])
    contrast = max(0.0, values["contrast"])
    saturation = max(0.0, values["saturation"])
    if abs(brightness - 1.0) > 1e-6:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    blacks = max(-1.0, min(1.0, values["blacks"]))
    shadows = max(-1.0, min(1.0, values["shadows"]))
    highlights = max(-1.0, min(1.0, values["highlights"]))
    whites = max(-1.0, min(1.0, values["whites"]))
    warmth = max(-1.0, min(1.0, values["warmth"]))
    tint = max(-1.0, min(1.0, values["tint"]))
    if any(abs(v) > 1e-6 for v in (blacks, shadows, highlights, whites, warmth, tint)):
        arr = np.asarray(img, dtype=np.float32) / 255.0
        lum = _luma(arr)
        if abs(blacks) > 1e-6:
            mask = (1.0 - lum) ** 4
            arr = _clip01(arr + (blacks * 0.30 * mask)[..., None])
            lum = _luma(arr)
        if abs(shadows) > 1e-6:
            mask = (1.0 - lum) ** 2
            arr = _clip01(arr + (shadows * 0.34 * mask)[..., None])
            lum = _luma(arr)
        if abs(highlights) > 1e-6:
            mask = lum ** 2
            arr = _clip01(arr + (highlights * 0.30 * mask)[..., None])
            lum = _luma(arr)
        if abs(whites) > 1e-6:
            mask = lum ** 4
            arr = _clip01(arr + (whites * 0.26 * mask)[..., None])
        if abs(warmth) > 1e-6:
            arr[..., 0] += 0.12 * warmth
            arr[..., 1] += 0.025 * warmth
            arr[..., 2] -= 0.12 * warmth
        if abs(tint) > 1e-6:
            arr[..., 0] += 0.075 * tint
            arr[..., 1] -= 0.10 * tint
            arr[..., 2] += 0.075 * tint
        img = _from_float_rgb(arr)

    # ------------------------------- Cor
    if abs(saturation - 1.0) > 1e-6:
        img = ImageEnhance.Color(img).enhance(saturation)
    img = _apply_vibrance(img, values["vibrance"])

    hue = max(-1.0, min(1.0, values["hue"]))
    if abs(hue) > 1e-6:
        img = _apply_hue_shift(img, hue)

    img = _apply_color_balance(
        img,
        values["color_balance_shadows"],
        values["color_balance_midtones"],
        values["color_balance_highlights"],
    )
    img = _apply_tonal_curve(
        img,
        values["curve_shadows"],
        values["curve_midtones"],
        values["curve_highlights"],
    )

    # ------------------------------- Detalhes
    img = _apply_dehaze(img, values["dehaze"])
    img = _apply_clarity(img, values["clarity"])

    noise = max(0.0, min(1.0, values["noise_reduction"]))
    if noise > 1e-6:
        filtered = img.filter(ImageFilter.MedianFilter(size=3 if noise < 0.72 else 5))
        img = Image.blend(img, filtered, min(0.92, noise * 0.90))

    moire = max(0.0, min(1.0, values["moire_reduction"]))
    if moire > 1e-6:
        softened = img.filter(ImageFilter.GaussianBlur(radius=0.45 + 1.25 * moire))
        img = Image.blend(img, softened, 0.18 + moire * 0.55)

    blur = max(0.0, min(1.0, values["blur"]))
    if blur > 1e-6:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur * 7.0))

    sharpness = max(0.0, min(2.0, values["sharpness"]))
    if sharpness > 1e-6:
        percent = int(round(40 + sharpness * 120))
        radius = 0.9 + sharpness * 0.35
        img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))

    # ------------------------------- Scan
    if values["scan_cleanup"] >= 0.5:
        img = _apply_scan_cleanup(img)

    whiten = max(0.0, min(1.0, values["whiten_background"]))
    darken = max(0.0, min(1.0, values["darken_lines"]))
    if whiten > 1e-6 or darken > 1e-6:
        arr = np.asarray(img, dtype=np.float32) / 255.0
        lum = _luma(arr)
        if whiten > 1e-6:
            mask = np.clip((lum - 0.48) / 0.52, 0.0, 1.0) ** 1.5
            arr += (1.0 - arr) * (mask * whiten * 0.72)[..., None]
        if darken > 1e-6:
            lum = _luma(_clip01(arr))
            mask = np.clip((0.62 - lum) / 0.62, 0.0, 1.0) ** 1.4
            arr -= arr * (mask * darken * 0.60)[..., None]
        img = _from_float_rgb(arr)

    # ------------------------------- Efeitos finais
    fade = max(0.0, min(1.0, values["fade"]))
    if fade > 1e-6:
        # Equivalente à fórmula anterior, mas o blend é executado em C e não
        # cria um array float32 de três canais.
        img = Image.blend(img, Image.new("RGB", img.size, (135, 135, 135)), 0.18 * fade)

    vignette = max(0.0, min(1.0, values["vignette"]))
    if vignette > 1e-6:
        arr = np.asarray(img, dtype=np.float32) / 255.0
        h, w = arr.shape[:2]
        yy, xx = np.ogrid[-1.0:1.0:complex(0, h), -1.0:1.0:complex(0, w)]
        radius = np.sqrt(xx * xx + yy * yy) / np.sqrt(2.0)
        edge = np.clip((radius - 0.30) / 0.70, 0.0, 1.0) ** 1.7
        factor = 1.0 - vignette * 0.72 * edge
        img = _from_float_rgb(arr * factor[..., None])

    grain = max(0.0, min(1.0, values["grain"]))
    if grain > 1e-6:
        arr = np.asarray(img, dtype=np.float32) / 255.0
        h, w = arr.shape[:2]
        seed = ((w * 73856093) ^ (h * 19349663)) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        noise_map = rng.normal(0.0, 0.055 * grain, size=(h, w, 1)).astype(np.float32)
        img = _from_float_rgb(arr + noise_map)

    if values["threshold_enabled"] >= 0.5:
        level = max(0.0, min(1.0, values["threshold_level"]))
        cutoff = int(round(level * 255.0))
        gray = ImageOps.grayscale(img)
        img = gray.point([0 if i < cutoff else 255 for i in range(256)]).convert("RGB")

    if alpha is not None:
        img = img.convert("RGBA")
        img.putalpha(alpha)
    return img


def rotate_image(image: Image.Image, clockwise_degrees: int) -> Image.Image:
    """Gira a imagem em múltiplos de 90 graus, positivo = para a direita."""
    deg = int(clockwise_degrees) % 360
    if deg == 0:
        return image.copy()
    if deg == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if deg == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if deg == 270:
        return image.transpose(Image.Transpose.ROTATE_90)
    return image.rotate(-deg, expand=True, resample=Image.Resampling.BICUBIC)


def flip_image(image: Image.Image, horizontal=False, vertical=False) -> Image.Image:
    """Espelha uma imagem sem alterar dimensões ou canal alfa."""
    out = image.copy()
    if horizontal:
        out = out.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if vertical:
        out = out.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return out
