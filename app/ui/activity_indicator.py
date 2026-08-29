"""Floating activity indicator shown when TalkTrack is minimized while busy.

Pure helpers are module-level and unit-testable, mirroring tray_icon.py's
pattern. ActivityIndicator (below) is the Qt widget that composes them.
"""
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QIcon, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QProgressBar, QGraphicsOpacityEffect,
)

from app.recording.recorder import RecordingState
from app.utils.icons import colored_pixmap
from app.ui import tokens


def resolve_activity_state(recording_state, transcription_busy, ai_busy=False):
    """Return "recording" | "paused" | "transcribing" | None.

    Recording/paused always wins over transcribing — if both are happening
    (e.g. auto-transcribe kicked off for a prior recording while a new one
    is being captured), the widget shows the recording, not the transcript
    job. `ai_busy` (summary / action-item generation) rides the same
    "transcribing" visual state per the "show it the same way" design, and
    sits below the real transcription workers in precedence. None means
    nothing to show.
    """
    if recording_state == RecordingState.RECORDING:
        return "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy or ai_busy:
        return "transcribing"
    return None


def format_activity_label(state, elapsed_seconds=None, progress_percent=None,
                          phase_label=None):
    """"MM:SS" or "HH:MM:SS" for "recording"/"paused"; "NN%" for "transcribing".

    `phase_label` (e.g. "Identifying speakers", "Generating summary") only
    applies to "transcribing" — when given it prefixes the verb; when None
    the bare percent/ellipsis form is kept.
    """
    if state in ("recording", "paused"):
        total = max(0, int(elapsed_seconds or 0))
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    if state == "transcribing":
        if progress_percent is None:
            return f"{phase_label}…" if phase_label else "…"
        pct = f"{int(progress_percent)}%"
        return f"{phase_label} {pct}" if phase_label else pct
    return ""


def resolve_dot_color(state):
    """Hex color for the state dot: red/amber/accent."""
    return {
        "recording": tokens.RED,
        "paused": tokens.YELLOW,
        "transcribing": tokens.ACCENT,
        "muted": tokens.RED,
    }.get(state)


_METER_STYLE = f"""
    QProgressBar {{ background-color: {tokens.SURFACE_2}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background-color: {tokens.GREEN}; border-radius: 3px; }}
"""

_STATE_EDGE_COLORS = {
    "idle": "rgba(63,66,77,0.9)",
    "recording": "rgba(243,139,168,0.35)",
    "paused": "rgba(249,226,175,0.45)",
    "muted": "rgba(243,139,168,0.35)",
    "transcribing": "rgba(145,132,217,0.30)",
    "done": "rgba(166,227,161,0.35)",
}

_BTN_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        border-radius: 6px;
    }
    QPushButton:hover {
        background: rgba(255, 255, 255, 0.08);
    }
    QPushButton:pressed {
        background: rgba(255, 255, 255, 0.14);
    }
"""


class ActivityIndicator(QWidget):
    """Floating always-on-top interactive pill widget shown while minimized/busy."""

    restore_requested = pyqtSignal()
    position_changed = pyqtSignal(int, int)
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    record_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(282, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = None
        self._label = ""
        self._drag_start_pos = None
        self._moved_distance = 0

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)

        self.pill_frame = QFrame(self)
        self.pill_frame.setObjectName("compactPillFrame")
        self._apply_frame_style("idle")

        pill_layout = QHBoxLayout(self.pill_frame)
        pill_layout.setContentsMargins(10, 4, 10, 4)
        pill_layout.setSpacing(8)

        # Pulsing mark dot
        self.pill_mark_icon = QLabel()
        self.pill_mark_icon.setFixedSize(11, 11)
        self._mark_opacity_effect = QGraphicsOpacityEffect(self.pill_mark_icon)
        self._mark_opacity_effect.setOpacity(1.0)
        self.pill_mark_icon.setGraphicsEffect(self._mark_opacity_effect)
        self._mark_pulse_anim = QPropertyAnimation(self._mark_opacity_effect, b"opacity", self)
        self._mark_pulse_anim.setDuration(1600)
        self._mark_pulse_anim.setKeyValueAt(0.0, 1.0)
        self._mark_pulse_anim.setKeyValueAt(0.5, 0.35)
        self._mark_pulse_anim.setKeyValueAt(1.0, 1.0)
        self._mark_pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._mark_pulse_anim.setLoopCount(-1)
        pill_layout.addWidget(self.pill_mark_icon)

        # Status text ("REC", "PAUSED", etc.)
        self.pill_status_label = QLabel("Ready")
        self.pill_status_label.setStyleSheet(f"font-size: {tokens.TYPE_BASE}; font-weight: 600; color: {tokens.TEXT_SECONDARY};")
        pill_layout.addWidget(self.pill_status_label)

        # Timer ("00:00:00")
        self.pill_timer = QLabel("00:00:00")
        self.pill_timer.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: {tokens.TYPE_LG}; font-weight: 600; color: {tokens.TEXT};"
        )
        pill_layout.addWidget(self.pill_timer)

        # Audio level meters
        pill_meters = QVBoxLayout()
        pill_meters.setSpacing(3)
        self.pill_mic_meter = QProgressBar()
        self.pill_mic_meter.setFixedSize(30, 4)
        self.pill_mic_meter.setTextVisible(False)
        self.pill_mic_meter.setStyleSheet(_METER_STYLE)
        self.pill_sys_meter = QProgressBar()
        self.pill_sys_meter.setFixedSize(30, 4)
        self.pill_sys_meter.setTextVisible(False)
        self.pill_sys_meter.setStyleSheet(_METER_STYLE)
        pill_meters.addWidget(self.pill_mic_meter)
        pill_meters.addWidget(self.pill_sys_meter)
        pill_layout.addLayout(pill_meters)

        pill_layout.addStretch(1)

        # Control buttons
        self.pill_btn_record = QPushButton()
        self.pill_btn_record.setFixedSize(28, 28)
        self.pill_btn_record.setIconSize(QSize(14, 14))
        self.pill_btn_record.setStyleSheet(_BTN_STYLE)
        self._set_btn_icon(self.pill_btn_record, "record", tokens.RED)
        self.pill_btn_record.clicked.connect(self.record_requested.emit)
        pill_layout.addWidget(self.pill_btn_record)

        self.pill_btn_pause = QPushButton()
        self.pill_btn_pause.setFixedSize(28, 28)
        self.pill_btn_pause.setIconSize(QSize(14, 14))
        self.pill_btn_pause.setStyleSheet(_BTN_STYLE)
        self._set_btn_icon(self.pill_btn_pause, "pause", tokens.TEXT)
        self.pill_btn_pause.clicked.connect(self._on_pause_clicked)
        pill_layout.addWidget(self.pill_btn_pause)

        self.pill_btn_stop = QPushButton()
        self.pill_btn_stop.setFixedSize(28, 28)
        self.pill_btn_stop.setIconSize(QSize(14, 14))
        self.pill_btn_stop.setStyleSheet(_BTN_STYLE)
        self._set_btn_icon(self.pill_btn_stop, "stop-fill", tokens.RED)
        self.pill_btn_stop.clicked.connect(self.stop_requested.emit)
        pill_layout.addWidget(self.pill_btn_stop)

        main_layout.addWidget(self.pill_frame)

    def _apply_frame_style(self, state):
        edge_color = _STATE_EDGE_COLORS.get(state, _STATE_EDGE_COLORS["idle"])
        self.pill_frame.setStyleSheet(f"""
            QFrame#compactPillFrame {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {tokens.SURFACE_3}, stop:1 {tokens.SURFACE_4});
                border: 1px solid {edge_color};
                border-radius: 22px;
            }}
        """)

    def _set_btn_icon(self, btn, name, color):
        btn.setIcon(QIcon(colored_pixmap(name, color, 16)))

    def _on_pause_clicked(self):
        if self._state == "paused":
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()

    def set_activity(self, state, elapsed_seconds=None, progress_percent=None,
                     phase_label="Transcribing"):
        self._state = state
        self._apply_frame_style(state)

        if elapsed_seconds is not None:
            total = max(0, int(elapsed_seconds))
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            self.pill_timer.setText(f"{h:02d}:{m:02d}:{s:02d}")

        if state == "recording":
            self.pill_status_label.setText("REC")
            self.pill_status_label.setStyleSheet(f"font-size: {tokens.TYPE_MD}; font-weight: 700; color: {tokens.RED};")
            self.pill_timer.setStyleSheet(f"font-family: 'Consolas', monospace; font-size: {tokens.TYPE_LG}; font-weight: 600; color: {tokens.TEXT};")
            self.pill_mark_icon.setPixmap(colored_pixmap("record", tokens.RED, 11))
            self._set_btn_icon(self.pill_btn_pause, "pause", tokens.TEXT)
            self.pill_timer.show()
            self.pill_btn_pause.show()
            self.pill_btn_stop.show()
            self.pill_btn_record.hide()
            self.pill_mic_meter.show()
            self.pill_sys_meter.show()
            if self._mark_pulse_anim.state() != QPropertyAnimation.State.Running:
                self._mark_pulse_anim.start()
        elif state == "paused":
            self.pill_status_label.setText("PAUSED")
            self.pill_status_label.setStyleSheet(f"font-size: {tokens.TYPE_MD}; font-weight: 700; color: {tokens.YELLOW};")
            self.pill_timer.setStyleSheet(f"font-family: 'Consolas', monospace; font-size: {tokens.TYPE_LG}; font-weight: 600; color: {tokens.YELLOW};")
            self.pill_mark_icon.setPixmap(colored_pixmap("pause", tokens.YELLOW, 11))
            self._set_btn_icon(self.pill_btn_pause, "play-fill", tokens.TEXT)
            self.pill_timer.show()
            self.pill_btn_pause.show()
            self.pill_btn_stop.show()
            self.pill_btn_record.hide()
            self.pill_mic_meter.hide()
            self.pill_sys_meter.hide()
            self._mark_pulse_anim.stop()
            self._mark_opacity_effect.setOpacity(1.0)
        elif state == "transcribing":
            label_text = (
                f"{phase_label} {int(progress_percent)}%"
                if progress_percent is not None else f"{phase_label}…"
            )
            self.pill_status_label.setText(label_text)
            self.pill_status_label.setStyleSheet(f"font-size: {tokens.TYPE_BASE}; font-weight: 600; color: {tokens.ACCENT};")
            self.pill_mark_icon.setPixmap(colored_pixmap("transcribe", tokens.ACCENT, 11))
            self.pill_timer.hide()
            self.pill_btn_pause.hide()
            self.pill_btn_stop.hide()
            self.pill_btn_record.hide()
            self.pill_mic_meter.hide()
            self.pill_sys_meter.hide()
            if self._mark_pulse_anim.state() != QPropertyAnimation.State.Running:
                self._mark_pulse_anim.start()
        else:
            self.pill_status_label.setText("Ready")
            self.pill_status_label.setStyleSheet(f"font-size: {tokens.TYPE_BASE}; font-weight: 600; color: {tokens.TEXT_SECONDARY};")
            self.pill_mark_icon.setPixmap(colored_pixmap("record", tokens.TEXT_SUBTLE, 11))
            self.pill_timer.hide()
            self.pill_btn_pause.hide()
            self.pill_btn_stop.hide()
            self.pill_btn_record.show()
            self.pill_mic_meter.hide()
            self.pill_sys_meter.hide()
            self._mark_pulse_anim.stop()
            self._mark_opacity_effect.setOpacity(1.0)

    def update_timer(self, text):
        self.pill_timer.setText(text)

    def update_meters(self, mic_value, sys_value):
        self.pill_mic_meter.setValue(max(0, min(100, int(mic_value))))
        self.pill_sys_meter.setValue(max(0, min(100, int(sys_value))))

    def show_at(self, x, y):
        screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        if screen is None:
            self.move(x, y)
            self.show()
            return
        geo = screen.availableGeometry()
        clamped_x = min(max(x, geo.left()), geo.right() - 282)
        clamped_y = min(max(y, geo.top()), geo.bottom() - 46)
        self.move(clamped_x, clamped_y)
        self.show()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved_distance = 0
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_start_pos - self.pos()
            self._moved_distance += abs(delta.x()) + abs(delta.y())
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_start_pos is not None:
            if self._moved_distance <= 4:
                self.restore_requested.emit()
            else:
                self.position_changed.emit(self.x(), self.y())
        self._drag_start_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.restore_requested.emit()
            event.accept()
