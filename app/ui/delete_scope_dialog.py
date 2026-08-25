"""Modal dialog for choosing what to delete: recordings, transcriptions, or both."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QRadioButton, QButtonGroup, QDialogButtonBox
)

DELETE_RECORDINGS = "recordings"
DELETE_TRANSCRIPTIONS = "transcriptions"
DELETE_BOTH = "both"


class DeleteScopeDialog(QDialog):
    """Asks what to delete for one or more recordings.

    Replaces the plain Yes/No confirmation previously used for delete: audio
    and transcript-derived files can now be removed independently, so the
    user needs to say which.
    """

    def __init__(self, count=1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Recording" if count == 1 else "Delete Recordings")
        self._setup_ui(count)

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    def _setup_ui(self, count):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        noun = "this recording" if count == 1 else f"these {count} recordings"
        prompt = QLabel(f"What do you want to delete for {noun}?")
        prompt.setStyleSheet("font-size: 14px; font-weight: 600; color: #e9e9ed;")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self._recordings_radio = QRadioButton(
            "Recording audio only — keeps transcript, summary, and notes"
        )
        self._transcriptions_radio = QRadioButton(
            "Transcriptions only — transcript, summary, action items; keeps audio"
        )
        self._both_radio = QRadioButton(
            "Everything — delete both audio and transcript files"
        )
        self._both_radio.setChecked(True)

        self._group = QButtonGroup(self)
        for radio in (self._recordings_radio, self._transcriptions_radio, self._both_radio):
            radio.setStyleSheet("font-size: 13px; padding: 4px 0;")
            self._group.addButton(radio)
            layout.addWidget(radio)

        layout.addSpacing(4)
        warning = QLabel("This cannot be undone.")
        warning.setStyleSheet("color: #f38ba8; font-size: 12px; font-weight: 500;")
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Delete")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_scope(self):
        if self._recordings_radio.isChecked():
            return DELETE_RECORDINGS
        if self._transcriptions_radio.isChecked():
            return DELETE_TRANSCRIPTIONS
        return DELETE_BOTH
