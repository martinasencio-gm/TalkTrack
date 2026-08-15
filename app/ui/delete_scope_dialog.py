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
        layout = QVBoxLayout(self)

        noun = "this recording" if count == 1 else f"these {count} recordings"
        prompt = QLabel(f"What do you want to delete for {noun}?")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self._recordings_radio = QRadioButton(
            "Recordings only — audio files; keeps transcript/summary"
        )
        self._transcriptions_radio = QRadioButton(
            "Transcriptions only — transcript/summary/action items; keeps audio"
        )
        self._both_radio = QRadioButton("Both — delete everything")
        self._both_radio.setChecked(True)

        self._group = QButtonGroup(self)
        for radio in (self._recordings_radio, self._transcriptions_radio, self._both_radio):
            self._group.addButton(radio)
            layout.addWidget(radio)

        warning = QLabel("This cannot be undone.")
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
