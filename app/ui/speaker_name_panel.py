"""Collapsible speaker name editing panel."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QTimer


# Speaker colors — must match the list in transcript_viewer.py
SPEAKER_COLORS = [
    "#89b4fa",  # blue
    "#a6e3a1",  # green
    "#fab387",  # peach
    "#f5c2e7",  # pink
    "#94e2d5",  # teal
    "#f9e2af",  # yellow
    "#cba6f7",  # mauve
    "#f38ba8",  # red
]


def _extract_speakers(segments):
    """Extract unique speaker IDs from segments, sorted."""
    speakers = set()
    for seg in segments:
        if seg.speaker:
            speakers.add(seg.speaker)
    return sorted(speakers)


def _available_options(speaker_id, speaker_ids, current_selections, attendees):
    """Attendee names selectable for speaker_id's dropdown: blank + every
    attendee not already assigned to a DIFFERENT speaker. speaker_id's own
    current selection (if any) always stays available in its own list."""
    own_selection = current_selections.get(speaker_id, "")
    taken_elsewhere = {
        name for sid, name in current_selections.items()
        if sid != speaker_id and name
    }
    available = [a for a in attendees if a not in taken_elsewhere]
    return [""] + available


def _speakers_holding_name(name, current_selections, exclude_speaker_id):
    """speaker_ids (other than exclude_speaker_id) currently mapped to name.
    Blank names never match."""
    if not name:
        return []
    return [
        sid for sid, n in current_selections.items()
        if sid != exclude_speaker_id and n == name
    ]


class SpeakerNamePanel(QWidget):
    """Collapsible panel for mapping speaker IDs to friendly names.

    Emits names_changed whenever any name is edited.
    """

    names_changed = pyqtSignal(dict)  # {speaker_id: name}

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._speaker_ids = []
        self._name_edits = {}      # speaker_id -> QLineEdit (no-attendee mode)
        self._name_combos = {}     # speaker_id -> QComboBox (attendee mode)
        self._combo_line_edits = {}  # QLineEdit -> QComboBox, for eventFilter
        self._speaker_names = {}   # speaker_id -> name str
        self._attendees = []
        self._collapsed = config.get("ui", "speakers_collapsed") if config else False
        self._setup_ui()
        self.hide()  # hidden until speakers exist

    def _setup_ui(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(4)

        # Header row with toggle
        header_row = QHBoxLayout()
        self._toggle_btn = QPushButton("\u25bc Speakers")
        self._toggle_btn.setObjectName("speakerPanelToggle")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.clicked.connect(self._toggle_collapsed)
        header_row.addWidget(self._toggle_btn)
        header_row.addStretch()
        self._main_layout.addLayout(header_row)

        # Container for speaker rows (collapsible)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(8, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._main_layout.addWidget(self._rows_container)

    def set_speakers(self, segments, speaker_names=None, attendees=None):
        """Populate panel from transcript segments and optional existing names.

        Args:
            segments: list of TranscriptSegment
            speaker_names: dict of {speaker_id: name} or None
            attendees: optional list of calendar attendee names. When
                provided (non-empty), rows become mutually-exclusive
                dropdowns instead of free-text fields.
        """
        self._speaker_ids = _extract_speakers(segments)
        self._speaker_names = dict(speaker_names) if speaker_names else {}
        self._attendees = list(attendees) if attendees else []

        if not self._speaker_ids:
            self.hide()
            return

        self.show()
        arrow = "\u25b6" if self._collapsed else "\u25bc"
        self._toggle_btn.setText(f"{arrow} Speakers ({len(self._speaker_ids)} detected)")
        self._rows_container.setVisible(not self._collapsed)

        # Clear existing rows
        self._name_edits.clear()
        self._name_combos.clear()
        self._combo_line_edits.clear()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build rows
        for i, speaker_id in enumerate(self._speaker_ids):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 2, 0, 2)

            # Color swatch
            color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
            swatch = QLabel("\u25cf")
            swatch.setStyleSheet(f"color: {color}; font-size: 16px;")
            swatch.setFixedWidth(20)
            row_layout.addWidget(swatch)

            # Speaker ID label
            id_label = QLabel(speaker_id)
            id_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
            id_label.setFixedWidth(100)
            row_layout.addWidget(id_label)

            # Arrow
            arrow_label = QLabel("\u2192")
            arrow_label.setStyleSheet("color: #585b70;")
            arrow_label.setFixedWidth(20)
            row_layout.addWidget(arrow_label)

            existing_name = self._speaker_names.get(speaker_id, "")

            if self._attendees:
                combo = QComboBox()
                combo.setEditable(True)
                combo.setMaximumHeight(28)
                options = _available_options(
                    speaker_id, self._speaker_ids, self._speaker_names, self._attendees
                )
                combo.addItems(options)
                if existing_name:
                    combo.setCurrentText(existing_name)
                combo.currentTextChanged.connect(
                    lambda text, sid=speaker_id: self._on_combo_changed(sid, text)
                )
                combo.lineEdit().installEventFilter(self)
                self._combo_line_edits[combo.lineEdit()] = combo
                row_layout.addWidget(combo)
                self._name_combos[speaker_id] = combo
            else:
                name_edit = QLineEdit()
                name_edit.setPlaceholderText("Enter name...")
                name_edit.setMaximumHeight(28)
                if existing_name:
                    name_edit.setText(existing_name)
                name_edit.textChanged.connect(self._on_name_changed)
                row_layout.addWidget(name_edit)
                self._name_edits[speaker_id] = name_edit

            self._rows_layout.addWidget(row_widget)

    def eventFilter(self, obj, event):
        """Show the full attendee list when the line edit is clicked.

        Editable QComboBox only inline-autocompletes as you type by default;
        the actual dropdown of options otherwise requires clicking the small
        arrow button, which isn't discoverable. FocusIn was tried first but
        only fires once per focus transition — a second click on an
        already-focused field produced no event at all. MouseButtonPress was
        tried next but caused the popup to flash open and immediately close:
        the press lands on the line edit (not the popup), so when the
        matching release for that same click arrives a moment later, Qt's
        popup sees a release it never saw a press for and treats it as a
        click outside the popup, closing it. Triggering on
        MouseButtonRelease instead leaves no further release event in the
        queue to be misread that way.
        """
        if event.type() == QEvent.Type.MouseButtonRelease:
            combo = self._combo_line_edits.get(obj)
            if combo is not None and not combo.view().isVisible():
                QTimer.singleShot(0, combo.showPopup)
        return super().eventFilter(obj, event)

    def _on_combo_changed(self, speaker_id, text):
        name = text.strip()
        self._speaker_names[speaker_id] = name
        for displaced_id in _speakers_holding_name(name, self._speaker_names, speaker_id):
            self._speaker_names[displaced_id] = ""
            displaced_combo = self._name_combos.get(displaced_id)
            if displaced_combo is not None:
                displaced_combo.blockSignals(True)
                displaced_combo.setCurrentText("")
                displaced_combo.blockSignals(False)
        self._refresh_combo_options(skip_speaker_id=speaker_id)
        self.names_changed.emit(self.get_speaker_names())

    def _refresh_combo_options(self, skip_speaker_id=None):
        for speaker_id, combo in self._name_combos.items():
            if speaker_id == skip_speaker_id:
                # Don't rebuild the combo the user is actively typing in —
                # this fires on every keystroke (_on_combo_changed calls us
                # for editable combos) and rebuilding mid-type breaks cursor
                # position / any inline completion popup. Its own option
                # list doesn't need to be current until it's touched again.
                continue
            options = _available_options(
                speaker_id, self._speaker_ids, self._speaker_names, self._attendees
            )
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(options)
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def get_speaker_names(self):
        """Return current speaker name mappings (only non-empty names)."""
        names = {}
        for speaker_id, edit in self._name_edits.items():
            name = edit.text().strip()
            if name:
                names[speaker_id] = name
        for speaker_id, combo in self._name_combos.items():
            name = combo.currentText().strip()
            if name:
                names[speaker_id] = name
        return names

    def focus_speaker(self, speaker_id):
        """Focus the name field for the given speaker ID."""
        if self._collapsed:
            self._toggle_collapsed()
        if speaker_id in self._name_edits:
            self._name_edits[speaker_id].setFocus()
            self._name_edits[speaker_id].selectAll()
        elif speaker_id in self._name_combos:
            self._name_combos[speaker_id].setFocus()

    def _on_name_changed(self, text):
        """Emit names_changed whenever any name field changes."""
        self.names_changed.emit(self.get_speaker_names())

    def _toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self._rows_container.setVisible(not self._collapsed)
        arrow = "\u25b6" if self._collapsed else "\u25bc"
        count = len(self._speaker_ids)
        self._toggle_btn.setText(f"{arrow} Speakers ({count} detected)")
        if self._config:
            self._config.set("ui", "speakers_collapsed", self._collapsed)
