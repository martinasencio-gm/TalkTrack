"""A thin drag handle that resizes a sibling widget's height.

Qt has no built-in resize handle for an arbitrary widget — QSplitter needs
two real panes and fights a scroll area's natural sizing when one pane is
a zero-height spacer. This is the textarea-resize-handle pattern instead:
a fixed-height bar placed directly below the target, translating drag
distance into the target's fixed height.
"""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPainter
from PyQt6.QtWidgets import QWidget


def compute_resized_height(start_height, delta_y, min_height, max_height):
    """Pure clamp logic, split out so it's testable without a QApplication."""
    return max(min_height, min(max_height, start_height + delta_y))


class VerticalResizeGrip(QWidget):
    """Dragging this bar changes `target`'s fixed height.

    `target` must already have an explicit height (via setFixedHeight) —
    this widget only adjusts it from there, it doesn't establish the
    starting size.
    """

    GRIP_HEIGHT = 10

    def __init__(self, target, min_height=80, max_height=640, parent=None):
        super().__init__(parent)
        self._target = target
        self._min_height = min_height
        self._max_height = max_height
        self._drag_start_y = None
        self._start_height = None
        self.setFixedHeight(self.GRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag to resize")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3f424d"))
        cy = self.height() // 2
        cx = self.width() // 2
        for dx in (-7, 0, 7):
            painter.drawEllipse(QPointF(cx + dx, cy), 1.5, 1.5)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = event.globalPosition().toPoint().y()
            self._start_height = self._target.height()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start_y is None:
            return
        delta = event.globalPosition().toPoint().y() - self._drag_start_y
        new_height = compute_resized_height(
            self._start_height, delta, self._min_height, self._max_height
        )
        self._target.setFixedHeight(new_height)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_start_y = None
        event.accept()
