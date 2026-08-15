"""Floating activity indicator shown when TalkTrack is minimized while busy.

Pure helpers are module-level and unit-testable, mirroring tray_icon.py's
pattern. The Qt widget (ActivityIndicator) comes in a later task and
composes them with QPainter.
"""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from app.recording.recorder import RecordingState


def resolve_activity_state(recording_state, transcription_busy):
    """Return "recording" | "paused" | "transcribing" | None.

    Recording/paused always wins over transcribing — if both are happening
    (e.g. auto-transcribe kicked off for a prior recording while a new one
    is being captured), the widget shows the recording, not the transcript
    job. None means nothing to show.
    """
    if recording_state == RecordingState.RECORDING:
        return "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy:
        return "transcribing"
    return None


def format_activity_label(state, elapsed_seconds=None, progress_percent=None):
    """"MM:SS" for "recording"/"paused"; "NN%" for "transcribing"."""
    if state in ("recording", "paused"):
        total = max(0, int(elapsed_seconds or 0))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"
    if state == "transcribing":
        return f"{int(progress_percent or 0)}%"
    return ""


def resolve_dot_color(state):
    """Hex color for the state dot: red/amber/blue."""
    return {
        "recording": "#f38ba8",
        "paused": "#f9e2af",
        "transcribing": "#89b4fa",
    }.get(state)


_WIDTH = 130
_HEIGHT = 36
_DRAG_THRESHOLD = 4
_PULSE_INTERVAL_MS = 800
_DOT_DIAMETER = 10
_DOT_MARGIN = 12


class ActivityIndicator(QWidget):
    """Floating always-on-top pill shown while minimized and busy.

    MainWindow owns one instance and is the sole place that decides when
    it shows, hides, or updates (see MainWindow._update_activity_visibility).
    """

    restore_requested = pyqtSignal()
    position_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(_WIDTH, _HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = None
        self._label = ""
        self._dot_color = None
        self._dot_visible = True

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(_PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._press_pos = None
        self._press_widget_pos = None
        self._moved_distance = 0

    def set_activity(self, state, elapsed_seconds=None, progress_percent=None):
        self._state = state
        self._label = format_activity_label(state, elapsed_seconds, progress_percent)
        self._dot_color = resolve_dot_color(state)
        if state == "recording":
            self._dot_visible = True
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._dot_visible = True
        self.update()

    def _toggle_pulse(self):
        self._dot_visible = not self._dot_visible
        self.update()

    def show_at(self, x, y):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        clamped_x = min(max(x, geo.left()), geo.right() - _WIDTH)
        clamped_y = min(max(y, geo.top()), geo.bottom() - _HEIGHT)
        self.move(clamped_x, clamped_y)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1e1e2e"))
        painter.drawRoundedRect(self.rect(), _HEIGHT / 2, _HEIGHT / 2)

        if self._dot_color and self._dot_visible:
            painter.setBrush(QColor(self._dot_color))
            dot_y = (_HEIGHT - _DOT_DIAMETER) // 2
            painter.drawEllipse(_DOT_MARGIN, dot_y, _DOT_DIAMETER, _DOT_DIAMETER)

        painter.setPen(QColor("#cdd6f4"))
        text_rect = self.rect().adjusted(_DOT_MARGIN + _DOT_DIAMETER + 8, 0, -10, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_widget_pos = self.pos()
            self._moved_distance = 0
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            delta = event.globalPosition().toPoint() - self._press_pos
            self._moved_distance = max(
                self._moved_distance, abs(delta.x()) + abs(delta.y())
            )
            self.move(self._press_widget_pos + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._moved_distance <= _DRAG_THRESHOLD:
            self.restore_requested.emit()
        else:
            self.position_changed.emit(self.x(), self.y())
        self._press_pos = None
        self._press_widget_pos = None
