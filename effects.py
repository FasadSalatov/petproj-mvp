"""Pixel-art visual effects that follow or react to a SpriteWidget.

Two long-lived effects:
    * Shadow — an elliptical translucent blob that always sits on the cat's
      current surface and shrinks/lightens when the cat is in mid-air.
    * Sparkles — short-lived ★-shaped widgets spawned on landings, each one
      flies a small parabola and fades.

All effects are independent transparent always-on-top windows, like the
cat itself. They don't catch input.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap, QShowEvent
from PyQt6.QtWidgets import QLabel, QWidget

from character import _disable_dwm_border, SpriteWidget


# ---- pixmap builders ----------------------------------------------------

def _shadow_pixmap(width: int, alpha: int = 140) -> QPixmap:
    """Soft elliptical shadow rendered pixel-by-pixel — looks pixelated
    on purpose, no anti-aliasing. Returned pixmap is exactly `width` × h."""
    h = max(4, width // 6)
    pix = QPixmap(width, h)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    cx = (width - 1) / 2.0
    cy = (h - 1) / 2.0
    rx = width / 2.0
    ry = h / 2.0
    for y in range(h):
        for x in range(width):
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            d = dx * dx + dy * dy
            if d > 1.0:
                continue
            a = int(alpha * (1.0 - d))
            if a <= 0:
                continue
            p.fillRect(x, y, 1, 1, QColor(0, 0, 0, a))
    p.end()
    return pix


def _sparkle_pixmap(scale: int) -> QPixmap:
    """5-pixel cross sparkle, scaled up nearest-neighbour."""
    pattern = [
        ".#.",
        "###",
        ".#.",
    ]
    cells = len(pattern)
    pix = QPixmap(cells * scale, cells * scale)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    ink = QColor(255, 240, 150, 235)
    for r, line in enumerate(pattern):
        for c, ch in enumerate(line):
            if ch != "#":
                continue
            for sx in range(scale):
                for sy in range(scale):
                    p.fillRect(c * scale + sx, r * scale + sy, 1, 1, ink)
    p.end()
    return pix


# ---- floating widget ----------------------------------------------------

class _FloatingSprite(QWidget):
    """Generic transparent always-on-top widget for an effect."""

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)
        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._label.setAutoFillBackground(False)
        self._dwm_applied = False
        self.set_pixmap(pixmap)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._dwm_applied:
            _disable_dwm_border(int(self.winId()))
            self._dwm_applied = True

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._label.setPixmap(pixmap)
        self._label.resize(pixmap.size())
        self.resize(pixmap.size())


# ---- effects layer ------------------------------------------------------

class EffectsLayer:
    """Owns all decorative widgets attached to one cat. Driven by the cat's
    tick — call `tick()` each frame and use the helpers below at scene
    transitions."""

    SPARKLE_LIFE_TICKS = 14
    SPARKLE_GRAVITY = 0.6
    SPARKLE_INITIAL_VY = (-5.0, -1.5)
    SPARKLE_INITIAL_VX = (-3.5, 3.5)

    def __init__(self, cat: SpriteWidget) -> None:
        self.cat = cat
        self._shadow: _FloatingSprite | None = None
        self._shadow_cache: dict[int, QPixmap] = {}    # alpha → pixmap, keyed by alpha
        self._shadow_w: int = 0
        # active sparkles: (widget, ttl, vx, vy)
        self._sparkles: list[tuple[_FloatingSprite, int, float, float]] = []

    # ---- shadow ------------------------------------------------------

    def _shadow_pix_for(self, alpha: int) -> QPixmap:
        cached = self._shadow_cache.get(alpha)
        if cached is not None:
            return cached
        pix = _shadow_pixmap(self._shadow_w, alpha=alpha)
        self._shadow_cache[alpha] = pix
        return pix

    def ensure_shadow(self) -> None:
        if self._shadow is not None:
            return
        self._shadow_w = max(20, int(self.cat.width() * 0.7))
        pix = self._shadow_pix_for(140)
        self._shadow = _FloatingSprite(pix)

    def update_shadow(self, surface_y: int, *, height_above_surface: int = 0) -> None:
        """Place the shadow at (cat-center-x, surface_y) and shrink it
        when the cat is up in the air. surface_y is the y of the surface
        the cat would land on, NOT the cat's current y_bottom."""
        self.ensure_shadow()
        s = self._shadow
        assert s is not None
        # Shrink and lighten when high.
        if height_above_surface > 30:
            f = max(0.3, 1.0 - height_above_surface / 600.0)
            alpha = max(40, int(140 * f))
        else:
            alpha = 140
        pix = self._shadow_pix_for(alpha)
        if pix.size() != s.size():
            s.set_pixmap(pix)
        cat_center_x = self.cat.x() + self.cat.width() // 2
        s.move(
            cat_center_x - s.width() // 2,
            surface_y - s.height() // 2,
        )
        if not s.isVisible():
            s.show()

    def hide_shadow(self) -> None:
        if self._shadow is not None:
            self._shadow.hide()

    # ---- sparkles ----------------------------------------------------

    def burst_sparkles(self, x: int, y: int, scale: int, n: int = 6) -> None:
        for _ in range(n):
            pix = _sparkle_pixmap(max(2, scale))
            sp = _FloatingSprite(pix)
            sp.move(x - sp.width() // 2, y - sp.height() // 2)
            sp.show()
            vx = random.uniform(*self.SPARKLE_INITIAL_VX)
            vy = random.uniform(*self.SPARKLE_INITIAL_VY)
            self._sparkles.append((sp, self.SPARKLE_LIFE_TICKS, vx, vy))

    # ---- per-frame ---------------------------------------------------

    def tick(self) -> None:
        survivors: list[tuple[_FloatingSprite, int, float, float]] = []
        for sp, ttl, vx, vy in self._sparkles:
            ttl -= 1
            if ttl <= 0:
                sp.hide()
                sp.deleteLater()
                continue
            sp.move(sp.x() + int(vx), sp.y() + int(vy))
            vy += self.SPARKLE_GRAVITY
            survivors.append((sp, ttl, vx, vy))
        self._sparkles = survivors

    def hide_all(self) -> None:
        if self._shadow is not None:
            self._shadow.hide()
        for sp, *_ in self._sparkles:
            sp.hide()
            sp.deleteLater()
        self._sparkles.clear()
