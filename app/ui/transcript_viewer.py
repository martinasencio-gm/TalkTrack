"""Transcript viewer with interactive segment editing, playback, and speaker naming."""
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar, QFileDialog, QScrollArea, QCheckBox,
    QApplication, QToolTip,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QShortcut, QKeySequence

from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.ui.transcript_search_bar import TranscriptSearchBar


def _format_progress_text(message, elapsed_seconds, percent=None):
    """Format the progress status line, including ETA when percent is known."""
    if elapsed_seconds is None:
        return message

    em, es = divmod(int(elapsed_seconds), 60)
    elapsed_str = f"{em:02d}:{es:02d}"

    if percent is None:
        return f"{message}  ({elapsed_str} elapsed)"

    if 0 < percent < 100:
        remaining = elapsed_seconds * (100 - percent) / percent
        rm, rs = divmod(int(remaining), 60)
        return f"{message}  {percent}%  ({elapsed_str} elapsed · ~{rm:02d}:{rs:02d} remaining)"

    return f"{message}  {percent}%  ({elapsed_str} elapsed)"


# Speaker colors for visual distinction — shared constant
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


class TranscriptViewer(QWidget):
    """Displays transcription results with interactive segments.

    Features:
    - Per-segment play buttons for audio clip playback
    - Inline text editing with undo (original_text preservation)
    - Speaker name panel for mapping IDs to friendly names
    - Export to TXT, SRT, JSON with speaker names
    """

    transcribe_requested = pyqtSignal(str)  # audio file path
    cancel_requested = pyqtSignal()         # emitted when cancel button clicked
    transcript_changed = pyqtSignal()       # emitted when text or names change
    speaker_names_changed = pyqtSignal(dict)  # emitted when speaker names change
    diarize_requested = pyqtSignal()        # run diarization on the loaded transcript
    diarize_toggled = pyqtSignal(bool)      # "Identify speakers" checkbox changed

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._transcript = None
        self._speaker_colors = {}
        self._speaker_names = {}
        self._calendar_attendees = []
        self._segment_widgets = []
        self._audio_path = None
        self._diarization_available = False
        self._player = None
        self._playing_index = -1
        self._continuous_play = False
        self._user_scrolled = False  # True when user manually scrolled during playback
        self._programmatic_scroll = False  # guard to ignore our own scrolls
        self._progress_message = ""
        self._progress_start_time = None
        self._progress_percent = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        # Bottom margin matches the 4px spacing above the toolbar row (the
        # gap between it and the scroll area's border above) — without it,
        # the row sits flush against the tab pane's bottom edge while
        # having visible breathing room above, reading as "not centered".
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(4)

        # Header row: title + transcribe button
        header = QHBoxLayout()
        title = QLabel("Transcript")
        title.setObjectName("sectionHeader")
        header.addWidget(title)
        header.addStretch()

        # Diarization is by far the slowest stage (often longer than the
        # recording itself on CPU), so it gets a per-run opt-out right next
        # to the button that starts the work, not only a buried setting.
        self.diarize_cb = QCheckBox("Identify speakers")
        self.diarize_cb.setToolTip(
            "Run full speaker diarization (pyannote) after transcription.\n"
            "Much slower — on CPU it often takes longer than the recording.\n"
            "Unchecked, separate mic and system tracks still label You/Remote."
        )
        self.diarize_cb.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.diarize_cb.toggled.connect(self.diarize_toggled)
        header.addWidget(self.diarize_cb, 0, Qt.AlignmentFlag.AlignVCenter)

        # On-demand diarization for a transcript that already exists, so a
        # fast unlabelled pass can be upgraded without transcribing again.
        self.diarize_btn = QPushButton("Identify Speakers")
        self.diarize_btn.setToolTip(
            "Run speaker diarization on the existing transcript."
        )
        self.diarize_btn.setEnabled(False)
        self.diarize_btn.hide()
        self.diarize_btn.clicked.connect(self.diarize_requested)
        header.addWidget(self.diarize_btn)

        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.clicked.connect(self._on_transcribe_clicked)
        header.addWidget(self.transcribe_btn)

        layout.addLayout(header)

        # Progress row (bar + cancel button)
        progress_row = QHBoxLayout()
        # Qt's indeterminate QProgressBar animates a moving block on every
        # style, not just Windows' native theme — there's no stylesheet
        # that turns it off. So we never use indeterminate mode: the bar
        # stays hidden until we have a real percent to show (during the
        # transcription loop), and status_label alone carries progress
        # for phases with no percent data (model loading, diarization).
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #313244; border-radius: 4px; "
            "background-color: #181825; }"
            "QProgressBar::chunk { background-color: #89b4fa; border-radius: 3px; }"
        )
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedWidth(70)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        progress_row.addWidget(self.cancel_btn)
        layout.addLayout(progress_row)

        self.status_label = QLabel("")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # Speaker name panel
        from app.ui.speaker_name_panel import SpeakerNamePanel
        self.speaker_panel = SpeakerNamePanel(config=self._config)
        self.speaker_panel.names_changed.connect(self._on_speaker_names_changed)
        layout.addWidget(self.speaker_panel)

        # Find/replace bar
        self.search_bar = TranscriptSearchBar()
        self.search_bar.navigate_to_match.connect(self._highlight_match)
        self.search_bar.replace_requested.connect(self._replace_match)
        layout.addWidget(self.search_bar)

        # Ctrl+F shortcut
        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self._show_search)

        # Scroll area for segment widgets
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: 1px solid #313244; border-radius: 6px; "
            "background-color: #181825; }"
        )

        self._segments_container = QWidget()
        self._segments_container.setStyleSheet("background-color: #181825;")
        self._segments_layout = QVBoxLayout(self._segments_container)
        self._segments_layout.setContentsMargins(8, 8, 8, 8)
        self._segments_layout.setSpacing(2)
        self._segments_layout.addStretch()

        self.scroll_area.setWidget(self._segments_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Placeholder text
        self._placeholder = QLabel(
            "Transcript will appear here after recording and transcription..."
        )
        self._placeholder.setStyleSheet("color: #585b70; padding: 20px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._segments_layout.insertWidget(0, self._placeholder)

        layout.addWidget(self.scroll_area, 1)

        # Bottom row: play all + export buttons. Widgets are explicitly
        # vertically centered \u2014 QCheckBox's natural height (~18px indicator)
        # is much shorter than a padded QPushButton's (~38px from the
        # global stylesheet), and without an explicit alignment flag the
        # two don't reliably line up on the same visual center.
        export_row = QHBoxLayout()
        row_align = Qt.AlignmentFlag.AlignVCenter

        self.play_all_btn = QPushButton("\u25b6 Play All")
        self.play_all_btn.setEnabled(False)
        self.play_all_btn.setFixedWidth(90)
        self.play_all_btn.clicked.connect(self._on_play_all_clicked)
        export_row.addWidget(self.play_all_btn, 0, row_align)

        self.continue_from_cb = QCheckBox("Continue playing")
        self.continue_from_cb.setChecked(True)
        self.continue_from_cb.setToolTip(
            "When checked, clicking a segment's play button\n"
            "will continue playing all segments from that point."
        )
        self.continue_from_cb.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.continue_from_cb.toggled.connect(self._on_continue_toggled)
        export_row.addWidget(self.continue_from_cb, 0, row_align)

        export_row.addStretch()

        self.copy_all_btn = QPushButton("Copy All")
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.setToolTip(
            "Copy the entire transcript to the clipboard as plain text\n"
            "(speakers + text, no timestamps)."
        )
        self.copy_all_btn.clicked.connect(self._on_copy_all_clicked)
        export_row.addWidget(self.copy_all_btn, 0, row_align)

        self.export_txt_btn = QPushButton("Export TXT")
        self.export_txt_btn.setEnabled(False)
        self.export_txt_btn.clicked.connect(lambda: self._export("txt"))
        export_row.addWidget(self.export_txt_btn, 0, row_align)

        self.export_srt_btn = QPushButton("Export SRT")
        self.export_srt_btn.setEnabled(False)
        self.export_srt_btn.clicked.connect(lambda: self._export("srt"))
        export_row.addWidget(self.export_srt_btn, 0, row_align)

        layout.addLayout(export_row)

    def _ensure_player(self):
        """Lazily create the SegmentPlayer."""
        if self._player is None:
            from app.audio.segment_player import SegmentPlayer
            self._player = SegmentPlayer(self)
            self._player.playback_finished.connect(self._on_playback_finished)

    def set_audio_path(self, path):
        self._audio_path = path
        self.transcribe_btn.setEnabled(path is not None)
        self._update_diarize_button()
        if self._player:
            self._player.stop()
            self._player.clear_cache()

    def set_diarization_available(self, available):
        """Whether pyannote can run at all (a HuggingFace token is set).

        Without one the checkbox would silently do nothing, so it is
        disabled rather than left looking operative.
        """
        self._diarization_available = bool(available)
        self.diarize_cb.setEnabled(self._diarization_available)
        if not self._diarization_available:
            self.diarize_cb.setToolTip(
                "Add a HuggingFace token in Settings > Transcription to "
                "enable speaker diarization."
            )
        self._update_diarize_button()

    def set_diarization_enabled(self, enabled):
        """Set the checkbox without reporting it back as a user change."""
        self.diarize_cb.blockSignals(True)
        self.diarize_cb.setChecked(bool(enabled))
        self.diarize_cb.blockSignals(False)

    def diarization_enabled(self):
        return self.diarize_cb.isChecked() and self.diarize_cb.isEnabled()

    def _update_diarize_button(self):
        """On-demand diarization needs a transcript to label and audio to
        read; without a token it cannot run at all."""
        can_run = bool(
            self._diarization_available
            and self._transcript is not None
            and self._audio_path is not None
        )
        self.diarize_btn.setVisible(can_run)
        self.diarize_btn.setEnabled(can_run)

    def set_speaker_names(self, names):
        """Set speaker names from loaded speaker_names.json."""
        self._speaker_names = dict(names) if names else {}

    def set_calendar_attendees(self, attendees):
        """Update the attendee list used for speaker-naming dropdowns and
        refresh the panel immediately if a transcript is already shown."""
        self._calendar_attendees = list(attendees) if attendees else []
        if self._transcript is not None:
            self.speaker_panel.set_speakers(
                self._transcript.segments, self._speaker_names,
                attendees=self._calendar_attendees
            )

    def _on_transcribe_clicked(self):
        if self._audio_path:
            self.transcribe_requested.emit(self._audio_path)

    def show_progress(self, message):
        self._progress_message = message
        # No percent data yet for this phase (model loading, diarization,
        # or before the first segment lands) — hide the bar rather than
        # show it in indeterminate mode, since status_label + the elapsed
        # timer already say "work is happening" without any animation.
        self._progress_percent = None
        self.progress_bar.hide()
        self.cancel_btn.show()
        if self._progress_start_time is None:
            self._progress_start_time = time.monotonic()
            self._elapsed_timer.start()
        self._update_status_label()
        self.status_label.show()

    def set_progress_percent(self, percent):
        """Show the bar at a real percent — the only mode it's ever shown in."""
        self._progress_percent = percent
        self.progress_bar.setValue(percent)
        self.progress_bar.show()
        self._update_status_label()

    def _tick_elapsed(self):
        self._update_status_label()

    def _update_status_label(self):
        elapsed = (
            time.monotonic() - self._progress_start_time
            if self._progress_start_time is not None else None
        )
        self.status_label.setText(
            _format_progress_text(self._progress_message, elapsed, self._progress_percent)
        )

    def hide_progress(self):
        self.progress_bar.hide()
        self.cancel_btn.hide()
        self.status_label.hide()
        self._elapsed_timer.stop()
        self._progress_start_time = None
        self._progress_percent = None

    def _on_cancel_clicked(self):
        self.cancel_requested.emit()

    def display_transcript(self, transcript, speaker_names=None, attendees=None):
        """Render transcript with interactive segment widgets."""
        self._transcript = transcript
        if speaker_names is not None:
            self._speaker_names = dict(speaker_names)

        # Stop any playing audio
        if self._player:
            self._player.stop()
        self._playing_index = -1

        # Assign colors to speakers
        speakers = sorted(set(s.speaker for s in transcript.segments if s.speaker))
        self._speaker_colors = {}
        for i, speaker in enumerate(speakers):
            self._speaker_colors[speaker] = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]

        # Clear existing segment widgets
        self._segment_widgets.clear()
        while self._segments_layout.count():
            item = self._segments_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Build segment widgets
        from app.ui.segment_widget import SegmentWidget

        for i, seg in enumerate(transcript.segments):
            color = self._speaker_colors.get(seg.speaker, "#cdd6f4")
            name = self._speaker_names.get(seg.speaker, "")

            widget = SegmentWidget(
                index=i,
                segment=seg,
                speaker_color=color,
                speaker_name=name,
                parent=self._segments_container,
            )
            widget.play_requested.connect(self._on_play_requested)
            widget.stop_requested.connect(self._on_stop_requested)
            widget.text_edited.connect(self._on_text_edited)
            widget.text_reverted.connect(self._on_text_reverted)
            widget.speaker_clicked.connect(self._on_speaker_label_clicked)

            self._segment_widgets.append(widget)
            self._segments_layout.addWidget(widget)

        self._segments_layout.addStretch()

        # Update speaker panel
        if attendees is not None:
            self._calendar_attendees = attendees
        self.speaker_panel.set_speakers(
            transcript.segments, self._speaker_names, attendees=self._calendar_attendees
        )

        # Enable export and playback buttons
        self.copy_all_btn.setEnabled(True)
        self.export_txt_btn.setEnabled(True)
        self.export_srt_btn.setEnabled(True)
        self.play_all_btn.setEnabled(self._audio_path is not None)
        self._update_diarize_button()

    def clear(self):
        """Clear all transcript data and reset to empty state."""
        if self._player:
            self._player.stop()
        self._playing_index = -1
        self._transcript = None
        self._speaker_colors = {}
        self._speaker_names = {}
        self._calendar_attendees = []
        self._audio_path = None

        # Remove segment widgets
        self._segment_widgets.clear()
        while self._segments_layout.count():
            item = self._segments_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Restore placeholder
        self._placeholder = QLabel(
            "Transcript will appear here after recording and transcription..."
        )
        self._placeholder.setStyleSheet("color: #585b70; padding: 20px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._segments_layout.addWidget(self._placeholder)
        self._segments_layout.addStretch()

        # Disable export, playback, and transcribe buttons
        self.copy_all_btn.setEnabled(False)
        self.export_txt_btn.setEnabled(False)
        self.export_srt_btn.setEnabled(False)
        self.play_all_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
        self._update_diarize_button()
        self._stop_continuous_play()

        # Clear speaker panel
        self.speaker_panel.set_speakers([], {})

    def get_speaker_count(self):
        """Return number of unique speakers in current transcript."""
        if not self._transcript:
            return 0
        return len(set(s.speaker for s in self._transcript.segments if s.speaker))

    # --- Audio playback ---

    def _on_play_all_clicked(self):
        if self._continuous_play:
            self._stop_continuous_play()
            return
        if not self._audio_path or not self._transcript:
            return
        self._start_continuous_play(0)

    def _on_continue_toggled(self, checked):
        """Disabling 'Continue playing' mid-playback stops the continuous advance.

        Without this, unchecking the box while audio is playing did nothing —
        playback kept advancing because _on_playback_finished only looked at the
        _continuous_play flag, not the checkbox state.
        """
        if not checked and self._continuous_play:
            self._stop_continuous_play()

    def _start_continuous_play(self, from_index):
        """Start playing all segments sequentially from the given index."""
        self._continuous_play = True
        self._user_scrolled = False
        self.play_all_btn.setText("\u23f9 Stop")
        self._play_segment_at(from_index)

    def _stop_continuous_play(self):
        """Stop continuous playback."""
        self._continuous_play = False
        self._user_scrolled = False
        self.play_all_btn.setText("\u25b6 Play All")
        if self._player:
            self._player.stop()
        self._clear_highlight()
        self._playing_index = -1

    def _on_scroll(self):
        """Track user-initiated scrolling during continuous playback."""
        if self._continuous_play and not self._programmatic_scroll:
            self._user_scrolled = True

    def _play_segment_at(self, index):
        """Play a specific segment and highlight it."""
        if not self._audio_path or not self._transcript:
            return
        if index >= len(self._transcript.segments):
            self._stop_continuous_play()
            return

        self._ensure_player()
        self._clear_highlight()

        seg = self._transcript.segments[index]
        self._player.play_segment(self._audio_path, seg.start, seg.end)
        self._playing_index = index
        self._segment_widgets[index].set_playing(True)
        self._set_highlight(index)

        # Scroll to the playing segment (skip if user manually scrolled during continuous play)
        if not (self._continuous_play and self._user_scrolled):
            self._programmatic_scroll = True
            self.scroll_area.ensureWidgetVisible(self._segment_widgets[index], 50, 50)
            self._programmatic_scroll = False

    def _set_highlight(self, index):
        """Highlight the currently playing segment."""
        if 0 <= index < len(self._segment_widgets):
            self._segment_widgets[index].setStyleSheet(
                "background-color: #313244; border-radius: 4px;"
            )

    def _clear_highlight(self):
        """Remove highlight from all segments."""
        if self._playing_index >= 0 and self._playing_index < len(self._segment_widgets):
            self._segment_widgets[self._playing_index].setStyleSheet("")
            self._segment_widgets[self._playing_index].set_playing(False)

    def _on_play_requested(self, index):
        if not self._audio_path:
            return
        self._ensure_player()

        # If clicking a segment during continuous play, jump to that segment
        if self._continuous_play:
            self._clear_highlight()
            self._play_segment_at(index)
            return

        # "Continue from here" checkbox: start continuous play from this segment
        if self.continue_from_cb.isChecked():
            self._clear_highlight()
            self._start_continuous_play(index)
            return

        # Stop previous
        self._clear_highlight()

        seg = self._transcript.segments[index]
        self._player.play_segment(self._audio_path, seg.start, seg.end)
        self._playing_index = index
        self._segment_widgets[index].set_playing(True)

    def _on_stop_requested(self):
        if self._continuous_play:
            self._stop_continuous_play()
            return
        if self._player:
            self._player.stop()
        self._clear_highlight()
        self._playing_index = -1

    def _on_playback_finished(self):
        if self._continuous_play and self._playing_index >= 0:
            # Advance to next segment
            next_index = self._playing_index + 1
            self._clear_highlight()
            if next_index < len(self._segment_widgets):
                self._play_segment_at(next_index)
            else:
                self._stop_continuous_play()
            return

        self._clear_highlight()
        self._playing_index = -1

    # --- Text editing ---

    def _on_text_edited(self, index, new_text):
        seg = self._transcript.segments[index]
        if not seg.original_text:
            seg.original_text = seg.text
        seg.text = new_text
        self.transcript_changed.emit()

    def _on_text_reverted(self, index):
        seg = self._transcript.segments[index]
        if seg.original_text:
            seg.text = seg.original_text
            seg.original_text = ""
        self.transcript_changed.emit()

    # --- Speaker names ---

    def _on_speaker_names_changed(self, names):
        self._speaker_names = names
        for widget in self._segment_widgets:
            widget.update_speaker(names)
        self.speaker_names_changed.emit(names)

    def _on_speaker_label_clicked(self, speaker_id):
        self.speaker_panel.focus_speaker(speaker_id)

    # --- Find/replace ---

    def _show_search(self):
        texts = [seg.text for seg in self._transcript.segments] if self._transcript else []
        self.search_bar.set_texts(texts)
        self.search_bar.show_bar()

    def _highlight_match(self, seg_idx, start, end):
        if 0 <= seg_idx < len(self._segment_widgets):
            widget = self._segment_widgets[seg_idx]
            self.scroll_area.ensureWidgetVisible(widget)
            widget.highlight_match(start, end)

    def _replace_match(self, seg_idx, new_text, start, end):
        if 0 <= seg_idx < len(self._segment_widgets):
            seg = self._transcript.segments[seg_idx]
            updated = seg.text[:start] + new_text + seg.text[end:]
            self._segment_widgets[seg_idx]._history.push(updated)
            self._segment_widgets[seg_idx].text_label.setText(updated)
            self._segment_widgets[seg_idx].edit_indicator.setVisible(
                self._segment_widgets[seg_idx]._history.is_modified()
            )
            seg.text = updated
            self.transcript_changed.emit()
            texts = [s.text for s in self._transcript.segments]
            self.search_bar.set_texts(texts)

    # --- Export ---

    def _export(self, format_type):
        if not self._transcript:
            return

        filters = {
            "txt": "Text Files (*.txt)",
            "srt": "SRT Subtitle Files (*.srt)",
        }

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Transcript", "", filters[format_type]
        )

        if not path:
            return

        names = self._speaker_names

        if format_type == "txt":
            content = self._transcript.to_text(speaker_names=names)
        elif format_type == "srt":
            content = self._transcript.to_srt(speaker_names=names)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _on_copy_all_clicked(self):
        if not self._transcript or not self._transcript.segments:
            return
        text = self._transcript.to_plain_text(speaker_names=self._speaker_names)
        QApplication.clipboard().setText(text)
        count = len(self._transcript.segments)
        pos = self.copy_all_btn.mapToGlobal(self.copy_all_btn.rect().bottomLeft())
        QToolTip.showText(pos, f"Copied {count} segments to clipboard", self.copy_all_btn, self.copy_all_btn.rect(), 2000)
