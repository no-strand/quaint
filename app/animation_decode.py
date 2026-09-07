"""Decodificação incremental de imagens/vídeos animados.

Este módulo não depende do Qt. A ideia é deliberadamente *streaming*: nunca
pré-carrega todos os quadros de GIF/WebP/APNG/TIFF ou WebM na memória. Isso é
importante para pastas grandes e para animações longas.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from app.i18n import tr

_IMAGEIO = ...

def _imageio_module():
    global _IMAGEIO
    if _IMAGEIO is ...:
        try:
            import imageio.v2 as module
        except Exception:
            module = None
        _IMAGEIO = module
    return _IMAGEIO

PIL_ANIMATION_EXTS = {".gif", ".webp", ".png", ".apng", ".tif", ".tiff"}
VIDEO_ANIMATION_EXTS = {".webm"}
ANIMATION_CANDIDATE_EXTS = PIL_ANIMATION_EXTS | VIDEO_ANIMATION_EXTS


def normalize_frame_delay_ms(value, default=100):
    """Normaliza delays defeituosos sem deixar uma animação consumir 100% CPU."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = float(default)
    if value <= 0:
        value = float(default)
    return int(max(20, min(10_000, round(value))))


def prepare_animation_frame(frame: Image.Image, max_dim=None) -> Image.Image:
    """Converte um quadro em RGB e reduz antes de entregá-lo à interface.

    BILINEAR é proposital aqui: em animação a prioridade é manter FPS e baixo
    consumo. A página estática/primeiro quadro continua usando LANCZOS no
    caminho normal do leitor.
    """
    try:
        orientation = frame.getexif().get(274, 1)
    except Exception:
        orientation = 1
    if orientation not in (None, 1):
        frame = ImageOps.exif_transpose(frame)
    if frame.mode != "RGB":
        frame = frame.convert("RGB")
    else:
        frame = frame.copy()
    if max_dim and max(frame.size) > int(max_dim):
        frame.thumbnail(
            (int(max_dim), int(max_dim)),
            Image.Resampling.BILINEAR,
            reducing_gap=2.0,
        )
    return frame


def pillow_frame_count(source) -> int:
    """Retorna o número de quadros sem decodificar todos para RGB."""
    with Image.open(source) as img:
        try:
            return int(getattr(img, "n_frames", 1) or 1)
        except Exception:
            return 1


def iter_pillow_frames(source, max_dim=None):
    """Itera uma volta da animação como ``(PIL.Image, delay_ms)``.

    ``source`` pode ser caminho, BytesIO ou qualquer objeto aceito por
    ``PIL.Image.open``. O chamador é responsável por repetir a função em loop.
    """
    with Image.open(source) as img:
        try:
            total = int(getattr(img, "n_frames", 1) or 1)
        except Exception:
            total = 1
        if total <= 1:
            return
        for frame_index in range(total):
            img.seek(frame_index)
            delay = normalize_frame_delay_ms(img.info.get("duration", 100))
            # Em GIF/APNG modernos o Pillow entrega o quadro já composto ao
            # converter depois do seek, respeitando disposal/blend.
            frame = prepare_animation_frame(img.convert("RGBA"), max_dim)
            yield frame, delay


def webm_fps(path) -> float:
    imageio = _imageio_module()
    if imageio is None:
        raise RuntimeError(tr("error.animation_backend"))
    reader = imageio.get_reader(str(path))
    try:
        meta = reader.get_meta_data() or {}
        fps = float(meta.get("fps") or 24.0)
        return max(1.0, min(60.0, fps))
    finally:
        reader.close()


def iter_webm_frames(path, max_dim=None):
    """Itera uma volta do WebM sem manter quadros anteriores em memória."""
    imageio = _imageio_module()
    if imageio is None:
        raise RuntimeError(tr("error.animation_backend"))
    reader = imageio.get_reader(str(path))
    try:
        meta = reader.get_meta_data() or {}
        fps = float(meta.get("fps") or 24.0)
        fps = max(1.0, min(60.0, fps))
        delay = normalize_frame_delay_ms(1000.0 / fps, default=42)
        for array in reader:
            frame = Image.fromarray(array)
            frame = prepare_animation_frame(frame, max_dim)
            yield frame, delay
    finally:
        reader.close()


def source_from_bytes(data: bytes):
    return io.BytesIO(data)


def suffix_of(value) -> str:
    return Path(str(value)).suffix.lower()
