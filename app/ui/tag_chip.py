"""Reusable tag chip widget for TalkTrack."""
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QCursor

from app.utils import tag_manager


class TagChip(QFrame):
    """A styled Catppuccin pill representing a tag."""

    clicked = pyqtSignal(str)          # tag_name
    remove_clicked = pyqtSignal(str)   # tag_name

    def __init__(self, name: str, color: str = None, removable: bool = False,
                 selectable: bool = False, selected: bool = False, parent=None):
        super().__init__(parent)
        self.tag_name = name
        self.color_hex = color or tag_manager.get_tag_color(name)
        self.removable = removable
        self.selectable = selectable
        self._selected = selected

        self.setObjectName("tagChip")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._setup_ui()
        self._update_style()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(4)

        self.label = QLabel(self.tag_name)
        self.label.setObjectName("tagChipLabel")
        font = self.label.font()
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        self.label.setFont(font)
        layout.addWidget(self.label)

        if self.removable:
            self.remove_btn = QPushButton("×")
            self.remove_btn.setObjectName("tagChipRemoveBtn")
            self.remove_btn.setFixedSize(14, 14)
            self.remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.tag_name))
            layout.addWidget(self.remove_btn)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.selectable:
                self.set_selected(not self._selected)
            self.clicked.emit(self.tag_name)
        super().mousePressEvent(event)

    def _update_style(self):
        c = self.color_hex
        if self.selectable:
            if self._selected:
                bg = f"rgba({QColor(c).red()}, {QColor(c).green()}, {QColor(c).blue()}, 0.35)"
                border = c
                text_color = "#e9e9ed"
            else:
                bg = "transparent"
                border = "rgba(233,233,237,0.16)"
                text_color = "#e9e9ed"
        else:
            bg = f"rgba({QColor(c).red()}, {QColor(c).green()}, {QColor(c).blue()}, 0.2)"
            border = f"rgba({QColor(c).red()}, {QColor(c).green()}, {QColor(c).blue()}, 0.6)"
            text_color = c

        self.setStyleSheet(f"""
            QFrame#tagChip {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 9px;
            }}
            QFrame#tagChip:hover {{
                border-color: {c};
            }}
            QLabel#tagChipLabel {{
                color: {text_color};
                background: transparent;
                border: none;
            }}
            QPushButton#tagChipRemoveBtn {{
                color: {text_color};
                background: transparent;
                border: none;
                font-size: 11px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
            }}
            QPushButton#tagChipRemoveBtn:hover {{
                color: #f38ba8;
            }}
        """)
