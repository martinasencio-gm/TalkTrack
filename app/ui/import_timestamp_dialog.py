"""Modal dialog for confirming/editing an imported recording's start time."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDateTimeEdit, QDialogButtonBox, QLabel
)
from PyQt6.QtCore import QDateTime, Qt


class ImportTimestampDialog(QDialog):
    """Asks the user to confirm when an imported recording actually happened."""

    def __init__(self, default_datetime, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("When was this recorded?")
        self._setup_ui(default_datetime)

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    def _setup_ui(self, default_datetime):
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
