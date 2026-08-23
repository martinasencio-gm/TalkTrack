import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QProgressBar,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon, QMouseEvent
import os

from app.recording.recorder import RecordingState
from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)


def resolve_compact_strip_state(recording_state, muted, transcription_busy, done):
    """Map recorder/mute/transcription state to one of CompactStrip's six
    states: armed | recording | paused | muted | transcribing | done.

    Mirrors activity_indicator.resolve_activity_state's precedence
    (recording/paused always wins over transcribing) and extends it with
    the mute and idle-terminal states CompactStrip also needs. `done` is a
    caller-tracked flag: True once the current recording's transcription
    has finished and hasn't yet been superseded by a new recording.
    """
    if recording_state == RecordingState.RECORDING:
        return "muted" if muted else "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy:
        return "transcribing"
    if done:
        return "done"
    return "armed"


class CompactStrip(QWidget):
    """
    A frameless, always-on-top window that sits over the user's call window.
    This replaces ActivityIndicator, MeetingBanner, MeetingNotificationToast, and tray balloons.
    """
    expand_requested = pyqtSignal()
    record_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    mute_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    open_transcript_requested = pyqtSignal()
    position_changed = pyqtSignal(int, int)
    full_ui_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_start_pos = None
        self._setup_ui()
        
    def _setup_ui(self):
        self.setFixedSize(700, 76)
        
        # Flags: frameless, always on top, tool window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        
        # Ensure translucency if we were doing custom rounded corners, 
        # but styling is generally handled by QSS or overriding paintEvent.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.frame = QFrame(self)
        self.frame.setObjectName("compactStripFrame")
        self.frame.setStyleSheet("""
            QFrame#compactStripFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1b1d2c, stop:1 #14161f);
                border: 1px solid rgba(145,132,217,0.30);
                border-radius: 14px;
            }
        """)
        
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(18, 0, 16, 0)
        frame_layout.setSpacing(15)
        
        # Mark (Left) — per-state Phosphor icon, pulsing while recording/muted
        # (ICONS.md "Drawn elements that are not icons": opacity animation,
        # not a GIF), with a drag-hint icon underneath.
        self.mark_layout = QVBoxLayout()
        self.mark_layout.setSpacing(2)
        self.mark_icon = QLabel()
        self.mark_icon.setFixedSize(16, 16)
        self._mark_opacity_effect = QGraphicsOpacityEffect(self.mark_icon)
        self._mark_opacity_effect.setOpacity(1.0)
        self.mark_icon.setGraphicsEffect(self._mark_opacity_effect)
        self._mark_pulse_anim = QPropertyAnimation(self._mark_opacity_effect, b"opacity", self)
        self._mark_pulse_anim.setDuration(1600)
        self._mark_pulse_anim.setKeyValueAt(0.0, 1.0)
        self._mark_pulse_anim.setKeyValueAt(0.5, 0.35)
        self._mark_pulse_anim.setKeyValueAt(1.0, 1.0)
        self._mark_pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._mark_pulse_anim.setLoopCount(-1)
        self.mark_layout.addWidget(self.mark_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        self.drag_hint = QLabel()
        self.drag_hint.setPixmap(colored_pixmap("dots-six-vertical", "#595d6c", 10))
        self.drag_hint.setFixedSize(10, 10)
        self.mark_layout.addWidget(self.drag_hint, alignment=Qt.AlignmentFlag.AlignCenter)

        frame_layout.addLayout(self.mark_layout)
        
        # Title block
        self.title_layout = QVBoxLayout()
        self.title_layout.setSpacing(2)
        self.title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.title_label = QLabel("Recording Name")
        self.title_label.setStyleSheet("font-size: 14.5px; font-weight: 600;")
        
        self.subtitle_label = QLabel("Teams is in a call · mic and system audio ready")
        self.subtitle_label.setStyleSheet("font-size: 11.5px; color: #9397ab;")
        
        self.title_layout.addWidget(self.title_label)
        self.title_layout.addWidget(self.subtitle_label)
        
        frame_layout.addLayout(self.title_layout, stretch=1)
        
        # Timer
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("timerLabel")
        frame_layout.addWidget(self.timer_label)
        
        # Meters — mic/speaker-high icons alongside each level row
        self.meters_layout = QVBoxLayout()
        self.meters_layout.setSpacing(4)
        self.meters_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(4)
        self.mic_icon_label = QLabel()
        self.mic_icon_label.setPixmap(colored_pixmap("microphone", "#75798c", 11))
        self.mic_icon_label.setFixedSize(11, 11)
        self.mic_meter = QProgressBar()
        self.mic_meter.setFixedSize(78, 6)
        mic_row.addWidget(self.mic_icon_label)
        mic_row.addWidget(self.mic_meter)

        sys_row = QHBoxLayout()
        sys_row.setSpacing(4)
        self.sys_icon_label = QLabel()
        self.sys_icon_label.setPixmap(colored_pixmap("speaker-high", "#75798c", 11))
        self.sys_icon_label.setFixedSize(11, 11)
        self.sys_meter = QProgressBar()
        self.sys_meter.setFixedSize(78, 6)
        sys_row.addWidget(self.sys_icon_label)
        sys_row.addWidget(self.sys_meter)

        self.meters_layout.addLayout(mic_row)
        self.meters_layout.addLayout(sys_row)

        frame_layout.addLayout(self.meters_layout)
        
        # Divider
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.Shape.VLine)
        self.divider.setStyleSheet("border-left: 1px solid #292b31; max-height: 40px;")
        frame_layout.addWidget(self.divider)
        
        # Actions
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(6)
        
        # Mute/pause: only shown during "recording"/"muted" — the two states
        # where btn_secondary is hidden, so they occupy that freed space
        # rather than adding a 5th slot. See set_state()'s recording/muted
        # branches for their icon and visibility per state.
        self.btn_mute = QPushButton()
        self.btn_mute.setFixedSize(36, 36)
        self.btn_mute.setIconSize(QSize(16, 16))
        self.btn_mute.hide()

        self.btn_pause = QPushButton()
        self.btn_pause.setFixedSize(36, 36)
        self.btn_pause.setIconSize(QSize(16, 16))
        self._set_icon(self.btn_pause, "pause", "#e9e9ed")
        self.btn_pause.hide()

        self.btn_primary = QPushButton("Record")
        self.btn_primary.setObjectName("primaryAction")
        self.btn_primary.setIconSize(QSize(12, 12))
        self.btn_primary.setFixedHeight(36)
        self._set_icon(self.btn_primary, "record-fill", "#9184d9")

        self.btn_secondary = QPushButton("Cancel")
        self.btn_secondary.setIconSize(QSize(12, 12))
        self.btn_secondary.setFixedHeight(36)
        self.btn_secondary.setStyleSheet("padding: 0 10px;")
        self.btn_secondary.hide()

        self.btn_expand = QPushButton()
        self.btn_expand.setFixedSize(30, 36)
        self.btn_expand.setIconSize(QSize(16, 16))
        self.btn_expand.setToolTip("Expand")
        self._set_icon(self.btn_expand, "arrows-out-simple", "#9184d9")

        self.actions_layout.addWidget(self.btn_mute)
        self.actions_layout.addWidget(self.btn_pause)
        self.actions_layout.addWidget(self.btn_secondary)
        self.actions_layout.addWidget(self.btn_primary)
        self.actions_layout.addWidget(self.btn_expand)
        
        frame_layout.addLayout(self.actions_layout)
        
        self.main_layout.addWidget(self.frame)
        
        # Connect signals
        self.btn_expand.clicked.connect(self.expand_requested.emit)
        self.btn_primary.clicked.connect(self._on_primary_clicked)
        self.btn_secondary.clicked.connect(self._on_secondary_clicked)
        self.btn_mute.clicked.connect(self.mute_requested.emit)
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        
        # Default state
        self.current_state = "armed"
        
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            self.move(event.globalPosition().toPoint() - self.drag_start_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.drag_start_pos = None
        event.accept()
        self.position_changed.emit(self.x(), self.y())

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = None
            self.full_ui_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


    def _on_primary_clicked(self):
        if self.current_state in ("armed", "transcribing", "done"):
            self.record_requested.emit()
        elif self.current_state in ("recording", "muted", "paused"):
            self.stop_requested.emit()
            
    def _on_secondary_clicked(self):
        if self.current_state == "transcribing":
            self.cancel_requested.emit()
        elif self.current_state == "paused":
            self.resume_requested.emit()
        elif self.current_state == "done":
            self.open_transcript_requested.emit()

    def set_state(self, state: str, **kwargs):
        """
        States: armed, recording, paused, muted, transcribing, done
        """
        self.current_state = state
        
        if state == "armed":
            self.frame.setStyleSheet(self._frame_style("rgba(145,132,217,0.30)"))
            self._set_mark_icon("phone-incoming", "#9184d9")
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("primaryAction")
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record-fill", "#9184d9")
            self.btn_secondary.hide()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.hide()
            self.mic_meter.show()
            self.mic_icon_label.show()
            self.sys_meter.show()
            self.sys_icon_label.show()

        elif state == "recording":
            self.frame.setStyleSheet(self._frame_style("rgba(243,139,168,0.35)"))
            self._set_mark_icon("record-fill", "#f38ba8")
            self._set_mark_pulsing(True)
            self.btn_primary.setText("Stop")
            self.btn_primary.setStyleSheet("padding: 0 10px; border-color: #f38ba8; color: #f38ba8;")
            self._set_icon(self.btn_primary, "stop-fill", "#f38ba8")
            self.btn_secondary.hide()
            self._set_icon(self.btn_mute, "microphone", "#e9e9ed")
            self.btn_mute.setStyleSheet("")
            self.btn_mute.show()
            self.btn_pause.show()
            self.timer_label.show()
            self.timer_label.setStyleSheet("color: #e9e9ed;")
            self.mic_meter.show()
            self.mic_icon_label.show()
            self.sys_meter.show()
            self.sys_icon_label.show()

        elif state == "paused":
            self.frame.setStyleSheet(self._frame_style("rgba(249,226,175,0.45)"))
            self._set_mark_icon("pause-fill", "#f9e2af")
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Stop")
            self.btn_primary.setStyleSheet("padding: 0 10px; border-color: #f38ba8; color: #f38ba8;")
            self._set_icon(self.btn_primary, "stop-fill", "#f38ba8")
            self.btn_secondary.setText("Resume")
            self._set_icon(self.btn_secondary, "play-fill", "#e9e9ed")
            self.btn_secondary.show()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.show()
            self.timer_label.setStyleSheet("color: #f9e2af;")

        elif state == "muted":
            # Same red edge as "recording" (per spec — mute doesn't deepen
            # the strip's own outline); the distinct cue is the mic button
            # itself flipping to a filled mic-slash icon below, plus the
            # timer staying normal-colored rather than following the mark.
            self.frame.setStyleSheet(self._frame_style("rgba(243,139,168,0.35)"))
            self._set_mark_icon("record-fill", "#f38ba8")
            self._set_mark_pulsing(True)
            self.btn_primary.setText("Stop")
            self.btn_primary.setStyleSheet("padding: 0 10px; border-color: #f38ba8; color: #f38ba8;")
            self._set_icon(self.btn_primary, "stop-fill", "#f38ba8")
            self.btn_secondary.hide()
            self._set_icon(self.btn_mute, "microphone-slash", "#f38ba8")
            self.btn_mute.setStyleSheet("border-color: #f38ba8;")
            self.btn_mute.show()
            self.btn_pause.show()
            self.timer_label.show()
            self.timer_label.setStyleSheet("color: #e9e9ed;")
            self.mic_meter.hide()
            self.mic_icon_label.hide()
            self.sys_meter.show()
            self.sys_icon_label.show()

        elif state == "transcribing":
            self.frame.setStyleSheet(self._frame_style("rgba(145,132,217,0.30)"))
            self._set_mark_icon("waveform", "#9184d9")
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("primaryAction")
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record-fill", "#9184d9")
            self.btn_secondary.setText("Cancel")
            self._set_icon(self.btn_secondary, "x", "#e9e9ed")
            self.btn_secondary.show()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.hide()
            self.mic_meter.hide()
            self.mic_icon_label.hide()
            self.sys_meter.hide()
            self.sys_icon_label.hide()

        elif state == "done":
            self.frame.setStyleSheet(self._frame_style("rgba(166,227,161,0.35)"))
            self._set_mark_icon("check-circle", "#a6e3a1")
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("primaryAction")
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record-fill", "#9184d9")
            self.btn_secondary.setText("Open transcript")
            self._set_icon(self.btn_secondary, "arrow-square-out", "#e9e9ed")
            self.btn_secondary.show()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.hide()

        self.btn_primary.style().unpolish(self.btn_primary)
        self.btn_primary.style().polish(self.btn_primary)

    def _set_icon(self, button, icon_name, color):
        button.setIcon(QIcon(colored_pixmap(icon_name, color, 16)))

    def _set_mark_icon(self, icon_name, color):
        self.mark_icon.setPixmap(colored_pixmap(icon_name, color, 16))

    def _set_mark_pulsing(self, pulsing):
        if pulsing:
            if self._mark_pulse_anim.state() != QPropertyAnimation.State.Running:
                self._mark_pulse_anim.start()
        else:
            self._mark_pulse_anim.stop()
            self._mark_opacity_effect.setOpacity(1.0)
            
    def _frame_style(self, border_color):
        return f"""
            QFrame#compactStripFrame {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1b1d2c, stop:1 #14161f);
                border: 1px solid {border_color};
                border-radius: 14px;
            }}
        """

    def update_timer(self, time_str: str):
        self.timer_label.setText(time_str)
        
    def update_meters(self, mic_val: int, sys_val: int):
        self.mic_meter.setValue(mic_val)
        self.sys_meter.setValue(sys_val)
