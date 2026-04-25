"""Single character actor — a transparent always-on-top window that renders sprites.

For the MVP we keep it simple: one widget per actor, frame swap on a timer,
position updated by the scene controller.
"""
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QWidget


class SpriteWidget(QWidget):
    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # no taskbar icon
            | Qt.WindowType.WindowTransparentForInput  # click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.set_pixmap(pixmap)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._label.setPixmap(pixmap)
        self._label.resize(pixmap.size())
        self.resize(pixmap.size())

    def move_to(self, x: int, y: int) -> None:
        # y is treated as the *bottom* of the sprite for easier ground-anchoring.
        self.move(x, y - self.height())
