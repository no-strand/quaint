"""Small Windows-only helpers for the native DWM window chrome.

Qt correctly fills the client area, but Windows 11 may still draw a one-pixel
DWM border around a maximized/fullscreen top-level window.  Quaint keeps the
native title bar in normal/maximized mode, so we only tune that *visual* DWM
border instead of replacing the whole non-client frame.
"""
from __future__ import annotations

import ctypes
import sys

# Windows 11 DWM attributes.  Older Windows builds simply reject these calls;
# the helper intentionally treats that as a harmless no-op.
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def colorref(r: int, g: int, b: int) -> int:
    """Return a Win32 COLORREF (0x00BBGGRR) from RGB components."""
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return r | (g << 8) | (b << 16)


QUAINT_FRAME_COLOR = colorref(23, 23, 28)  # #17171c, same as QMainWindow.


def apply_edge_chrome(hwnd: int, edge_to_edge: bool) -> bool:
    """Make the Windows 11 1px outer frame visually disappear when enlarged.

    ``edge_to_edge=True`` is used for maximized/fullscreen windows.  We keep
    the normal Win32 window styles intact (therefore the title bar, snap,
    resize and F11 behaviour remain native) and only ask DWM to paint its
    one-pixel border with Quaint's own background colour.  We also disable
    rounded corners so corner pixels cannot reveal the desktop.

    Returns True when both DWM attributes were accepted.  On non-Windows or
    older systems this is deliberately a no-op and returns False.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        dwmapi = ctypes.windll.dwmapi
        border_value = ctypes.c_uint32(
            QUAINT_FRAME_COLOR if edge_to_edge else DWMWA_COLOR_DEFAULT
        )
        corner_value = ctypes.c_int(
            DWMWCP_DONOTROUND if edge_to_edge else DWMWCP_DEFAULT
        )
        hr_border = int(
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(int(hwnd)),
                DWMWA_BORDER_COLOR,
                ctypes.byref(border_value),
                ctypes.sizeof(border_value),
            )
        )
        hr_corner = int(
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(int(hwnd)),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner_value),
                ctypes.sizeof(corner_value),
            )
        )
        return hr_border == 0 and hr_corner == 0
    except Exception:
        return False
