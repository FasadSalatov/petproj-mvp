"""Pixel-art visual effects that follow or react to a SpriteWidget.

Long-lived effects:
    * Shadow — a hand-rolled pixel-art oval (drawn at low native res then
      scaled up nearest-neighbour) that always sits on the cat's current
      surface and shrinks/lightens when the cat is mid-air.
    * Sparkles — short-lived ★ widgets spawned on landings, each one flies
      a small parabola and fades.
    * Treat — a stationary biscuit dropped at a target point that the cat
      walks over to and "eats". Disappears on consume or expiry.

All effects are independent transparent always-on-top windows. They don't
catch input. Z-order trickery: every time we touch a top-most effect we
re-`raise_()` the cat so it stays visually above its own shadow.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap, QShowEvent
from PyQt6.QtWidgets import QLabel, QWidget

from character import _disable_dwm_border, SpriteWidget


# ---- pixmap builders ----------------------------------------------------

def _shadow_native(native_w: int, alpha_inner: int, alpha_outer: int) -> QPixmap:
    """Two-tone elliptical pixel shadow at NATIVE resolution.

    The inner 65% of the ellipse is `alpha_inner` (solid core), the rest
    fades to `alpha_outer`. Drawn pixel-by-pixel — no anti-aliasing.
    """
    h = max(3, native_w // 6)
    pix = QPixmap(native_w, h)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    cx = (native_w - 1) / 2.0
    cy = (h - 1) / 2.0
    rx = native_w / 2.0
    ry = h / 2.0
    inner_rx = rx * 0.65
    inner_ry = ry * 0.65
    inner_color = QColor(0, 0, 0, alpha_inner)
    outer_color = QColor(0, 0, 0, alpha_outer)
    for y in range(h):
        for x in range(native_w):
            dx_o = (x - cx) / rx
            dy_o = (y - cy) / ry
            if dx_o * dx_o + dy_o * dy_o > 1.0:
                continue
            dx_i = (x - cx) / max(inner_rx, 1.0)
            dy_i = (y - cy) / max(inner_ry, 1.0)
            if dx_i * dx_i + dy_i * dy_i <= 1.0:
                p.fillRect(x, y, 1, 1, inner_color)
            else:
                p.fillRect(x, y, 1, 1, outer_color)
    p.end()
    return pix


def _shadow_pixmap(target_w: int, scale: int,
                   alpha_inner: int, alpha_outer: int) -> QPixmap:
    """Render shadow at target_w pixels, but using a low-res native then
    nearest-neighbour scale-up so the result reads as pixel-art."""
    native = max(8, target_w // max(scale, 1))
    pix = _shadow_native(native, alpha_inner, alpha_outer)
    if scale != 1:
        pix = pix.scaled(
            pix.width() * scale, pix.height() * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    return pix


def _sparkle_pixmap(scale: int) -> QPixmap:
    pattern = (
        ".#.",
        "###",
        ".#.",
    )
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


def _treat_pixmap(scale: int) -> QPixmap:
    """A small fish-shaped pixel snack, brown and golden.

    Native art is 9x5; scaled by `scale`. Two-tone for a subtle 3D feel.
    """
    body = (
        ".###..#..",
        "#####.##.",
        "#####.###",
        "#####.##.",
        ".###..#..",
    )
    base_h = len(body)
    base_w = len(body[0])
    pix = QPixmap(base_w * scale, base_h * scale)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    body_color = QColor(180, 110, 60, 255)
    shade_color = QColor(120, 70, 30, 255)
    eye_color = QColor(20, 20, 20, 255)
    for r, line in enumerate(body):
        for c, ch in enumerate(line):
            if ch == ".":
                continue
            color = body_color
            # Shade a single pixel as the eye, and the bottom row as a
            # lighter underbelly highlight.
            if (r, c) == (1, 1):
                color = eye_color
            elif r == base_h - 1 and ch == "#":
                color = shade_color
            for sx in range(scale):
                for sy in range(scale):
                    p.fillRect(c * scale + sx, r * scale + sy, 1, 1, color)
    p.end()
    return pix


# ---- floating widget ----------------------------------------------------

class _FloatingSprite(QWidget):
    def __init__(self, pixmap: QPixmap, top_most: bool = True) -> None:
        super().__init__()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if top_most:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
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
    SPARKLE_LIFE_TICKS = 14
    SPARKLE_GRAVITY = 0.6
    SPARKLE_INITIAL_VY = (-5.0, -1.5)
    SPARKLE_INITIAL_VX = (-3.5, 3.5)

    SHADOW_BASE_ALPHA_INNER = 150
    SHADOW_BASE_ALPHA_OUTER = 80

    def __init__(self, cat: SpriteWidget) -> None:
        self.cat = cat
        self._shadow: _FloatingSprite | None = None
        self._shadow_scale: int = 1
        self._shadow_cache: dict[tuple[int, int, int, int], QPixmap] = {}
        self._sparkles: list[tuple[_FloatingSprite, int, float, float]] = []
        # Treat is owned here too — it's a static visible target.
        self._treat: _FloatingSprite | None = None
        self._treat_pos: tuple[int, int] | None = None

    # ---- shadow ------------------------------------------------------

    def _shadow_pix(self, target_w: int, scale: int,
                    alpha_inner: int, alpha_outer: int) -> QPixmap:
        key = (target_w, scale, alpha_inner, alpha_outer)
        cached = self._shadow_cache.get(key)
        if cached is not None:
            return cached
        pix = _shadow_pixmap(target_w, scale, alpha_inner, alpha_outer)
        self._shadow_cache[key] = pix
        return pix

    def configure_scale(self, scale_int: int) -> None:
        """Tell the shadow what cat-scale to render at. Call when scale
        changes via the Config dialog so the shadow tracks the cat size."""
        scale_int = max(1, scale_int)
        if scale_int == self._shadow_scale:
            return
        self._shadow_scale = scale_int
        self._shadow_cache.clear()

    def update_shadow(self, surface_y: int, *, height_above_surface: int = 0) -> None:
        if self._shadow is None:
            self._shadow_scale = max(1, self._shadow_scale)
            base_w = max(24, int(self.cat.width() * 0.65))
            pix = self._shadow_pix(
                base_w, self._shadow_scale,
                self.SHADOW_BASE_ALPHA_INNER, self.SHADOW_BASE_ALPHA_OUTER,
            )
            self._shadow = _FloatingSprite(pix, top_most=True)
        s = self._shadow
        # Shrink and lighten the shadow as the cat rises.
        base_w = max(24, int(self.cat.width() * 0.65))
        if height_above_surface > 24:
            f = max(0.35, 1.0 - height_above_surface / 800.0)
            target_w = max(20, int(base_w * f))
            # Quantise to multiples of (4 * scale) so we don't blow up the cache.
            step = max(4, 4 * self._shadow_scale)
            target_w = (target_w // step) * step + step
            ai = max(40, int(self.SHADOW_BASE_ALPHA_INNER * f))
            ao = max(20, int(self.SHADOW_BASE_ALPHA_OUTER * f))
        else:
            target_w = base_w
            ai = self.SHADOW_BASE_ALPHA_INNER
            ao = self.SHADOW_BASE_ALPHA_OUTER
        pix = self._shadow_pix(target_w, self._shadow_scale, ai, ao)
        if s.size() != pix.size():
            s.set_pixmap(pix)
        cat_center_x = self.cat.x() + self.cat.width() // 2
        s.move(
            cat_center_x - pix.width() // 2,
            surface_y - pix.height() // 2,
        )
        if not s.isVisible():
            s.show()
        # Both cat and shadow are top-most. Re-raise the cat so it stays
        # above its own shadow even after the shadow's first show().
        if self.cat.isVisible():
            self.cat.raise_()

    def hide_shadow(self) -> None:
        if self._shadow is not None:
            self._shadow.hide()

    # ---- sparkles ----------------------------------------------------

    def burst_sparkles(self, x: int, y: int, scale: int, n: int = 6) -> None:
        for _ in range(n):
            pix = _sparkle_pixmap(max(2, scale))
            sp = _FloatingSprite(pix, top_most=True)
            sp.move(x - sp.width() // 2, y - sp.height() // 2)
            sp.show()
            vx = random.uniform(*self.SPARKLE_INITIAL_VX)
            vy = random.uniform(*self.SPARKLE_INITIAL_VY)
            self._sparkles.append((sp, self.SPARKLE_LIFE_TICKS, vx, vy))

    # ---- treats ------------------------------------------------------

    def drop_treat(self, x: int, y: int, scale: int) -> tuple[int, int]:
        """Show a treat sprite centered horizontally at x, with bottom
        at y. Returns the world-coords (x, y) the cat should walk to."""
        self.clear_treat()
        pix = _treat_pixmap(max(2, scale))
        t = _FloatingSprite(pix, top_most=True)
        t.move(x - pix.width() // 2, y - pix.height())
        t.show()
        self._treat = t
        self._treat_pos = (x, y)
        return self._treat_pos

    def has_treat(self) -> bool:
        return self._treat is not None

    @property
    def treat_pos(self) -> tuple[int, int] | None:
        return self._treat_pos

    def clear_treat(self) -> None:
        if self._treat is not None:
            self._treat.hide()
            self._treat.deleteLater()
        self._treat = None
        self._treat_pos = None

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
        self.clear_treat()
