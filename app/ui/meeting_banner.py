"""Banner prompting to start, or stop/pause, a recording for a detected meeting.

Mirrors CalendarSuggestionBanner's frame, colours and spacing so the two read as
the same kind of prompt when they appear in the same column.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt6.QtCore import pyqtSignal


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
        self._frame.setStyleSheet(
            "#meetingBanner { background-color: #313244; border-radius: 4px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.addWidget(self._frame)

        self._layout = QVBoxLayout(self._frame)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)

        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setStyleSheet("font-weight: bold; color: #89b4fa;")
        self._layout.addWidget(self._text)

        self._buttons = QWidget()
        self._button_row = QHBoxLayout(self._buttons)
        self._button_row.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._buttons)

    def _clear_buttons(self):
        while self._button_row.count():
            item = self._button_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_buttons(self, specs):
        self._clear_buttons()
        self._button_row.addStretch()
        for label, handler in specs:
            button = QPushButton(label)
            button.clicked.connect(handler)
            self._button_row.addWidget(button)

    def show_start(self, meeting_name, elapsed_seconds):
        self._text.setText(format_start_text(meeting_name, elapsed_seconds))
        self._set_buttons([
            ("Record", self._on_record),
            ("Not now", self._on_not_now),
        ])
        self.show()

    def show_end(self, meeting_name, recorded_seconds):
        self._text.setText(format_end_text(meeting_name, recorded_seconds))
        self._set_buttons([
            ("Stop & save", lambda checked=False: self._on_end("stop")),
            ("Pause", lambda checked=False: self._on_end("pause")),
            ("Keep recording", lambda checked=False: self._on_end("keep")),
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
