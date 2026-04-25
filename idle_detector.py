"""Windows idle detection via GetLastInputInfo."""
import ctypes
from ctypes import wintypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


def seconds_since_last_input() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis_now = _kernel32.GetTickCount()
    return max(0.0, (millis_now - info.dwTime) / 1000.0)
