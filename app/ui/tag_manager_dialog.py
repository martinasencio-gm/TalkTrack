"""Dialog for managing global tags, colors, renaming, and deleting."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog, QWidget,
    QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont

from app.utils import tag_manager
from app.utils.tag_manager import TAG_PALETTE


class ColorPickerDialog(QDialog):
    """Simple palette picker for selecting a Catppuccin tag color."""

    def __init__(self, current_color="#89b4fa", parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Choose Tag Color")
        self.setFixedSize(280, 180)
        self.selected_color = current_color
        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        label = QLabel("Select a color from the Catppuccin palette:")
        label.setStyleSheet("color: #e9e9ed; font-size: 11px;")
        layout.addWidget(label)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)

        cols = 6
        for idx, color_hex in enumerate(TAG_PALETTE):
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            border = "3px solid #ffffff" if color_hex.lower() == self.selected_color.lower() else "1px solid #3f424d"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color_hex};
                    border: {border};
                    border-radius: 16px;
                }}
                QPushButton:hover {{
                    border: 2px solid #ffffff;
                }}
            """)
            btn.clicked.connect(lambda checked=False, c=color_hex: self._select_and_accept(c))
            grid.addWidget(btn, idx // cols, idx % cols)

        layout.addWidget(grid_widget)

    def _select_and_accept(self, color_hex):
        self.selected_color = color_hex
        self.accept()


class TagManagerDialog(QDialog):
    """Full CRUD Tag Manager dialog."""

    tags_changed = pyqtSignal()

    def __init__(self, recordings_dir=None, tags_file=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.recordings_dir = recordings_dir
        self.tags_file = tags_file
        self.setWindowTitle("Manage Tags")
        self.setMinimumSize(540, 400)
        self.resize(580, 450)
        self._setup_ui()
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header description
        desc_label = QLabel(
            "Manage tags across your recordings. Renaming or deleting a tag updates all assigned recordings."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #9397ab; font-size: 11px; margin-bottom: 4px;")
        layout.addWidget(desc_label)

        # Table
        self.table = QTableWidget()
        self.table.setObjectName("tagsTable")
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Color", "Tag Name", "Recordings"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 60)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._update_button_states)
        self.table.itemDoubleClicked.connect(self._on_rename_clicked)
        layout.addWidget(self.table, 1)

        # Action button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.new_btn = QPushButton("New Tag...")
        self.new_btn.clicked.connect(self._on_new_clicked)
        btn_row.addWidget(self.new_btn)

        self.rename_btn = QPushButton("Rename...")
        self.rename_btn.clicked.connect(self._on_rename_clicked)
        btn_row.addWidget(self.rename_btn)

        self.color_btn = QPushButton("Change Color...")
        self.color_btn.clicked.connect(self._on_color_clicked)
        btn_row.addWidget(self.color_btn)

        self.delete_btn = QPushButton("Delete Tag")
        self.delete_btn.setObjectName("deleteTagBtn")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        self.setStyleSheet("""
            QDialog {
                background-color: #161826;
                color: #e9e9ed;
            }
            QTableWidget#tagsTable {
                background-color: #12141f;
                color: #e9e9ed;
                border: 1px solid #292b31;
                border-radius: 6px;
                gridline-color: #292b31;
                selection-background-color: #3f424d;
                selection-color: #e9e9ed;
            }
            QHeaderView::section {
                background-color: #161826;
                color: #9397ab;
                border: none;
                border-bottom: 1px solid #292b31;
                padding: 6px 8px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton#deleteTagBtn {
                background-color: #232532;
                color: #f38ba8;
                border: 1px solid #3f424d;
            }
            QPushButton#deleteTagBtn:hover {
                background-color: rgba(243, 139, 168, 0.2);
                border-color: #f38ba8;
            }
            QPushButton#deleteTagBtn:disabled {
                color: #4d5063;
                border-color: #292b31;
                background-color: #161826;
            }
        """)

    def refresh(self):
        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        counts = tag_manager.get_tag_counts(self.recordings_dir) if self.recordings_dir else {}

        self.table.setRowCount(len(tags))
        for row, t in enumerate(tags):
            name = t["name"]
            color = t.get("color") or tag_manager.get_tag_color(name, tags_file=self.tags_file)
            count = counts.get(name, 0)

            # Swatch cell
            swatch_item = QTableWidgetItem()
            swatch_item.setData(Qt.ItemDataRole.UserRole, t)
            swatch_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, swatch_item)

            swatch_widget = QWidget()
            swatch_layout = QHBoxLayout(swatch_widget)
            swatch_layout.setContentsMargins(4, 2, 4, 2)
            swatch_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot = QFrame()
            dot.setFixedSize(14, 14)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 7px; border: 1px solid #3f424d;")
            swatch_layout.addWidget(dot)
            self.table.setCellWidget(row, 0, swatch_widget)

            # Name cell
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, t)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 1, name_item)

            # Count cell
            count_item = QTableWidgetItem(f"{count} recording{'s' if count != 1 else ''}")
            count_item.setData(Qt.ItemDataRole.UserRole, count)
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 2, count_item)

        self._update_button_states()

    def _selected_tag(self):
        row = self.table.currentRow()
        if row >= 0:
            item = self.table.item(row, 1)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _update_button_states(self):
        has_sel = self._selected_tag() is not None
        self.rename_btn.setEnabled(has_sel)
        self.color_btn.setEnabled(has_sel)
        self.delete_btn.setEnabled(has_sel)

    def _on_new_clicked(self):
        name, ok = QInputDialog.getText(self, "New Tag", "Enter tag name:")
        if ok and name.strip():
            name = name.strip()
            picker = ColorPickerDialog(parent=self)
            color = picker.selected_color if picker.exec() else None
            tag_manager.create_tag(name, color=color, tags_file=self.tags_file)
            self.refresh()
            self.tags_changed.emit()

    def _on_rename_clicked(self):
        tag = self._selected_tag()
        if not tag:
            return
        old_name = tag["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename Tag", f"Enter new name for tag '{old_name}':", text=old_name
        )
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            tag_manager.rename_tag(old_name, new_name, recordings_dir=self.recordings_dir, tags_file=self.tags_file)
            self.refresh()
            self.tags_changed.emit()

    def _on_color_clicked(self):
        tag = self._selected_tag()
        if not tag:
            return
        current_color = tag.get("color") or "#89b4fa"
        picker = ColorPickerDialog(current_color=current_color, parent=self)
        if picker.exec():
            tag_manager.update_tag_color(tag["name"], picker.selected_color, tags_file=self.tags_file)
            self.refresh()
            self.tags_changed.emit()

    def _on_delete_clicked(self):
        tag = self._selected_tag()
        if not tag:
            return
        name = tag["name"]
        counts = tag_manager.get_tag_counts(self.recordings_dir) if self.recordings_dir else {}
        count = counts.get(name, 0)

        msg = f"Are you sure you want to delete tag '{name}'?"
        if count > 0:
            msg += f"\n\nIt is currently assigned to {count} recording{'s' if count != 1 else ''}. Deleting it will remove it from those recordings."

        reply = QMessageBox.question(
            self,
            "Delete Tag",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            tag_manager.delete_tag(name, recordings_dir=self.recordings_dir, tags_file=self.tags_file)
            self.refresh()
            self.tags_changed.emit()
