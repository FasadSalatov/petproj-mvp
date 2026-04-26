"""Pixel-art Qt widgets for the petproj-mvp Config window.

Everything paints itself with QPainter at low native resolution then is
either scaled up via FastTransformation or composed at the device's
native pixel grid (depending on the widget). The 5×5 font from
`bubble.py` is reused so labels match the speech-bubble vocabulary.

Widgets exported:
    PixelLabel        — static pixel-font text
    PixelButton       — clickable, with depress animation
    PixelCheckbox     — empty box / X mark, label to the right
    PixelSlider       — horizontal track + knob + value caption
    PixelSpinBox      — integer / float spinner with +/- buttons
    PixelComboBox     — dropdown with pixel-styled popup
    PixelPanel        — bordered group with titled top edge
    PixelProgress     — bordered fill bar (used for hunger / pomodoro)
    PixelHRule        — single horizontal divider
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Iterable

from PyQt6.QtCore import (
    QPoint, QRect, QSize, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QMouseEvent, QPaintEvent, QPainter, QPalette, QPen, QPixmap,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
    QWidget,
)

from bubble import GLYPHS, GLYPH_H, GLYPH_SPACING, GLYPH_W

try:
    import sfx as _sfx
except ImportError:
    _sfx = None


# ---- theme (mutable in place so widgets see live updates) ---------------

LIGHT_THEME = dict(
    ink=QColor(20, 20, 20),
    paper=QColor(248, 246, 235),
    paper_hover=QColor(255, 252, 240),
    paper_pressed=QColor(228, 220, 195),
    accent=QColor(220, 130, 60),
    accent_dim=QColor(180, 100, 40),
    panel_bg=QColor(232, 226, 205),
    fill_good=QColor(120, 175, 90),
    fill_warn=QColor(220, 175, 70),
    fill_bad=QColor(210, 95, 70),
    window_bg=QColor(248, 246, 235),
)

DARK_THEME = dict(
    ink=QColor(232, 228, 215),
    paper=QColor(48, 44, 40),
    paper_hover=QColor(64, 58, 52),
    paper_pressed=QColor(36, 32, 28),
    accent=QColor(232, 142, 70),
    accent_dim=QColor(192, 110, 50),
    panel_bg=QColor(38, 34, 30),
    fill_good=QColor(120, 195, 100),
    fill_warn=QColor(232, 188, 80),
    fill_bad=QColor(232, 100, 80),
    window_bg=QColor(28, 25, 22),
)

# Mutating attributes of THEME (rather than reassigning) keeps every widget
# that imported this module's reference seeing the live values.
THEME = SimpleNamespace(**LIGHT_THEME)


def set_theme(name: str) -> None:
    """Swap the global palette in place and force every visible widget to
    repaint so the change is instant."""
    values = DARK_THEME if name == "dark" else LIGHT_THEME
    for k, v in values.items():
        setattr(THEME, k, v)
    app = QApplication.instance()
    if app is not None:
        for w in app.allWidgets():
            w.update()


def current_theme_name() -> str:
    return "dark" if THEME.paper == DARK_THEME["paper"] else "light"


DEFAULT_SCALE = 3        # one native pixel = 3 device pixels


def _click() -> None:
    if _sfx is not None:
        _sfx.play("click")


def _hover_sfx() -> None:
    if _sfx is not None:
        _sfx.play("hover")


def _toggle_sfx(state: bool) -> None:
    if _sfx is not None:
        _sfx.play("toggle_on" if state else "toggle_off")


def _slider_sfx() -> None:
    if _sfx is not None:
        _sfx.play("slider")


# ---- font helpers -------------------------------------------------------

def measure_text(text: str, scale: int) -> QSize:
    n = len(text)
    if n == 0:
        return QSize(0, GLYPH_H * scale)
    w = (n * GLYPH_W + (n - 1) * GLYPH_SPACING) * scale
    h = GLYPH_H * scale
    return QSize(w, h)


def draw_text(p: QPainter, text: str, x: int, y: int,
              scale: int, color: QColor) -> int:
    """Paint `text` at (x, y) with the 5×5 pixel font scaled by `scale`.
    Returns the right-edge x of the rendered text (useful for chained drawing)."""
    cur_x = x
    for ch in text.lower():
        glyph = GLYPHS.get(ch) or GLYPHS["?"]
        for r, line in enumerate(glyph):
            for c, mark in enumerate(line):
                if mark != "#":
                    continue
                p.fillRect(
                    cur_x + c * scale, y + r * scale, scale, scale, color,
                )
        cur_x += (GLYPH_W + GLYPH_SPACING) * scale
    return cur_x


def draw_pixel_rect(p: QPainter, rect: QRect, *,
                    fill: QColor | None, outline: QColor | None,
                    border_px: int = 1) -> None:
    """Fill a rect, then draw a hard-edged 1-px outline (multiplied by `border_px`)."""
    if fill is not None:
        p.fillRect(rect, fill)
    if outline is None or border_px <= 0:
        return
    # Top + bottom rows.
    p.fillRect(rect.left(), rect.top(), rect.width(), border_px, outline)
    p.fillRect(rect.left(), rect.bottom() - border_px + 1,
               rect.width(), border_px, outline)
    # Left + right cols.
    p.fillRect(rect.left(), rect.top(), border_px, rect.height(), outline)
    p.fillRect(rect.right() - border_px + 1, rect.top(),
               border_px, rect.height(), outline)


# ---- widgets ------------------------------------------------------------

class PixelLabel(QWidget):
    """Static pixel-font text. Scaling is via the font scale, not Qt DPI."""

    def __init__(self, text: str = "", scale: int = DEFAULT_SCALE,
                 color: QColor | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._scale = scale
        self._color = color or THEME.ink
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_size()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def setText(self, text: str) -> None:
        self._text = text
        self._update_size()
        self.update()

    def text(self) -> str:
        return self._text

    def setColor(self, color: QColor) -> None:
        self._color = color
        self.update()

    def _update_size(self) -> None:
        s = measure_text(self._text, self._scale)
        # Add a tiny breathing-margin so descenders don't get clipped.
        self.setFixedSize(QSize(s.width(), s.height() + self._scale))

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        draw_text(p, self._text, 0, 0, self._scale, self._color)
        p.end()


class PixelButton(QWidget):
    """Cream-paper button with 1-px ink outline. Click presses 1 px down-right."""

    clicked = pyqtSignal()

    def __init__(self, text: str, scale: int = DEFAULT_SCALE,
                 padding_px: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._scale = scale
        self._padding = padding_px
        self._hover = False
        self._pressed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_size()

    def setText(self, text: str) -> None:
        self._text = text
        self._update_size()
        self.update()

    def _update_size(self) -> None:
        ts = measure_text(self._text, self._scale)
        w = ts.width() + 2 * (self._padding + 2 * self._scale)
        h = ts.height() + 2 * (self._padding + self._scale)
        self.setFixedSize(QSize(w, h))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self._pressed = False
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(e.position().toPoint()):
                _click()
                self.clicked.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        body = self.rect()
        offset_x = 1 if self._pressed else 0
        offset_y = 1 if self._pressed else 0
        body = body.adjusted(offset_x, offset_y, offset_x, offset_y)
        body = body.intersected(self.rect())
        fill = (THEME.paper_pressed if self._pressed
                else THEME.paper_hover if self._hover
                else THEME.paper)
        draw_pixel_rect(p, body, fill=fill, outline=THEME.ink, border_px=2)
        # Centred text.
        ts = measure_text(self._text, self._scale)
        tx = body.left() + (body.width() - ts.width()) // 2
        ty = body.top() + (body.height() - ts.height()) // 2 - self._scale // 2
        draw_text(p, self._text, tx, ty, self._scale, THEME.ink)
        p.end()


class PixelCheckbox(QWidget):
    """Square pixel checkbox with a label on its right."""

    toggled = pyqtSignal(bool)

    def __init__(self, text: str = "", checked: bool = False,
                 scale: int = DEFAULT_SCALE,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._checked = bool(checked)
        self._scale = scale
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_size()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        if bool(value) == self._checked:
            return
        self._checked = bool(value)
        self.update()
        self.toggled.emit(self._checked)

    def setText(self, text: str) -> None:
        self._text = text
        self._update_size()
        self.update()

    def _box_size(self) -> int:
        return GLYPH_H * self._scale + 2 * self._scale  # ~17 px at scale=3

    def _update_size(self) -> None:
        box = self._box_size()
        ts = measure_text(self._text, self._scale)
        gap = self._scale * 2
        w = box + (gap + ts.width() if self._text else 0)
        h = max(box, ts.height()) + self._scale
        self.setFixedSize(QSize(w, h))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            _toggle_sfx(self._checked)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        box = self._box_size()
        rect = QRect(0, 0, box, box)
        fill = THEME.paper_hover if self._hover else THEME.paper
        draw_pixel_rect(p, rect, fill=fill, outline=THEME.ink, border_px=2)
        if self._checked:
            # Big X inside the box.
            inset = self._scale * 2
            for i in range(box - 2 * inset):
                p.fillRect(inset + i, inset + i, self._scale, self._scale,
                           THEME.accent)
                p.fillRect(box - inset - self._scale - i, inset + i,
                           self._scale, self._scale, THEME.accent)
        if self._text:
            tx = box + self._scale * 2
            ty = (rect.height() - GLYPH_H * self._scale) // 2
            draw_text(p, self._text, tx, ty, self._scale, THEME.ink)
        p.end()


class PixelSlider(QWidget):
    """Horizontal int/float slider with a draggable knob and a value caption.

    `valueChanged(value)` fires both during drag and on click. Step size
    is determined by `step` (defaults to 1 — for floats use a smaller step).
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum: float, maximum: float, value: float,
                 step: float = 1.0, scale: int = DEFAULT_SCALE,
                 width_px: int = 220, suffix: str = "",
                 decimals: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(value)
        self._step = float(step)
        self._scale = scale
        self._suffix = suffix
        self._decimals = max(0, int(decimals))
        self._dragging = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(width_px)
        self._track_h = max(4, scale)
        self._knob_w = scale * 4
        self._knob_h = scale * 6
        self._caption_h = GLYPH_H * scale + scale
        self.setFixedSize(QSize(width_px, self._knob_h + self._caption_h + scale * 2))
        self.setMouseTracking(True)

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = max(self._min, min(self._max, float(v)))
        if abs(v - self._value) < 1e-9:
            return
        self._value = v
        _slider_sfx()
        self.update()
        self.valueChanged.emit(self._value)

    def _val_to_x(self, v: float) -> int:
        if self._max == self._min:
            return 0
        track_w = self.width() - self._knob_w
        return int((v - self._min) / (self._max - self._min) * track_w)

    def _x_to_val(self, x: int) -> float:
        track_w = self.width() - self._knob_w
        if track_w <= 0:
            return self._min
        ratio = max(0.0, min(1.0, (x - self._knob_w / 2) / track_w))
        raw = self._min + ratio * (self._max - self._min)
        # Snap to step.
        if self._step > 0:
            raw = self._min + round((raw - self._min) / self._step) * self._step
        return max(self._min, min(self._max, raw))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setValue(self._x_to_val(int(e.position().x())))

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            self.setValue(self._x_to_val(int(e.position().x())))

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._dragging = False

    def _format_value(self) -> str:
        if self._decimals == 0:
            txt = str(int(round(self._value)))
        else:
            txt = f"{self._value:.{self._decimals}f}"
        return f"{txt}{self._suffix}"

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Track centered vertically in the upper region.
        track_y = (self._knob_h - self._track_h) // 2
        track_rect = QRect(self._knob_w // 2, track_y,
                           self.width() - self._knob_w, self._track_h)
        draw_pixel_rect(p, track_rect, fill=THEME.panel_bg, outline=THEME.ink, border_px=1)
        # Filled portion of the track.
        knob_x = self._val_to_x(self._value)
        fill_rect = QRect(self._knob_w // 2, track_y, knob_x, self._track_h)
        p.fillRect(fill_rect.adjusted(1, 1, -1, -1), THEME.accent)
        # Knob.
        knob_rect = QRect(knob_x, 0, self._knob_w, self._knob_h)
        draw_pixel_rect(p, knob_rect, fill=THEME.paper, outline=THEME.ink, border_px=2)
        # Caption underneath.
        cap = self._format_value()
        cap_y = self._knob_h + self._scale
        draw_text(p, cap, 0, cap_y, self._scale, THEME.ink)
        p.end()


class PixelSpinBox(QWidget):
    """Numeric spin with -/+ pixel-art buttons. Supports floats via decimals."""

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum: float, maximum: float, value: float,
                 step: float = 1.0, decimals: int = 0,
                 scale: int = DEFAULT_SCALE, suffix: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(value)
        self._step = float(step)
        self._dec = max(0, int(decimals))
        self._scale = scale
        self._suffix = suffix
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_size()

    def _format(self) -> str:
        if self._dec == 0:
            return f"{int(round(self._value))}{self._suffix}"
        return f"{self._value:.{self._dec}f}{self._suffix}"

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = max(self._min, min(self._max, float(v)))
        if self._step > 0 and self._dec > 0:
            v = round(v / self._step) * self._step
        if abs(v - self._value) < 1e-9:
            return
        self._value = v
        self._update_size()
        self.update()
        self.valueChanged.emit(self._value)

    def _btn_size(self) -> int:
        return GLYPH_H * self._scale + 2 * self._scale

    def _value_width(self) -> int:
        # Reserve space for the longest expected formatted value.
        widest = max(self._format(),
                     f"{self._max:.{self._dec}f}{self._suffix}",
                     f"{self._min:.{self._dec}f}{self._suffix}",
                     key=len)
        return measure_text(widest, self._scale).width()

    def _update_size(self) -> None:
        bs = self._btn_size()
        gap = self._scale * 2
        body_w = self._value_width() + 2 * self._scale * 2
        w = bs + gap + body_w + gap + bs
        h = bs + self._scale
        self.setFixedSize(QSize(w, h))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        x = int(e.position().x())
        bs = self._btn_size()
        if x < bs:
            self.setValue(self._value - self._step)
            _click()
        elif x > self.width() - bs:
            self.setValue(self._value + self._step)
            _click()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bs = self._btn_size()
        # Minus button.
        minus = QRect(0, 0, bs, bs)
        draw_pixel_rect(p, minus, fill=THEME.paper_hover, outline=THEME.ink, border_px=2)
        draw_text(p, "-",
                  minus.left() + (bs - GLYPH_W * self._scale) // 2,
                  minus.top() + (bs - GLYPH_H * self._scale) // 2,
                  self._scale, THEME.ink)
        # Plus button.
        plus = QRect(self.width() - bs, 0, bs, bs)
        draw_pixel_rect(p, plus, fill=THEME.paper_hover, outline=THEME.ink, border_px=2)
        draw_text(p, "+",
                  plus.left() + (bs - GLYPH_W * self._scale) // 2,
                  plus.top() + (bs - GLYPH_H * self._scale) // 2,
                  self._scale, THEME.ink)
        # Body with current value.
        gap = self._scale * 2
        body = QRect(bs + gap, 0, self.width() - 2 * (bs + gap), bs)
        draw_pixel_rect(p, body, fill=THEME.paper, outline=THEME.ink, border_px=2)
        txt = self._format()
        ts = measure_text(txt, self._scale)
        draw_text(p, txt,
                  body.left() + (body.width() - ts.width()) // 2,
                  body.top() + (bs - GLYPH_H * self._scale) // 2,
                  self._scale, THEME.ink)
        p.end()


class PixelComboBox(QWidget):
    """Dropdown — current item shown in a pixel-bordered box; clicking pops
    a vertical list of options. No native popup is used (we draw our own)."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, items: Iterable[str], current: int = 0,
                 scale: int = DEFAULT_SCALE,
                 min_width: int = 160,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._current = max(0, min(current, len(self._items) - 1)) if self._items else 0
        self._scale = scale
        self._open = False
        self._hover_idx = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._row_h = GLYPH_H * scale + 2 * scale
        # Width: longest item + arrow.
        widest = max(self._items, key=len) if self._items else ""
        text_w = measure_text(widest, scale).width()
        self._w = max(min_width, text_w + scale * 8)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._collapse()

    def setItems(self, items: Iterable[str]) -> None:
        self._items = list(items)
        if not self._items:
            self._current = 0
        elif self._current >= len(self._items):
            self._current = 0
        widest = max(self._items, key=len) if self._items else ""
        text_w = measure_text(widest, self._scale).width()
        self._w = max(self._w, text_w + self._scale * 8)
        self._collapse()
        self.update()

    def setCurrentIndex(self, idx: int) -> None:
        if not self._items:
            return
        idx = max(0, min(idx, len(self._items) - 1))
        if idx == self._current:
            return
        self._current = idx
        self.update()
        self.currentIndexChanged.emit(idx)

    def currentIndex(self) -> int:
        return self._current

    def currentText(self) -> str:
        if not self._items:
            return ""
        return self._items[self._current]

    def _collapse(self) -> None:
        self._open = False
        self.setFixedSize(QSize(self._w, self._row_h))

    def _expand(self) -> None:
        self._open = True
        h = self._row_h * (1 + len(self._items)) + self._scale
        self.setFixedSize(QSize(self._w, h))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        y = int(e.position().y())
        if not self._open:
            _click()
            self._expand()
            self.update()
            return
        # Open: detect which row was hit.
        if y < self._row_h:
            self._collapse()
            self.update()
            return
        idx = (y - self._row_h - self._scale) // self._row_h
        if 0 <= idx < len(self._items):
            old = self._current
            self._current = int(idx)
            self._collapse()
            self.update()
            if old != self._current:
                _click()
                self.currentIndexChanged.emit(self._current)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self._open:
            return
        y = int(e.position().y())
        idx = (y - self._row_h - self._scale) // self._row_h
        new_hover = int(idx) if 0 <= idx < len(self._items) else -1
        if new_hover != self._hover_idx:
            self._hover_idx = new_hover
            self.update()

    def leaveEvent(self, e):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        s = self._scale
        # Top header showing current selection.
        header = QRect(0, 0, self._w, self._row_h)
        draw_pixel_rect(p, header, fill=THEME.paper, outline=THEME.ink, border_px=2)
        draw_text(p, self.currentText(),
                  header.left() + s * 2,
                  header.top() + s,
                  s, THEME.ink)
        # Arrow on the right.
        arrow = "v" if not self._open else "*"
        ax = header.right() - s * (GLYPH_W + 2)
        draw_text(p, arrow, ax, header.top() + s, s, THEME.accent_dim)

        if not self._open:
            p.end()
            return

        # Drop-down body.
        body_top = self._row_h + s
        body_h = self._row_h * len(self._items)
        body = QRect(0, body_top, self._w, body_h)
        draw_pixel_rect(p, body, fill=THEME.paper, outline=THEME.ink, border_px=2)
        for i, item in enumerate(self._items):
            row = QRect(0, body_top + i * self._row_h, self._w, self._row_h)
            if i == self._current:
                p.fillRect(row.adjusted(2, 2, -2, -2), THEME.accent)
                color = THEME.paper
            elif i == self._hover_idx:
                p.fillRect(row.adjusted(2, 2, -2, -2), THEME.paper_hover)
                color = THEME.ink
            else:
                color = THEME.ink
            draw_text(p, item,
                      row.left() + s * 2,
                      row.top() + s,
                      s, color)
        p.end()


class PixelPanel(QFrame):
    """Bordered group with a small title strip on the top edge."""

    def __init__(self, title: str = "", scale: int = DEFAULT_SCALE,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._scale = scale
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Inner content layout — clients add to `self.body`.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(scale * 4, scale * 7, scale * 4, scale * 4)
        outer.setSpacing(scale * 2)
        self.body = outer

    def setTitle(self, title: str) -> None:
        self._title = title
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(0, self._scale * 2, 0, 0)
        draw_pixel_rect(p, rect, fill=THEME.panel_bg, outline=THEME.ink, border_px=2)
        # Title strip — draw a small notch above the body and the title text.
        if self._title:
            ts = measure_text(self._title, self._scale)
            tx = self._scale * 6
            ty = 0
            cutout = QRect(tx - self._scale, ty,
                           ts.width() + self._scale * 2,
                           self._scale * 4 + GLYPH_H * self._scale)
            p.fillRect(cutout, self.parentWidget().palette().color(QPalette.ColorRole.Window)
                       if self.parentWidget() else THEME.paper)
            draw_text(p, self._title, tx, ty, self._scale, THEME.ink)
        super().paintEvent(event)


class PixelProgress(QWidget):
    """Bordered horizontal fill bar. fraction in 0..1."""

    def __init__(self, label: str = "", scale: int = DEFAULT_SCALE,
                 width_px: int = 200, fill_color: QColor | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._scale = scale
        self._fraction = 0.0
        self._fill_color = fill_color or THEME.fill_good
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._h = scale * 6
        self._caption_h = GLYPH_H * scale + scale
        self.setFixedSize(QSize(width_px, self._h + self._caption_h + scale))

    def setFraction(self, fraction: float) -> None:
        f = max(0.0, min(1.0, float(fraction)))
        if abs(f - self._fraction) < 1e-6:
            return
        self._fraction = f
        self.update()

    def setLabel(self, label: str) -> None:
        self._label = label
        self.update()

    def setFillColor(self, color: QColor) -> None:
        self._fill_color = color
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bar = QRect(0, 0, self.width(), self._h)
        draw_pixel_rect(p, bar, fill=THEME.panel_bg, outline=THEME.ink, border_px=2)
        inner = bar.adjusted(2, 2, -2, -2)
        fill_w = int(inner.width() * self._fraction)
        if fill_w > 0:
            p.fillRect(QRect(inner.left(), inner.top(), fill_w, inner.height()),
                       self._fill_color)
        if self._label:
            cap_y = self._h + self._scale
            draw_text(p, self._label, 0, cap_y, self._scale, THEME.ink)
        p.end()


class PixelHRule(QWidget):
    """A 1-px-thick horizontal divider with cream paper above/below it."""

    def __init__(self, scale: int = DEFAULT_SCALE,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(scale * 2)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(0, self._scale, self.width(), 1, THEME.ink)
        p.end()


class PixelTitleBar(QWidget):
    """Custom drag-to-move title strip with a pixel icon, title text and
    minimise/close buttons. Replaces the native Win11 chrome on
    frameless windows so the whole shell looks pixel-art."""

    close_clicked = pyqtSignal()
    minimize_clicked = pyqtSignal()

    def __init__(self, title: str, icon_pixmap: QPixmap | None = None,
                 scale: int = DEFAULT_SCALE,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._title = title
        self._dragging = False
        self._drag_offset = QPoint()
        self._icon_pixmap = icon_pixmap
        bar_h = scale * 14
        self.setFixedHeight(bar_h)
        self.setMouseTracking(True)
        # Use a fixed-position layout: icon -> title -> stretch -> min -> close.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(scale * 3, scale * 2, scale * 3, scale * 2)
        layout.setSpacing(scale * 2)

        icon_size = scale * 10
        if icon_pixmap is not None and not icon_pixmap.isNull():
            icon_label = QLabel()
            scaled = icon_pixmap.scaled(
                icon_size, icon_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            icon_label.setPixmap(scaled)
            icon_label.setFixedSize(icon_size, icon_size)
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._title_label = PixelLabel(title, scale=scale + 1, color=THEME.paper)
        self._title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self._min_btn = PixelButton("-", scale=scale, padding_px=4)
        self._min_btn.clicked.connect(self.minimize_clicked.emit)
        layout.addWidget(self._min_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._close_btn = PixelButton("x", scale=scale, padding_px=4)
        self._close_btn.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def setTitle(self, text: str) -> None:
        self._title = text
        self._title_label.setText(text)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        p.fillRect(rect, THEME.accent_dim)
        # 1-px highlight along the top, ink line along the bottom.
        p.fillRect(rect.left(), rect.top(), rect.width(), self._scale,
                   THEME.accent)
        p.fillRect(rect.left(), rect.bottom() - self._scale + 1,
                   rect.width(), self._scale, THEME.ink)
        # Update the title's color in case the theme switched.
        self._title_label.setColor(THEME.paper)
        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = (
                e.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            new_pos = e.globalPosition().toPoint() - self._drag_offset
            self.window().move(new_pos)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False


# ---- a few composition helpers -----------------------------------------

class LabeledRow(QWidget):
    """A label paired with any widget on the right, with consistent padding.

    Useful when laying out form-like rows of (label : control)."""

    def __init__(self, label: str, control: QWidget,
                 scale: int = DEFAULT_SCALE,
                 label_min_width: int = 130,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale * 3)
        lbl = PixelLabel(label, scale=scale)
        lbl.setMinimumWidth(label_min_width)
        layout.addWidget(lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.label_widget = lbl
        self.control = control
