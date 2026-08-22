"""Reusable tag chip widget and tag picker popup for TalkTrack."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QFrame, QCheckBox, QGraphicsDropShadowEffect, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize
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
                text_color = "#ffffff"
            else:
                bg = "rgba(49, 50, 68, 0.6)"
                border = "rgba(69, 71, 90, 0.8)"
                text_color = "#a6adc8"
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


class TagPickerPopup(QFrame):
    """Popup panel to quickly select or create tags for a recording."""

    tag_toggled = pyqtSignal(str, bool)    # (tag_name, is_assigned)
    new_tag_created = pyqtSignal(str)      # tag_name
    manage_tags_requested = pyqtSignal()

    def __init__(self, assigned_tags: list[str] = None, recordings_dir=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.assigned_tags = set(assigned_tags or [])
        self.recordings_dir = recordings_dir
        self.setObjectName("tagPickerPopup")

        # Shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        self.setFixedWidth(240)
        self.setMaximumHeight(320)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Search / Add input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search or create tag...")
        self.search_input.setObjectName("tagPickerSearch")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._on_return_pressed)
        layout.addWidget(self.search_input)

        # Scrollable tag checkboxes
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setObjectName("tagPickerScroll")

        self.items_container = QWidget()
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(2, 2, 2, 2)
        self.items_layout.setSpacing(4)
        self.scroll.setWidget(self.items_container)
        layout.addWidget(self.scroll, 1)

        # Create tag button (visible when query doesn't match existing)
        self.create_btn = QPushButton()
        self.create_btn.setObjectName("tagPickerCreateBtn")
        self.create_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.create_btn.clicked.connect(self._create_current_query)
        self.create_btn.hide()
        layout.addWidget(self.create_btn)

        # Bottom row: Manage Tags
        manage_btn = QPushButton("Manage Tags...")
        manage_btn.setObjectName("tagPickerManageBtn")
        manage_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        manage_btn.clicked.connect(self._on_manage_clicked)
        layout.addWidget(manage_btn)

        self.setStyleSheet("""
            QFrame#tagPickerPopup {
                background-color: #1e1e2e;
                border: 1px solid #45475a;
                border-radius: 8px;
            }
            QLineEdit#tagPickerSearch {
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit#tagPickerSearch:focus {
                border-color: #89b4fa;
            }
            QScrollArea#tagPickerScroll, QWidget#tagPickerScroll QWidget {
                background-color: transparent;
            }
            QPushButton#tagPickerCreateBtn {
                background-color: #313244;
                color: #89b4fa;
                border: 1px dashed #89b4fa;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                text-align: left;
            }
            QPushButton#tagPickerCreateBtn:hover {
                background-color: #45475a;
            }
            QPushButton#tagPickerManageBtn {
                background-color: transparent;
                color: #a6adc8;
                border: none;
                font-size: 10px;
                text-align: center;
                padding: 4px;
            }
            QPushButton#tagPickerManageBtn:hover {
                color: #cdd6f4;
                text-decoration: underline;
            }
            QCheckBox {
                color: #cdd6f4;
                font-size: 11px;
                spacing: 6px;
            }
        """)

    def refresh(self):
        query = self.search_input.text().strip().lower()
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_tags = tag_manager.load_all_tags()
        exact_match = False
        matching_count = 0

        for tag in all_tags:
            name = tag["name"]
            color = tag.get("color") or tag_manager.get_tag_color(name)
            if name.lower() == query:
                exact_match = True

            if not query or query in name.lower():
                matching_count += 1
                row = QHBoxLayout()
                row.setContentsMargins(2, 2, 2, 2)
                row.setSpacing(6)

                cb = QCheckBox(name)
                cb.setChecked(name in self.assigned_tags)
                cb.toggled.connect(lambda checked, n=name: self._on_checkbox_toggled(n, checked))

                # Color indicator dot
                dot = QFrame()
                dot.setFixedSize(8, 8)
                dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")

                row.addWidget(dot, 0)
                row.addWidget(cb, 1)

                container = QWidget()
                container.setLayout(row)
                self.items_layout.addWidget(container)

        self.items_layout.addStretch(1)

        raw_query = self.search_input.text().strip()
        if raw_query and not exact_match:
            self.create_btn.setText(f"+ Create tag \"{raw_query}\"")
            self.create_btn.show()
        else:
            self.create_btn.hide()

    def _on_search_changed(self, text):
        self.refresh()

    def _on_return_pressed(self):
        raw = self.search_input.text().strip()
        if raw:
            self._create_current_query()

    def _create_current_query(self):
        raw = self.search_input.text().strip()
        if not raw:
            return
        tag_manager.create_tag(raw)
        self.assigned_tags.add(raw)
        self.tag_toggled.emit(raw, True)
        self.new_tag_created.emit(raw)
        self.search_input.clear()
        self.refresh()

    def _on_checkbox_toggled(self, name: str, checked: bool):
        if checked:
            self.assigned_tags.add(name)
        else:
            self.assigned_tags.discard(name)
        self.tag_toggled.emit(name, checked)

    def _on_manage_clicked(self):
        self.close()
        self.manage_tags_requested.emit()

    def show_at(self, global_pos: QPoint):
        self.move(global_pos)
        self.show()
        self.search_input.setFocus()
