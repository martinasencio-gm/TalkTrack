"""Transcript viewer with interactive segment editing, playback, and speaker naming."""
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QScrollArea, QCheckBox,
    QApplication, QToolTip,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon

from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.ui.transcript_search_bar import TranscriptSearchBar
from app.utils.icons import colored_pixmap

_EMPTY_ICON_COLOR = "#3f424d"
_EMPTY_ICON_SIZE = 34


def _format_progress_text(message, elapsed_seconds, percent=None):
    """Format a progress status line, including ETA when percent is known.

    No longer used to drive any visible widget in this panel (see
    _setup_ui) — RecordingControls.set_transcribing formats its own strip
    text now — but kept as the tested, reusable formatting helper in case
    a future surface needs the same "message  NN%  (elapsed · ETA)" shape.
    """
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
    transcript_changed = pyqtSignal()       # emitted when text or names change
    speaker_names_changed = pyqtSignal(dict)  # emitted when speaker names change
    diarize_requested = pyqtSignal()        # run diarization on the loaded transcript
    diarize_toggled = pyqtSignal(bool)      # "Identify speakers" checkbox changed
    summarize_toggled = pyqtSignal(bool)    # "Summarize" checkbox changed
    open_last_requested = pyqtSignal()      # "Open the last one" clicked in the empty state

    def __init__(self, config=None, parent=None, speaker_panel=None):
        super().__init__(parent)
        self._config = config
        self._transcript = None
        self._speaker_colors = {}
        self._speaker_names = {}
        self._calendar_attendees = []
        self._segment_widgets = []
        self._audio_path = None
        self._diarization_available = False
        self._summarize_available = False
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
        self._injected_speaker_panel = speaker_panel
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        # Horizontal inset matches RecordingHeader's 8px directly above in
        # the same column — at 0 the section title and toolbar sat 8px left
        # of the recording name and the Transcribe button ended 1px from
        # the splitter divider. Top stays 0 (the header supplies the gap);
        # the bottom 4px matches the spacing above the toolbar row, without
        # which it sits flush against the column's bottom edge while having
        # visible breathing room above, reading as "not centered".
        layout.setContentsMargins(8, 0, 8, 4)
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
            "Run speaker diarization after transcription to identify individual speakers.\n"
            "Unchecked, separate mic and system tracks still label You/Remote."
        )
        self.diarize_cb.toggled.connect(self.diarize_toggled)
        header.addWidget(self.diarize_cb, 0, Qt.AlignmentFlag.AlignVCenter)

        # Same per-run opt-in shape as "Identify speakers": whether the AI
        # summary + action items run automatically once the next
        # transcription finishes. Mirrors config["ai"]["auto_summarize"].
        self.summarize_cb = QCheckBox("Summarize")
        self.summarize_cb.setToolTip(
            "Generate an AI summary and action items after transcription."
        )
        # Inert until MainWindow reports a configured provider via
        # set_summarize_available(); an un-synced viewer must not show an
        # operative box.
        self.summarize_cb.setEnabled(False)
        self.summarize_cb.toggled.connect(self.summarize_toggled)
        header.addWidget(self.summarize_cb, 0, Qt.AlignmentFlag.AlignVCenter)

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

        # No progress bar/status label/Cancel button here — the recording
        # controls' transcribing strip (RecordingControls.set_transcribing)
        # already shows percent/elapsed/remaining and a Cancel button for
        # whichever job (transcription or diarization) is running, so this
        # panel would just be a duplicate. Progress state (elapsed time,
        # percent) is still tracked below since main_window reads
        # _progress_start_time to feed that strip.

        # Speaker name panel. Normally injected by MainWindow (it lives in
        # the Inspector's "Speakers" section, not this column) — building
        # our own here is a fallback for standalone construction (tests).
        if self._injected_speaker_panel is not None:
            self.speaker_panel = self._injected_speaker_panel
            self.speaker_panel.names_changed.connect(self._on_speaker_names_changed)
        else:
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

        self._segments_container = QWidget()
        self._segments_container.setStyleSheet("background-color: #12141f;")
        self._segments_layout = QVBoxLayout(self._segments_container)
        self._segments_layout.setContentsMargins(8, 8, 8, 8)
        self._segments_layout.setSpacing(2)
        self._segments_layout.addStretch()

        self.scroll_area.setWidget(self._segments_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Placeholder — nothing is selected yet on construction.
        self._placeholder = self._build_placeholder(nothing_selected=True)
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
        self._audio_path = str(path) if path else None
        has_audio = self._audio_path is not None
        self.transcribe_btn.setEnabled(has_audio)
        self.play_all_btn.setEnabled(has_audio)
        self.continue_from_cb.setEnabled(has_audio)
        self._update_diarize_button()
        for widget in self._segment_widgets:
            widget.set_has_audio(has_audio)
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

    def set_summarize_available(self, available):
        """Whether an AI provider is configured at all.

        Without one the checkbox would silently do nothing, so it is
        disabled rather than left looking operative — same treatment as
        "Identify speakers" without a HuggingFace token.
        """
        self._summarize_available = bool(available)
        self.summarize_cb.setEnabled(self._summarize_available)
        if self._summarize_available:
            self.summarize_cb.setToolTip(
                "Generate an AI summary and action items after transcription."
            )
        else:
            self.summarize_cb.setToolTip(
                "Choose an AI provider in Settings > AI Assistant to enable "
                "summaries."
            )

    def set_summarize_enabled(self, enabled):
        """Set the checkbox without reporting it back as a user change."""
        self.summarize_cb.blockSignals(True)
        self.summarize_cb.setChecked(bool(enabled))
        self.summarize_cb.blockSignals(False)

    def summarize_enabled(self):
        return self.summarize_cb.isChecked() and self.summarize_cb.isEnabled()

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
        """Track that a job is running — no visible UI here, but
        main_window reads _progress_start_time/_progress_percent off this
        object to drive the recording controls' transcribing strip, which
        is the only place progress is now shown (see _setup_ui)."""
        self._progress_message = message
        self._progress_percent = None
        if self._progress_start_time is None:
            self._progress_start_time = time.monotonic()
            self._elapsed_timer.start()

    def set_progress_percent(self, percent):
        self._progress_percent = percent

    def _tick_elapsed(self):
        pass

    def hide_progress(self):
        self._elapsed_timer.stop()
        self._progress_start_time = None
        self._progress_percent = None

    def show_loading(self, message="Loading transcript..."):
        """Show a clean loading state while transcript data is being processed."""
        self.hide_progress()
        self.scroll_area.setUpdatesEnabled(False)
        self._segments_container.setUpdatesEnabled(False)
        try:
            self._segment_widgets.clear()
            while self._segments_layout.count():
                item = self._segments_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            loading_container = QWidget()
            loading_layout = QVBoxLayout(loading_container)
            loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            loading_layout.setSpacing(10)
            loading_layout.setContentsMargins(0, 60, 0, 60)

            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setPixmap(colored_pixmap("hourglass", "#9184d9", 28))
            loading_layout.addWidget(icon_lbl)

            loading_label = QLabel(message)
            loading_label.setObjectName("transcriptPlaceholder")
            loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            loading_label.setStyleSheet("color: #9397ab; font-size: 13.5px; font-weight: 500;")
            loading_layout.addWidget(loading_label)

            self._segments_layout.addWidget(loading_container)
            self._segments_layout.addStretch()
        finally:
            self._segments_container.setUpdatesEnabled(True)
            self.scroll_area.setUpdatesEnabled(True)

    def display_transcript(self, transcript, speaker_names=None, attendees=None):
        """Render transcript with interactive segment widgets."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._do_display_transcript(transcript, speaker_names=speaker_names, attendees=attendees)
        finally:
            QApplication.restoreOverrideCursor()

    def _do_display_transcript(self, transcript, speaker_names=None, attendees=None):
        self.hide_progress()
        self._transcript = transcript
        if speaker_names is not None:
            self._speaker_names = dict(speaker_names)

        # Stop any playing audio
        if self._player:
            self._player.stop()
        self._playing_index = -1

        # Assign colors to speakers
        speakers = sorted(set(s.speaker for s in transcript.segments if s.speaker))
        if self._config and self._config.get("general", "replace_you_with_name"):
            if "You" in speakers and "You" not in self._speaker_names:
                from app.utils.platform_info import get_current_user_name
                user_name = get_current_user_name(self._config)
                if user_name and user_name.strip() and user_name.strip().lower() != "you":
                    self._speaker_names["You"] = user_name.strip()

        self._speaker_colors = {}
        for i, speaker in enumerate(speakers):
            self._speaker_colors[speaker] = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]

        self.scroll_area.setUpdatesEnabled(False)
        self._segments_container.setUpdatesEnabled(False)
        try:
            # Clear existing segment widgets
            self._segment_widgets.clear()
            while self._segments_layout.count():
                item = self._segments_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # Build segment widgets
            from app.ui.segment_widget import SegmentWidget

            has_audio = self._audio_path is not None

            for i, seg in enumerate(transcript.segments):
                color = self._speaker_colors.get(seg.speaker, "#e9e9ed")
                name = self._speaker_names.get(seg.speaker, "")

                widget = SegmentWidget(
                    index=i,
                    segment=seg,
                    speaker_color=color,
                    speaker_name=name,
                    has_audio=has_audio,
                    parent=None,
                )
                widget.play_requested.connect(self._on_play_requested)
                widget.stop_requested.connect(self._on_stop_requested)
                widget.text_edited.connect(self._on_text_edited)
                widget.text_reverted.connect(self._on_text_reverted)
                widget.speaker_clicked.connect(self._on_speaker_label_clicked)

                self._segment_widgets.append(widget)
                self._segments_layout.addWidget(widget)

            self._segments_layout.addStretch()
        finally:
            self._segments_container.setUpdatesEnabled(True)
            self.scroll_area.setUpdatesEnabled(True)

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
        self.play_all_btn.setEnabled(has_audio)
        self.continue_from_cb.setEnabled(has_audio)
        self._update_diarize_button()

    def _build_placeholder(self, nothing_selected):
        """The transcript column's empty-state widget.

        nothing_selected=True is the "no recording chosen at all" case
        (startup, or the loaded recording was deleted/cleared) — icon,
        title, subtitle, and an "Open the last one" shortcut, per the
        design handoff's NOTIFICATIONS.md. False is the plainer
        "a recording is selected but has no transcript yet" case, which
        that spec doesn't separately define.
        """
        if not nothing_selected:
            placeholder = QLabel(
                "Transcript will appear here after recording and transcription..."
            )
            placeholder.setObjectName("transcriptPlaceholder")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return placeholder

        placeholder = QWidget()
        placeholder.setObjectName("transcriptPlaceholder")
        layout = QVBoxLayout(placeholder)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(14)
        # Vertical centering via addStretch() (not layout.setAlignment()) so
        # QVBoxLayout still computes each child's real heightForWidth — setting
        # alignment on the layout itself switches it to sizeHint-only packing,
        # which under-allocates height to the word-wrapped subtitle below and
        # lets the button after it clip/overlap the wrapped text.
        layout.addStretch(1)

        icon = QLabel()
        icon.setPixmap(colored_pixmap("waveform", _EMPTY_ICON_COLOR, _EMPTY_ICON_SIZE))
        layout.addWidget(icon)

        title = QLabel("Nothing selected")
        title.setStyleSheet("font-size: 20px; font-weight: 500;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Pick a recording on the left, or press Record when your call "
            "starts — TalkTrack will name it from your calendar and "
            "transcribe it when you stop."
        )
        subtitle.setWordWrap(True)
        subtitle.setFixedWidth(440)
        subtitle.setStyleSheet("font-size: 14px; color: #75798c;")
        layout.addWidget(subtitle)

        open_last_btn = QPushButton("Open the last one")
        open_last_btn.setIcon(QIcon(colored_pixmap("clock-counter-clockwise", "#9184d9", 16)))
        open_last_btn.setObjectName("primaryAction")
        open_last_btn.clicked.connect(self.open_last_requested.emit)
        layout.addWidget(open_last_btn)
        layout.addStretch(1)

        return placeholder

    def clear(self, nothing_selected=False):
        """Clear all transcript data and reset to empty state.

        nothing_selected=True shows the richer "Nothing selected" empty
        state instead of the plain "still needs transcribing" placeholder
        — pass it when nothing is loaded at all (startup, or the loaded
        recording was deleted), not when a recording is selected but
        simply hasn't been transcribed yet.
        """
        self.hide_progress()
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
        self._placeholder = self._build_placeholder(nothing_selected=nothing_selected)
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

    def show_empty_state(self, is_empty):
        """Revert to the placeholder state, e.g. after a transcription error."""
        if is_empty:
            self.clear()

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
                "background-color: #232532; border-radius: 4px;"
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
