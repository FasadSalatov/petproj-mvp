"""Global Win32 hotkey support via RegisterHotKey + Qt native event filter.

Usage:
    GlobalHotkey.register_all(app, [
        (("ctrl", "shift", "h"), my_callback),
    ])

The callback fires from Qt's main thread (the native event filter is
invoked synchronously by Qt's event dispatcher). On non-Windows
platforms the registration is a no-op.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter, QByteArray
from PyQt6.QtWidgets import QApplication


# --- Win32 constants -----------------------------------------------------

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "esc": 0x1B, "space": 0x20, "tab": 0x09, "enter": 0x0D,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
MOD_MAP = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "alt": MOD_ALT, "win": MOD_WIN,
}


def _vk_for(key: str) -> int:
    k = key.lower()
    if k in VK_MAP:
        return VK_MAP[k]
    if len(k) == 1:
        return ord(k.upper())
    raise ValueError(f"unknown key: {key!r}")


def _parse_combo(parts: tuple[str, ...]) -> tuple[int, int]:
    mods = 0
    vk = None
    for p in parts:
        m = MOD_MAP.get(p.lower())
        if m is not None:
            mods |= m
        else:
            if vk is not None:
                raise ValueError(f"only one non-modifier key per hotkey: {parts}")
            vk = _vk_for(p)
    if vk is None:
        raise ValueError(f"hotkey needs a key: {parts}")
    return mods | MOD_NOREPEAT, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Catches WM_HOTKEY and dispatches to the registered callback."""

    def __init__(self, mapping: dict[int, Callable[[], None]]) -> None:
        super().__init__()
        self._mapping = mapping

    def nativeEventFilter(self, eventType, message):  # type: ignore[override]
        try:
            if eventType not in (b"windows_generic_MSG", "windows_generic_MSG"):
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                cb = self._mapping.get(int(msg.wParam))
                if cb is not None:
                    cb()
                    return True, 0
        except Exception:
            # Native filters must never raise; swallow.
            return False, 0
        return False, 0


class GlobalHotkey:
    """Holds registrations for the lifetime of the app."""

    _instance: "GlobalHotkey | None" = None

    def __init__(self) -> None:
        if sys.platform != "win32":
            self._user32 = None
            self._filter = None
            self._mapping = {}
            return
        self._user32 = ctypes.windll.user32
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._mapping: dict[int, Callable[[], None]] = {}
        self._filter = _HotkeyFilter(self._mapping)
        QApplication.instance().installNativeEventFilter(self._filter)

    @classmethod
    def register_all(cls, app: QApplication,
                     bindings: list[tuple[tuple[str, ...], Callable[[], None]]]) -> "GlobalHotkey":
        if cls._instance is None:
            cls._instance = cls()
        inst = cls._instance
        if inst._user32 is None:
            return inst
        for combo, cb in bindings:
            mods, vk = _parse_combo(combo)
            hk_id = len(inst._mapping) + 1
            ok = inst._user32.RegisterHotKey(None, hk_id, mods, vk)
            if not ok:
                # Likely already taken — silently skip; most hotkeys are
                # niche enough that conflicts are rare.
                continue
            inst._mapping[hk_id] = cb
        return inst
