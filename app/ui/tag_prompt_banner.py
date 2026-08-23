"""Banner prompting user to tag a newly finished recording."""
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QLineEdit, QGraphicsDropShadowEffect, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QCursor

from app.ui.tag_chip import TagChip
from app.utils import tag_manager
from app.utils.icons import colored_pixmap


class TagPromptBanner(QWidget):
    """Post-recording banner prompting user to assign tags to the recording."""

    tags_updated = pyqtSignal(list)   # list of currently assigned tags
    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._assigned_tags = []
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self._frame = QFrame(self)
        self._frame.setObjectName("tagPromptBanner")

        shadow = QGraphicsDropShadowEffect(self._frame)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 90))
        shadow.setOffset(0, 4)
        self._frame.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.addWidget(self._frame)

        self._layout = QVBoxLayout(self._frame)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(8)

        # Header row
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title_icon = QLabel()
        title_icon.setPixmap(colored_pixmap("bookmark-simple", "#9184d9", 14))
        header_row.addWidget(title_icon)

        self._title_label = QLabel("Tag this recording:")
        self._title_label.setObjectName("bannerTitle")
        font = self._title_label.font()
        font.setWeight(QFont.Weight.DemiBold)
        self._title_label.setFont(font)
        header_row.addWidget(self._title_label)

        header_row.addStretch()

        self._done_btn = QPushButton("Done")
        self._done_btn.setObjectName("primaryAction")
        self._done_btn.setFixedWidth(70)
        self._done_btn.clicked.connect(self._on_done)
        header_row.addWidget(self._done_btn)

        self._layout.addLayout(header_row)

        # Chips & quick-add container
        self._chips_container = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)

        # Scroll area for tags row in case of many tags
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFixedHeight(34)
        self._scroll.setWidget(self._chips_container)
        self._layout.addWidget(self._scroll)

        # Quick add input row
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._new_tag_input = QLineEdit()
        self._new_tag_input.setObjectName("tagBannerNewInput")
        self._new_tag_input.setPlaceholderText("+ Type custom tag & press Enter...")
        self._new_tag_input.returnPressed.connect(self._add_custom_tag)
        self._new_tag_input.setFixedHeight(26)
        add_row.addWidget(self._new_tag_input, 1)

        add_btn = QPushButton("Add")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_custom_tag)
        add_row.addWidget(add_btn)

        add_row.addStretch(2)
        self._layout.addLayout(add_row)

        self.setStyleSheet("""
            QFrame#tagPromptBanner {
                background-color: #1c1e29;
                border: 1px solid #292b31;
                border-radius: 8px;
            }
            QLabel#bannerTitle {
                color: #e9e9ed;
                font-size: 11pt;
            }
            QLineEdit#tagBannerNewInput {
                font-size: 10px;
                padding: 2px 8px;
            }
            QScrollArea, QScrollArea QWidget {
                background: transparent;
            }
        """)

    def show_prompt(self, session: dict):
        """Show the tag prompt banner for the given session."""
        self._session = session
        self._assigned_tags = list(tag_manager.get_recording_tags(session))
        self._rebuild_chips()
        self.show()

    def _rebuild_chips(self):
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_tags = tag_manager.load_all_tags()
        # Ensure any currently assigned tag is also available in the list
        all_tag_names = {t["name"].lower() for t in all_tags}
        for assigned in self._assigned_tags:
            if assigned.lower() not in all_tag_names:
                all_tags.append({"name": assigned, "color": tag_manager.get_tag_color(assigned)})
                all_tag_names.add(assigned.lower())

        for t in all_tags:
            name = t["name"]
            color = t.get("color") or tag_manager.get_tag_color(name)
            is_sel = name in self._assigned_tags
            chip = TagChip(name, color=color, selectable=True, selected=is_sel, removable=False)
            chip.clicked.connect(lambda n=name: self._toggle_tag(n))
            self._chips_layout.addWidget(chip)

        self._chips_layout.addStretch(1)

    def _toggle_tag(self, tag_name: str):
        if not self._session or not self._session.get("directory"):
            return
        if tag_name in self._assigned_tags:
            self._assigned_tags = tag_manager.remove_tag_from_recording(self._session["directory"], tag_name)
        else:
            self._assigned_tags = tag_manager.add_tag_to_recording(self._session["directory"], tag_name)

        self._rebuild_chips()
        self.tags_updated.emit(self._assigned_tags)

    def _add_custom_tag(self):
        text = self._new_tag_input.text().strip()
        if not text or not self._session or not self._session.get("directory"):
            return
        self._assigned_tags = tag_manager.add_tag_to_recording(self._session["directory"], text)
        self._new_tag_input.clear()
        self._rebuild_chips()
        self.tags_updated.emit(self._assigned_tags)

    def _on_done(self):
        self.hide_and_clear()
        self.dismissed.emit()

    def hide_and_clear(self):
        self._session = None
        self._assigned_tags = []
        self._new_tag_input.clear()
        self.hide()
