"""Windows Shell thumbnail helpers.

For local video files on Windows, Quaint asks the same Shell thumbnail provider
used by File Explorer.  This gives the same representative frame Explorer
chooses instead of decoding an arbitrary frame ourselves.  The helper is kept
Windows-only and is called from background workers, so importing Quaint or
opening the thumbnail panel never starts a video decoder.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

_CACHE_LIMIT = 72
_CACHE: "OrderedDict[tuple, QImage]" = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _size_tuple(size: QSize | tuple[int, int]) -> tuple[int, int]:
    if isinstance(size, QSize):
        return max(1, int(size.width())), max(1, int(size.height()))
    return max(1, int(size[0])), max(1, int(size[1]))


def _cache_key(path: str | os.PathLike, width: int, height: int):
    p = Path(path)
    try:
        st = p.stat()
        stamp = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        file_size = int(st.st_size)
    except OSError:
        return None
    try:
        resolved = str(p.resolve())
    except OSError:
        resolved = str(p)
    return (resolved.casefold(), stamp, file_size, width, height)


def _cache_get(key):
    if key is None:
        return None
    with _CACHE_LOCK:
        image = _CACHE.get(key)
        if image is None:
            return None
        _CACHE.move_to_end(key)
        return image.copy()


def _cache_put(key, image: QImage):
    if key is None or image is None or image.isNull():
        return
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = image.copy()
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def explorer_thumbnail(path: str | os.PathLike, size: QSize | tuple[int, int]) -> QImage | None:
    """Return File Explorer's Shell thumbnail for *path*.

    ``None`` is returned outside Windows or when the installed Shell thumbnail
    provider cannot create a thumbnail for the file.  No FFmpeg/Pillow decode
    fallback is performed here: callers can keep their cheap video placeholder
    instead of doing expensive work during fast scrolling.
    """
    if not sys.platform.startswith("win"):
        return None

    width, height = _size_tuple(size)
    key = _cache_key(path, width, height)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    image = _explorer_thumbnail_windows(str(path), width, height)
    if image is not None and not image.isNull():
        _cache_put(key, image)
        return image
    return None


def clear_thumbnail_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _explorer_thumbnail_windows(path: str, width: int, height: int) -> QImage | None:
    # Everything Win32-specific stays inside this function so the module can be
    # imported by tests/non-Windows builds without touching ctypes.windll.
    from ctypes import wintypes

    HRESULT = ctypes.c_long
    ULONG = ctypes.c_ulong
    WINFUNCTYPE = ctypes.WINFUNCTYPE

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long),
            ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long),
            ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", ctypes.c_ubyte),
            ("rgbGreen", ctypes.c_ubyte),
            ("rgbRed", ctypes.c_ubyte),
            ("rgbReserved", ctypes.c_ubyte),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

    def guid(text: str) -> GUID:
        value = uuid.UUID(text)
        raw = value.bytes
        return GUID(
            int.from_bytes(raw[0:4], "big"),
            int.from_bytes(raw[4:6], "big"),
            int.from_bytes(raw[6:8], "big"),
            (ctypes.c_ubyte * 8)(*raw[8:16]),
        )

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    iid = guid("bcc18b79-ba16-442f-80c4-8a59c30c463b")  # IShellItemImageFactory
    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = HRESULT

    # COM is initialized per worker thread. RPC_E_CHANGED_MODE means another
    # component initialized the apartment differently; Shell calls may still be
    # used, but we must not balance it with CoUninitialize.
    COINIT_APARTMENTTHREADED = 0x2
    RPC_E_CHANGED_MODE = ctypes.c_long(0x80010106).value
    hr_init = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    should_uninit = hr_init in (0, 1)  # S_OK / S_FALSE
    if hr_init < 0 and hr_init != RPC_E_CHANGED_MODE:
        return None

    factory = ctypes.c_void_p()
    hbitmap = ctypes.c_void_p()
    try:
        hr = int(shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(iid), ctypes.byref(factory)))
        if hr < 0 or not factory.value:
            return None

        vtbl = ctypes.cast(factory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = WINFUNCTYPE(ULONG, ctypes.c_void_p)(vtbl[2])
        get_image = WINFUNCTYPE(
            HRESULT,
            ctypes.c_void_p,
            SIZE,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )(vtbl[3])

        # THUMBNAILONLY ensures the Shell's registered thumbnail provider is
        # used instead of a generic file icon. BIGGERSIZEOK mirrors Explorer's
        # preference for a cached large thumbnail when one is available.
        SIIGBF_BIGGERSIZEOK = 0x1
        SIIGBF_THUMBNAILONLY = 0x8
        flags = SIIGBF_BIGGERSIZEOK | SIIGBF_THUMBNAILONLY
        try:
            hr = int(get_image(factory, SIZE(width, height), flags, ctypes.byref(hbitmap)))
        finally:
            release(factory)
            factory = ctypes.c_void_p()
        if hr < 0 or not hbitmap.value:
            return None

        bitmap = BITMAP()
        gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        gdi32.GetObjectW.restype = ctypes.c_int
        if not gdi32.GetObjectW(hbitmap, ctypes.sizeof(BITMAP), ctypes.byref(bitmap)):
            return None
        bmp_w = abs(int(bitmap.bmWidth))
        bmp_h = abs(int(bitmap.bmHeight))
        if bmp_w <= 0 or bmp_h <= 0:
            return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = bmp_w
        # Negative height asks GDI for a top-down DIB, avoiding a second flip.
        bmi.bmiHeader.biHeight = -bmp_h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        byte_count = bmp_w * bmp_h * 4
        raw = bytearray(byte_count)
        raw_buffer = (ctypes.c_ubyte * byte_count).from_buffer(raw)

        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int

        hdc = user32.GetDC(None)
        try:
            rows = gdi32.GetDIBits(
                hdc,
                hbitmap,
                0,
                bmp_h,
                raw_buffer,
                ctypes.byref(bmi),
                0,  # DIB_RGB_COLORS
            )
        finally:
            if hdc:
                user32.ReleaseDC(None, hdc)
        if rows != bmp_h:
            return None

        # Some Shell providers return an XRGB bitmap with all alpha bytes zero.
        # In that case the thumbnail is opaque, not transparent.
        alpha = raw[3::4]
        if alpha and not any(alpha):
            raw[3::4] = b"\xff" * len(alpha)

        image = QImage(bytes(raw), bmp_w, bmp_h, bmp_w * 4, QImage.Format_ARGB32)
        return image.copy()
    except Exception:
        return None
    finally:
        if hbitmap.value:
            try:
                gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
                gdi32.DeleteObject.restype = wintypes.BOOL
                gdi32.DeleteObject(hbitmap)
            except Exception:
                pass
        if should_uninit:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
