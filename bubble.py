"""Pixel-art speech bubble that floats above a SpriteWidget.

Everything is rendered with a hand-rolled 5x5 pixel font so the bubble
stays crisp at any scale — no anti-aliased system font sneaking in.

Lifecycle:
    bubble = SpeechBubble(cat_widget)   # constructed once
    bubble.say("meow")                  # shows for ~1.8s, auto-hides
    bubble.update_position()            # call each frame so it tracks the cat
    bubble.hide()                       # force-hide (e.g. on flee)

The bubble is its own click-through, always-on-top, transparent window —
same flags as SpriteWidget — so it doesn't catch input or steal focus.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap, QShowEvent
from PyQt6.QtWidgets import QLabel, QWidget

from character import _disable_dwm_border, SpriteWidget


# ---- 5x5 pixel font -----------------------------------------------------
# Hand-drawn so the look is consistent and tiny. Each glyph is exactly 5px
# wide and 5px tall; '#' = ink pixel, anything else = transparent.

GLYPH_W = 5
GLYPH_H = 5
GLYPH_SPACING = 1   # px between glyphs at native resolution

GLYPHS: dict[str, tuple[str, ...]] = {
    " ": (
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
    ),
    "b": (
        "####.",
        "#...#",
        "####.",
        "#...#",
        "####.",
    ),
    "c": (
        ".####",
        "#....",
        "#....",
        "#....",
        ".####",
    ),
    "d": (
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "####.",
    ),
    "f": (
        "#####",
        "#....",
        "####.",
        "#....",
        "#....",
    ),
    "g": (
        ".####",
        "#....",
        "#..##",
        "#...#",
        ".####",
    ),
    "j": (
        ".####",
        "...#.",
        "...#.",
        "#..#.",
        ".##..",
    ),
    "k": (
        "#...#",
        "#..#.",
        "###..",
        "#..#.",
        "#...#",
    ),
    "l": (
        "#....",
        "#....",
        "#....",
        "#....",
        "#####",
    ),
    "s": (
        ".####",
        "#....",
        ".###.",
        "....#",
        "####.",
    ),
    "v": (
        "#...#",
        "#...#",
        "#...#",
        ".#.#.",
        "..#..",
    ),
    "x": (
        "#...#",
        ".#.#.",
        "..#..",
        ".#.#.",
        "#...#",
    ),
    "m": (
        "#...#",
        "##.##",
        "#.#.#",
        "#...#",
        "#...#",
    ),
    "e": (
        "####.",
        "#....",
        "###..",
        "#....",
        "####.",
    ),
    "o": (
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "w": (
        "#...#",
        "#...#",
        "#.#.#",
        "##.##",
        "#...#",
    ),
    "r": (
        "####.",
        "#...#",
        "####.",
        "#.#..",
        "#..##",
    ),
    "u": (
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "p": (
        "####.",
        "#...#",
        "####.",
        "#....",
        "#....",
    ),
    "z": (
        "#####",
        "...#.",
        "..#..",
        ".#...",
        "#####",
    ),
    "h": (
        "#...#",
        "#...#",
        "#####",
        "#...#",
        "#...#",
    ),
    "t": (
        "#####",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
    "y": (
        "#...#",
        "#...#",
        ".###.",
        "..#..",
        "..#..",
    ),
    "i": (
        "#####",
        "..#..",
        "..#..",
        "..#..",
        "#####",
    ),
    "a": (
        ".###.",
        "#...#",
        "#####",
        "#...#",
        "#...#",
    ),
    "n": (
        "#...#",
        "##..#",
        "#.#.#",
        "#..##",
        "#...#",
    ),
    "?": (
        ".###.",
        "....#",
        "..##.",
        ".....",
        "..#..",
    ),
    "!": (
        "..#..",
        "..#..",
        "..#..",
        ".....",
        "..#..",
    ),
    ".": (
        ".....",
        ".....",
        ".....",
        ".....",
        "..#..",
    ),
    "<3": (   # multi-char "ligature" key — substituted when found in text
        ".#.#.",
        "#####",
        "#####",
        ".###.",
        "..#..",
    ),
}


def _measure(text: str) -> tuple[int, int]:
    n = len(text)
    if n == 0:
        return (0, 0)
    return (n * GLYPH_W + (n - 1) * GLYPH_SPACING, GLYPH_H)


def _draw_text(painter: QPainter, text: str, x: int, y: int, ink: QColor) -> None:
    cur_x = x
    for ch in text.lower():
        glyph = GLYPHS.get(ch) or GLYPHS["?"]
        for row, line in enumerate(glyph):
            for col, c in enumerate(line):
                if c == "#":
                    painter.fillRect(cur_x + col, y + row, 1, 1, ink)
        cur_x += GLYPH_W + GLYPH_SPACING


# ---- bubble rendering ---------------------------------------------------

INK = QColor(20, 20, 20)
PAPER = QColor(248, 246, 235)   # warm cream — reads better than pure white

PADDING_X = 3
PADDING_Y = 2
TAIL_HEIGHT = 3
TAIL_HALF_WIDTH = 2  # tail base = 2*half + 1 = 5px


def render_bubble(text: str, scale: int = 4, *, tail_left_bias: float = 0.5) -> QPixmap:
    """Render the bubble at native low resolution then nearest-neighbour
    scale. `tail_left_bias` 0..1 picks where the tail attaches (0 = far
    left, 1 = far right) so the tail points toward the cat's body when
    the bubble is offset sideways."""
    text_w, text_h = _measure(text)
    body_w = max(text_w + 2 * PADDING_X, TAIL_HALF_WIDTH * 2 + 3)
    body_h = text_h + 2 * PADDING_Y
    total_w = body_w
    total_h = body_h + TAIL_HEIGHT

    pix = QPixmap(total_w, total_h)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    # Body fill, then 1-px outline.
    p.fillRect(0, 0, body_w, body_h, PAPER)
    for x in range(body_w):
        p.fillRect(x, 0, 1, 1, INK)
        p.fillRect(x, body_h - 1, 1, 1, INK)
    for y in range(body_h):
        p.fillRect(0, y, 1, 1, INK)
        p.fillRect(body_w - 1, y, 1, 1, INK)

    # Tail: filled triangle pointing down with a 1-px outline on each slope.
    tail_cx = max(
        TAIL_HALF_WIDTH + 1,
        min(body_w - TAIL_HALF_WIDTH - 2, int(round(body_w * tail_left_bias))),
    )
    for i in range(TAIL_HEIGHT):
        y = body_h - 1 + i  # overlap by 1 with body to hide the bottom edge
        half = TAIL_HALF_WIDTH - i
        if half < 0:
            half = 0
        for xx in range(tail_cx - half, tail_cx + half + 1):
            if 0 <= xx < total_w:
                p.fillRect(xx, y, 1, 1, PAPER)
        # Re-paint the slopes with ink for a clean outline.
        if 0 <= tail_cx - half < total_w:
            p.fillRect(tail_cx - half, y, 1, 1, INK)
        if 0 <= tail_cx + half < total_w:
            p.fillRect(tail_cx + half, y, 1, 1, INK)

    # The body's bottom edge had an outline drawn earlier, but we want the
    # tail base to merge cleanly. Re-paint the body's bottom row WHITE in the
    # tail-base span, leaving only the slope endpoints inked.
    base_left = tail_cx - TAIL_HALF_WIDTH + 1
    base_right = tail_cx + TAIL_HALF_WIDTH - 1
    for xx in range(base_left, base_right + 1):
        if 0 <= xx < body_w:
            p.fillRect(xx, body_h - 1, 1, 1, PAPER)

    # Text.
    _draw_text(p, text, PADDING_X, PADDING_Y, INK)
    p.end()

    if scale != 1:
        pix = pix.scaled(
            total_w * scale, total_h * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    return pix


# ---- widget -------------------------------------------------------------

class SpeechBubble(QWidget):
    """Transparent always-on-top window that mirrors an anchor SpriteWidget."""

    def __init__(self, anchor: SpriteWidget) -> None:
        super().__init__()
        self._anchor = anchor
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

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._dwm_applied = False

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._dwm_applied:
            _disable_dwm_border(int(self.winId()))
            self._dwm_applied = True

    def say(self, text: str, scale: int = 4, duration_ms: int = 1800,
            *, facing_left: bool = False) -> None:
        """Show the bubble with `text` for `duration_ms` ms. Replaces any
        in-flight bubble instantly."""
        # Tail points toward the cat's body — bias depends on which side the
        # bubble is rendered relative to the head. Centre is fine for now.
        bias = 0.5
        pix = render_bubble(text, scale=scale, tail_left_bias=bias)
        self._label.setPixmap(pix)
        self._label.resize(pix.size())
        self.resize(pix.size())
        self.update_position()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def update_position(self) -> None:
        """Reposition above the anchor; auto-hide if the anchor is gone."""
        if not self._anchor.isVisible():
            self.hide()
            return
        ax = self._anchor.x()
        ay = self._anchor.y()
        bx = ax + (self._anchor.width() - self.width()) // 2
        by = ay - self.height() - 4
        # Clamp to the primary screen so the bubble doesn't render off-screen
        # if the cat is at the very top edge.
        if by < 0:
            by = ay + self._anchor.height() + 4
        self.move(bx, by)


# ---- handy line picker --------------------------------------------------

LINES = {
    "enter":   ["meow", "mew", "meow.", "..."],
    "walk":    ["mew", "mrr"],
    "sit":     ["?", "hmm", "mrr"],
    "lie":     ["zzz", "purr", "..."],
    "land":    ["!", "tada"],
    "prep":    ["nya", "hup"],
    "happy":   ["<3", "purr"],
}


def line_for(mood: str) -> str:
    pool = LINES.get(mood) or LINES["walk"]
    return random.choice(pool)
