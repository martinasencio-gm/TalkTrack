"""Circular letter avatar badge for speakers."""
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen, QFont


class AvatarWidget(QWidget):
    """Circular speaker avatar badge displaying the initial in a colored ring."""

    def __init__(self, initial, color, size=24, parent=None):
        super().__init__(parent)
        self.initial = initial[:1].upper() if initial else "?"
        self.color = color
        self._size = size
        self.setFixedSize(size, size)

    def set_initial(self, initial):
        self.initial = initial[:1].upper() if initial else "?"
        self.update()

    def set_color(self, color):
        self.color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        s = self._size
        pen = QPen(QColor(self.color))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawEllipse(1, 1, s - 2, s - 2)

        painter.setPen(QColor(self.color))
        font_size = max(8, s // 2 - 2)
        font = QFont("Inter", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.initial)
        painter.end()
