import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel
)
from PyQt6.QtCore import Qt

from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

# state -> (color, icon name) for the verdict badge.
_STATE_STYLE = {
    "ready": ("#a6e3a1", "check-circle-fill"),
    "warning": ("#f9e2af", "warning"),
    "blocked": ("#f38ba8", "warning-octagon"),
}

_VERDICT_ICON_SIZE = 18
_VERDICT_BADGE_SIZE = 48


class PreflightWidget(QWidget):
    """
    Shows the pre-flight verdict before recording begins: a colored badge
    plus a title naming the actual problem (not just its severity) and a
    fix-oriented subtitle. Folds in conferencing warnings, device
    mismatches, and the diarization-token check — see
    app/utils/preflight_status.py for the truth table.

    The "what's being captured" summary (mic + call source) is a sibling
    block in RecordingControls, not part of this widget — see
    RecordingControls.set_capturing().
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

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

        self.set_verdict("ready")

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
