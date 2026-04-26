"""Single character actor — a transparent always-on-top window that renders sprites.

The widget is frameless, click-through, and translucent. On Windows 11 the DWM
still paints a thin border around even frameless windows (Mica-style),
so we explicitly disable non-client rendering and clear the border colour
right after the native window handle exists.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QShowEvent
from PyQt6.QtWidgets import QLabel, QWidget


# DwmSetWindowAttribute IDs.
_DWMWA_NCRENDERING_POLICY = 2
_DWMNCRP_DISABLED = 1
_DWMWA_BORDER_COLOR = 34
_DWMWA_COLOR_NONE = 0xFFFFFFFE
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1

if sys.platform == "win32":
    try:
        _dwmapi = ctypes.windll.dwmapi
        _dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        _dwmapi.DwmSetWindowAttribute.restype = ctypes.HRESULT
    except (AttributeError, OSError):
        _dwmapi = None
else:
    _dwmapi = None


def _disable_dwm_border(hwnd: int) -> None:
    """Kill the Win11 DWM border + rounded corners that survive Qt's
    FramelessWindowHint. Each attribute is silently ignored on Windows
    versions that don't know it (older Win10 builds don't have BORDER_COLOR
    or WINDOW_CORNER_PREFERENCE), so it's safe to call unconditionally on
    win32."""
    if _dwmapi is None:
        return
    # Disable non-client rendering — primary nuke for the 1-px chrome line.
    ncrp = ctypes.c_int(_DWMNCRP_DISABLED)
    _dwmapi.DwmSetWindowAttribute(
        hwnd, _DWMWA_NCRENDERING_POLICY,
        ctypes.byref(ncrp), ctypes.sizeof(ncrp),
    )
    # Force border colour to NONE (Win11 22000+ — the Mica/border-accent line).
    color = ctypes.c_uint32(_DWMWA_COLOR_NONE)
    _dwmapi.DwmSetWindowAttribute(
        hwnd, _DWMWA_BORDER_COLOR,
        ctypes.byref(color), ctypes.sizeof(color),
    )
    # Square corners — the rounded-corner aliasing also reads as a faint halo.
    corners = ctypes.c_int(_DWMWCP_DONOTROUND)
    _dwmapi.DwmSetWindowAttribute(
        hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(corners), ctypes.sizeof(corners),
    )


class SpriteWidget(QWidget):
    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # no taskbar icon
            | Qt.WindowType.WindowTransparentForInput  # click-through
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._label.setAutoFillBackground(False)
        self._dwm_applied = False
        self.set_pixmap(pixmap)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # winId() materialises the native HWND if it doesn't already exist.
        # We only need this once per widget — DWM attributes persist.
        if not self._dwm_applied:
            _disable_dwm_border(int(self.winId()))
            self._dwm_applied = True

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._label.setPixmap(pixmap)
        self._label.resize(pixmap.size())
        self.resize(pixmap.size())

    def move_to(self, x: int, y: int) -> None:
        # y is the *bottom* of the sprite — easier ground-anchoring math.
        self.move(x, y - self.height())
