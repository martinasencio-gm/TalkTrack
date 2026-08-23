import logging
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QFrame,
    QStackedWidget, QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon
from app.recording.recorder import RecordingState
from app.ui.preflight import PreflightWidget
from app.ui.level_meter import LevelMeter
from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

_REC_MARK_ICON_SIZE = 18
_REC_MARK_BADGE_SIZE = 48


class RecordingControls(QWidget):
    """
    Horizontal capture bar spanning the full window.

    Shares CompactStrip's visual language (colored border per activity
    state, circular badge icon, title/subtitle typography) so the two
    surfaces read as one design rather than two unrelated bars — see
    the "1b Capture Bar" design-handoff mockup, which uses the same
    state-driven card treatment at full-window width.
    """

    record_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    mute_clicked = pyqtSignal()
    sources_clicked = pyqtSignal()
    test_mic_toggled = pyqtSignal(bool)
    compact_mode_requested = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._muted = False
        self._setup_ui()
        self.set_state(RecordingState.IDLE)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget()

        # 1) Ready Variant (Preflight embedded)
        self.ready_widget = QFrame()
        self.ready_widget.setObjectName("captureBarReady")
        self.ready_widget.setStyleSheet(
            "QFrame#captureBarReady { border-bottom: 1px solid #292b31; }"
        )
        ready_layout = QHBoxLayout(self.ready_widget)
        ready_layout.setContentsMargins(16, 0, 16, 0)

        self.preflight = PreflightWidget()
        ready_layout.addWidget(self.preflight, stretch=1)

        self.sources_btn = QPushButton("Sources")
        self.sources_btn.clicked.connect(self.sources_clicked.emit)

        self.record_btn = QPushButton("Record")
        self.record_btn.setObjectName("primaryAction")
        self._set_button_icon(self.record_btn, "record-fill", "#9184d9")
        self.record_btn.clicked.connect(self.record_clicked.emit)

        ready_layout.addWidget(self.sources_btn)
        ready_layout.addWidget(self.record_btn)

        # 2) Recording Variant (also covers Paused)
        self.rec_widget = QFrame()
        self.rec_widget.setObjectName("captureBarActive")
        rec_layout = QHBoxLayout(self.rec_widget)
        rec_layout.setContentsMargins(20, 0, 16, 0)
        rec_layout.setSpacing(18)

        self.rec_mark = QLabel()
        self.rec_mark.setFixedSize(_REC_MARK_BADGE_SIZE, _REC_MARK_BADGE_SIZE)
        self.rec_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rec_layout.addWidget(self.rec_mark)

        timer_layout = QVBoxLayout()
        timer_layout.setSpacing(1)
        self.rec_kicker = QLabel("RECORDING")
        self.rec_kicker.setObjectName("sectionHeader")
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("timerLabel")
        timer_layout.addWidget(self.rec_kicker)
        timer_layout.addWidget(self.timer_label)

        rec_layout.addLayout(timer_layout)

        self.live_meters = LevelMeter()
        rec_layout.addWidget(self.live_meters, stretch=1)

        self.mute_btn = QPushButton("Mute mic")
        self._set_button_icon(self.mute_btn, "microphone-slash", "#e9e9ed")
        self.mute_btn.clicked.connect(self.mute_clicked.emit)

        self.pause_btn = QPushButton("Pause")
        self._set_button_icon(self.pause_btn, "pause", "#e9e9ed")
        self.pause_btn.clicked.connect(self.pause_clicked.emit)

        self.stop_btn = QPushButton("Stop && transcribe")
        self.stop_btn.setStyleSheet("border-color: #f38ba8; color: #f38ba8;")
        self._set_button_icon(self.stop_btn, "stop-fill", "#f38ba8")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)

        rec_layout.addWidget(self.mute_btn)
        rec_layout.addWidget(self.pause_btn)
        rec_layout.addWidget(self.stop_btn)

        self.stack.addWidget(self.ready_widget)
        self.stack.addWidget(self.rec_widget)

        self.main_layout.addWidget(self.stack)

        # 3) Transcribing strip — coexists with the Ready variant (a
        # background job can be transcribing while idle, ready to record
        # the next call), mirroring the mockup's slim accent-tinted row.
        self.transcribing_strip = QFrame()
        self.transcribing_strip.setObjectName("captureBarTranscribing")
        self.transcribing_strip.setStyleSheet(
            "QFrame#captureBarTranscribing {"
            " background-color: rgba(145,132,217,0.07);"
            " border-bottom: 1px solid rgba(145,132,217,0.30);"
            "}"
        )
        ts_layout = QHBoxLayout(self.transcribing_strip)
        ts_layout.setContentsMargins(20, 6, 16, 6)
        ts_layout.setSpacing(10)

        self.transcribing_icon = QLabel()
        self.transcribing_icon.setPixmap(colored_pixmap("waveform", "#9184d9", 15))
        ts_layout.addWidget(self.transcribing_icon)

        self.transcribing_label = QLabel("Transcribing…")
        self.transcribing_label.setStyleSheet("font-size: 12.5px; color: #b8b3d9;")
        ts_layout.addWidget(self.transcribing_label)

        self.transcribing_bar = QProgressBar()
        self.transcribing_bar.setTextVisible(False)
        self.transcribing_bar.setRange(0, 100)
        ts_layout.addWidget(self.transcribing_bar, stretch=1)

        self.transcribing_percent = QLabel("")
        self.transcribing_percent.setStyleSheet(
            "font-size: 11.5px; color: #9397ab; font-family: Consolas, monospace;"
        )
        ts_layout.addWidget(self.transcribing_percent)

        self.transcribing_cancel_btn = QPushButton("Cancel")
        self.transcribing_cancel_btn.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
        self.transcribing_cancel_btn.clicked.connect(self.cancel_clicked.emit)
        ts_layout.addWidget(self.transcribing_cancel_btn)

        self.transcribing_strip.hide()
        self.main_layout.addWidget(self.transcribing_strip)

    def set_state(self, state):
        if state == RecordingState.IDLE:
            self.stack.setCurrentWidget(self.ready_widget)
            self.stop_btn.setEnabled(True)
            self.pause_btn.setEnabled(True)
            self.mute_btn.setEnabled(True)
        elif state == RecordingState.RECORDING:
            self.stack.setCurrentWidget(self.rec_widget)
            self.pause_btn.setText("Pause")
            self._set_button_icon(self.pause_btn, "pause", "#e9e9ed")
            self.rec_kicker.setText("RECORDING")
            self.rec_kicker.setStyleSheet("color: #f38ba8;")
            self._set_badge("record-fill", "#f38ba8")
            self.rec_widget.setStyleSheet(self._card_style("#f38ba8", "rgba(243,139,168,0.07)"))
            self.stop_btn.setEnabled(True)
            self.pause_btn.setEnabled(True)
            self.mute_btn.setEnabled(True)
        elif state == RecordingState.PAUSED:
            self.stack.setCurrentWidget(self.rec_widget)
            self.pause_btn.setText("Resume")
            self._set_button_icon(self.pause_btn, "play-fill", "#f9e2af")
            self.rec_kicker.setText("PAUSED")
            self.rec_kicker.setStyleSheet("color: #f9e2af;")
            self._set_badge("pause-fill", "#f9e2af")
            self.rec_widget.setStyleSheet(self._card_style("#f9e2af", "rgba(249,226,175,0.07)"))
        elif state in (RecordingState.STOPPING, RecordingState.PROCESSING):
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.mute_btn.setEnabled(False)

    def set_transcribing(self, active, percent=None):
        """Show/update the slim transcribing strip below the capture bar,
        mirroring CompactStrip's "transcribing" state so both surfaces
        agree about background work happening between recordings."""
        self.transcribing_strip.setVisible(active)
        if not active:
            return
        if percent is None:
            self.transcribing_bar.setRange(0, 0)  # indeterminate
            self.transcribing_percent.setText("")
        else:
            self.transcribing_bar.setRange(0, 100)
            self.transcribing_bar.setValue(int(percent))
            self.transcribing_percent.setText(f"{int(percent)}%")

    def update_time(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        self.timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def set_muted(self, muted):
        self._muted = bool(muted)
        self.mute_btn.setText("Muted" if self._muted else "Mute mic")
        if self._muted:
            self.mute_btn.setStyleSheet("border-color: #f38ba8; color: #f38ba8;")
            self._set_button_icon(self.mute_btn, "microphone-slash", "#f38ba8")
        else:
            self.mute_btn.setStyleSheet("")
            self._set_button_icon(self.mute_btn, "microphone-slash", "#e9e9ed")

    def reset_timer(self):
        self.timer_label.setText("00:00:00")

    def clear_test_mic(self):
        pass # Migrated to preflight / sources dialog

    def _set_badge(self, icon_name, color):
        radius = _REC_MARK_BADGE_SIZE // 2
        self.rec_mark.setStyleSheet(
            f"border: 1px solid {color}; border-radius: {radius}px; background: transparent;"
        )
        self.rec_mark.setPixmap(colored_pixmap(icon_name, color, _REC_MARK_ICON_SIZE))

    def _card_style(self, border_color, tint):
        return (
            f"QFrame#captureBarActive {{"
            f" background-color: {tint};"
            f" border-bottom: 1px solid {border_color};"
            f"}}"
        )

    def _set_button_icon(self, button, icon_name, color):
        button.setIcon(QIcon(colored_pixmap(icon_name, color, 14)))
        button.setIconSize(QSize(14, 14))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.compact_mode_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
