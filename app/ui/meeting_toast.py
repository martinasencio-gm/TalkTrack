"""Floating desktop toast notification for detected meeting start and end prompts.

Shows a sleek, modern popup in the corner of the screen with explicit [Record]
and [Dismiss] buttons so the user can act immediately without needing to open
the main window.
"""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout, QWidget
)

from app.ui.meeting_banner import format_start_text, format_end_text
from app.utils.icons import colored_pixmap


class MeetingNotificationToast(QWidget):
    """Floating desktop notification with action buttons."""

    record_accepted = pyqtSignal()
    dismissed = pyqtSignal()
    end_chosen = pyqtSignal(str)  # "stop" | "pause" | "keep"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.setInterval(12000)  # 12 seconds
        self._auto_close_timer.timeout.connect(self.hide_and_clear)

        self._setup_ui()

    def _setup_ui(self):
        self.setFixedWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self._frame = QFrame(self)
        self._frame.setObjectName("meetingToastFrame")
        self._frame.setStyleSheet("""
            QFrame#meetingToastFrame {
                background-color: #161826;
                border: 1px solid #3f424d;
                border-left: 4px solid #f38ba8;
                border-radius: 8px;
            }
            QFrame#meetingToastFrame[mode="end"] {
                border-left: 4px solid #9184d9;
            }
            QLabel#toastTitle {
                color: #e9e9ed;
                font-weight: bold;
                font-size: 10pt;
            }
            QLabel#toastBody {
                color: #9397ab;
                font-size: 9pt;
            }
            QPushButton#toastCloseBtn {
                background: transparent;
                color: #75798c;
                border: none;
                font-size: 11pt;
                font-weight: bold;
                padding: 0 4px;
            }
            QPushButton#toastCloseBtn:hover {
                color: #f38ba8;
            }
            QPushButton#toastRecordBtn {
                background-color: #f38ba8;
                color: #12141f;
                font-weight: bold;
                font-size: 9pt;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                min-height: 22px;
            }
            QPushButton#toastRecordBtn:hover {
                background-color: #eba0ac;
            }
            QPushButton#toastDismissBtn {
                background-color: #232532;
                color: #e9e9ed;
                font-size: 9pt;
                border: 1px solid #3f424d;
                border-radius: 4px;
                padding: 5px 14px;
                min-height: 22px;
            }
            QPushButton#toastDismissBtn:hover {
                background-color: #3f424d;
            }
            QPushButton#toastPauseBtn {
                background-color: #f9e2af;
                color: #12141f;
                font-weight: bold;
                font-size: 9pt;
                border: none;
                border-radius: 4px;
                padding: 5px 12px;
                min-height: 22px;
            }
            QPushButton#toastPauseBtn:hover {
                background-color: #f5e0dc;
            }
        """)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self._frame)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self._frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self._frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header: Icon + Title + Close button
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._icon_label = QLabel()
        self._icon_label.setPixmap(colored_pixmap("phone-incoming", "#9184d9", 16))
        header.addWidget(self._icon_label)

        self._title_label = QLabel("Meeting Detected")
        self._title_label.setObjectName("toastTitle")
        header.addWidget(self._title_label, 1)

        close_btn = QPushButton()
        close_btn.setIcon(QIcon(colored_pixmap("x", "#9397ab", 12)))
        close_btn.setObjectName("toastCloseBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_dismiss)
        header.addWidget(close_btn)

        layout.addLayout(header)

        # Body text
        self._body_label = QLabel()
        self._body_label.setObjectName("toastBody")
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)

        # Buttons row
        self._button_container = QWidget()
        self._button_row = QHBoxLayout(self._button_container)
        self._button_row.setContentsMargins(0, 4, 0, 0)
        self._button_row.setSpacing(8)
        layout.addWidget(self._button_container)

        outer.addWidget(self._frame)

    def _clear_buttons(self):
        while self._button_row.count():
            item = self._button_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_start(self, meeting_name, elapsed_seconds):
        """Show meeting start notification with [Record] and [Dismiss] buttons."""
        self._clear_buttons()
        self._frame.setProperty("mode", "start")
        self._frame.style().unpolish(self._frame)
        self._frame.style().polish(self._frame)

        self._icon_label.setPixmap(colored_pixmap("phone-incoming", "#9184d9", 16))
        display_name = meeting_name or "A meeting"
        self._title_label.setText(display_name)
        self._body_label.setText(format_start_text(meeting_name, elapsed_seconds))

        self._button_row.addStretch()

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setObjectName("toastDismissBtn")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self._on_dismiss)
        self._button_row.addWidget(dismiss_btn)

        record_btn = QPushButton("Record")
        record_btn.setObjectName("toastRecordBtn")
        record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        record_btn.clicked.connect(self._on_record)
        self._button_row.addWidget(record_btn)

        self._reposition()
        self.show()
        self.raise_()
        self._auto_close_timer.start()

    def show_end(self, meeting_name, recorded_seconds):
        """Show meeting end notification with [Stop & save], [Pause], [Keep recording]."""
        self._clear_buttons()
        self._frame.setProperty("mode", "end")
        self._frame.style().unpolish(self._frame)
        self._frame.style().polish(self._frame)

        self._icon_label.setPixmap(colored_pixmap("stop", "#f9e2af", 16))
        display_name = meeting_name or "The meeting"
        self._title_label.setText("Meeting Ended")
        self._body_label.setText(format_end_text(meeting_name, recorded_seconds))

        self._button_row.addStretch()

        keep_btn = QPushButton("Keep")
        keep_btn.setObjectName("toastDismissBtn")
        keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        keep_btn.clicked.connect(lambda checked=False: self._on_end("keep"))
        self._button_row.addWidget(keep_btn)

        pause_btn = QPushButton("Pause")
        pause_btn.setObjectName("toastPauseBtn")
        pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pause_btn.clicked.connect(lambda checked=False: self._on_end("pause"))
        self._button_row.addWidget(pause_btn)

        stop_btn = QPushButton("Stop & save")
        stop_btn.setObjectName("toastRecordBtn")
        stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stop_btn.clicked.connect(lambda checked=False: self._on_end("stop"))
        self._button_row.addWidget(stop_btn)

        self._reposition()
        self.show()
        self.raise_()
        self._auto_close_timer.start()

    def hide_and_clear(self):
        self._auto_close_timer.stop()
        self._clear_buttons()
        self.hide()

    def _reposition(self):
        self.adjustSize()
        from app.utils.screen_utils import position_corner_on_active_screen
        position_corner_on_active_screen(self, corner="bottom-right", margin=20, reference_widget=self.parent())

    def _on_record(self):
        self.hide_and_clear()
        self.record_accepted.emit()

    def _on_dismiss(self):
        self.hide_and_clear()
        self.dismissed.emit()

    def _on_end(self, action):
        self.hide_and_clear()
        self.end_chosen.emit(action)

    def enterEvent(self, event):
        """Pause auto-close when user hovers over toast."""
        self._auto_close_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Resume auto-close when user stops hovering."""
        self._auto_close_timer.start()
        super().leaveEvent(event)
