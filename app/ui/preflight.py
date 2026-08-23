import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal

from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

# state -> (color, icon name). Same three states drive the verdict icon and
# each of the three per-check icons.
_STATE_STYLE = {
    "ready": ("#a6e3a1", "check-circle-fill"),
    "warning": ("#f9e2af", "warning"),
    "blocked": ("#f38ba8", "warning-octagon"),
}

_VERDICT_ICON_SIZE = 18
_VERDICT_BADGE_SIZE = 40
_CHECK_ICON_SIZE = 20


class PreflightWidget(QWidget):
    """
    Shows the pre-flight verdict before recording begins.
    Folds in conferencing warnings, device mismatches, and mic tests.

    Single horizontal row per the capture-bar spec: verdict badge, title/
    subtitle, a short vertical divider, then the three checks inline —
    not a verdict row stacked above a separate checks row.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(22)

        # Verdict block: badge + title/subtitle
        self.verdict_icon = QLabel()
        self.verdict_icon.setFixedSize(_VERDICT_BADGE_SIZE, _VERDICT_BADGE_SIZE)
        self.verdict_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verdict_title = QLabel("Ready to record")
        self.verdict_title.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.verdict_subtitle = QLabel("Microphone and system audio ready")
        self.verdict_subtitle.setStyleSheet("color: #9397ab; font-size: 12px;")

        v_text_layout = QVBoxLayout()
        v_text_layout.setSpacing(2)
        v_text_layout.addWidget(self.verdict_title)
        v_text_layout.addWidget(self.verdict_subtitle)

        layout.addWidget(self.verdict_icon)
        layout.addLayout(v_text_layout)

        # Divider — short and vertical, between the verdict block and the
        # checks, both in the same row.
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedHeight(_VERDICT_BADGE_SIZE)
        divider.setStyleSheet("background-color: #292b31; max-width: 1px;")
        layout.addWidget(divider)

        # Three checks, inline with the verdict block
        self.checks_layout = QHBoxLayout()
        self.checks_layout.setSpacing(26)

        # Helper to build a check item
        def build_check(title, value):
            container = QWidget()
            c_layout = QHBoxLayout(container)
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setSpacing(8)
            icon = QLabel()
            t_layout = QVBoxLayout()
            t_layout.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("sectionHeader")
            val_lbl = QLabel(value)
            val_lbl.setStyleSheet("color: #cfd3e5; font-size: 12.5px;")
            t_layout.addWidget(title_lbl)
            t_layout.addWidget(val_lbl)
            c_layout.addWidget(icon)
            c_layout.addLayout(t_layout)
            return container, icon, val_lbl

        self.voice_widget, self.voice_icon, self.voice_val = build_check("YOUR VOICE", "Ready")
        self.call_widget, self.call_icon, self.call_val = build_check("THE CALL", "Ready")
        self.transcription_widget, self.transcription_icon, self.transcription_val = build_check("TRANSCRIPTION", "Ready")

        self.checks_layout.addWidget(self.voice_widget)
        self.checks_layout.addWidget(self.call_widget)
        self.checks_layout.addWidget(self.transcription_widget)

        layout.addLayout(self.checks_layout)
        layout.addStretch()

        self.set_verdict("ready")
        self.update_checks("ready", "Ready", "ready", "Ready", "ready", "Ready")

    def set_verdict(self, state, title="", subtitle=""):
        """
        state can be 'ready', 'warning', 'blocked'
        """
        color, icon_name = _STATE_STYLE.get(state, _STATE_STYLE["ready"])
        if state == "ready" and not title:
            title = "Ready to record"

        self.verdict_title.setText(title)
        self.verdict_subtitle.setText(subtitle)
        self.verdict_title.setStyleSheet(f"font-weight: 600; font-size: 15px; color: {color};")
        self.verdict_icon.setPixmap(colored_pixmap(icon_name, color, _VERDICT_ICON_SIZE))
        badge_radius = _VERDICT_BADGE_SIZE // 2
        self.verdict_icon.setStyleSheet(
            f"border: 1px solid {color}; border-radius: {badge_radius}px; background: transparent;"
        )

    def update_checks(self, voice_status, voice_val, call_status, call_val, trans_status, trans_val):
        self.voice_val.setText(voice_val)
        self.call_val.setText(call_val)
        self.transcription_val.setText(trans_val)
        self._set_check_icon(self.voice_icon, voice_status)
        self._set_check_icon(self.call_icon, call_status)
        self._set_check_icon(self.transcription_icon, trans_status)

    def _set_check_icon(self, label, status):
        color, icon_name = _STATE_STYLE.get(status, _STATE_STYLE["ready"])
        label.setPixmap(colored_pixmap(icon_name, color, _CHECK_ICON_SIZE))

