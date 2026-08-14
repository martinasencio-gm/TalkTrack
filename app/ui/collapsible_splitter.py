"""A two-pane QSplitter whose handle can collapse the second pane to zero width.

Collapse state lives on the splitter, not the handle - the handle is just a
button that calls back into it. That keeps the state machine in one place even
though Qt asks the splitter to create a fresh handle object internally.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter, QSplitterHandle, QToolButton, QVBoxLayout


class CollapsibleSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()

        self._button = QToolButton(self)
        self._button.setAutoRaise(True)
        self._button.setFixedSize(12, 28)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setStyleSheet(
            "QToolButton { background-color: rgba(255, 255, 255, 30); "
            "border: none; border-radius: 3px; color: #cdd6f4; }"
            "QToolButton:hover { background-color: rgba(255, 255, 255, 60); }"
        )
        self._button.clicked.connect(self.splitter().toggle_collapse)
        layout.addWidget(self._button)

        layout.addStretch()
        self._update_arrow(self.splitter().is_collapsed())
        self.splitter().collapse_changed.connect(self._update_arrow)

    def _update_arrow(self, collapsed):
        self._button.setText("▸" if collapsed else "◂")
        self._button.setToolTip("Expand panel" if collapsed else "Collapse panel")


class CollapsibleSplitter(QSplitter):
    """Two-pane splitter; index 1 (the right/second pane) is what collapses."""

    collapse_changed = pyqtSignal(bool)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._collapsed = False
        self._expanded_size = None

    def createHandle(self):
        return CollapsibleSplitterHandle(self.orientation(), self)

    def is_collapsed(self):
        return self._collapsed

    def toggle_collapse(self):
        sizes = self.sizes()
        if len(sizes) < 2:
            return
        if self._collapsed:
            total = sum(sizes)
            restore = self._expanded_size or max(total // 3, 1)
            restore = min(restore, total)
            self.setSizes([total - restore, restore])
        else:
            self._expanded_size = sizes[1]
            self.setSizes([sum(sizes), 0])
        self._collapsed = not self._collapsed
        self.collapse_changed.emit(self._collapsed)

    def set_collapsed(self, collapsed):
        """Apply a restored state (e.g. on startup) without a double toggle."""
        if collapsed == self._collapsed:
            return
        self.toggle_collapse()
