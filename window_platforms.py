"""Enumerate top-level windows and turn them into walkable segments.

A *segment* is a contiguous horizontal slice on the top edge of a window
that is not occluded by any window stacked above it. The cat can stand,
walk, and jump only on segments — not on the full top edge of a window
that's hidden behind another, and not on the slice that another window
crosses over.

Coordinates are absolute virtual-desktop pixels.

Filtering rules for windows:
    - skip invisible / minimized / cloaked
    - skip windows owned by our own process (the SpriteWidget instances)
    - skip tool/popup windows (WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE)
    - skip fullscreen-on-monitor windows
    - skip windows with degenerate size

DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) is preferred over
GetWindowRect because Aero adds an invisible drop-shadow margin to the
plain rect.

Z-order
-------
EnumWindows enumerates top-level windows from the front-most to the
back-most. We assign `z = 0` to the first window we keep, increasing
afterwards — so a smaller `z` means "rendered above". A front window F
occludes a target T's top edge at point (x, T.top) iff:
    F.z < T.z   AND   F.left <= x <= F.right   AND   F.top <= T.top <= F.bottom
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

_GetWindowLong = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = ctypes.c_ssize_t

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT


# --- public types --------------------------------------------------------

@dataclass(frozen=True)
class Window:
    """A visible top-level window in absolute virtual-desktop coords.

    `z` is the EnumWindows-derived stacking index: 0 = front-most,
    larger = further back. Used to compute occlusion.
    """
    hwnd: int
    left: int
    top: int
    right: int
    bottom: int
    z: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WalkSegment:
    """A walkable, non-occluded slice of a window's top edge.

    The cat can stand at any (x, `y`) where `x1` <= x <= `x2`. Multiple
    segments per source window are possible when other windows partially
    cover the top edge (e.g. a chat window covering the middle of a
    browser's title bar leaves a left and a right segment).
    """
    hwnd: int
    y: int
    x1: int
    x2: int
    z: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    def contains_x(self, x: int) -> bool:
        return self.x1 <= x <= self.x2


# --- helpers -------------------------------------------------------------

_OWN_PID = os.getpid()


def _frame_rect(hwnd: int) -> wintypes.RECT | None:
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
        return None
    if _pid_for(hwnd) in exclude_pids:
        return None
    rect = _frame_rect(hwnd)
    if rect is None:
        return None
    if rect.right - rect.left < min_width:
        return None
    if rect.bottom - rect.top < min_height:
        return None
    return rect


# --- public API ----------------------------------------------------------

def collect_windows(
    *,
    min_width: int = 120,
    min_height: int = 80,
    exclude_pids: set[int] | None = None,
    desktop_bounds: tuple[int, int, int, int] | None = None,
) -> list[Window]:
    """All "interesting" top-level windows, in front-to-back z-order.

    Windows that fully cover `desktop_bounds` (treat as fullscreen apps)
    are skipped — no point sitting on a full-screen YouTube tab.
    """
    pids = {_OWN_PID}
    if exclude_pids:
        pids |= set(exclude_pids)

    out: list[Window] = []
    z_counter = [0]

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        rect = _interesting(hwnd, min_width, min_height, pids)
        if rect is None:
            return True
        if desktop_bounds is not None:
            l, t, r, b = desktop_bounds
            if rect.left <= l and rect.top <= t and rect.right >= r and rect.bottom >= b:
                return True
        out.append(Window(
            hwnd=int(hwnd),
            left=int(rect.left), top=int(rect.top),
            right=int(rect.right), bottom=int(rect.bottom),
            z=z_counter[0],
        ))
        z_counter[0] += 1
        return True

    _user32.EnumWindows(_enum, 0)
    return out


def compute_segments(
    windows: list[Window],
    *,
    lane_bounds: tuple[int, int, int, int] | None = None,
    min_segment_width: int = 0,
) -> list[WalkSegment]:
    """For every window in `windows`, return the visible (non-occluded)
    intervals of its top edge.

    Segments narrower than `min_segment_width` are dropped — the cat
    needs room to stand. If `lane_bounds` is given, segments whose host
    window's top edge is outside the lane's vertical range are dropped,
    and segment x-extents are clipped to the lane's horizontal range.
    """
    out: list[WalkSegment] = []
    for w in windows:
        # Lane vertical filter: w.top must fall inside the lane's y range.
        if lane_bounds is not None:
            ll, lt, lr, lb = lane_bounds
            if not (lt <= w.top <= lb):
                continue
        else:
            ll = lr = None  # unused

        pieces = [(w.left, w.right)]
        # Subtract every front-of-w window whose body crosses w.top
        # along the x-axis where the windows overlap.
        for f in windows:
            if f.hwnd == w.hwnd:
                continue
            if f.z >= w.z:
                continue
            # f must vertically span w.top.
            if f.top > w.top or f.bottom < w.top:
                continue
            if f.right <= w.left or f.left >= w.right:
                continue
            new_pieces: list[tuple[int, int]] = []
            for (a, b) in pieces:
                if f.right <= a or f.left >= b:
                    new_pieces.append((a, b))
                    continue
                if f.left > a:
                    new_pieces.append((a, f.left))
                if f.right < b:
                    new_pieces.append((f.right, b))
            pieces = new_pieces
            if not pieces:
                break

        for (x1, x2) in pieces:
            if lane_bounds is not None:
                x1 = max(x1, ll)
                x2 = min(x2, lr)
            width = x2 - x1
            if width < min_segment_width:
                continue
            out.append(WalkSegment(hwnd=w.hwnd, y=w.top, x1=x1, x2=x2, z=w.z))
    return out


def find_segment_under(x: int, segments: list[WalkSegment], min_y: int,
                       min_width: int = 0) -> WalkSegment | None:
    """Highest segment whose horizontal extent contains `x` and whose y is
    at or below `min_y`. Used when the cat falls off and needs to know
    what catches it."""
    best: WalkSegment | None = None
    for s in segments:
        if s.width < min_width:
            continue
        if not s.contains_x(x):
            continue
        if s.y < min_y:
            continue
        if best is None or s.y < best.y:
            best = s
    return best
