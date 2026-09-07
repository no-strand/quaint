"""Integração simples com o papel de parede do Windows."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PIL import Image

from app.i18n import tr

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02


def wallpaper_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / "AppData" / "Local"
    folder = root / "Quaint"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "wallpaper.bmp"


def save_wallpaper_bitmap(image: Image.Image, path=None) -> Path:
    target = Path(path) if path is not None else wallpaper_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(target, format="BMP")
    return target


def set_windows_wallpaper(image: Image.Image) -> Path:
    if sys.platform != "win32":
        raise RuntimeError(tr("error.wallpaper_windows_only"))

    target = save_wallpaper_bitmap(image)
    flags = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, str(target), flags
    )
    if not ok:
        raise OSError(tr("error.wallpaper_failed"))
    return target
