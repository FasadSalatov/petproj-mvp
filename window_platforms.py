"""Enumerate top-level windows and expose their top edges as platforms.

The cat uses these as horizontal surfaces it can stand and jump on. We pull
the geometry straight from Win32 (via ctypes — no pywin32 dependency) so the
detection works the same way as `idle_detector.py`.

Coordinates are absolute virtual-desktop pixels, matching the same space
`Lane.full` uses, so platforms can be compared directly with lane bounds.

Filtering rules:
    - skip invisible / minimized / cloaked windows
    - skip windows owned by our own process (the SpriteWidget instances)
    - skip tool/popup windows (WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE)
    - skip fullscreen-on-monitor windows (no point sitting on a full-screen game)
    - skip windows with degenerate size

DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) is preferred over
GetWindowRect because Aero adds an invisible drop-shadow margin to GetWindowRect.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_dwmapi = ctypes.windll.dwmapi

# --- WinAPI constants ----------------------------------------------------

GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_VISIBLE = 0x10000000
WS_MINIMIZE = 0x20000000

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

# --- prototypes ----------------------------------------------------------

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

# GetWindowLongPtrW is 64-bit safe; fall back to GetWindowLongW on 32-bit.
_GetWindowLong = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = ctypes.c_ssize_t

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT


@dataclass(frozen=True)
class Platform:
    """A horizontal surface the cat can walk / land on.

    `y` is the top edge of the window in absolute virtual-desktop coords —
    that's where the cat's feet should rest. `x1`/`x2` are the left and
    right walkable extents (inclusive). `hwnd` lets callers re-check
    aliveness; `priority` is a tiebreaker (higher = preferred when overlap).
    """
    x1: int
    x2: int
    y: int
    hwnd: int
    priority: int = 0

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    def contains_x(self, x: int) -> bool:
        return self.x1 <= x <= self.x2


# --- helpers -------------------------------------------------------------

_OWN_PID = os.getpid()


def _frame_rect(hwnd: int) -> wintypes.RECT | None:
    """Return the *visual* window rect (no Aero shadow), or None on failure."""
    rect = wintypes.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if hr == 0:
        return rect
    if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect
    return None


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = _dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED,
        ctypes.byref(cloaked), ctypes.sizeof(cloaked),
    )
    return hr == 0 and cloaked.value != 0


def _pid_for(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _interesting(hwnd: int, min_width: int, min_height: int,
                 exclude_pids: set[int]) -> wintypes.RECT | None:
    """Return the window's frame rect if we should treat it as a platform."""
    if not _user32.IsWindowVisible(hwnd):
        return None
    if _user32.IsIconic(hwnd):
        return None
    if _is_cloaked(hwnd):
        return None

    style = _GetWindowLong(hwnd, GWL_STYLE)
    if not (style & WS_VISIBLE):
        return None
    if style & WS_MINIMIZE:
        return None

    ex_style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    if ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
        return None

    if _user32.GetWindowTextLengthW(hwnd) == 0:
        return None  # blank-titled top-level windows are usually invisible shells

    pid = _pid_for(hwnd)
    if pid in exclude_pids:
        return None

    rect = _frame_rect(hwnd)
    if rect is None:
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w < min_width or h < min_height:
        return None

    return rect


# --- public API ----------------------------------------------------------

def collect_platforms(
    *,
    min_width: int = 120,
    min_height: int = 80,
    exclude_pids: set[int] | None = None,
    desktop_bounds: tuple[int, int, int, int] | None = None,
) -> list[Platform]:
    """Return the top edges of all "interesting" top-level windows as platforms.

    Platforms are sorted by Z-order (front-most first), so front windows
    occlude rear ones in `visible_segments_for`. Windows that look fullscreen
    on a monitor inside `desktop_bounds` are skipped (no point sitting on a
    full-screen YouTube tab).
    """
    pids = {_OWN_PID}
    if exclude_pids:
        pids |= set(exclude_pids)

    out: list[Platform] = []
    z = [0]  # captured by the callback below

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        rect = _interesting(hwnd, min_width, min_height, pids)
        if rect is None:
            return True
        # Skip apparent fullscreen windows: anything that covers >= a known
        # monitor area entirely. Caller passes the union/per-monitor bounds.
        if desktop_bounds is not None:
            l, t, r, b = desktop_bounds
            if rect.left <= l and rect.top <= t and rect.right >= r and rect.bottom >= b:
                return True
        z[0] += 1
        out.append(Platform(
            x1=int(rect.left),
            x2=int(rect.right),
            y=int(rect.top),
            hwnd=int(hwnd),
            priority=-z[0],   # earlier in z-order = front-most = higher priority
        ))
        return True

    _user32.EnumWindows(_enum, 0)
    return out


def visible_segments_for(target: Platform, fronts: list[Platform]) -> list[tuple[int, int]]:
    """Return the parts of `target` not occluded by any window in `fronts`.

    `fronts` should be platforms with a higher Z-order (= rendered above
    `target`). Returns a list of (x1, x2) ranges that are walkable; an empty
    list means the platform is fully obscured.

    Front-window occlusion is approximated by treating any front window whose
    rect crosses `target.y` and overlaps horizontally as a wall — we cut its
    [x1, x2] out of `target.x1..x2`. Good enough for the cat: it never
    appears to walk *into* an overlapping window, and the segment list lines
    up with reality for the common left-of/right-of cases.
    """
    pieces = [(target.x1, target.x2)]
    for f in fronts:
        if f.hwnd == target.hwnd:
            continue
        # We only care about fronts whose body covers target.y. We don't have
        # the front rect's bottom here (Platform stores only the top y), so we
        # approximate: any front above (smaller y) doesn't occlude; any front
        # at the same y or below crosses the top edge and counts.
        if f.y > target.y:
            continue
        new_pieces = []
        for (a, b) in pieces:
            if f.x2 <= a or f.x1 >= b:
                new_pieces.append((a, b))
                continue
            if f.x1 > a:
                new_pieces.append((a, f.x1))
            if f.x2 < b:
                new_pieces.append((f.x2, b))
        pieces = new_pieces
        if not pieces:
            break
    return pieces


def find_platform_under(x: int, platforms: list[Platform], min_y: int) -> Platform | None:
    """Find the highest platform whose horizontal extent contains `x` and whose
    top is at or below `min_y` (closer to the floor). Used when the cat falls
    off the current platform and needs to know what catches it."""
    best: Platform | None = None
    for p in platforms:
        if not p.contains_x(x):
            continue
        if p.y < min_y:
            continue
        if best is None or p.y < best.y:
            best = p
    return best
