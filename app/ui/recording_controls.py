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

_CAPTURE_ICON_COLORS = {"ready": "#75798c", "warning": "#f9e2af", "blocked": "#f38ba8"}

_DIVIDER_COLOR = "#292b31"


def _v_divider(height):
    """A hairline vertical rule for the capture bar.

    Shape.NoFrame rather than VLine: Qt paints its own etched line for a
    VLine underneath whatever the stylesheet draws, so the two variants of
    this bar (one styled with background-color, one with border-left)
    rendered subtly differently from each other.
    """
    line = QFrame()
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedWidth(1)
    line.setFixedHeight(height)
    line.setStyleSheet(f"background-color: {_DIVIDER_COLOR};")
    return line


class _ClickableFrame(QFrame):
    """A QFrame that emits `clicked` on left-click — used for the
    "CAPTURING" sources block, which the mock shows as a single clickable
    card (not a QPushButton) so it can hold a two-line icon+text layout."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
        self.ready_widget.setMinimumHeight(76)
        self.ready_widget.setStyleSheet(
            "QFrame#captureBarReady { border-bottom: 1px solid #292b31; }"
        )
        ready_layout = QHBoxLayout(self.ready_widget)
        ready_layout.setContentsMargins(20, 12, 20, 12)

        self.preflight = PreflightWidget()
        ready_layout.addWidget(self.preflight)

        # Divider between the verdict and the "what's being captured" block.
        ready_layout.addWidget(_v_divider(50))

        # "CAPTURING" sources block — mic + call source at a glance,
        # clickable to open the sources dialog. Replaces the old bare
        # "Sources" button per the capture-bar design spec.
        self.capturing_block = _ClickableFrame()
        self.capturing_block.setObjectName("capturingBlock")
        self.capturing_block.setCursor(Qt.CursorShape.PointingHandCursor)
        self.capturing_block.setStyleSheet(
            "QFrame#capturingBlock {"
            " border: 1px solid #292b31; border-radius: 8px;"
            "}"
            "QFrame#capturingBlock:hover {"
            " background-color: rgba(233,233,237,0.05);"
            "}"
        )
        cb_layout = QHBoxLayout(self.capturing_block)
        cb_layout.setContentsMargins(15, 8, 15, 8)
        cb_layout.setSpacing(12)

        cb_text_layout = QVBoxLayout()
        cb_text_layout.setSpacing(3)
        cb_kicker = QLabel("CAPTURING")
        cb_kicker.setObjectName("sectionHeader")
        cb_kicker.setStyleSheet("color: #75798c;")
        cb_text_layout.addWidget(cb_kicker)

        cb_row = QHBoxLayout()
        cb_row.setSpacing(8)
        self.capturing_mic_icon = QLabel()
        self.capturing_mic_name = QLabel("")
        self.capturing_mic_name.setStyleSheet("font-size: 13px; color: #cfd3e5;")
        cb_plus = QLabel("+")
        cb_plus.setStyleSheet("color: #4d5063; font-size: 12px;")
        self.capturing_call_icon = QLabel()
        self.capturing_call_name = QLabel("")
        self.capturing_call_name.setStyleSheet("font-size: 13px; color: #cfd3e5;")
        cb_row.addWidget(self.capturing_mic_icon)
        cb_row.addWidget(self.capturing_mic_name)
        cb_row.addWidget(cb_plus)
        cb_row.addWidget(self.capturing_call_icon)
        cb_row.addWidget(self.capturing_call_name)
        cb_text_layout.addLayout(cb_row)

        cb_layout.addLayout(cb_text_layout)
        cb_caret = QLabel()
        cb_caret.setPixmap(colored_pixmap("caret-down", "#75798c", 12))
        cb_layout.addWidget(cb_caret)

        self.capturing_block.clicked.connect(self.sources_clicked.emit)
        ready_layout.addWidget(self.capturing_block)
        self.set_capturing("No microphone", "No source")

        ready_layout.addStretch(1)

        self.record_btn = QPushButton("Record")
        self.record_btn.setObjectName("recordAction")
        self.record_btn.setStyleSheet("font-size: 15px; font-weight: 600; padding: 12px 24px;")
        self._set_button_icon(self.record_btn, "record", "#f38ba8")
        self.record_btn.clicked.connect(self.record_clicked.emit)

        ready_layout.addWidget(self.record_btn)

        # 2) Recording Variant (also covers Paused)
        self.rec_widget = QFrame()
        self.rec_widget.setObjectName("captureBarActive")
        self.rec_widget.setMinimumHeight(76)
        # Same bottom rule as the Ready and Transcribing variants —
        # without it the separator under the bar disappeared the
        # moment recording started.
        self.rec_widget.setStyleSheet(
            "QFrame#captureBarActive { border-bottom: 1px solid #292b31; }"
        )
        rec_layout = QHBoxLayout(self.rec_widget)
        # Matches the Ready variant exactly; at 22 the whole bar
        # shifted 2px right when recording started.
        rec_layout.setContentsMargins(20, 12, 20, 12)
        rec_layout.setSpacing(17)

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

        rec_layout.addWidget(_v_divider(40))

        self.live_meters = LevelMeter()
        rec_layout.addWidget(self.live_meters, stretch=1)

        source_layout = QVBoxLayout()
        source_layout.setSpacing(1)
        self.source_line = QLabel("")
        self.source_line.setStyleSheet("font-size: 12px; color: #cfd3e5;")
        self.health_line = QLabel("")
        self.health_line.setStyleSheet("font-size: 11.5px; color: #75798c;")
        source_layout.addWidget(self.source_line)
        source_layout.addWidget(self.health_line)
        rec_layout.addLayout(source_layout)

        self.mute_btn = QPushButton("Mute mic")
        self.mute_btn.setStyleSheet("padding: 8px 14px; font-size: 13px;")
        self._set_button_icon(self.mute_btn, "microphone-slash", "#e9e9ed")
        self.mute_btn.clicked.connect(self.mute_clicked.emit)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setStyleSheet("padding: 8px 14px; font-size: 13px;")
        self._set_button_icon(self.pause_btn, "pause", "#e9e9ed")
        self.pause_btn.clicked.connect(self.pause_clicked.emit)

        self.stop_btn = QPushButton("Stop && transcribe")
        self.stop_btn.setStyleSheet("border-color: #f38ba8; color: #f38ba8; padding: 8px 16px; font-size: 13px; font-weight: 600;")
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
            " border-bottom: 1px solid #423a6a;"
            "}"
        )
        ts_layout = QHBoxLayout(self.transcribing_strip)
        ts_layout.setContentsMargins(22, 10, 17, 10)
        ts_layout.setSpacing(11)

        self.transcribing_icon = QLabel()
        self.transcribing_icon.setPixmap(colored_pixmap("waveform", "#9184d9", 15))
        ts_layout.addWidget(self.transcribing_icon)

        self.transcribing_label = QLabel("Transcribing…")
        self.transcribing_label.setStyleSheet("font-size: 12.5px; color: #d2cefd;")
        ts_layout.addWidget(self.transcribing_label)

        self.transcribing_bar = QProgressBar()
        self.transcribing_bar.setTextVisible(False)
        self.transcribing_bar.setFixedSize(220, 4)
        self.transcribing_bar.setRange(0, 100)
        ts_layout.addWidget(self.transcribing_bar)

        self.transcribing_percent = QLabel("")
        self.transcribing_percent.setStyleSheet(
            "font-size: 11.5px; color: #9397ab; font-family: Consolas, monospace;"
        )
        ts_layout.addWidget(self.transcribing_percent, stretch=1)

        self.transcribing_queued_label = QLabel("")
        self.transcribing_queued_label.setStyleSheet("font-size: 11.5px; color: #75798c;")
        ts_layout.addWidget(self.transcribing_queued_label)

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

    def set_transcribing(self, active, percent=None, name=None, elapsed_seconds=None, queued=0):
        """Show/update the slim transcribing strip below the capture bar,
        mirroring CompactStrip's "transcribing" state so both surfaces
        agree about background work happening between recordings.

        `name` and `elapsed_seconds` surface the job's identity and pace —
        per the design spec this strip is where that information belongs,
        rather than buried in one transcript tab (see
        transcript_viewer._format_progress_text, which computes the same
        elapsed/remaining shape for the tab-local status label)."""
        self.transcribing_strip.setVisible(active)
        if not active:
            return
        self.transcribing_label.setText(
            f"Transcribing <b>{name}</b>" if name else "Transcribing…"
        )
        if percent is None:
            self.transcribing_bar.setRange(0, 0)  # indeterminate
            self.transcribing_percent.setText("")
        else:
            self.transcribing_bar.setRange(0, 100)
            self.transcribing_bar.setValue(int(percent))
            if elapsed_seconds is not None:
                em, es = divmod(int(elapsed_seconds), 60)
                pct = int(percent)
                text = f"{pct}% · {em:02d}:{es:02d} elapsed"
                if 0 < pct < 100:
                    remaining = elapsed_seconds * (100 - pct) / pct
                    rm, rs = divmod(int(remaining), 60)
                    text += f" · ~{rm:02d}:{rs:02d} left"
                self.transcribing_percent.setText(text)
            else:
                self.transcribing_percent.setText(f"{int(percent)}%")
        self.transcribing_queued_label.setText(
            f"{queued} more queued" if queued else ""
        )

    def set_capturing(self, mic_name, call_name, mic_state="ready", call_state="ready"):
        """Update the "CAPTURING" sources block next to the pre-flight
        verdict. `mic_state`/`call_state` are preflight_status severities
        ("ready"/"warning"/"blocked") — only that side's icon tints to flag
        the problem, the name text itself stays neutral (matches the mock:
        the icon carries the warning, not the whole line)."""
        mic_color = _CAPTURE_ICON_COLORS.get(mic_state, _CAPTURE_ICON_COLORS["ready"])
        call_color = _CAPTURE_ICON_COLORS.get(call_state, _CAPTURE_ICON_COLORS["ready"])
        self.capturing_mic_icon.setPixmap(colored_pixmap("microphone", mic_color, 14))
        self.capturing_mic_name.setText(mic_name)
        self.capturing_call_icon.setPixmap(colored_pixmap("speaker-high", call_color, 14))
        self.capturing_call_name.setText(call_name)

    def set_source_summary(self, source_text, health_text=""):
        """Two-line source/health block next to the level meters — what's
        being captured and whether it's actually coming through."""
        self.source_line.setText(source_text)
        self.health_line.setText(health_text)

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
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {tint}, stop:1 transparent);"
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
