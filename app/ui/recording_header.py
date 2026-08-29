"""Recording info header with rename capability."""
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QCompleter, QToolButton, QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon


from app.utils.icons import colored_pixmap

_CALENDAR_ICON_COLOR = "#9397ab"
_CALENDAR_ICON_SIZE = 12
_OVERFLOW_ICON_COLOR = "#9397ab"


class _DoubleClickableLabel(QLabel):
    """QLabel that reports double-clicks.

    The recording name is renamed by double-clicking it, which is what
    replaced the permanent Rename button beside it.
    """

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


def _display_name_from_metadata(metadata):
    """Extract display name from metadata, falling back to directory name."""
    name = metadata.get("name", "")
    if name:
        return name
    directory = metadata.get("directory", "")
    return Path(directory).name if directory else "Untitled Recording"


def _format_duration(seconds):
    """Format seconds as human-readable duration string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _format_transcribe_line(model_size, transcribe_seconds):
    """Format the 'transcribed in Xs using Y model' line, if timing is known."""
    if not transcribe_seconds:
        return ""
    time_str = _format_duration(transcribe_seconds)
    if model_size:
        return f"Transcribed in {time_str} using {model_size} model"
    return f"Transcribed in {time_str}"


def _format_calendar_line(calendar_event):
    """Format a calendar_event.json dict as a display line (icon shown separately)."""
    subject = calendar_event.get("subject", "")
    attendees = calendar_event.get("attendees", [])
    line = subject
    if attendees:
        count = len(attendees)
        noun = "attendee" if count == 1 else "attendees"
        line += f" · {count} {noun}"
    return line


def match_event_by_subject(name, events):
    """The suggested event whose subject is exactly this name, or None.

    Renaming to a suggestion means the user picked that meeting, so the
    recording gets tagged with it too. The match is exact because a
    freely-typed name must never silently tag the recording to a meeting
    the user didn't choose.
    """
    name = name.strip()
    if not name:
        return None
    for event in events:
        if event.get("subject", "").strip() == name:
            return event
    return None


from app.ui.tag_chip import TagChip
from app.utils import tag_manager


class RecordingHeader(QWidget):
    """Displays recording info (name, date, duration) with rename capability."""

    name_changed = pyqtSignal(str)  # emitted when user renames the recording
    rename_started = pyqtSignal()   # user opened the inline editor — cue to
                                    # fetch calendar suggestions for it
    change_calendar_requested = pyqtSignal()  # emitted when user clicks "Change" on the calendar line
    tags_changed = pyqtSignal(list)           # emitted when tags on current recording change
    manage_tags_requested = pyqtSignal()      # open tag manager
    tag_dialog_requested = pyqtSignal()       # "+ Tag" clicked — open the Tag this recording dialog

    def __init__(self, parent=None):
        super().__init__(parent)
        self._metadata = None
        self._editing = False
        self._suggested_subjects = []
        self._setup_ui()
        self.hide()  # hidden until a recording is loaded

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)

        # Top row: name + rename button
        top_row = QHBoxLayout()

        # Double-click the name to rename it, the way a file name is renamed
        # everywhere else — this is what replaced the permanent Rename button.
        self.name_label = _DoubleClickableLabel("")
        self.name_label.setObjectName("recordingName")
        self.name_label.setToolTip("Double-click to rename")
        self.name_label.double_clicked.connect(self._start_rename)
        top_row.addWidget(self.name_label)

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("recordingNameEdit")
        self.name_edit.hide()
        # editingFinished rather than returnPressed: it covers Enter *and*
        # clicking away, so losing the button costs no way to commit.
        # _finish_rename is re-entrancy guarded because Qt can emit this
        # twice (Return, then the focus-out that follows it).
        self.name_edit.editingFinished.connect(self._finish_rename)
        top_row.addWidget(self.name_edit)

        top_row.addStretch()

        # One overflow for the three actions that used to be three permanent
        # buttons (Rename / + Tag / Change meeting). "Change meeting" is only
        # meaningful once the recording is tagged to a calendar event, so it
        # is enabled per-recording in set_recording().
        self.rename_action = QAction("Rename", self)
        self.rename_action.triggered.connect(self._start_rename)
        self.add_tag_action = QAction("Add tag…", self)
        self.add_tag_action.triggered.connect(self._on_add_tag_clicked)
        self.change_calendar_action = QAction("Change meeting…", self)
        self.change_calendar_action.triggered.connect(
            self.change_calendar_requested.emit
        )
        self.change_calendar_action.setVisible(False)

        self._overflow_menu = QMenu(self)
        self._overflow_menu.addAction(self.rename_action)
        self._overflow_menu.addAction(self.add_tag_action)
        self._overflow_menu.addAction(self.change_calendar_action)

        self.overflow_btn = QToolButton()
        self.overflow_btn.setObjectName("headerOverflow")
        # The vendored icon, not a "⋯" character — Inter has no glyph for
        # U+22EF and it renders as tofu.
        self.overflow_btn.setIcon(
            QIcon(colored_pixmap("dots-three", _OVERFLOW_ICON_COLOR, 16))
        )
        self.overflow_btn.setToolTip("Rename, tag, or change the meeting")
        self.overflow_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.overflow_btn.setMenu(self._overflow_menu)
        top_row.addWidget(self.overflow_btn)

        layout.addLayout(top_row)

        # Bottom row: date, duration, speaker count
        self.info_label = QLabel("")
        self.info_label.setObjectName("recordingInfo")
        layout.addWidget(self.info_label)

        # Tags row
        self.tags_row = QHBoxLayout()
        self.tags_row.setContentsMargins(0, 2, 0, 2)
        self.tags_row.setSpacing(6)

        self.tags_container = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_row.addWidget(self.tags_container)

        # "+ Tag" is now the overflow's "Add tag…" — the tag chips themselves
        # stay on this row, since they are content rather than chrome.
        self.tags_row.addStretch()
        layout.addLayout(self.tags_row)

        # Calendar event line + remap button
        calendar_row = QHBoxLayout()
        self.calendar_icon = QLabel()
        self.calendar_icon.setPixmap(
            colored_pixmap("calendar-blank", _CALENDAR_ICON_COLOR, _CALENDAR_ICON_SIZE)
        )
        self.calendar_icon.hide()
        calendar_row.addWidget(self.calendar_icon)
        self.calendar_label = QLabel("")
        self.calendar_label.setObjectName("recordingCalendarInfo")
        self.calendar_label.hide()
        calendar_row.addWidget(self.calendar_label)

        # The "Change" button moved into the header overflow as
        # "Change meeting…"; the calendar line itself stays, it is content.
        calendar_row.addStretch()
        layout.addLayout(calendar_row)

    def set_recording(self, metadata, speaker_count=0, calendar_event=None,
                       model_size="", transcribe_seconds=0.0):
        """Display info for the given recording metadata."""
        self._metadata = metadata
        if metadata is None:
            self.hide()
            return

        self.show()

        name = _display_name_from_metadata(metadata)
        self.name_label.setText(name)

        # Build info line
        parts = []
        started = metadata.get("started_at", "")
        if started:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(started)
                parts.append(dt.strftime("%Y-%m-%d %H:%M"))
            except (ValueError, TypeError):
                parts.append(started)

        duration = metadata.get("duration", 0)
        if duration:
            parts.append(f"Duration: {_format_duration(duration)}")

        if speaker_count > 0:
            parts.append(f"{speaker_count} speaker{'s' if speaker_count != 1 else ''}")

        transcribe_line = _format_transcribe_line(model_size, transcribe_seconds)
        if transcribe_line:
            parts.append(transcribe_line)

        self.info_label.setText("  |  ".join(parts))

        # Rebuild tags
        self._rebuild_tags()

        if calendar_event:
            self.calendar_label.setText(_format_calendar_line(calendar_event))
            self.calendar_icon.show()
            self.calendar_label.show()
            self.change_calendar_action.setVisible(True)
        else:
            self.calendar_label.clear()
            self.calendar_icon.hide()
            self.calendar_label.hide()
            self.change_calendar_action.setVisible(False)

    def _rebuild_tags(self):
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._metadata:
            return

        assigned_tags = tag_manager.get_recording_tags(self._metadata)
        for t_name in assigned_tags:
            color = tag_manager.get_tag_color(t_name)
            chip = TagChip(t_name, color=color, removable=True)
            chip.remove_clicked.connect(self._on_remove_tag)
            self.tags_layout.addWidget(chip)

    def _on_remove_tag(self, tag_name: str):
        if not self._metadata or not self._metadata.get("directory"):
            return
        updated = tag_manager.remove_tag_from_recording(self._metadata["directory"], tag_name)
        self._metadata["tags"] = updated
        self._rebuild_tags()
        self.tags_changed.emit(updated)

    def _on_add_tag_clicked(self):
        if not self._metadata or not self._metadata.get("directory"):
            return
        self.tag_dialog_requested.emit()

    def refresh_tags(self):
        """External call to refresh tag chips on the current recording."""
        self._rebuild_tags()

    def clear(self):
        """Clear the header, hiding it."""
        self.set_recording(None)

    def set_name_suggestions(self, subjects):
        """Offer these meeting subjects as completions while renaming.

        Arrives after the editor is already open — the Outlook lookup runs
        off-thread and takes long enough that blocking the rename on it
        would be worse than the suggestions appearing a moment late.
        """
        self._suggested_subjects = list(subjects)
        if not self._editing:
            return
        completer = QCompleter(self._suggested_subjects, self.name_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.name_edit.setCompleter(completer)
        completer.complete()

    def _start_rename(self):
        if self._editing:
            self._finish_rename()
            return

        self._editing = True
        self.name_edit.setText(self.name_label.text())
        self.name_label.hide()
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.rename_started.emit()

    def _finish_rename(self):
        if not self._editing:
            return
        self._editing = False
        new_name = self.name_edit.text().strip()
        if new_name:
            self.name_label.setText(new_name)
            self.name_changed.emit(new_name)
        self.name_edit.setCompleter(None)
        self._suggested_subjects = []
        self.name_edit.hide()
        self.name_label.show()
