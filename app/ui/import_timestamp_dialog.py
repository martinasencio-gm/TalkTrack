"""Modal dialog for confirming/editing an imported recording's start time."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDateTimeEdit, QDialogButtonBox, QLabel
)
from PyQt6.QtCore import QDateTime


class ImportTimestampDialog(QDialog):
    """Asks the user to confirm when an imported recording actually happened."""

    def __init__(self, default_datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("When was this recorded?")
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Pre-filled from the file's last-modified time — adjust if that's not "
            "when the call actually happened. This is used to match a calendar "
            "event, if calendar tagging is enabled."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._datetime_edit = QDateTimeEdit()
        self._datetime_edit.setCalendarPopup(True)
        self._datetime_edit.setDateTime(QDateTime(default_datetime))
        form.addRow("Recording start:", self._datetime_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_datetime(self):
        return self._datetime_edit.dateTime().toPyDateTime()
