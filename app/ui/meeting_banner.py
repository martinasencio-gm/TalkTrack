"""Banner prompting to start, or stop/pause, a recording for a detected meeting.

Mirrors CalendarSuggestionBanner's frame, colours and spacing so the two read as
the same kind of prompt when they appear in the same column.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor

from app.utils.icons import colored_pixmap


def _minutes_phrase(seconds):
    """"2 minutes", "1 minute", or None when it does not round to a minute."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return None
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def format_start_text(meeting_name, elapsed_seconds):
    subject = meeting_name or "A meeting"
    phrase = _minutes_phrase(elapsed_seconds)
    when = "just now" if phrase is None else f"{phrase} ago"
    return f"{subject} started {when} - record it?"


def format_end_text(meeting_name, recorded_seconds):
    subject = meeting_name or "The meeting"
    phrase = _minutes_phrase(recorded_seconds) or "less than a minute"
    return f"{subject} ended - stop recording? ({phrase} captured)"


class MeetingBanner(QWidget):
    """Start and end prompts share one banner - only one can ever be relevant."""

    start_accepted = pyqtSignal()
    start_dismissed = pyqtSignal()
    end_chosen = pyqtSignal(str)   # "stop" | "pause" | "keep"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        self._frame = QFrame(self)
        self._frame.setObjectName("meetingBanner")

        # Subtle drop shadow
        shadow = QGraphicsDropShadowEffect(self._frame)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 90))
        shadow.setOffset(0, 4)
        self._frame.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.addWidget(self._frame)

        self._layout = QHBoxLayout(self._frame)
        self._layout.setContentsMargins(14, 8, 14, 8)
        self._layout.setSpacing(10)

        self._icon = QLabel()
        self._icon.setPixmap(colored_pixmap("phone-incoming", "#9184d9", 16))
        self._icon.setObjectName("meetingBannerIcon")
        self._layout.addWidget(self._icon)

        self._text = QLabel()
        self._text.setObjectName("meetingBannerText")
        self._text.setWordWrap(True)
        self._layout.addWidget(self._text, 1)

        self._buttons = QWidget()
        self._button_row = QHBoxLayout(self._buttons)
        self._button_row.setContentsMargins(0, 0, 0, 0)
        self._button_row.setSpacing(8)
        self._layout.addWidget(self._buttons)

    def _clear_buttons(self):
        while self._button_row.count():
            item = self._button_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_buttons(self, specs):
        self._clear_buttons()
        for label, handler, btn_type in specs:
            button = QPushButton(label)
            if btn_type == "primary":
                button.setObjectName("bannerRecordBtn")
            elif btn_type == "pause":
                button.setObjectName("bannerPauseBtn")
            else:
                button.setObjectName("bannerDismissBtn")
            button.clicked.connect(handler)
            self._button_row.addWidget(button)

    def show_start(self, meeting_name, elapsed_seconds):
        self._icon.setPixmap(colored_pixmap("phone-incoming", "#9184d9", 16))
        self._frame.setProperty("mode", "start")
        self._frame.style().unpolish(self._frame)
        self._frame.style().polish(self._frame)
        self._text.setText(format_start_text(meeting_name, elapsed_seconds))
        self._set_buttons([
            ("Record", self._on_record, "primary"),
            ("Not now", self._on_not_now, "secondary"),
        ])
        self.show()

    def show_end(self, meeting_name, recorded_seconds):
        self._icon.setPixmap(colored_pixmap("stop", "#f9e2af", 16))
        self._frame.setProperty("mode", "end")
        self._frame.style().unpolish(self._frame)
        self._frame.style().polish(self._frame)
        self._text.setText(format_end_text(meeting_name, recorded_seconds))
        self._set_buttons([
            ("Stop & save", lambda checked=False: self._on_end("stop"), "primary"),
            ("Pause", lambda checked=False: self._on_end("pause"), "pause"),
            ("Keep recording", lambda checked=False: self._on_end("keep"), "secondary"),
        ])
        self.show()

    def hide_and_clear(self):
        self._clear_buttons()
        self.hide()

    def _on_record(self):
        self.hide_and_clear()
        self.start_accepted.emit()

    def _on_not_now(self):
        self.hide_and_clear()
        self.start_dismissed.emit()

    def _on_end(self, action):
        self.hide_and_clear()
        self.end_chosen.emit(action)
