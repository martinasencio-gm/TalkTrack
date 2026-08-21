"""Banner suggesting a calendar-event tag for a finished recording."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor


class CalendarSuggestionBanner(QWidget):
    """Shows overlapping calendar events with per-event Tag buttons."""

    tag_requested = pyqtSignal(dict)   # the chosen event dict
    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events = []
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self._frame = QFrame(self)
        self._frame.setObjectName("calendarBanner")

        # Subtle drop shadow to give depth
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
        self._layout.setSpacing(6)

        self._title_label = QLabel("Calendar match found")
        self._title_label.setObjectName("bannerTitle")
        self._layout.addWidget(self._title_label)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._rows_container)

        dismiss_row = QHBoxLayout()
        dismiss_row.addStretch()
        self._dismiss_btn = QPushButton("Dismiss")
        self._dismiss_btn.clicked.connect(self._on_dismiss)
        dismiss_row.addWidget(self._dismiss_btn)
        self._layout.addLayout(dismiss_row)

    def show_matches(self, events):
        self._events = events
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not events:
            self.hide()
            return

        for event in events:
            row = QHBoxLayout()
            start_str = event["start"].strftime("%H:%M")
            end_str = event["end"].strftime("%H:%M")
            organizer = event.get("organizer", "")
            text = f'"{event["subject"]}"  ·  {start_str}–{end_str}'
            if organizer:
                text += f"  ·  {organizer}"
            label = QLabel(text)
            label.setObjectName("bannerText")
            row.addWidget(label, 1)

            tag_btn = QPushButton("Tag Recording")
            tag_btn.clicked.connect(lambda checked=False, e=event: self._on_tag(e))
            row.addWidget(tag_btn)

            row_widget = QWidget()
            row_widget.setLayout(row)
            self._rows_layout.addWidget(row_widget)

        self.show()

    def hide_and_clear(self):
        self._events = []
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.hide()

    def _on_tag(self, event):
        self.tag_requested.emit(event)
        self.hide_and_clear()

    def _on_dismiss(self):
        self.dismissed.emit()
        self.hide_and_clear()
