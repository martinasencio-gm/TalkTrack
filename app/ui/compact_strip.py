import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QProgressBar,
    QGraphicsOpacityEffect, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon, QMouseEvent
import os

from app.recording.recorder import RecordingState
from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

# Meter trough/fill per spec: trough `#23252f`, fill `ok` — distinct from the
# global QSS QProgressBar rule (accent fill), which is for the transcribing
# progress bar, not these level meters.
_METER_STYLE = """
    QProgressBar { background-color: #23252f; border: none; border-radius: 3px; }
    QProgressBar::chunk { background-color: #a6e3a1; border-radius: 3px; }
"""

# Per-state edge color, shared by the full frame's inline setStyleSheet calls
# in set_state() and the pill frame's stylesheet in _sync_pill_for_state().
_STATE_EDGE_COLORS = {
    "idle": "rgba(63,66,77,0.9)",
    "armed": "rgba(145,132,217,0.30)",
    "recording": "rgba(243,139,168,0.35)",
    "paused": "rgba(249,226,175,0.45)",
    "muted": "rgba(243,139,168,0.35)",
    "transcribing": "rgba(145,132,217,0.30)",
    "done": "rgba(166,227,161,0.35)",
}


def resolve_compact_strip_state(recording_state, muted, transcription_busy, done,
                                meeting_active=False, ai_busy=False):
    """Map recorder/mute/transcription/meeting state to one of CompactStrip's
    seven states: idle | armed | recording | paused | muted | transcribing | done.

    Mirrors activity_indicator.resolve_activity_state's precedence
    (recording/paused always wins over transcribing) and extends it with
    the mute and idle-terminal states CompactStrip also needs. `done` is a
    caller-tracked flag: True once the current recording's transcription
    has finished and hasn't yet been superseded by a new recording.
    `ai_busy` (summary / action-item generation) rides the same
    "transcribing" state and outranks `done` — a summary starting after the
    transcript lands is still work in progress, not a finished recording.
    `meeting_active` distinguishes Idle (nothing detected, plain resting
    state) from Armed (a call is currently detected) per the design spec —
    Idle must not look like a degraded or waiting state.
    """
    if recording_state == RecordingState.RECORDING:
        return "muted" if muted else "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy or ai_busy:
        return "transcribing"
    if done:
        return "done"
    return "armed" if meeting_active else "idle"


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
    shrink_requested = pyqtSignal()     # double-click: one step along the
                                        # full -> compact_bar -> pill -> full
                                        # chain, which MainWindow owns
    variant_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_start_pos = None
        self._variant = "full"
        # Verb shown in the "transcribing" state — "Transcribing",
        # "Identifying speakers", "Generating summary", ... Reset on every
        # set_state() so a stale phase can't leak into the next job.
        self._phase_label = "Transcribing"
        self._setup_ui()
        
    def _setup_ui(self):
        # 2px over the frame's own 740x76: the layout below insets the
        # frame by 1px on every side so its antialiased 14px corner
        # radius and 1px border have a pixel to land on instead of
        # being cut off by the window boundary.
        self.setFixedSize(742, 78)
        
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
        self.main_layout.setContentsMargins(1, 1, 1, 1)
        
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
        # Symmetric, and non-zero vertically: at 0 the row's contents
        # ran into the frame's top and bottom border and through the
        # inward curve of its 14px corners.
        frame_layout.setContentsMargins(18, 6, 18, 6)
        frame_layout.setSpacing(15)
        
        # Mark (Left) — per-state Phosphor icon, pulsing while recording/muted
        # (ICONS.md "Drawn elements that are not icons": opacity animation,
        # not a GIF), with a drag-hint icon underneath.
        self.mark_layout = QVBoxLayout()
        self.mark_layout.setSpacing(2)
        self.mark_icon = QLabel()
        self.mark_icon.setFixedSize(14, 14)
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
        self.drag_hint.setPixmap(colored_pixmap("dots-six-vertical", "#3f424d", 10))
        self.drag_hint.setFixedSize(10, 10)
        self.mark_layout.addWidget(self.drag_hint, alignment=Qt.AlignmentFlag.AlignCenter)

        frame_layout.addLayout(self.mark_layout)
        
        # Title block
        self.title_layout = QVBoxLayout()
        self.title_layout.setSpacing(2)
        self.title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.title_label = QLabel("Recording Name")
        self.title_label.setStyleSheet("font-size: 14.5px; font-weight: 600;")
        self.title_label.setMinimumWidth(80)
        
        self.subtitle_label = QLabel("")
        self.subtitle_label.setStyleSheet("font-size: 11.5px; color: #9397ab;")
        self.subtitle_label.setMinimumWidth(80)
        
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
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setStyleSheet(_METER_STYLE)
        mic_row.addWidget(self.mic_icon_label)
        mic_row.addWidget(self.mic_meter)

        sys_row = QHBoxLayout()
        sys_row.setSpacing(4)
        self.sys_icon_label = QLabel()
        self.sys_icon_label.setPixmap(colored_pixmap("speaker-high", "#75798c", 11))
        self.sys_icon_label.setFixedSize(11, 11)
        self.sys_meter = QProgressBar()
        self.sys_meter.setFixedSize(78, 6)
        self.sys_meter.setTextVisible(False)
        self.sys_meter.setStyleSheet(_METER_STYLE)
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
        self.btn_primary.setObjectName("recordAction")
        self.btn_primary.setIconSize(QSize(12, 12))
        self.btn_primary.setFixedHeight(36)
        self.btn_primary.setMinimumWidth(80)
        self.btn_primary.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._set_icon(self.btn_primary, "record", "#f38ba8")

        self.btn_secondary = QPushButton("Cancel")
        self.btn_secondary.setIconSize(QSize(12, 12))
        self.btn_secondary.setFixedHeight(36)
        self.btn_secondary.setMinimumWidth(80)
        self.btn_secondary.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_secondary.setStyleSheet("padding: 0 10px;")
        self.btn_secondary.hide()

        self.btn_collapse = QPushButton()
        self.btn_collapse.setFixedSize(30, 36)
        self.btn_collapse.setIconSize(QSize(14, 14))
        self.btn_collapse.setToolTip("Collapse to pill")
        self._set_icon(self.btn_collapse, "minus", "#75798c")

        self.btn_expand = QPushButton()
        self.btn_expand.setFixedSize(30, 36)
        self.btn_expand.setIconSize(QSize(16, 16))
        self.btn_expand.setToolTip("Expand")
        self._set_icon(self.btn_expand, "arrows-out-simple", "#9184d9")

        self.actions_layout.addWidget(self.btn_mute)
        self.actions_layout.addWidget(self.btn_pause)
        self.actions_layout.addWidget(self.btn_secondary)
        self.actions_layout.addWidget(self.btn_primary)
        self.actions_layout.addWidget(self.btn_collapse)
        self.actions_layout.addWidget(self.btn_expand)

        frame_layout.addLayout(self.actions_layout)

        self.main_layout.addWidget(self.frame)
        self._setup_pill_frame()
        self.main_layout.addWidget(self.pill_frame)
        self.pill_frame.hide()

        # Connect signals
        self.btn_expand.clicked.connect(self.expand_requested.emit)
        self.btn_collapse.clicked.connect(lambda: self.set_variant("pill"))
        self.btn_primary.clicked.connect(self._on_primary_clicked)
        self.btn_secondary.clicked.connect(self._on_secondary_clicked)
        self.btn_mute.clicked.connect(self.mute_requested.emit)
        self.btn_pause.clicked.connect(self.pause_requested.emit)

        # Default state
        self.current_state = "idle"

    def _setup_pill_frame(self):
        """232x44 minimal variant for screen-sharing: pulsing mark, timer,
        two compact meter bars, pause/stop icon buttons. Drops name/source
        per the Compact Bar design spec — a second frame swapped in by
        set_variant() rather than a second widget class, since it shares
        the mark/timer/meter state that set_state()/update_timer()/
        update_meters() already drive on the full frame."""
        self.pill_frame = QFrame(self)
        self.pill_frame.setObjectName("compactPillFrame")
        pill_layout = QHBoxLayout(self.pill_frame)
        pill_layout.setContentsMargins(10, 4, 10, 4)
        pill_layout.setSpacing(8)

        self.pill_mark_icon = QLabel()
        self.pill_mark_icon.setFixedSize(11, 11)
        self._pill_mark_opacity_effect = QGraphicsOpacityEffect(self.pill_mark_icon)
        self._pill_mark_opacity_effect.setOpacity(1.0)
        self.pill_mark_icon.setGraphicsEffect(self._pill_mark_opacity_effect)
        self._pill_mark_pulse_anim = QPropertyAnimation(self._pill_mark_opacity_effect, b"opacity", self)
        self._pill_mark_pulse_anim.setDuration(1600)
        self._pill_mark_pulse_anim.setKeyValueAt(0.0, 1.0)
        self._pill_mark_pulse_anim.setKeyValueAt(0.5, 0.35)
        self._pill_mark_pulse_anim.setKeyValueAt(1.0, 1.0)
        self._pill_mark_pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pill_mark_pulse_anim.setLoopCount(-1)
        pill_layout.addWidget(self.pill_mark_icon)

        self.pill_status_label = QLabel("Ready")
        self.pill_status_label.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #cfd3e5;"
        )
        pill_layout.addWidget(self.pill_status_label)

        self.pill_timer = QLabel("00:00:00")
        self.pill_timer.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 15px; font-weight: 600;"
        )
        pill_layout.addWidget(self.pill_timer)

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

        # Shown instead of pause/stop outside recording/muted/paused — the
        # pill otherwise has no way to start a recording without expanding
        # back to the full bar first.
        self.pill_btn_record = QPushButton()
        self.pill_btn_record.setFixedSize(28, 28)
        self.pill_btn_record.setIconSize(QSize(14, 14))
        self._set_icon(self.pill_btn_record, "record", "#f38ba8")
        self.pill_btn_record.clicked.connect(self.record_requested.emit)
        pill_layout.addWidget(self.pill_btn_record)

        self.pill_btn_pause = QPushButton()
        self.pill_btn_pause.setFixedSize(28, 28)
        self.pill_btn_pause.setIconSize(QSize(14, 14))
        self._set_icon(self.pill_btn_pause, "pause", "#e9e9ed")
        self.pill_btn_pause.clicked.connect(self.pause_requested.emit)
        pill_layout.addWidget(self.pill_btn_pause)

        self.pill_btn_stop = QPushButton()
        self.pill_btn_stop.setFixedSize(28, 28)
        self.pill_btn_stop.setIconSize(QSize(14, 14))
        self._set_icon(self.pill_btn_stop, "stop-fill", "#f38ba8")
        self.pill_btn_stop.clicked.connect(self.stop_requested.emit)
        pill_layout.addWidget(self.pill_btn_stop)
        
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
        """Double-click always shrinks one step; the strip doesn't decide
        where that lands. MainWindow walks the chain (window_presentation)
        so the same gesture means the same thing on the capture bar, the
        compact bar and the pill."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = None
            self.shrink_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def variant(self) -> str:
        """"full" or "pill" — which size the strip is currently showing."""
        return self._variant

    def set_variant(self, variant: str):
        """variant: "full" or "pill" — the frames are 700x76 and 280x44,
        each inside a 1px window margin (see _setup_ui). The Compact Bar
        spec is explicit that the pill is "the same class ... with a
        different fixed size ... not a second widget", so this swaps which
        internal frame is visible rather than constructing a second
        CompactStrip. Emits variant_changed so MainWindow can persist the
        choice; does nothing if already in that variant."""
        if variant not in ("full", "pill") or variant == self._variant:
            return
        self._variant = variant
        if variant == "pill":
            self.frame.hide()
            self.pill_frame.show()
            self.setFixedSize(282, 46)
        else:
            self.pill_frame.hide()
            self.frame.show()
            self.setFixedSize(742, 78)
        self.variant_changed.emit(variant)


    def _on_primary_clicked(self):
        if self.current_state in ("idle", "armed", "transcribing", "done"):
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

        kwargs: phase_label — the verb for the "transcribing" state
        ("Transcribing" default, or "Identifying speakers" / "Generating
        summary" / "Extracting action items"). Reset to the default on
        every call.
        """
        self.current_state = state
        self._phase_label = kwargs.get("phase_label") or "Transcribing"

        if state == "idle":
            # The resting state: nothing is wrong, nothing is pending — a
            # plain neutral hairline with a live Record button. The mark
            # is `record` regular weight (outline, "available"), not the
            # filled dot recording uses ("running").
            self.frame.setStyleSheet(self._frame_style("rgba(63,66,77,0.9)"))
            self._set_mark_icon("record", "#75798c", size=13)
            self._set_mark_pulsing(False)
            self.title_label.setText("Ready to record")
            self.title_label.setStyleSheet("font-size: 14.5px; font-weight: 600; color: #cfd3e5;")
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("recordAction")
            self.btn_primary.setMinimumWidth(86)
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record", "#f38ba8")
            self.btn_secondary.hide()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.hide()
            self.mic_meter.hide()
            self.mic_icon_label.hide()
            self.sys_meter.hide()
            self.sys_icon_label.hide()

        elif state == "armed":
            self.frame.setStyleSheet(self._frame_style("rgba(145,132,217,0.30)"))
            self._set_mark_icon("phone-incoming", "#9184d9", size=13)
            self._set_mark_pulsing(False)
            if self.title_label.text() == "Ready to record":
                self.title_label.setText("Recording Name")
            self.title_label.setStyleSheet("font-size: 14.5px; font-weight: 600; color: #e9e9ed;")
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("recordAction")
            self.btn_primary.setMinimumWidth(86)
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record", "#f38ba8")
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
            self._set_mark_icon("record-fill", "#f38ba8", size=11)
            self._set_mark_pulsing(True)
            if self.title_label.text() == "Ready to record":
                self.title_label.setText("Recording Name")
            self.title_label.setStyleSheet("font-size: 14.5px; font-weight: 600; color: #e9e9ed;")
            self.btn_primary.setText("Stop")
            self.btn_primary.setMinimumWidth(76)
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
            self._set_mark_icon("pause-fill", "#f9e2af", size=13)
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Stop")
            self.btn_primary.setMinimumWidth(76)
            self.btn_primary.setStyleSheet("padding: 0 10px; border-color: #f38ba8; color: #f38ba8;")
            self._set_icon(self.btn_primary, "stop-fill", "#f38ba8")
            self.btn_secondary.setText("Resume")
            self.btn_secondary.setMinimumWidth(88)
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
            self._set_mark_icon("record-fill", "#f38ba8", size=11)
            self._set_mark_pulsing(True)
            self.btn_primary.setText("Stop")
            self.btn_primary.setMinimumWidth(76)
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
            self._set_mark_icon("waveform", "#9184d9", size=13)
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("recordAction")
            self.btn_primary.setMinimumWidth(86)
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record", "#f38ba8")
            self.btn_secondary.setText("Cancel")
            self.btn_secondary.setMinimumWidth(76)
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
            self._set_mark_icon("check-circle", "#a6e3a1", size=13)
            self._set_mark_pulsing(False)
            self.btn_primary.setText("Record")
            self.btn_primary.setObjectName("recordAction")
            self.btn_primary.setMinimumWidth(86)
            self.btn_primary.setStyleSheet("padding: 0 10px;")
            self._set_icon(self.btn_primary, "record", "#f38ba8")
            self.btn_secondary.setText("Open transcript")
            self.btn_secondary.setMinimumWidth(130)
            self._set_icon(self.btn_secondary, "arrow-square-out", "#e9e9ed")
            self.btn_secondary.show()
            self.btn_mute.hide()
            self.btn_pause.hide()
            self.timer_label.hide()

        self.btn_primary.style().unpolish(self.btn_primary)
        self.btn_primary.style().polish(self.btn_primary)
        self._sync_pill_for_state(state)

    def _sync_pill_for_state(self, state):
        """Keep the pill's mark/timer/meters/buttons current even while it
        isn't the visible variant, so collapsing to it never shows stale
        content from whatever state was active last time it was shown."""
        edge_color = _STATE_EDGE_COLORS.get(state, _STATE_EDGE_COLORS["idle"])
        self.pill_frame.setStyleSheet(self._frame_style(edge_color, "compactPillFrame", 22))
        self.pill_timer.setText(self.timer_label.text())

        if state == "idle":
            self.pill_status_label.setText("Ready")
            self.pill_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #cfd3e5;")
            self.pill_status_label.show()
        elif state == "armed":
            self.pill_status_label.setText("Call Active")
            self.pill_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #9184d9;")
            self.pill_status_label.show()
        elif state == "recording":
            self.pill_status_label.setText("REC")
            self.pill_status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #f38ba8;")
            self.pill_status_label.show()
        elif state == "paused":
            self.pill_status_label.setText("PAUSED")
            self.pill_status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #f9e2af;")
            self.pill_status_label.show()
        elif state == "muted":
            self.pill_status_label.setText("MUTED")
            self.pill_status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #f38ba8;")
            self.pill_status_label.show()
        elif state == "transcribing":
            self.pill_status_label.setText(f"{self._phase_label}…")
            self.pill_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #9184d9;")
            self.pill_status_label.show()
        elif state == "done":
            self.pill_status_label.setText("Done")
            self.pill_status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #a6e3a1;")
            self.pill_status_label.show()

        if state in ("recording", "muted", "paused"):
            self.pill_timer.show()
            self.pill_btn_record.hide()
            self.pill_btn_stop.show()
            self.pill_btn_pause.show()
            if state == "paused":
                self.pill_timer.setStyleSheet(
                    "font-family: 'Consolas', monospace; font-size: 15px; "
                    "font-weight: 600; color: #f9e2af;"
                )
                self._set_icon(self.pill_btn_pause, "play-fill", "#e9e9ed")
            else:
                self.pill_timer.setStyleSheet(
                    "font-family: 'Consolas', monospace; font-size: 15px; "
                    "font-weight: 600; color: #e9e9ed;"
                )
                self._set_icon(self.pill_btn_pause, "pause", "#e9e9ed")
        else:
            self.pill_timer.hide()
            self.pill_btn_pause.hide()
            self.pill_btn_stop.hide()
            self.pill_btn_record.show()

        if state == "recording":
            self.pill_mic_meter.show()
            self.pill_sys_meter.show()
        elif state == "muted":
            self.pill_mic_meter.hide()
            self.pill_sys_meter.show()
        else:
            self.pill_mic_meter.hide()
            self.pill_sys_meter.hide()

    def _set_icon(self, button, icon_name, color):
        button.setIcon(QIcon(colored_pixmap(icon_name, color, 16)))

    def _set_mark_icon(self, icon_name, color, size=13):
        self.mark_icon.setPixmap(colored_pixmap(icon_name, color, size))
        self.pill_mark_icon.setPixmap(colored_pixmap(icon_name, color, 11))

    def _set_mark_pulsing(self, pulsing):
        if pulsing:
            if self._mark_pulse_anim.state() != QPropertyAnimation.State.Running:
                self._mark_pulse_anim.start()
            if self._pill_mark_pulse_anim.state() != QPropertyAnimation.State.Running:
                self._pill_mark_pulse_anim.start()
        else:
            self._mark_pulse_anim.stop()
            self._mark_opacity_effect.setOpacity(1.0)
            self._pill_mark_pulse_anim.stop()
            self._pill_mark_opacity_effect.setOpacity(1.0)

    def _frame_style(self, border_color, object_name="compactStripFrame", radius=14):
        return f"""
            QFrame#{object_name} {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1b1d2c, stop:1 #14161f);
                border: 1px solid {border_color};
                border-radius: {radius}px;
            }}
        """

    def set_subtitle(self, text: str):
        """Reflect the real pre-flight verdict (see preflight_status.compute_verdict)
        instead of the mockup placeholder this label shipped with — it must
        never assert a call/device status the app hasn't actually checked."""
        self.subtitle_label.setText(text)

    def update_timer(self, time_str: str):
        self.timer_label.setText(time_str)
        self.pill_timer.setText(time_str)

    def update_meters(self, mic_val: int, sys_val: int):
        self.mic_meter.setValue(mic_val)
        self.sys_meter.setValue(sys_val)
        self.pill_mic_meter.setValue(mic_val)
        self.pill_sys_meter.setValue(sys_val)
