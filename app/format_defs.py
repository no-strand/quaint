"""Definições leves de formatos usadas no caminho de inicialização.

Este módulo deliberadamente não importa Pillow, Qt, PyMuPDF nem codecs.
Assim drag-and-drop e validação de caminhos não puxam o subsistema de decode
antes de o usuário abrir um arquivo.
"""
from __future__ import annotations

import re

IMG_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif", ".jxl",
    ".svg", ".svgz",
}
STANDALONE_IMAGE_EXTS = IMG_EXTS | {".webm"}
SUPPORTED_FILE_EXTS = {
    ".cbz", ".cbr", ".pdf", ".epub",
    ".zip", ".rar", ".7z", ".tar", ".tgz", ".tbz2", ".txz",
    *STANDALONE_IMAGE_EXTS,
}
CONTAINER_SUFFIXES = (
    ".zip", ".rar", ".7z", ".tar", ".tgz", ".tar.gz", ".tar.bz2",
    ".tbz2", ".tar.xz", ".txz",
)

_NATURAL_SPLIT_RE = re.compile(r"(\d+)")


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in _NATURAL_SPLIT_RE.split(str(value))
    ]


def is_container_path(path) -> bool:
    name = str(path).lower()
    return any(name.endswith(ext) for ext in CONTAINER_SUFFIXES)
