import json
import logging
import os
import sys
import time
import webbrowser
from pathlib import Path
from datetime import datetime

import sounddevice as sd

logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSplitter, QFrame, QMenu, QMessageBox, QApplication, QInputDialog, QStatusBar
)
from PyQt6.QtCore import Qt, QTimer, QEvent, QThread
from PyQt6.QtGui import QAction, QIcon, QKeySequence

from app.utils.atomic_io import atomic_write_json, atomic_write_text
from app.utils.config import Config
from app.utils import batch_queue, session_io, preflight_status
from app.utils.mic_level_tracker import MicLevelTracker
from app.utils.icons import colored_pixmap
from app.recording.audio_capture import LoopbackStream
from app.recording.process_audio_capture import ProcessAudioCapture
from app.recording.mic_monitor import MicMonitor
from app.recording.recorder import Recorder, RecordingState
from app.transcription.transcriber import TranscriptionWorker, TranscriptResult
from app.transcription.track_merge import dual_track_plan
from app.transcription.job_status import transcribing_directories
from app.transcription.diarizer import DiarizationWorker, SimpleDiarizeWorker
from app.ui.collapsible_splitter import CollapsibleSplitter
from app.ui.recording_controls import RecordingControls
from app.ui.meters_panel import MetersPanel
from app.ui.source_selector import SourceSelector
from app.ui.transcript_viewer import TranscriptViewer
from app.ui.notes_panel import NotesPanel
from app.ui.recordings_list import RecordingsList
from app.ui.inspector import InspectorWidget
from app.ui.notification_region import NotificationRegion
from app.ui.collapsible_section import CollapsibleSection
from app.ui.settings_dialog import SettingsDialog
from app.ui.status_panel import SystemStatusDialog
from app.ui.tray_icon import TrayIcon
from app.ui.activity_indicator import ActivityIndicator, resolve_activity_state
from app.ui.compact_strip import CompactStrip, resolve_compact_strip_state
from app.ui.window_presentation import next_presentation
from app.ui.level_meter import compute_rms_db, db_to_fraction
from app.ui.recording_header import RecordingHeader, match_event_by_subject
from app.ui.waveform_display import WaveformDisplay
from app.ui.about_dialog import AboutDialog, BMAC_URL
from app.ui.summary_panel import SummaryPanel
from app.ui.action_items_panel import ActionItemsPanel
from app.ui.chat_panel import ChatPanel
from app.ai.chat import build_chat_context
from app.ui.calendar_banner import CalendarSuggestionBanner
from app.ui.meeting_banner import MeetingBanner
from app.ui.meeting_toast import MeetingNotificationToast
from app.integrations.meeting_detector import MeetingDetector
from app.utils import meeting_signals, tag_manager
from app.utils.com_session_worker import ComSessionPoller
from app.ui.calendar_lookup_worker import CalendarLookupWorker
from app.ui.import_timestamp_dialog import ImportTimestampDialog
from app.recording.import_session import build_import_metadata, needs_conversion
from app.utils.platform_info import get_current_user_name

# Bleed duplicates below this count are the odd loud moment, not a setup
# worth interrupting the user about.
BLEED_WARNING_SEGMENTS = 5


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self._import_stranded_transcript_exports()
        self.recorder = Recorder(self.config)
        self._current_session = None
        self._transcription_worker = None
        self._batch_worker = None
        self._calendar_lookup_workers = []
        self._calendar_banner_session = None
        self._rename_candidate_events = []
        self._diarization_worker = None
        self._simple_diarize_worker = None
        self._summarize_worker = None
        self._pending_transcriptions = []
        self._closing = False
        self._silent_capture_warned = False
        self._bleed_warned = False
        self._mic_muted = False
        self._pending_gain = None  # holds latest slider value awaiting debounced save
        self._gain_save_timer = QTimer(self)
        self._gain_save_timer.setSingleShot(True)
        self._gain_save_timer.timeout.connect(self._flush_gain_to_config)

        # Meeting detection. The detector holds all the decision logic and the
        # timing rules; this window only polls the signals and carries out what
        # it decides. It replaces the old apps_became_active auto-record path,
        # which triggered on any audio from a selected app.
        self._meeting_detector = MeetingDetector()
        self._last_meeting_snapshot = None
        self._active_detected_meeting_name = None
        self._detected_session_meeting_name = None
        # "start" | "end" | None — which meeting suggestion the last tray
        # balloon was for, so a click on it (see _on_tray_message_clicked)
        # knows whether to start recording or just bring the window forward.
        self._pending_meeting_notification = None
        self._current_calendar_event = None
        self._meeting_poll_timer = QTimer(self)
        self._meeting_poll_timer.timeout.connect(self._poll_meeting_signals)
        self._meeting_poll_timer.start(1000)

        self._com_poller = ComSessionPoller(main_pid=os.getpid())
        self._com_poller.start()

        self._batch_worker_start_time = None
        self._running_batch_processes = []
        self._batch_monitor_timer = QTimer(self)
        self._batch_monitor_timer.setInterval(2000)
        self._batch_monitor_timer.timeout.connect(self._poll_batch_processes)
        self._batch_monitor_timer.start()

        self.setWindowTitle("TalkTrack - Call Recorder, Transcriber & AI Summarizer")
        self.setMinimumSize(1180, 720)
        self.resize(1360, 860)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._connect_signals()

        self._really_quit = False
        self._success_pending = False
        self._error_pending = False
        self._current_capture_failures = {}

        self.tray = TrayIcon(self)
        if self.tray.is_supported():
            self.tray.show()
            self.tray.show_requested.connect(self._restore_from_tray)
            self.tray.record_requested.connect(self._start_recording)
            self.tray.pause_requested.connect(self._toggle_pause)
            self.tray.resume_requested.connect(self._toggle_pause)
            self.tray.stop_requested.connect(self._stop_recording)
            self.tray.quit_requested.connect(self._quit_from_tray)
            self.tray.message_clicked.connect(self._on_tray_message_clicked)
        else:
            import logging
            logging.getLogger("talktrack").warning(
                "System tray not available; hide-to-tray on close is disabled."
            )

        self._current_transcription_percent = None
        self._activity_widget = ActivityIndicator()
        self._activity_widget.restore_requested.connect(self._restore_from_tray)
        self._activity_widget.position_changed.connect(self._on_activity_widget_moved)

        self._compact_strip_done = False
        # True while the strip stands in for a minimized window (so restoring
        # from the taskbar dismisses it); False when it's a free-floating
        # panel opened alongside the window from View > Show Compact Strip.
        self._strip_is_minimized_form = False
        self.compact_strip = CompactStrip()
        self.compact_strip.expand_requested.connect(self._switch_to_full_ui)
        self.compact_strip.open_transcript_requested.connect(self._switch_to_full_ui)
        self.compact_strip.record_requested.connect(self._start_recording)
        self.compact_strip.stop_requested.connect(self._stop_recording)
        self.compact_strip.pause_requested.connect(self._toggle_pause)
        self.compact_strip.resume_requested.connect(self._toggle_pause)
        self.compact_strip.cancel_requested.connect(self._cancel_transcription)
        self.compact_strip.mute_requested.connect(self._toggle_mute)
        self.compact_strip.position_changed.connect(self._on_compact_strip_moved)
        self.compact_strip.shrink_requested.connect(self._advance_presentation)
        self.compact_strip.variant_changed.connect(self._on_compact_strip_variant_changed)
        self.compact_strip.set_variant(self.config.get("ui", "strip_variant") or "full")
        self._update_compact_strip_state()
        self._update_preflight()
        if self.config.get("ui", "compact_strip_visible"):
            self.compact_strip_action.setChecked(True)

        QTimer.singleShot(500, self._check_startup_status)

    def _setup_menu(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        import_action = QAction("&Import Recording...", self)
        import_action.setShortcut(QKeySequence("Ctrl+O"))
        import_action.triggered.connect(lambda: self.recordings_list._on_import_clicked())
        file_menu.addAction(import_action)

        export_action = QAction("&Export Transcript...", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(lambda: self.transcript_viewer._export("txt"))
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        open_recordings_action = QAction("Open Recordings Folder", self)
        open_recordings_action.triggered.connect(self._open_recordings_folder)
        file_menu.addAction(open_recordings_action)

        open_batch_logs_action = QAction("Open Batch Logs Folder", self)
        open_batch_logs_action.triggered.connect(self._open_batch_logs_folder)
        file_menu.addAction(open_batch_logs_action)

        file_menu.addSeparator()

        self.compact_strip_action = QAction("Show Compact Strip", self)
        self.compact_strip_action.setCheckable(True)
        self.compact_strip_action.toggled.connect(self._on_compact_strip_toggled)
        file_menu.addAction(self.compact_strip_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Record menu
        record_menu = menubar.addMenu("&Record")

        toggle_record_action = QAction("Start/Stop Recording", self)
        toggle_record_action.setShortcut(QKeySequence("Ctrl+R"))
        toggle_record_action.triggered.connect(self._toggle_recording_from_menu)
        record_menu.addAction(toggle_record_action)

        toggle_pause_action = QAction("Pause/Resume", self)
        toggle_pause_action.setShortcut(QKeySequence("Ctrl+P"))
        toggle_pause_action.triggered.connect(self._toggle_pause)
        record_menu.addAction(toggle_pause_action)

        toggle_mute_action = QAction("Mute Microphone", self)
        toggle_mute_action.setShortcut(QKeySequence("Ctrl+M"))
        toggle_mute_action.triggered.connect(self._toggle_mute)
        record_menu.addAction(toggle_mute_action)

        record_menu.addSeparator()

        sources_action = QAction("&Audio Sources...", self)
        sources_action.setShortcut(QKeySequence("Ctrl+."))
        sources_action.triggered.connect(self._open_source_selector)
        record_menu.addAction(sources_action)

        # Transcribe menu
        transcribe_menu = menubar.addMenu("&Transcribe")

        transcribe_selected_action = QAction("Transcribe &Selected", self)
        transcribe_selected_action.setShortcut(QKeySequence("Ctrl+T"))
        transcribe_selected_action.triggered.connect(lambda: self.recordings_list.transcribe_selected())
        transcribe_menu.addAction(transcribe_selected_action)

        diarize_action = QAction("&Identify Speakers", self)
        diarize_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        diarize_action.triggered.connect(self._on_diarize_requested)
        transcribe_menu.addAction(diarize_action)

        batch_action = QAction("Run &Batch Transcription...", self)
        batch_action.triggered.connect(self._open_batch_run_dialog)
        transcribe_menu.addAction(batch_action)

        transcribe_menu.addSeparator()

        manage_tags_action = QAction("Manage &Tags...", self)
        manage_tags_action.triggered.connect(self._open_manage_tags_dialog)
        transcribe_menu.addAction(manage_tags_action)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")

        settings_action = QAction("&Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

        status_action = QAction("&System Status...", self)
        status_action.triggered.connect(self._show_system_status)
        tools_menu.addAction(status_action)

        tools_menu.addSeparator()

        log_action = QAction("Open &Log File", self)
        log_action.triggered.connect(self._open_log_file)
        tools_menu.addAction(log_action)

        batch_log_action = QAction("Open &Batch Log", self)
        batch_log_action.triggered.connect(self._open_batch_log_file)
        tools_menu.addAction(batch_log_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        help_action = QAction("TalkTrack &Help && Docs", self)
        help_action.triggered.connect(self._open_help)
        help_menu.addAction(help_action)

        contact_action = QAction("&Contact && Discussions", self)
        contact_action.triggered.connect(self._open_contact)
        help_menu.addAction(contact_action)

        report_action = QAction("&Report a Bug...", self)
        report_action.triggered.connect(self._report_bug)
        help_menu.addAction(report_action)

        help_menu.addSeparator()

        support_action = QAction("&Support TalkTrack", self)
        support_action.triggered.connect(lambda: webbrowser.open(BMAC_URL))
        help_menu.addAction(support_action)

        help_menu.addSeparator()

        about_action = QAction("&About TalkTrack", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _toggle_recording_from_menu(self):
        """Ctrl+R: start when idle, stop when recording/paused. The compact
        strip and recording controls buttons already swap their own icon;
        this is the single entry point so the accelerator doesn't need to
        know which button is currently shown."""
        if self.recorder.state in (RecordingState.RECORDING, RecordingState.PAUSED):
            self._stop_recording()
        elif self.recorder.state == RecordingState.IDLE:
            self._start_recording()

    def _import_stranded_transcript_exports(self):
        """Move exports stranded in the old separate transcripts/ folder
        into their matching recording's own session folder, once.

        The transcripts/ folder (and its `transcripts.directory` setting) is
        gone as of #74 — exports now live at <session_dir>/transcript.md.
        Scans both the last-configured folder (if the old setting is still
        present in settings.json) and the legacy repo-relative default the
        app used before the Documents data-dir move (c49d8c6/d8e86fc), since
        either could hold exports never migrated forward. The config flag
        makes every later launch free, and is set even when nothing moved.
        """
        if self.config.get("transcripts", "session_import_done"):
            return
        from app.utils.transcripts_migration import import_exports_into_sessions
        legacy_default = str(Path(__file__).parent.parent / "transcripts")
        last_configured = (self.config.data.get("transcripts") or {}).get("directory")
        moved = import_exports_into_sessions(
            [last_configured, legacy_default],
            self.config.get("output", "directory"),
        )
        # Config.set() persists on its own — no separate save() call.
        self.config.set("transcripts", "session_import_done", True)
        if moved:
            logger.info("Imported %d stranded transcript export(s) into session folders", len(moved))

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top: Capture Bar
        self.recording_controls = RecordingControls()
        main_layout.addWidget(self.recording_controls)

        # Top: Notification Region
        self.notification_region = NotificationRegion()
        main_layout.addWidget(self.notification_region)

        # Three-column splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setStyleSheet("QSplitter::handle { background-color: #292b31; }")

        # Column A: Library
        self.library_panel = QWidget()
        library_layout = QVBoxLayout(self.library_panel)
        library_layout.setContentsMargins(0, 0, 0, 0)
        recordings_dir = self.config.get("output", "directory")
        self.recordings_list = RecordingsList(recordings_dir)
        library_layout.addWidget(self.recordings_list)
        self.splitter.addWidget(self.library_panel)

        # Column B: Transcript
        self.transcript_panel = QWidget()
        transcript_layout = QVBoxLayout(self.transcript_panel)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        
        self.recording_header = RecordingHeader()
        transcript_layout.addWidget(self.recording_header)

        # Built here (not by TranscriptViewer) because it lives in the
        # Inspector's "Speakers" section, not the transcript column.
        from app.ui.speaker_name_panel import SpeakerNamePanel
        self.speaker_panel = SpeakerNamePanel(config=self.config)

        self.transcript_viewer = TranscriptViewer(config=self.config, speaker_panel=self.speaker_panel)
        transcript_layout.addWidget(self.transcript_viewer)
        self.splitter.addWidget(self.transcript_panel)

        # Column C: Inspector
        self.inspector = InspectorWidget()

        self.notes_panel = NotesPanel()
        self.inspector.add_notes_panel(self.notes_panel)

        self.inspector.add_speakers_panel(self.speaker_panel)

        self.summary_panel = SummaryPanel()
        self.action_items_panel = ActionItemsPanel()
        self.inspector.add_summary_panel(self.summary_panel, self.action_items_panel)
        
        self.chat_panel = ChatPanel()
        self.inspector.add_chat_panel(self.chat_panel)
        
        self.splitter.addWidget(self.inspector)

        # Set default sizes — library and inspector fixed-ish, transcript
        # absorbs resize slack (per the capture-bar design spec).
        self.splitter.setSizes([262, 776, 322])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, True)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        main_layout.addWidget(self.splitter, 1)

        # Background monitors
        self._mic_level_tracker = MicLevelTracker()
        self.mic_monitor = MicMonitor(
            sample_rate=self.config.get("audio", "sample_rate"),
            level_callback=self._on_idle_mic_chunk,
        )
        self.mic_monitor_2 = MicMonitor(
            sample_rate=self.config.get("audio", "sample_rate"),
            level_callback=lambda chunk: None,
        )
        self.system_monitor = None
        # Polls the "quiet mic" reading into the pre-flight verdict while
        # idle — the level itself streams in off-thread via mic_monitor
        # (see _on_idle_mic_chunk); this timer is what's allowed to touch
        # Qt widgets with it.
        self._preflight_poll_timer = QTimer(self)
        self._preflight_poll_timer.timeout.connect(self._poll_preflight_level)
        self._preflight_poll_timer.start(1000)
        
        # Instantiate these so the rest of MainWindow doesn't crash
        from app.ui.source_selector import SourceSelector
        self.source_selector = SourceSelector(config=self.config, com_poller=self._com_poller)
        self.source_selector.hide()
        self.calendar_banner = CalendarSuggestionBanner()
        self.calendar_banner.hide()
        self.meeting_banner = MeetingBanner()
        self.meeting_banner.hide()
        self.meeting_toast = MeetingNotificationToast()
        self.meeting_toast.hide()
        self.waveform = WaveformDisplay(seconds=5, sample_rate=16000)
        self.meters_panel = MetersPanel()

    def _on_right_panel_collapse_changed(self, collapsed):
        self.config.set("ui", "right_panel_collapsed", collapsed)



    def _setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.status_label = QLabel("Ready")
        self.statusbar.addWidget(self.status_label)

        self.batch_indicator = QPushButton()
        self.batch_indicator.setIcon(QIcon(colored_pixmap("cpu", "#cba6f7", 12)))
        self.batch_indicator.setObjectName("batchIndicatorBtn")
        self.batch_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_indicator.setStyleSheet(
            "QPushButton#batchIndicatorBtn {"
            " background-color: rgba(203, 166, 247, 0.18);"
            " color: #cba6f7;"
            " font-size: 11px; font-weight: bold;"
            " border: 1px solid rgba(203, 166, 247, 0.4);"
            " border-radius: 10px; padding: 2px 10px;"
            "}"
            "QPushButton#batchIndicatorBtn:hover {"
            " background-color: rgba(203, 166, 247, 0.32);"
            " border-color: #cba6f7;"
            "}"
        )
        self.batch_indicator.clicked.connect(self._show_batch_process_info)
        self.batch_indicator.hide()
        self.statusbar.addPermanentWidget(self.batch_indicator)

    def _connect_signals(self):
        self.inspector.connect_provider_requested.connect(
            lambda: self._open_settings(initial_tab="AI Assistant")
        )
        self.transcript_viewer.open_last_requested.connect(self._open_last_recording)

        # Recording controls
        self.recording_controls.record_clicked.connect(self._start_recording)
        self.recording_controls.pause_clicked.connect(self._toggle_pause)
        self.recording_controls.stop_clicked.connect(self._stop_recording)
        self.recording_controls.mute_clicked.connect(self._toggle_mute)
        self.recording_controls.test_mic_toggled.connect(self._on_test_mic_toggled)
        self.recording_controls.compact_mode_requested.connect(self._advance_presentation)
        self.recording_controls.cancel_clicked.connect(self._cancel_transcription)

        # Recorder signals
        self.recorder.state_changed.connect(self._on_state_changed)
        self.recorder.time_updated.connect(self.recording_controls.update_time)
        self.recorder.time_updated.connect(self._on_recording_tick)
        self.recorder.recording_finished.connect(self._on_recording_finished)
        self.recorder.recording_discarded.connect(self._on_recording_discarded)
        self.recorder.finalize_progress.connect(self.status_label.setText)
        self.recorder.error_occurred.connect(self._on_error)
        self.recorder.mic_level.connect(self.meters_panel.update_mic_level)
        self.recorder.mic_level.connect(self.waveform.append_audio)
        self.recorder.mic_level.connect(self.recording_controls.live_meters.update_mic_level)
        self.recorder.mic_level.connect(self._on_compact_strip_mic_level)
        self.recorder.system_level.connect(self.meters_panel.update_system_level)
        self.recorder.system_level.connect(self.waveform.append_system_audio)
        self.recorder.system_level.connect(self.recording_controls.live_meters.update_system_level)
        self.recorder.system_level.connect(self._on_compact_strip_system_level)

        # Transcript
        self.transcript_viewer.transcribe_requested.connect(self._on_viewer_transcribe_requested)
        self.transcript_viewer.cancel_requested.connect(self._cancel_transcription)
        self.transcript_viewer.diarize_toggled.connect(self._on_diarize_toggled)
        self.transcript_viewer.diarize_requested.connect(self._on_diarize_requested)
        self._sync_diarization_controls()

        # Recordings list
        self.recordings_list.recording_selected.connect(self._on_recording_selected)
        self.recordings_list.about_to_delete.connect(self._on_recording_about_to_delete)
        self.recordings_list.recording_deleted.connect(self._on_recording_deleted)
        self.recordings_list.recording_files_changed.connect(self._on_recording_files_changed)
        self.recordings_list.search_result_selected.connect(self._on_search_result_selected)
        self.recordings_list.import_requested.connect(self._on_import_requested)
        self.recordings_list.transcribe_selected_requested.connect(self._on_transcribe_selected)
        self.recordings_list.export_selected_requested.connect(self._on_export_selected)
        self.recordings_list.run_batch_requested.connect(self._open_batch_run_dialog)

        # Mic device change: restart monitor on new device if it's running
        self.source_selector.mic_changed.connect(self._on_mic_device_changed)

        # Pre-flight verdict: recompute on anything that could change it.
        self.recording_controls.sources_clicked.connect(self._open_source_selector)
        self.source_selector.devices_changed.connect(self._update_preflight)
        self.source_selector.mic_changed.connect(lambda _: self._update_preflight())
        self.source_selector.mismatch_changed.connect(self._update_preflight)

        # Auto-stop when call ends / auto-start when call begins
        self.source_selector.apps_went_inactive.connect(self._on_apps_went_inactive)
        self.recorder.silence_detected.connect(self._on_silence_detected)
        self.recorder.capture_status.connect(self._on_capture_status)
        self.recorder.pid_lost.connect(self._on_pid_lost)
        self.recorder.capture_lost.connect(self._on_capture_lost)

        # Recording header
        self.recording_header.name_changed.connect(self._on_recording_renamed_with_tag)
        self.recording_header.rename_started.connect(self._on_rename_started)
        self.recording_header.change_calendar_requested.connect(self._on_change_calendar_requested)
        self.recording_header.tags_changed.connect(self._on_header_tags_changed)
        self.recording_header.manage_tags_requested.connect(self._open_manage_tags_dialog)
        self.recording_header.tag_dialog_requested.connect(self._open_tag_dialog)
        self.recordings_list.manage_tags_requested.connect(self._open_manage_tags_dialog)
        self.recordings_list.recording_tags_changed.connect(self._on_recordings_list_tags_changed)

        # Transcript editing
        self.transcript_viewer.transcript_changed.connect(self._save_transcript)
        self.transcript_viewer.speaker_names_changed.connect(self._save_speaker_names)

        # Summary / action items
        self.summary_panel.regenerate_requested.connect(self._regenerate_summary)
        self.action_items_panel.regenerate_requested.connect(self._regenerate_summary)
        self.action_items_panel.items_changed.connect(self._on_action_items_changed)

        # Meeting detection prompts (floating desktop toast only;
        # the compact strip's armed state covers the visible-strip case)
        self.meeting_toast.record_accepted.connect(self._on_meeting_start_accepted)
        self.meeting_toast.dismissed.connect(self._on_meeting_start_dismissed)
        self.meeting_toast.end_chosen.connect(self._on_meeting_end_chosen)

        # Calendar suggestion banner (post-recording calendar match)
        self.calendar_banner.tag_requested.connect(self._on_calendar_tag_requested)
        self.calendar_banner.dismissed.connect(self._on_calendar_dismissed)

        # Deferred (not called synchronously here): opens a real audio device,
        # so firing it during __init__ made every test that constructs a bare
        # MainWindow() start a live sounddevice capture thread that outlived
        # the test and was never stopped. singleShot follows the same
        # avoid-firing-during-construction idiom as _check_startup_status /
        # _maybe_offer_start_menu_shortcut below — no test processes events
        # for this long, so it never fires in the test suite.
        QTimer.singleShot(500, self._start_idle_mic_level_monitor)
        self._update_preflight()

    def _start_recording(self):
        if getattr(self, "_meeting_detector", None) is not None and self._meeting_detector.state == "suggested":
            self._meeting_detector.accept_start()
        self.meeting_banner.hide_and_clear()
        if hasattr(self, "meeting_toast"):
            self.meeting_toast.hide_and_clear()
        if getattr(self, "_pending_meeting_notification", None) == "start":
            self._pending_meeting_notification = None
        self._silent_capture_warned = False

        mic = self.source_selector.get_selected_mic()
        mic2 = self.source_selector.get_selected_mic2()
        capture_mode = self.source_selector.get_capture_mode()
        app_pids = self.source_selector.get_selected_app_pids()
        loopback = self.source_selector.get_selected_loopback()

        # Validate: need at least one audio source
        from app.ui.notification_region import PRIORITY_BLOCKING_ERROR
        if mic is None and mic2 is None and loopback is None and not app_pids:
            self.notification_region.enqueue(
                priority=PRIORITY_BLOCKING_ERROR,
                text="Please select at least one audio source (microphone, system audio, or app)."
            )
            return

        # Validate: per-app mode needs at least one app checked
        if capture_mode == "per_app" and not app_pids:
            self.notification_region.enqueue(
                priority=PRIORITY_BLOCKING_ERROR,
                text="Select at least one app to capture, or switch to 'Capture all system audio' mode."
            )
            return

        # Save capture settings for next session
        self.source_selector.save_capture_settings()

        # Release the test monitors so the recorder can claim the devices.
        # No signal fired — we've already handled the teardown here.
        if self.mic_monitor.is_active:
            self.mic_monitor.stop()
        if self.mic_monitor_2.is_active:
            self.mic_monitor_2.stop()
        self._stop_system_monitor()
        self.recording_controls.clear_test_mic()

        self.recorder.start_recording(
            mic_device=mic,
            loopback_device=loopback,
            capture_mode=capture_mode,
            app_pids=app_pids,
            mic_device_2=mic2,
        )
        # Apply "start muted" setting
        start_muted = self.config.get("audio", "mic_mute_on_start")
        self._mic_muted = bool(start_muted)
        if self.recorder._capture is not None:
            self.recorder._capture.set_muted(self._mic_muted)
        self.recording_controls.set_muted(self._mic_muted)
        self.waveform.set_mic_muted(self._mic_muted)
        # Apply saved mic gain
        mic_gain = self.config.get("audio", "mic_gain")
        if self.recorder._capture is not None:
            self.recorder._capture.set_gain(mic_gain)
        self.notes_panel.set_recording_start(datetime.now())
        self.chat_panel.clear_chat()
        self.status_label.setText("Recording...")

    def _toggle_pause(self):
        if self.recorder.state == RecordingState.RECORDING:
            self.recorder.pause_recording()
            self.status_label.setText("Paused")
        elif self.recorder.state == RecordingState.PAUSED:
            self.recorder.resume_recording()
            self.status_label.setText("Recording...")

    def _toggle_mute(self):
        """Toggle mic mute state mid-recording."""
        if self.recorder.state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            return
        self._mic_muted = not self._mic_muted
        if self.recorder._capture is not None:
            self.recorder._capture.set_muted(self._mic_muted)
        self.recording_controls.set_muted(self._mic_muted)
        self.waveform.set_mic_muted(self._mic_muted)
        self.status_label.setText("Microphone muted" if self._mic_muted else "Recording...")
        self._update_compact_strip_state()

    def _on_test_mic_toggled(self, enabled):
        """User clicked Test Mic. Start or stop mic + system level monitors."""
        if enabled:
            if self.recorder.state != RecordingState.IDLE:
                # Safety — the button is disabled during recording, but guard
                # against races rather than double-opening the device.
                self.recording_controls.clear_test_mic()
                return
            gain = self.config.get("audio", "mic_gain")
            self.mic_monitor.set_gain(gain)
            self.mic_monitor.start(self.source_selector.get_selected_mic())
            mic2 = self.source_selector.get_selected_mic2()
            if mic2 is not None:
                self.mic_monitor_2.set_gain(gain)
                self.mic_monitor_2.start(mic2)
            self._start_system_monitor()
        else:
            self.mic_monitor.stop()
            self.mic_monitor_2.stop()
            self._stop_system_monitor()
            self.meters_panel.reset()

    def _start_system_monitor(self):
        """Start a buffer-less system audio stream feeding the system meter.

        In per-app mode, uses ProcessAudioCapture on the selected PIDs.
        In legacy mode (or when no PIDs checked), uses LoopbackStream.
        """
        self._stop_system_monitor()
        mode = self.source_selector.get_capture_mode()

        if mode == "per_app":
            pids = self.source_selector.get_selected_app_pids()
            if not pids:
                return
            monitor = ProcessAudioCapture(
                pids=pids,
                sample_rate=self.config.get("audio", "sample_rate"),
                level_callback=self.meters_panel.update_system_level,
                enable_buffer=False,
            )
            status = monitor.start()
            if status["active"] == 0:
                logger.warning("Test per-app monitor failed: %s", status["failures"])
                self.status_label.setText(
                    f"Test failed: 0 of {status['total']} selected app PIDs activated"
                )
                return
            if status["failures"]:
                logger.warning(
                    "Test per-app monitor: partial activation %d of %d — failures=%s",
                    status["active"], status["total"], status["failures"],
                )
                self.status_label.setText(
                    f"Testing {status['active']} of {status['total']} app PIDs "
                    "(some failed — see log)"
                )
            self.system_monitor = monitor
            return

        # Legacy WASAPI loopback path.
        device = self.source_selector.get_selected_loopback()
        if device is None:
            return
        try:
            dev_info = sd.query_devices(device)
            device_name = dev_info.get("name", "")
            self.system_monitor = LoopbackStream(
                device_name=device_name,
                sample_rate=self.config.get("audio", "sample_rate"),
                level_callback=self.meters_panel.update_system_level,
            )
            self.system_monitor.start()
        except Exception as e:
            logger.warning("Test system monitor failed: %s", e)
            self.system_monitor = None

    def _stop_system_monitor(self):
        if self.system_monitor is None:
            return
        try:
            self.system_monitor.stop()
        except Exception as e:
            logger.debug("System monitor stop error: %s", e)
        self.system_monitor = None

    def _open_source_selector(self):
        """Sources button on the capture bar. Non-modal — per the redesign,
        a modal here would steal focus from a call in progress."""
        self.source_selector.show()
        self.source_selector.raise_()
        self.source_selector.activateWindow()

    def _on_mic_device_changed(self, device_index):
        """Mic dropdown changed. If the monitor is running, move it to the new device."""
        if self.mic_monitor.is_active:
            self._mic_level_tracker.reset()
            self.mic_monitor.start(device_index)

    def _on_idle_mic_chunk(self, chunk):
        """MicMonitor's level_callback while idle — runs on the audio
        callback thread, not the UI thread. Only touches the tracker's
        plain list (no Qt calls here); _poll_preflight_level reads it back
        from a QTimer on the UI thread."""
        self._mic_level_tracker.ingest(chunk)

    def _start_idle_mic_level_monitor(self):
        """Keep MicMonitor running whenever idle and a mic is selected, so
        the pre-flight bar can flag a too-quiet mic before the user hits
        Record — not just missing/mismatched device selection.

        Skipped under the offscreen platform (every test sets
        QT_QPA_PLATFORM=offscreen; real runs never do): opening a real
        sounddevice input stream as a side effect of constructing a window
        is fine for the live app but not for the many tests that build a
        real MainWindow() without ever closing it — that left a live audio
        callback thread running unstopped for the rest of the suite and
        crashed the process (native access violation racing torch's DLL
        loading later in the run). Deferring the call via singleShot at the
        call sites reduces exposure but doesn't remove it, since the bound
        singleShot keeps even a test's MainWindow alive until it fires — the
        guard below is the actual fix."""
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        if self.recorder.state != RecordingState.IDLE:
            return
        mic = self.source_selector.get_selected_mic()
        if mic is None:
            self._stop_idle_mic_level_monitor()
            return
        if self.mic_monitor.is_active and self.mic_monitor.device_index == mic:
            return
        self._mic_level_tracker.reset()
        self.mic_monitor.start(mic)

    def _stop_idle_mic_level_monitor(self):
        self.mic_monitor.stop()
        self._mic_level_tracker.reset()

    def _poll_preflight_level(self):
        """1s tick: only recomputes the verdict while idle — while recording
        the pre-flight bar isn't shown, and mic_monitor doesn't own the
        device anyway."""
        if getattr(self, "_closing", False):
            return
        if self.recorder.state != RecordingState.IDLE:
            return
        self._update_preflight()

    def _on_gain_changed(self, gain):
        """Slider moved - apply live gain to capture or test monitor, debounce config write."""
        self._pending_gain = float(gain)
        if self.recorder._capture is not None:
            self.recorder._capture.set_gain(gain)
        if self.mic_monitor.is_active:
            self.mic_monitor.set_gain(gain)
        if self.mic_monitor_2.is_active:
            self.mic_monitor_2.set_gain(gain)
        self._gain_save_timer.start(500)

    def _flush_gain_to_config(self):
        """Write pending gain value to config."""
        if self._pending_gain is None:
            return
        if self._pending_gain != self.config.get("audio", "mic_gain"):
            self.config.set("audio", "mic_gain", self._pending_gain)
            self.config.save()
        self._pending_gain = None

    def _stop_recording(self):
        # Set the label BEFORE the call. It used to be set after, so it
        # only ever appeared once the work was finished — and it then
        # overwrote the status _on_recording_finished had just set, leaving
        # the bar reading "Stopping..." while transcription was underway.
        self.status_label.setText("Finishing recording...")
        self.recorder.stop_recording()

    def _on_apps_went_inactive(self):
        """Auto-stop recording when all selected apps leave their call."""
        if self.recorder.state in (RecordingState.RECORDING, RecordingState.PAUSED):
            if self.source_selector.is_per_app_mode():
                logger.warning("Auto-stop: selected apps went inactive (per-app mode)")
                self.status_label.setText("Call ended — stopping recording...")
                self.recorder.stop_recording()

    def _on_silence_detected(self, seconds):
        """Auto-stop recording when system audio has been silent too long."""
        if self.recorder.state in (RecordingState.RECORDING, RecordingState.PAUSED):
            logger.warning(
                "Auto-stop: silence on system audio for %.1fs (threshold=%.3f, duration=%ss)",
                seconds,
                0.005,
                self.config.get("general", "silence_duration"),
            )
            self.status_label.setText(
                f"Silence detected ({seconds:.0f}s) — stopping recording..."
            )
            self.recorder.stop_recording()

    # --- meeting detection ----------------------------------------------
    def _meeting_settings(self):
        return self.config.data.get("meeting_detection", {})

    def _poll_meeting_signals(self):
        settings = self._meeting_settings()
        if settings.get("mode", "off") == "off":
            return
        com_snapshot = self._com_poller.get_snapshot()
        if hasattr(self, "source_selector") and self.source_selector is not None:
            self.source_selector.check_device_mismatches(com_snapshot.get("app_devices", {}))
        snapshot = meeting_signals.probe(
            settings, calendar_event=self._current_calendar_event,
            _audio_apps_fn=lambda: com_snapshot["audio_apps"],
            _mic_pids_fn=lambda: com_snapshot["mic_pids"])
        self._last_meeting_snapshot = snapshot

        # A recording can start (or already be running) through a route the
        # detector never saw — e.g. it began while mode was "off", or the user
        # clicked Record manually. Resync before every tick so a start
        # suggestion is never produced for a meeting we're already recording.
        if (self.recorder.state != RecordingState.IDLE
                and self._meeting_detector.state not in ("recording", "paused_by_detection")):
            self._meeting_detector.note_recording_started(snapshot)

        decision = self._meeting_detector.update(snapshot, settings)
        if decision.action != "none":
            logger.info("Meeting detection decision: %s (%s)",
                        decision.action, decision.meeting_name)
            self._handle_meeting_decision(decision, snapshot)

    def _handle_meeting_decision(self, decision, snapshot):
        action = decision.action
        if action in ("suggest_start", "start") and self.recorder.state != RecordingState.IDLE:
            # Belt-and-suspenders: never offer or auto-start a recording on
            # top of one already running.
            self.meeting_banner.hide_and_clear()
            if hasattr(self, "meeting_toast"):
                self.meeting_toast.hide_and_clear()
            return
        if action == "suggest_start":
            self._active_detected_meeting_name = decision.meeting_name
            elapsed = self._meeting_elapsed(snapshot)
            # Show the floating toast only when the compact strip isn't
            # already visible — the strip's armed state with its Record
            # button is sufficient when it's on screen.
            if hasattr(self, "meeting_toast") and not self.compact_strip.isVisible():
                self.meeting_toast.show_start(decision.meeting_name, elapsed)
        elif action == "start":
            self._active_detected_meeting_name = decision.meeting_name
            display_name = decision.meeting_name or "Meeting"
            self.status_label.setText(f"{display_name} detected — auto-recording...")
            self._start_recording()
        elif action == "suggest_end":
            elapsed = self.recorder.get_elapsed_time()
            if hasattr(self, "meeting_toast") and not self.compact_strip.isVisible():
                self.meeting_toast.show_end(decision.meeting_name, elapsed)
        elif action == "stop":
            self.status_label.setText("Meeting ended — stopping recording...")
            self.recorder.stop_recording()
        elif action == "pause":
            self.status_label.setText("Meeting ended — recording paused.")
            self.recorder.pause_recording()
        elif action == "resume":
            self.status_label.setText("Meeting resumed — recording.")
            self.recorder.resume_recording()

    def _meeting_elapsed(self, snapshot):
        """Seconds since this meeting's signals first appeared."""
        started = self._meeting_detector.active_since
        if started is None:
            return 0
        return max(0, snapshot["timestamp"] - started)

    def _on_meeting_start_accepted(self):
        self._meeting_detector.accept_start()
        self.meeting_banner.hide_and_clear()
        if hasattr(self, "meeting_toast"):
            self.meeting_toast.hide_and_clear()
        if self.recorder.state == RecordingState.IDLE:
            self._start_recording()

    def _on_tray_message_clicked(self):
        """Clicking the meeting-notification balloon acts on it directly.

        A "start" notification starts recording without raising the window —
        _start_recording's own _on_state_changed call already shows the
        floating activity pill instead when the window is minimized/hidden,
        same as starting from the tray menu's Record action. An "end"
        notification instead brings the window forward, since stop/pause/keep
        is a 3-way choice the banner already shows and a single click can't
        collapse into one action.
        """
        kind = self._pending_meeting_notification
        self._pending_meeting_notification = None
        if kind == "start":
            self.meeting_banner.hide_and_clear()
            if hasattr(self, "meeting_toast"):
                self.meeting_toast.hide_and_clear()
            self._on_meeting_start_accepted()
        elif kind == "end":
            self._restore_from_tray()

    def _on_meeting_start_dismissed(self):
        self._meeting_detector.dismiss_start()
        self.meeting_banner.hide_and_clear()
        if hasattr(self, "meeting_toast"):
            self.meeting_toast.hide_and_clear()

    def _on_meeting_end_chosen(self, action):
        self._meeting_detector.choose_end(action)
        self.meeting_banner.hide_and_clear()
        if hasattr(self, "meeting_toast"):
            self.meeting_toast.hide_and_clear()
        if action == "stop":
            self.recorder.stop_recording()
        elif action == "pause":
            self.recorder.pause_recording()

    def _on_recording_discarded(self, duration):
        """Handle recording discarded due to min length."""
        min_len = self.config.get("general", "min_recording_length")
        self.status_label.setText(
            f"Recording discarded ({duration:.0f}s < {min_len}s minimum)"
        )

    def _on_state_changed(self, state):
        self.recording_controls.set_state(state)
        self.source_selector.set_enabled(state == RecordingState.IDLE)

        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(state, int(self.recorder.get_elapsed_time()))

        if state == RecordingState.RECORDING:
            self._stop_idle_mic_level_monitor()
            self._compact_strip_done = False
            self.meeting_banner.hide_and_clear()
            if hasattr(self, "meeting_toast"):
                self.meeting_toast.hide_and_clear()
            if getattr(self, "_pending_meeting_notification", None) == "start":
                self._pending_meeting_notification = None
            self.source_selector.set_recording_active(True)
            if not self.waveform.isVisible():
                self.waveform.start()
            else:
                self.waveform._paint_timer.start()
            if self._last_meeting_snapshot and self._meeting_detector.state not in (
                    "recording", "paused_by_detection"):
                self._meeting_detector.note_recording_started(self._last_meeting_snapshot)
            if not self._active_detected_meeting_name and self._last_meeting_snapshot:
                self._active_detected_meeting_name = MeetingDetector._name(self._last_meeting_snapshot)
            self._detected_session_meeting_name = self._active_detected_meeting_name
        elif state == RecordingState.PAUSED:
            self.waveform._paint_timer.stop()
        elif state == RecordingState.IDLE:
            self.source_selector.set_recording_active(False)
            self.waveform.stop()
            self.recording_controls.reset_timer()
            self.meters_panel.reset()
            self.recording_controls.live_meters.reset()
            self._mic_muted = False
            self.waveform.set_mic_muted(False)
            self._current_capture_failures = {}
            self.source_selector.mark_capture_failures({})
            self._meeting_detector.note_recording_stopped()
            self.meeting_banner.hide_and_clear()
            if hasattr(self, "meeting_toast"):
                self.meeting_toast.hide_and_clear()
            self._active_detected_meeting_name = None
            # See the singleShot in _connect_signals for why this is deferred
            # rather than called synchronously.
            QTimer.singleShot(0, self._start_idle_mic_level_monitor)

        self._update_activity_visibility()

    def _on_recording_tick(self, seconds):
        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(self.recorder.state, int(seconds))
        self._check_silent_capture(seconds)
        self._update_activity_visibility()
        total = max(0, int(seconds))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        self.compact_strip.update_timer(f"{h:02d}:{m:02d}:{s:02d}")

    def _check_silent_capture(self, seconds):
        """Warn once if per-app capture has produced zero audio.

        Conferencing apps (Teams/Zoom/WebEx) opt their call audio out of
        process-loopback: activation succeeds but only silence arrives, and
        the user finds out after the meeting. 15s in with nothing received,
        tell them now.
        """
        if self._silent_capture_warned or seconds < 15:
            return
        if self.recorder.state != RecordingState.RECORDING:
            return
        capture = getattr(self.recorder, "_capture", None)
        if capture is None or capture.system_audio_received():
            return
        self._silent_capture_warned = True
        msg = (
            "No audio has been received from the selected app(s) yet.\n\n"
            "Conferencing apps (Teams, Zoom, WebEx) block per-app capture "
            "of their call audio. To record a call, stop this recording and "
            "switch the audio source to legacy system audio mode."
        )
        self.status_label.setText(
            "Warning: no audio received from selected app(s) — "
            "see Audio Sources."
        )
        if self._is_hidden_to_tray():
            self._flag_error_notification()
        else:
            QMessageBox.warning(self, "Capturing Silence", msg)

    def _warn_speaker_bleed(self, dropped):
        """Tell the user once that their mic is picking up the other side.

        The duplicates are removed from the transcript, but bleed also
        degrades the mic track itself and makes the remote voice audible
        under the user's own. Headphones are the only real fix — the app
        has no echo cancellation.
        """
        if self._bleed_warned or dropped < BLEED_WARNING_SEGMENTS:
            return
        self._bleed_warned = True
        self.status_label.setText(
            f"Your microphone picked up the other side ({dropped} duplicate "
            "segments removed) — headphones will improve quality."
        )
        if self._is_hidden_to_tray():
            return
        QMessageBox.information(
            self, "Microphone Picking Up Speakers",
            f"{dropped} segments of the other side's speech were also "
            "recorded through your microphone and have been removed from "
            "the transcript.\n\n"
            "This happens when call audio plays through speakers instead of "
            "headphones. Using headphones gives a cleaner recording of your "
            "own voice.",
        )

    def _is_hidden_to_tray(self):
        return hasattr(self, "tray") and self.tray.is_supported() and self.isHidden()

    def _flag_error_notification(self):
        self._error_pending = True
        from app.ui.tray_icon import resolve_overlay
        self.tray.set_overlay(resolve_overlay(self._success_pending, self._error_pending))

    def _flag_success_notification(self):
        self._success_pending = True
        from app.ui.tray_icon import resolve_overlay
        self.tray.set_overlay(resolve_overlay(self._success_pending, self._error_pending))

    def _on_import_requested(self, source_path):
        import os
        import shutil
        import subprocess
        import soundfile as sf
        from datetime import datetime
        from app.utils.atomic_io import atomic_write_json

        mtime = datetime.fromtimestamp(os.path.getmtime(source_path))
        dialog = ImportTimestampDialog(mtime, parent=self)
        if not dialog.exec():
            return
        started_at = dialog.selected_datetime()

        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.config.get("output", "directory"))
        session_dir = output_dir / f"recording_{timestamp}"
        try:
            session_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(
                self, "Import Failed",
                "A recording already exists for this timestamp — adjust the "
                "time slightly and try again."
            )
            return

        audio_filename = "combined_audio.wav"
        dest_path = session_dir / audio_filename

        # ffmpeg conversion / large-file copy run synchronously on the GUI
        # thread (a full off-thread rewrite is out of scope for this fix) —
        # at minimum show a busy cursor so the window doesn't look hung.
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            if needs_conversion(source_path):
                if not shutil.which("ffmpeg"):
                    QMessageBox.warning(
                        self, "Import Failed",
                        "This file needs FFmpeg to convert from M4A, but FFmpeg "
                        "wasn't found on PATH. Install FFmpeg and try again, or "
                        "convert the file to WAV/MP3 first."
                    )
                    shutil.rmtree(session_dir, ignore_errors=True)
                    return
                subprocess.run(
                    ["ffmpeg", "-y", "-i", source_path, str(dest_path)],
                    capture_output=True, check=True, timeout=300,
                )
            else:
                shutil.copy2(source_path, dest_path)

            duration = sf.info(str(dest_path)).duration
            if not duration:
                QMessageBox.warning(
                    self, "Import Failed",
                    "Could not import file: audio has zero duration."
                )
                shutil.rmtree(session_dir, ignore_errors=True)
                return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                RuntimeError, OSError) as e:
            QMessageBox.warning(self, "Import Failed", f"Could not import file: {e}")
            shutil.rmtree(session_dir, ignore_errors=True)
            return
        finally:
            self.unsetCursor()

        session = build_import_metadata(
            source_path=source_path,
            session_dir=str(session_dir),
            started_at=started_at,
            duration=duration,
            audio_filename=audio_filename,
        )
        atomic_write_json(session_dir / "metadata.json", session, indent=2)

        # The notes editor still holds whatever was loaded for the
        # last-viewed recording (from _on_recording_selected). Clear it
        # before funneling into _on_recording_finished — which treats a
        # non-empty editor as "notes typed live during this recording" and
        # would otherwise duplicate the stale notes into the imported
        # session's notes.txt.
        self.notes_panel.clear()

        self._on_recording_finished(session)

    def _on_recording_finished(self, session):
        # Clear any stale calendar-suggestion banner from a previous session
        # before switching — otherwise a Tag/Dismiss click after this point
        # can write the previous recording's calendar match into this one's
        # calendar_event.json / metadata.json.
        self.calendar_banner.hide_and_clear()
        self._calendar_banner_session = None

        self._current_session = session
        self._transcript = None
        self.status_label.setText("Recording saved.")

        # Clear previous recording's view
        self.transcript_viewer.clear()
        self.summary_panel.clear()
        self.action_items_panel.clear()

        # Set up transcript viewer for new recording
        audio_files = session.get("audio_files", {})
        combined = audio_files.get("combined")
        system = audio_files.get("system")
        mic = audio_files.get("mic")

        audio_for_transcript = combined or system or mic
        self.transcript_viewer.set_audio_path(audio_for_transcript)

        # Save notes typed during the recording into the new session.
        self.notes_panel.set_session_dir(session["directory"], keep_editor_text=True)
        self.notes_panel.save_notes()
        self._export_transcript()

        # Refresh recordings list
        self.recordings_list.refresh()

        # Update recording header
        self.recording_header.set_recording(session)

        # Auto-start transcription if enabled, audio available, long enough
        duration = session.get("duration", 0)
        min_duration = self.config.get("transcription", "min_duration")
        auto_transcribe = self.config.get("general", "auto_transcribe")
        if not auto_transcribe:
            if audio_for_transcript:
                queued = self._maybe_queue_for_batch(session)
                self.status_label.setText(
                    "Recording saved — queued for the next batch transcription run."
                    if queued else
                    "Recording saved — auto-transcribe disabled. "
                    "Use Transcribe button to transcribe manually."
                )
        elif audio_for_transcript and duration >= min_duration:
            self._start_transcription(audio_for_transcript)
        elif audio_for_transcript:
            queued = self._maybe_queue_for_batch(session)
            self.status_label.setText(
                f"Recording too short ({duration:.0f}s < {min_duration}s) — "
                + ("queued for the next batch transcription run." if queued else
                   "skipping auto-transcription. Use Transcribe button to "
                   "transcribe manually.")
            )

        detected_name = self._detected_session_meeting_name
        self._detected_session_meeting_name = None
        self._maybe_lookup_calendar(session, detected_name=detected_name)

        # Auto-tag if matched to previous recording by name
        rec_name = session.get("name") or detected_name
        if rec_name:
            self._maybe_autotag_recording(session, rec_name)

        # Prompt for tagging if enabled
        if self.config.get("general", "prompt_tags_after_recording"):
            self._open_tag_dialog(session)

    def _maybe_queue_for_batch(self, session):
        """Tag a recording the app isn't going to transcribe itself.

        Without this the feature needs a right-click after every call,
        which is exactly the manual step an overnight run exists to
        remove. Returns whether the tag was written.
        """
        if not self.config.get("general", "batch_auto_queue"):
            return False
        if not session or not session.get("directory"):
            return False
        if not batch_queue.set_queued(session["directory"], True):
            return False
        self.recordings_list.refresh()
        return True

    def _transcription_busy(self):
        return (
            (self._transcription_worker is not None and self._transcription_worker.isRunning())
            or (self._diarization_worker is not None and self._diarization_worker.isRunning())
            or (self._simple_diarize_worker is not None and self._simple_diarize_worker.isRunning())
            or (self._batch_worker is not None and self._batch_worker.isRunning())
        )

    def _on_transcribe_selected(self, recordings):
        queued = 0
        for metadata in recordings:
            audio_files = metadata.get("audio_files", {})
            audio_path = (audio_files.get("combined") or audio_files.get("system")
                          or audio_files.get("mic"))
            if audio_path and os.path.exists(audio_path):
                self._start_transcription(audio_path, session=metadata)
                queued += 1
        if queued:
            self.status_label.setText(f"Queued {queued} recording(s) for transcription.")

    def _on_export_selected(self, recordings):
        for metadata in recordings:
            self._export_transcript(metadata)
        self.status_label.setText(f"Exported {len(recordings)} transcript(s).")

    def _open_batch_run_dialog(self):
        """Show the batch transcription launch options dialog."""
        if self._batch_worker is not None and self._batch_worker.isRunning():
            QMessageBox.information(
                self, "Batch Transcription",
                "A batch transcription run is already active in the background.",
            )
            return

        from app.batch.worklist import build_worklist
        from app.ui.batch_run_dialog import BatchRunDialog, MODE_IN_APP, MODE_DETACHED

        recordings_dir = self.config.get("output", "directory")
        jobs = build_worklist(recordings_dir)
        dialog = BatchRunDialog(queued_count=len(jobs), config=self.config, parent=self)
        if not dialog.exec() or len(jobs) == 0:
            return

        mode = dialog.execution_mode()
        diarize = dialog.diarize_enabled()
        limit = dialog.limit()

        if mode == MODE_IN_APP:
            self._start_in_app_batch(diarize=diarize, limit=limit)
        else:
            self._launch_detached_batch(diarize=diarize, limit=limit)

    def _start_in_app_batch(self, diarize=None, limit=None):
        """Run batch transcription asynchronously in a background QThread."""
        if self._closing or (self._batch_worker is not None and self._batch_worker.isRunning()):
            return

        import time
        from app.batch.pipeline import BatchSettings
        from app.batch.worker import BatchRunnerWorker

        recordings_dir = self.config.get("output", "directory")
        settings = BatchSettings.from_config(self.config, diarize=diarize)

        self._batch_worker_start_time = time.time()
        self._batch_worker = BatchRunnerWorker(
            recordings_dir, settings=settings, limit=limit, parent=self
        )
        self._batch_worker.job_started.connect(self._on_batch_job_started)
        self._batch_worker.job_progress.connect(self._on_batch_job_progress)
        self._batch_worker.job_finished.connect(self._on_batch_job_finished)
        self._batch_worker.batch_finished.connect(self._on_batch_finished)
        self._batch_worker.cancelled.connect(self._on_batch_cancelled)
        self._batch_worker.start(QThread.Priority.LowPriority)

        self.status_label.setText("Starting batch transcription...")
        self._update_activity_visibility()
        self._poll_batch_processes()

    def _launch_detached_batch(self, diarize=None, limit=None):
        """Spawn batch_transcribe.py as a detached background OS process."""
        from app.batch.launcher import launch_detached_batch

        try:
            proc = launch_detached_batch(diarize=diarize, limit=limit)
            self.status_label.setText(
                f"Background batch process started (PID {proc.pid})."
            )
            self._poll_batch_processes()
            QMessageBox.information(
                self, "Batch Transcription",
                f"Background batch process launched (PID {proc.pid}).\n\n"
                "It will continue running even if TalkTrack is closed.\n"
                "Detailed progress is logged to Documents\\TalkTrack\\batch Log.",
            )
        except Exception as e:
            logger.exception("Failed to launch detached batch process")
            QMessageBox.warning(
                self, "Batch Launch Failed",
                f"Could not launch background batch process: {e}",
            )

    def _poll_batch_processes(self):
        """Check for active batch processes (detached OS processes or in-app worker)
        and update the status bar batch indicator."""
        from app.batch.process_monitor import find_running_batch_processes

        try:
            self._running_batch_processes = find_running_batch_processes(
                in_app_worker=self._batch_worker,
                in_app_start_time=self._batch_worker_start_time,
            )
        except Exception as e:
            logger.debug("Error polling batch processes: %s", e)
            self._running_batch_processes = []

        is_batch_running = bool(self._running_batch_processes)
        if hasattr(self, "recordings_list"):
            self.recordings_list.set_batch_running(is_batch_running)

        if self._running_batch_processes:
            primary = self._running_batch_processes[0]
            count = len(self._running_batch_processes)
            if count == 1:
                text = f"Batch Active (PID {primary.pid})"
                tooltip = (
                    f"Batch transcription running ({primary.process_type_label}, "
                    f"PID {primary.pid}, {primary.formatted_duration} elapsed).\n"
                    "Click to view details or end process."
                )
            else:
                text = f"Batch Active ({count} jobs)"
                tooltip = (
                    f"{count} batch transcription processes running.\n"
                    "Click to view details or end process."
                )
            self.batch_indicator.setText(text)
            self.batch_indicator.setToolTip(tooltip)
            self.batch_indicator.show()
        else:
            self.batch_indicator.hide()

    def _show_batch_process_info(self):
        """Show dialog with information about the currently active batch process."""
        from app.ui.batch_process_info_dialog import BatchProcessInfoDialog

        self._poll_batch_processes()
        if not self._running_batch_processes:
            QMessageBox.information(
                self, "Batch Process", "No batch transcription process is currently active."
            )
            return

        dialog = BatchProcessInfoDialog(
            self._running_batch_processes,
            in_app_worker=self._batch_worker,
            parent=self,
        )
        dialog.process_terminated.connect(self._on_batch_process_terminated)
        dialog.exec()
        self._poll_batch_processes()

    def _on_batch_process_terminated(self, pid):
        if self._batch_worker is not None and getattr(self._batch_worker, "isRunning", lambda: False)():
            self._batch_worker = None
            self._batch_worker_start_time = None
        self._poll_batch_processes()
        self.recordings_list.refresh()

    def _open_batch_logs_folder(self):
        """Open the folder containing batch process logs in Windows Explorer."""
        from app.batch.logging_setup import open_batch_logs_folder
        open_batch_logs_folder()

    def _open_batch_log_file(self):
        """Open the newest batch process log file, or folder if none exists."""
        from app.batch.logging_setup import open_batch_log, get_latest_log
        latest = get_latest_log()
        if latest and latest.exists():
            open_batch_log(latest)
        else:
            QMessageBox.information(
                self, "Batch Log", "No batch process log file found yet. Opening logs folder."
            )
            open_batch_log()

    def _on_batch_job_started(self, label, index, total):
        self.status_label.setText(f"Batch [{index}/{total}]: Transcribing {label}...")
        self._update_activity_visibility()

    def _on_batch_job_progress(self, message):
        self.status_label.setText(message)

    def _on_batch_job_finished(self, job, outcome):
        self.recordings_list.refresh()
        if (
            self._current_session
            and self._current_session.get("directory") == job.directory
            and outcome.ok
        ):
            self._on_recording_selected(self._current_session)

    def _on_batch_finished(self, processed, failed, deferred):
        self.recordings_list.refresh()
        self._batch_worker = None
        self._batch_worker_start_time = None
        self._update_activity_visibility()
        self._poll_batch_processes()

        msg = f"Batch transcription finished: {processed} recording(s) processed."
        if failed:
            msg += f" {failed} failed."
        if deferred:
            msg += f" {deferred} deferred past cutoff."

        self.status_label.setText(msg)
        if self._is_hidden_to_tray():
            if failed:
                self._flag_error_notification()
            else:
                self._flag_success_notification()
        else:
            QMessageBox.information(self, "Batch Run Finished", msg)

    def _on_batch_cancelled(self):
        self.recordings_list.refresh()
        self._batch_worker = None
        self._batch_worker_start_time = None
        self._update_activity_visibility()
        self._poll_batch_processes()
        self.status_label.setText("Batch transcription cancelled.")

    def _on_viewer_transcribe_requested(self, audio_path):
        session = self._current_session
        if session and session.get("directory"):
            transcript_file = Path(session["directory"]) / "transcript.json"
            if transcript_file.is_file():
                name = session.get("name") or Path(session["directory"]).name
                reply = QMessageBox.question(
                    self,
                    "Overwrite Existing Transcription?",
                    f"The recording '{name}' already has a transcription.\n\n"
                    "Transcribing it again will overwrite the existing transcript.\n\n"
                    "Do you want to continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        self._start_transcription(audio_path, session=session)

    def _start_transcription(self, audio_path, session=None):
        if self._closing:
            return
        # Bind the session at start time — completion handlers must not read
        # self._current_session, which the user may have switched meanwhile.
        if session is None:
            session = self._current_session

        if self._transcription_busy():
            if not any(p[0] == audio_path for p in self._pending_transcriptions):
                self._pending_transcriptions.append((audio_path, session))
                self.status_label.setText(
                    "Transcription queued — another recording is still processing."
                )
            return

        model_size = self.config.get("transcription", "model_size")
        language = self.config.get("transcription", "language")
        device = self.config.get("transcription", "device")

        # Read the viewer's checkbox, not the config, so the choice in force
        # is the one visible next to the button that started this job. Bound
        # onto the worker for the same reason the session is: the user may
        # toggle it again while this job runs.
        diarize = self.transcript_viewer.diarization_enabled()

        # With separate mic and system tracks on disk, transcribe each one
        # instead of the mix: Whisper never sees the doubled copy of remote
        # speech that bleed puts into combined_audio.wav, and the You/Remote
        # labels come from which file a segment was read out of.
        tracks = dual_track_plan(
            session, diarize, self.config.get("diarization", "hf_token"),
        )

        self._current_transcription_percent = None
        self._transcription_worker = TranscriptionWorker(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            device=device,
            tracks=tracks,
            # Half the cores exist to protect the live capture callback;
            # with nothing recording that cap only slows the job down.
            full_cpu=self.recorder.state == RecordingState.IDLE,
        )
        self._transcription_worker.session = session
        self._transcription_worker.diarize = diarize
        self._transcription_worker.progress.connect(self._on_transcription_progress)
        self._transcription_worker.progress_percent.connect(self.transcript_viewer.set_progress_percent)
        self._transcription_worker.progress_percent.connect(self._on_transcription_percent)
        self._transcription_worker.finished.connect(self._on_transcription_finished)
        self._transcription_worker.error.connect(self._on_transcription_error)
        self._transcription_worker.cancelled.connect(self._on_transcription_cancelled)
        self._transcription_worker.start(QThread.Priority.LowPriority)

        self.transcript_viewer.show_progress("Starting transcription...")
        self.status_label.setText("Transcribing...")
        self._update_activity_visibility()

    def _on_transcription_percent(self, pct):
        self._current_transcription_percent = pct
        self._update_activity_visibility()

    def _cancel_transcription(self):
        if self._transcription_worker and self._transcription_worker.isRunning():
            self._transcription_worker.cancel()
            self.transcript_viewer.show_progress("Cancelling...")

    def _on_transcription_cancelled(self):
        self.transcript_viewer.hide_progress()
        self.status_label.setText("Transcription cancelled.")
        self._process_pending_transcriptions()

    def _process_pending_transcriptions(self):
        self._update_activity_visibility()
        if self._closing or self._transcription_busy():
            return
        if not self._pending_transcriptions:
            return
        audio_path, session = self._pending_transcriptions.pop(0)
        self._start_transcription(audio_path, session)

    def _on_transcription_progress(self, message):
        self.transcript_viewer.show_progress(message)
        self.status_label.setText(message)

    def _on_transcription_finished(self, result):
        session = getattr(self._transcription_worker, "session", None)
        # The worker carries the choice made when the job started; the
        # checkbox may have been toggled since.
        diarization_enabled = getattr(self._transcription_worker, "diarize", False)
        hf_token = self.config.get("diarization", "hf_token")

        if getattr(self._transcription_worker, "tracks", None):
            # Per-track transcription already labelled every segment.
            dropped = getattr(self._transcription_worker, "bleed_dropped", 0)
            self._display_final_transcript(result, session)
            self._warn_speaker_bleed(dropped)
        elif diarization_enabled and hf_token:
            # Run full diarization with pyannote
            self._start_diarization(result, session)
        elif session:
            # Try simple channel-based diarization (off-thread — it reads
            # both full WAVs, which freezes the UI on long recordings).
            audio_files = session.get("audio_files", {})
            mic_path = audio_files.get("mic")
            sys_path = audio_files.get("system")

            if mic_path and sys_path:
                self._start_simple_diarization(result, session, mic_path, sys_path)
            else:
                self._display_final_transcript(result, session)
        else:
            self._display_final_transcript(result, session)

    def _start_simple_diarization(self, transcript_result, session, mic_path, sys_path):
        self._current_transcription_percent = None
        self._simple_diarize_worker = SimpleDiarizeWorker(
            mic_path, sys_path, transcript_result
        )
        self._simple_diarize_worker.session = session
        self._simple_diarize_worker.finished.connect(self._on_simple_diarization_finished)
        self._simple_diarize_worker.error.connect(self._on_simple_diarization_error)
        self._simple_diarize_worker.start(QThread.Priority.LowPriority)
        self.transcript_viewer.show_progress("Labeling speakers...")
        self._update_activity_visibility()

    def _on_simple_diarization_finished(self, result):
        session = getattr(self._simple_diarize_worker, "session", None)
        self._display_final_transcript(result, session)

    def _on_simple_diarization_error(self, error_msg):
        # Labeling is best-effort — show the unlabeled transcript.
        worker = self._simple_diarize_worker
        self.status_label.setText(error_msg)
        # worker is only None if _simple_diarize_worker were reset elsewhere,
        # which nothing in this file does today — this branch is unreachable
        # in practice. If that ever changes, the else must still reach
        # _process_pending_transcriptions() (via _display_final_transcript),
        # or the activity widget can get stuck showing "transcribing".
        if worker is not None:
            self._display_final_transcript(
                worker.transcript_result, getattr(worker, "session", None)
            )

    def _sync_diarization_controls(self):
        """Push the saved diarization preference into the transcript viewer.

        The checkbox is the live source of truth for new jobs; this keeps it
        agreeing with Settings, in both directions (see _on_diarize_toggled).
        """
        self.transcript_viewer.set_diarization_available(
            bool(self.config.get("diarization", "hf_token"))
        )
        self.transcript_viewer.set_diarization_enabled(
            self.config.get("diarization", "enabled")
        )

    def _on_diarize_toggled(self, enabled):
        self.config.set("diarization", "enabled", enabled)
        self._update_preflight()

    def _update_preflight(self):
        """Recompute the capture bar's pre-flight verdict from real state.

        Triggered on device change, mismatch recompute, capture-mode change,
        model/settings change, and once a second while idle (see
        docs/superpowers/specs for the capture-bar redesign) — the checks
        and the verdict block were previously hardcoded placeholders.
        """
        has_mic = self.source_selector.get_selected_mic() is not None
        mic_name = self.source_selector.get_selected_mic_name() or "No microphone"
        mic_peak_db = self._mic_level_tracker.peak_db_over_window()
        mic_check = preflight_status.compute_mic_check(
            has_mic, self.source_selector.mic_mismatch,
            mic_peak_db=mic_peak_db, mic_name=mic_name,
        )

        if self.source_selector.is_per_app_mode():
            has_source = bool(self.source_selector.get_selected_app_pids())
        else:
            has_source = self.source_selector.get_selected_loopback() is not None
        call_check = preflight_status.compute_call_check(
            has_source,
            self.source_selector.is_conferencing_blocked(),
            self.source_selector.output_mismatch,
        )

        diarization_enabled = self.transcript_viewer.diarization_enabled()
        hf_token_present = bool(self.config.get("diarization", "hf_token"))
        model_check = preflight_status.compute_transcription_check(
            diarization_enabled, hf_token_present
        )

        verdict, title, subtitle = preflight_status.compute_verdict(
            mic_check, call_check, model_check
        )
        self.recording_controls.preflight.set_verdict(verdict, title, subtitle)
        compact_strip = getattr(self, "compact_strip", None)
        if compact_strip is not None:
            compact_strip.set_subtitle(subtitle)

        call_name = self.source_selector.get_selected_source_name() or "No source"
        self.recording_controls.set_capturing(
            mic_name, call_name, mic_state=mic_check[0], call_state=call_check[0]
        )

    def _on_diarize_requested(self):
        """Run diarization on the transcript already on screen."""
        if self._transcript is None or self._current_session is None:
            return
        if self._transcription_busy():
            self.status_label.setText(
                "Busy — speaker identification will have to wait for the "
                "current job to finish."
            )
            return
        self._start_diarization(self._transcript, self._current_session)

    def _start_diarization(self, transcript_result, session):
        if self._diarization_worker and self._diarization_worker.isRunning():
            # Shouldn't happen with the serial queue — display rather than drop.
            self._display_final_transcript(transcript_result, session)
            return

        audio_files = session.get("audio_files", {}) if session else {}
        audio_path = audio_files.get("combined") or audio_files.get("system") or audio_files.get("mic")

        if not audio_path:
            self._display_final_transcript(transcript_result, session)
            return

        hf_token = self.config.get("diarization", "hf_token")
        min_speakers = self.config.get("diarization", "min_speakers")
        max_speakers = self.config.get("diarization", "max_speakers")

        self._current_transcription_percent = None
        self._diarization_worker = DiarizationWorker(
            audio_path=audio_path,
            transcript_result=transcript_result,
            hf_token=hf_token,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            full_cpu=self.recorder.state == RecordingState.IDLE,
        )
        self._diarization_worker.session = session
        self._diarization_worker.progress.connect(self._on_transcription_progress)
        self._diarization_worker.finished.connect(self._on_diarization_finished)
        self._diarization_worker.error.connect(self._on_diarization_error)
        self._diarization_worker.start(QThread.Priority.LowPriority)

        self.transcript_viewer.show_progress("Running speaker diarization...")
        self._update_activity_visibility()

    def _on_diarization_finished(self, result):
        session = getattr(self._diarization_worker, "session", None)
        self._display_final_transcript(result, session)

    def _on_diarization_error(self, error_msg):
        # Transcription itself succeeded — render it without speaker labels.
        # _diarization_worker is only None if it were reset elsewhere, which
        # nothing in this file does today — this else is unreachable in
        # practice. If that ever changes, it must still reach
        # _process_pending_transcriptions() (e.g. via _display_final_transcript),
        # or the activity widget can get stuck showing "transcribing".
        if self._diarization_worker is not None:
            self._display_final_transcript(
                self._diarization_worker.transcript_result,
                getattr(self._diarization_worker, "session", None),
            )
        else:
            self.transcript_viewer.hide_progress()
        self.status_label.setText("Diarization failed - showing transcript without speakers")
        if self._is_hidden_to_tray():
            self._flag_error_notification()
        else:
            QMessageBox.warning(self, "Diarization Error", error_msg)

    def _is_current_session(self, session):
        if session is None or session is self._current_session:
            return True
        if not self._current_session:
            return False
        return session.get("directory") == self._current_session.get("directory")

    def _write_transcript_for_session(self, result, session):
        """Persist a transcript for a session that is no longer displayed."""
        if not session_io.write_transcript(session, result):
            if session and session.get("directory"):
                self.status_label.setText("Failed to save transcript.")

    def _export_transcript(self, session=None):
        """Best-effort LLM-readable Markdown export for a session, reading
        everything fresh from disk. Deliberately does not touch
        self.transcript_viewer / self.notes_panel — the caller in
        _on_recording_selected runs this for a session that is no longer
        the one those widgets currently display."""
        session = session if session is not None else self._current_session
        session_io.export_session_markdown(session)

    def _load_calendar_event(self, session):
        """Load calendar_event.json for a session, if present.

        Returns (calendar_event: dict|None, attendees: list[str]). Shared by
        _display_final_transcript (just-finished-transcribing path) and
        _on_recording_selected (browse-to-past-recording path) so both show
        a previously saved calendar tag, not just the former.
        """
        return session_io.load_calendar_event(session)

    def _display_final_transcript(self, result, session=None):
        result.merge_adjacent_same_speaker()
        self._compact_strip_done = True
        self._update_compact_strip_state()

        if session is None:
            session = self._current_session

        # A recording that just got transcribed here no longer needs a
        # scheduled batch run — without this, a recording queued before the
        # user manually transcribed it would sit tagged forever (or get
        # needlessly re-transcribed by the next batch run).
        if session and session.get("directory"):
            batch_queue.clear(session["directory"])

        if not self._is_current_session(session):
            # Finished after the user switched recordings: save to the
            # recording's own directory, leave the displayed UI alone.
            self._write_transcript_for_session(result, session)
            name = session.get("name") or Path(session.get("directory", "")).name
            self.status_label.setText(f"Transcription of '{name}' complete.")
            self.recordings_list.refresh()
            if self._is_hidden_to_tray():
                self._flag_success_notification()
            self._process_pending_transcriptions()
            return

        self.transcript_viewer.hide_progress()

        # Load speaker names if available
        speaker_names = {}
        if self._current_session:
            names_path = Path(self._current_session["directory"]) / "speaker_names.json"
            if names_path.exists():
                try:
                    with open(names_path, "r", encoding="utf-8") as f:
                        speaker_names = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

        if self.config.get("general", "replace_you_with_name") and "You" not in speaker_names:
            if any(getattr(s, "speaker", "") == "You" for s in result.segments):
                user_name = get_current_user_name(self.config)
                if user_name and user_name.strip() and user_name.strip().lower() != "you":
                    speaker_names["You"] = user_name.strip()
                    if self._current_session:
                        names_path = Path(self._current_session["directory"]) / "speaker_names.json"
                        try:
                            atomic_write_json(names_path, speaker_names, indent=2, ensure_ascii=False)
                        except OSError:
                            pass

        calendar_event, calendar_attendees = self._load_calendar_event(self._current_session)

        self.transcript_viewer.display_transcript(
            result, speaker_names=speaker_names, attendees=calendar_attendees
        )
        self.status_label.setText("Transcription complete.")
        if self._is_hidden_to_tray():
            self._flag_success_notification()

        # Update recording header with speaker count
        if self._current_session:
            self.recording_header.set_recording(
                self._current_session,
                speaker_count=self.transcript_viewer.get_speaker_count(),
                calendar_event=calendar_event,
                model_size=result.model_size,
                transcribe_seconds=result.transcribe_seconds,
            )

        # Save transcript
        self._save_transcript()

        # Auto-summarize if AI provider configured
        self._transcript = result
        self.summary_panel.set_ready()
        self.action_items_panel.set_ready()
        self.inspector.set_ai_configured(self._ai_provider_configured())
        self._maybe_auto_summarize()

        # Update chat panel context
        self._update_chat_context()

        self._process_pending_transcriptions()

    def _on_transcription_error(self, error_msg, allow_retry=True):
        self._transcription_busy = False
        self.recording_controls.set_state(RecordingState.IDLE)
        # Check if the active recording in the viewer is the one that failed.
        # Read the worker's own bound session, not self._current_session — the
        # user may have switched recordings while the job ran (see
        # .claude/rules/transcription-pipeline.md "Session binding").
        from app.ui.notification_region import PRIORITY_BLOCKING_ERROR
        session = getattr(self._transcription_worker, "session", None)
        if self._is_current_session(session):
            # Revert UI block
            self.transcript_viewer.show_empty_state(True)
            self.inspector.set_empty_state(True)
            
            self.notification_region.enqueue(
                priority=PRIORITY_BLOCKING_ERROR,
                text=f"Transcription Failed: {error_msg}",
                action_text="Retry" if allow_retry else None,
                action_callback=self._on_transcribe_selected if allow_retry else None
            )
        else:
            self.notification_region.enqueue(
                priority=PRIORITY_BLOCKING_ERROR,
                text=f"Background transcription failed: {error_msg}",
                action_text="Retry" if allow_retry else None,
                action_callback=self._on_transcribe_selected if allow_retry else None
            )
        self.recordings_list.refresh_status()
        self._process_pending_transcriptions()

    def _on_recording_about_to_delete(self, directory):
        """Release any file handles on the session about to be deleted.

        Runs BEFORE rmtree so SegmentPlayer's cached audio data is cleared
        and any active playback is stopped. Without this, a Windows file
        lock on the WAV can leave the session folder partially removed.
        """
        if self._current_session and self._current_session.get("directory") == directory:
            # Clearing the audio path stops the player and clears its cache.
            self.transcript_viewer.set_audio_path(None)

    def _on_recording_deleted(self, directory):
        """Clear UI if the deleted recording was currently loaded."""
        if self._current_session and self._current_session.get("directory") == directory:
            self._current_session = None
            self._transcript = None
            self.transcript_viewer.clear(nothing_selected=True)
            self.recording_header.clear()
            self.summary_panel.clear()
            self.action_items_panel.clear()
            self.inspector.set_empty_state(True)
            self.status_label.setText("Recording deleted.")

    def _on_recording_files_changed(self, directory):
        """Clear UI if a partial delete (recordings-only or transcriptions-
        only) touched the currently loaded session.

        The session itself survives a partial delete, but its displayed
        transcript/audio may now reference a file that's gone — clearing
        and letting the user reselect avoids showing stale state without
        needing separate audio-only/transcript-only refresh logic.
        """
        if self._current_session and self._current_session.get("directory") == directory:
            self._current_session = None
            self._transcript = None
            self.transcript_viewer.clear(nothing_selected=True)
            self.recording_header.clear()
            self.summary_panel.clear()
            self.action_items_panel.clear()
            self.inspector.set_empty_state(True)
            self.status_label.setText("Recording updated.")

    def _open_last_recording(self):
        """The "Open the last one" button in the transcript column's empty
        state — loads the newest recording, same as double-clicking it."""
        metadata = self.recordings_list.most_recent_recording()
        if metadata is not None:
            self.recordings_list.recording_selected.emit(metadata)

    def _on_recording_selected(self, metadata):
        """Load a past recording for viewing/transcription."""
        # Clear any stale calendar-suggestion banner from the previously
        # displayed recording — see _on_recording_finished for why.
        self.calendar_banner.hide_and_clear()
        self._calendar_banner_session = None
        # Suggestions belong to the recording they were looked up for; a
        # leftover one must not tag the recording being opened now.
        self._rename_candidate_events = []

        previous_session = self._current_session
        self._current_session = metadata

        # Clear previous state before loading
        self.transcript_viewer.clear()
        self.summary_panel.clear()
        self.action_items_panel.clear()
        self._transcript = None
        self.inspector.set_empty_state(False)

        audio_files = metadata.get("audio_files", {})
        audio_path = audio_files.get("combined") or audio_files.get("system") or audio_files.get("mic")
        if audio_path and not Path(audio_path).exists():
            session_dir = Path(metadata.get("directory", ""))
            found_audio = None
            if session_dir.exists():
                for pat in ("combined_audio.wav", "combined_audio.mp3", "system_audio.wav", "mic_audio.wav", "*.wav", "*.mp3", "*.m4a"):
                    matches = list(session_dir.glob(pat))
                    if matches:
                        found_audio = str(matches[0])
                        break
            audio_path = found_audio
        self.transcript_viewer.set_audio_path(audio_path)

        # Load speaker names
        speaker_names = {}
        names_path = Path(metadata["directory"]) / "speaker_names.json"
        if names_path.exists():
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    speaker_names = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        if self.config.get("general", "replace_you_with_name") and "You" not in speaker_names:
            user_name = get_current_user_name(self.config)
            if user_name and user_name.strip() and user_name.strip().lower() != "you":
                speaker_names["You"] = user_name.strip()

        # Load a previously saved calendar tag, if any — same lookup used by
        # the just-finished-transcribing path, so browsing to an
        # already-tagged recording shows its calendar line and attendee
        # dropdowns too.
        calendar_event, calendar_attendees = self._load_calendar_event(metadata)

        # Load existing transcript if available
        transcript_path = Path(metadata["directory"]) / "transcript.json"
        loaded_model_size = ""
        loaded_transcribe_seconds = 0.0
        if transcript_path.exists():
            try:
                self.transcript_viewer.show_loading("Loading transcript...")
                QApplication.processEvents()
                with open(transcript_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                from app.transcription.transcriber import TranscriptSegment
                result = TranscriptResult(
                    segments=[TranscriptSegment.from_dict(s) for s in data["segments"]],
                    language=data.get("language", ""),
                    duration=data.get("duration", 0),
                    model_size=data.get("model_size", ""),
                    transcribe_seconds=data.get("transcribe_seconds", 0.0),
                )
                self.transcript_viewer.display_transcript(
                    result, speaker_names=speaker_names, attendees=calendar_attendees
                )
                self._transcript = result
                loaded_model_size = result.model_size
                loaded_transcribe_seconds = result.transcribe_seconds
            except Exception as e:
                print(f"[MainWindow] Failed to load transcript: {e}")

        # Update recording header
        self.recording_header.set_recording(
            metadata,
            speaker_count=self.transcript_viewer.get_speaker_count(),
            calendar_event=calendar_event,
            model_size=loaded_model_size,
            transcribe_seconds=loaded_transcribe_seconds,
        )

        # Persist any edits to the previously loaded recording's notes
        # before the editor is repointed, then load this recording's notes.
        self.notes_panel.save_notes()
        self._export_transcript(previous_session)
        self.notes_panel.set_session_dir(metadata["directory"])

        # Load saved summary and action items
        session_dir = Path(metadata["directory"])
        summary_path = session_dir / "summary.md"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    self.summary_panel.set_summary(f.read())
            except OSError:
                pass

        actions_path = session_dir / "action_items.json"
        if actions_path.exists():
            try:
                with open(actions_path, "r", encoding="utf-8") as f:
                    self.action_items_panel.set_items(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass

        # Show generate buttons if transcript loaded but no summary/actions yet
        if hasattr(self, '_transcript') and self._transcript is not None:
            self.summary_panel.set_ready()
            self.action_items_panel.set_ready()
            self.inspector.set_ai_configured(self._ai_provider_configured())

        # Update chat panel context for loaded recording
        self.chat_panel.set_session_dir(metadata["directory"])
        try:
            from app.ai.provider_factory import create_provider
            ai_config = self.config.data.get("ai", {})
            provider = create_provider(ai_config)
            self.chat_panel.set_provider(provider)
        except Exception:
            self.chat_panel.set_provider(None)

        if hasattr(self, '_transcript') and self._transcript is not None:
            self._update_chat_context()

    def _on_search_result_selected(self, recording_id, timestamp):
        """Load a recording from a search result."""
        recordings_dir = Path(self.config.get("output", "directory"))
        rec_dir = recordings_dir / recording_id
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                self._on_recording_selected(metadata)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[MainWindow] Failed to load search result: {e}")

    def _save_transcript(self):
        """Save current transcript to session directory."""
        if not self._current_session or not self.transcript_viewer._transcript:
            return
        result = self.transcript_viewer._transcript
        names = self.transcript_viewer._speaker_names

        try:
            transcript_path = Path(self._current_session["directory"]) / "transcript.json"
            atomic_write_json(transcript_path,
                              result.to_dict(speaker_names=names),
                              indent=2, ensure_ascii=False)
            txt_path = Path(self._current_session["directory"]) / "transcript.txt"
            atomic_write_text(txt_path, result.to_text(speaker_names=names))
        except OSError as e:
            self.status_label.setText(f"Failed to save transcript: {e}")
            return

        self._export_transcript()
        self.recordings_list.refresh()

    def _save_speaker_names(self, names):
        """Save speaker names to session directory."""
        if not self._current_session:
            return
        names_path = Path(self._current_session["directory"]) / "speaker_names.json"
        try:
            atomic_write_json(names_path, names, indent=2, ensure_ascii=False)
        except OSError as e:
            self.status_label.setText(f"Failed to save speaker names: {e}")

        # Also re-save transcript with updated names
        self._save_transcript()

    def _on_recording_renamed(self, new_name):
        """Handle recording rename from RecordingHeader."""
        if not self._current_session:
            return
        self._current_session["name"] = new_name

        # Update metadata.json
        meta_path = Path(self._current_session["directory"]) / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                metadata["name"] = new_name
                atomic_write_json(meta_path, metadata, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Failed to save recording name: {e}")

        calendar_event, _ = self._load_calendar_event(self._current_session)
        self.recording_header.set_recording(
            self._current_session,
            speaker_count=self.transcript_viewer.get_speaker_count(),
            calendar_event=calendar_event,
        )

        self.recordings_list.refresh()

        # Check for auto-tagging or retag suggestion based on matching previous names
        self._maybe_autotag_recording(self._current_session, new_name)

    def _maybe_autotag_recording(self, session, name):
        """Auto-tag recording if another recording with the same name has tags.

        If the recording has no tags: automatically copies the matching tags.
        If the recording already has tags and they differ from the matching tags:
        prompts the user suggesting to retag/update tags to match.
        """
        if not session or not name:
            return
        if not self.config.get("general", "auto_tag_by_name"):
            return

        session_dir = session.get("directory")
        if not session_dir:
            return

        recordings_dir = self.config.get("output", "directory")
        matching_tags = tag_manager.find_tags_for_recording_name(
            name, recordings_dir, exclude_dir=session_dir
        )
        if not matching_tags:
            return

        current_tags = tag_manager.get_recording_tags(session)

        if not current_tags:
            # Auto-apply tags
            updated = tag_manager.set_recording_tags(session_dir, matching_tags)
            session["tags"] = updated
            self.recording_header.refresh_tags()
            self.recordings_list.refresh()
            self.status_label.setText(
                f"Auto-tagged as '{', '.join(matching_tags)}' (matched previous '{name}')."
            )
        elif set(current_tags) != set(matching_tags):
            # Already tagged, but differs: suggest retagging
            matching_str = ", ".join(matching_tags)
            current_str = ", ".join(current_tags)
            reply = QMessageBox.question(
                self,
                "Update Tags to Match?",
                f"Previous recordings named \"{name}\" are tagged with:\n  [{matching_str}]\n\n"
                f"This recording currently has tags:\n  [{current_str}]\n\n"
                f"Would you like to update this recording's tags to match?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                updated = tag_manager.set_recording_tags(session_dir, matching_tags)
                session["tags"] = updated
                self.recording_header.refresh_tags()
                self.recordings_list.refresh()
                self.status_label.setText(
                    f"Tags updated to '{matching_str}'."
                )

    def _on_error(self, error_msg):
        from app.ui.notification_region import PRIORITY_BLOCKING_ERROR
        if self._is_hidden_to_tray():
            self._flag_error_notification()
        else:
            self.notification_region.enqueue(
                priority=PRIORITY_BLOCKING_ERROR,
                text=error_msg,
                action_text="View log",
                action_callback=self._open_log_file,
                ttl=0
            )

    def _on_capture_status(self, status):
        """Render initial 'K of N apps capturing' feedback after Record start."""
        total = status.get("total", 0)
        active = status.get("active", 0)
        failures = status.get("failures", {})
        self._current_capture_failures = dict(failures)
        self.source_selector.mark_capture_failures(self._current_capture_failures)
        if total > 0 and active < total and active > 0:
            self.status_label.setText(
                f"Recording — capturing {active} of {total} apps"
            )

    def _on_pid_lost(self, pid, error):
        """One PID died during recording. Update the warning label + status bar."""
        logger.warning("PID lost during recording: pid=%s error=%s", pid, error)
        if not hasattr(self, "_current_capture_failures"):
            self._current_capture_failures = {}
        self._current_capture_failures[pid] = error
        self.source_selector.mark_capture_failures(self._current_capture_failures)
        active = len(self.recorder._capture.system_stream.active_pids) \
            if self.recorder._capture and self.recorder._capture.system_stream else 0
        total = active + len(self._current_capture_failures)
        if active > 0:
            self.status_label.setText(
                f"Recording — capturing {active} of {total} apps"
            )

    def _on_capture_lost(self):
        """All selected apps became unavailable. Stop and save what we have."""
        if self.recorder.state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            return
        logger.warning("Auto-stop: capture_lost (all per-app streams unavailable)")
        self.status_label.setText(
            "Capture ended: all selected apps became unavailable"
        )
        self.recorder.stop_recording()

    def _restore_from_tray(self):
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        try:
            import ctypes
            hwnd = int(self.winId())
            if hwnd:
                SW_RESTORE = 9
                ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self._success_pending = False
        self._error_pending = False
        self.tray.set_overlay(None)
        self._update_activity_visibility()

    def _quit_from_tray(self):
        self.close()

    def _open_settings(self, initial_tab=None):
        dialog = SettingsDialog(self.config, self, initial_tab=initial_tab)
        if dialog.exec():
            # Update recordings list with potentially new directory
            self.recordings_list.recordings_dir = Path(self.config.get("output", "directory"))
            self.recordings_list.refresh()
            # Refresh devices in case hidden devices changed
            self.source_selector.refresh_devices()
            # Update mic2 visibility in case mic_count changed
            self.source_selector.update_mic_count(self.config.get("audio", "mic_count"))
            # The dialog owns the same diarization flag and token the
            # transcript viewer's checkbox mirrors.
            self._sync_diarization_controls()
            self._update_preflight()
            self.inspector.set_ai_configured(self._ai_provider_configured())

    def _open_recordings_folder(self):
        import os
        recordings_dir = self.config.get("output", "directory")
        os.makedirs(recordings_dir, exist_ok=True)
        os.startfile(recordings_dir)

    def _show_system_status(self):
        dialog = SystemStatusDialog(self.config, self)
        dialog.exec()

    def _show_diarization_setup(self):
        from app.ui.diarization_setup import DiarizationSetupWizard
        wizard = DiarizationSetupWizard(self.config, self)
        wizard.exec()

    def _check_startup_status(self):
        # Show diarization setup wizard first if no HF token configured
        hf_token = self.config.get("diarization", "hf_token")
        if not hf_token:
            self._show_diarization_setup()

        if SystemStatusDialog.should_show_on_startup(self.config):
            QTimer.singleShot(300, self._show_system_status)

        # One-time offer to add a Start Menu shortcut (correct taskbar icon).
        # Delayed so it lands after any setup/status dialogs above.
        QTimer.singleShot(1500, self._maybe_offer_start_menu_shortcut)

    def _maybe_offer_start_menu_shortcut(self):
        """Offer to add a Start Menu shortcut once, on startup.

        The shortcut targets the venv interpreter and carries TalkTrack's icon +
        AppUserModelID, so Windows shows the correct taskbar icon. We record the
        choice (yes or no) so the user is asked at most once.
        """
        if self.config.get("general", "start_menu_offer_done"):
            return
        if self._is_hidden_to_tray():
            return  # don't pop a modal the user can't see; ask next visible launch
        try:
            from app.utils.start_menu import needs_shortcut, create_shortcut
            app_dir = Path(__file__).parent.parent

            if not needs_shortcut(app_dir):
                self.config.set("general", "start_menu_offer_done", True)
                return

            reply = QMessageBox.question(
                self,
                "Add TalkTrack to Start Menu",
                "Add a TalkTrack shortcut to your Start Menu?\n\n"
                "This gives the correct taskbar icon. Once it's running you can "
                "right-click the taskbar icon and choose Pin to taskbar.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            # Record the choice either way so we don't ask again.
            self.config.set("general", "start_menu_offer_done", True)

            if reply == QMessageBox.StandardButton.Yes:
                create_shortcut(app_dir)
                QMessageBox.information(
                    self, "Start Menu Shortcut",
                    "Shortcut created. The taskbar icon updates next time you "
                    "launch from the Start Menu."
                )
        except Exception as e:
            import logging
            logging.getLogger("talktrack").warning(
                "Start Menu shortcut offer failed: %s", e
            )

    def _open_log_file(self):
        import os
        from main import get_log_file
        log_path = get_log_file()
        if log_path.exists():
            os.startfile(str(log_path))
        else:
            QMessageBox.information(self, "Log File", "No log file found yet.")

    def _open_help(self):
        webbrowser.open("https://github.com/martinasencio-gm/TalkTrack#readme")

    def _open_contact(self):
        webbrowser.open("https://github.com/martinasencio-gm/TalkTrack/discussions")

    def _report_bug(self):
        from main import build_bug_report_url
        webbrowser.open(build_bug_report_url())

    def _show_about(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def _update_chat_context(self):
        if self._transcript:
            speaker_names = getattr(self, '_speaker_names', {})
            if not speaker_names:
                speaker_names = self.transcript_viewer._speaker_names
            context = build_chat_context(self._transcript.segments, speaker_names)
            self.chat_panel.set_context(context)

        if self._current_session:
            self.chat_panel.set_session_dir(self._current_session["directory"])

        # Set provider
        try:
            from app.ai.provider_factory import create_provider
            ai_config = self.config.data.get("ai", {})
            provider = create_provider(ai_config)
            self.chat_panel.set_provider(provider)
        except Exception:
            self.chat_panel.set_provider(None)

    @staticmethod
    def _is_generic_meeting_name(name):
        if not name:
            return True
        return name.lower().strip() in ("ms-teams", "teams", "zoom", "webex", "microsoft teams")

    def _maybe_tag_detected_meeting(self, session, detected_name):
        """Tag a recorded session with detected meeting/contact name when no Outlook calendar event exists."""
        if not session or not detected_name or self._is_generic_meeting_name(detected_name):
            return
        session_dir = session.get("directory")
        if session_dir and (Path(session_dir) / "calendar_event.json").exists():
            return
        from datetime import datetime
        started = session.get("started_at")
        stopped = session.get("stopped_at")
        try:
            started_dt = datetime.fromisoformat(started) if started else datetime.now()
            stopped_dt = datetime.fromisoformat(stopped) if stopped else datetime.now()
        except (ValueError, TypeError):
            started_dt = datetime.now()
            stopped_dt = datetime.now()

        current_user = get_current_user_name(self.config)
        attendees = [detected_name]
        if current_user and current_user.lower() != detected_name.lower():
            attendees.append(current_user)

        event = {
            "subject": detected_name,
            "start": started_dt,
            "end": stopped_dt,
            "organizer": current_user or "",
            "attendees": attendees,
        }
        if self._is_current_session(session):
            event_to_save = self._apply_calendar_event(event)
            self._maybe_suggest_rename(session, event_to_save)
            self._export_transcript()
        else:
            event_to_save = dict(event)
            event_to_save["start"] = started_dt.isoformat()
            event_to_save["end"] = stopped_dt.isoformat()
            atomic_write_json(Path(session["directory"]) / "calendar_event.json", event_to_save, indent=2)

    def _maybe_lookup_calendar(self, session, detected_name=None):
        """Kick off an off-thread Outlook calendar lookup for this session,
        if the feature is enabled. Best-effort — no-op on any failure,
        never surfaces an error to the user (see outlook_calendar.py)."""
        if not self.config.get("calendar", "enabled"):
            self._maybe_tag_detected_meeting(session, detected_name)
            return
        if session is None:
            return
        if session.get("calendar_prompt_dismissed"):
            return
        session_dir = session.get("directory")
        if session_dir and (Path(session_dir) / "calendar_event.json").exists():
            return  # already tagged
        started = session.get("started_at")
        stopped = session.get("stopped_at")
        if not started or not stopped:
            self._maybe_tag_detected_meeting(session, detected_name)
            return
        from datetime import datetime
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            self._maybe_tag_detected_meeting(session, detected_name)
            return

        self._dispatch_calendar_lookup(session, started_dt, stopped_dt, detected_name=detected_name)

    def _on_rename_started(self):
        """Fetch calendar matches to offer as rename suggestions.

        Runs even when the recording is already tagged: renaming is also
        how the user retags a recording that matched the wrong meeting.
        """
        session = self._current_session
        if session is None or not self.config.get("calendar", "enabled"):
            return
        started, stopped = session.get("started_at"), session.get("stopped_at")
        if not started or not stopped:
            return
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            return
        self._dispatch_calendar_lookup(session, started_dt, stopped_dt, for_rename=True)

    def _on_recording_renamed_with_tag(self, new_name):
        """Rename, and tag too when the name came from a suggested meeting."""
        event = match_event_by_subject(new_name, self._rename_candidate_events)
        self._on_recording_renamed(new_name)
        if event is not None and self._current_session is not None:
            self._apply_calendar_event(event)
            self._export_transcript()
        self._rename_candidate_events = []

    def _dispatch_calendar_lookup(self, session, started_dt, stopped_dt, manual=False,
                                  for_rename=False, detected_name=None):
        worker = CalendarLookupWorker(started_dt, stopped_dt)
        worker.session = session
        worker.manual = manual
        worker.for_rename = for_rename
        worker.detected_name = detected_name
        worker.finished.connect(self._on_calendar_lookup_finished)
        self._calendar_lookup_workers.append(worker)
        worker.start()

    def _on_calendar_lookup_finished(self, events):
        # Calendar lookups are NOT serialized like transcription — two
        # recordings finishing back-to-back can have overlapping lookups in
        # flight. Read the emitting worker via sender(), never a single
        # instance attribute (which would get overwritten by a second
        # lookup and could be read after the first worker is GC'd).
        worker = self.sender()
        session = getattr(worker, "session", None) if worker else None
        manual = getattr(worker, "manual", False) if worker else False
        for_rename = getattr(worker, "for_rename", False) if worker else False
        detected_name = getattr(worker, "detected_name", None) if worker else None
        if worker in self._calendar_lookup_workers:
            self._calendar_lookup_workers.remove(worker)
        if session is None:
            return
        if for_rename:
            # Feeds the rename field's completer rather than the banner —
            # the user is already renaming, and a banner offering the same
            # meetings a second way would just compete with the editor.
            if self._is_current_session(session):
                self._rename_candidate_events = events
                self.recording_header.set_name_suggestions(
                    [e.get("subject", "") for e in events if e.get("subject")]
                )
            return
        if not events:
            # Only the manual "Change" lookup should report a no-match
            # status — the automatic post-recording lookup fires for every
            # untagged recording and would otherwise clobber transient
            # status text like "Transcribing...".
            if not manual:
                self._maybe_tag_detected_meeting(session, detected_name)
            if manual and self._is_current_session(session):
                self.status_label.setText("No other matching calendar events found.")
            return
        if not self._is_current_session(session):
            return  # user switched recordings — don't surface a stale banner
        # Unlike QMessageBox, the banner is a normal child widget embedded in
        # the window layout — no tray-hidden special-casing needed. Calling
        # show_matches() while the main window is hidden to tray just leaves
        # the banner visible-but-unseen until the window is next shown, same
        # as the recording header or transcript already sitting there.
        self._calendar_banner_session = session
        self.calendar_banner.show_matches(events)

    def _on_calendar_tag_requested(self, event):
        if not self._current_session:
            return
        # Defense in depth: the banner should already have been hidden by
        # _on_recording_selected/_on_recording_finished on any session
        # switch, but guard against writing a stale banner's event into the
        # wrong recording's directory.
        if self._calendar_banner_session is not None and not self._is_current_session(
            self._calendar_banner_session
        ):
            return
        event_to_save = self._apply_calendar_event(event)
        self._maybe_suggest_rename(self._current_session, event_to_save)
        self._export_transcript()

    def _apply_calendar_event(self, event):
        """Tag the displayed recording with this event and refresh the UI.

        Returns the serialized form written to disk. Datetimes are the only
        thing needing conversion — the banner and the rename suggestions
        both hand over events straight from the Outlook lookup.
        """
        session_dir = Path(self._current_session["directory"])
        event_to_save = dict(event)
        start_val = event.get("start")
        end_val = event.get("end")
        event_to_save["start"] = start_val.isoformat() if hasattr(start_val, "isoformat") else str(start_val or "")
        event_to_save["end"] = end_val.isoformat() if hasattr(end_val, "isoformat") else str(end_val or "")
        atomic_write_json(session_dir / "calendar_event.json", event_to_save, indent=2)
        self.recording_header.set_recording(
            self._current_session,
            speaker_count=self.transcript_viewer.get_speaker_count(),
            calendar_event=event_to_save,
        )
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)
        return event_to_save

    def _maybe_suggest_rename(self, session, event):
        """Offer to rename the recording to the calendar event's subject.
        Never overwrites a name the user already set — a recording counts
        as "already custom-named" the moment metadata["name"] is truthy,
        whether that happened via manual rename or an earlier accepted
        suggestion."""
        if session is None or session.get("name"):
            return
        subject = event.get("subject", "").strip()
        if not subject:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Recording?",
            "Rename this recording to match the calendar event?",
            text=subject,
        )
        if ok and new_name.strip():
            self._on_recording_renamed(new_name.strip())

    def _on_calendar_dismissed(self):
        if not self._current_session:
            return
        if self._calendar_banner_session is not None and not self._is_current_session(
            self._calendar_banner_session
        ):
            return
        self._current_session["calendar_prompt_dismissed"] = True
        session_dir = Path(self._current_session["directory"])
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            atomic_write_json(meta_path, self._current_session, indent=2)

    def _on_change_calendar_requested(self):
        session = self._current_session
        if session is None:
            return
        if not self.config.get("calendar", "enabled"):
            return
        started, stopped = session.get("started_at"), session.get("stopped_at")
        if not started or not stopped:
            return
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            return
        self.status_label.setText("Looking up calendar events...")
        self._calendar_banner_session = session
        self._dispatch_calendar_lookup(session, started_dt, stopped_dt, manual=True)

    def _on_header_tags_changed(self, tags):
        if self._current_session:
            self._current_session["tags"] = tags
        self.recordings_list.refresh()

    def _open_tag_dialog(self, session=None):
        """The header's "+ Tag" button and post-recording prompt — opens the
        standard "Tag this recording" dialog as used throughout the app."""
        target_session = session if session is not None else self._current_session
        if not target_session or not target_session.get("directory"):
            return
        from app.ui.tag_recording_dialog import TagRecordingDialog
        recordings_dir = self.config.get("output", "directory")
        dialog = TagRecordingDialog(target_session, recordings_dir, parent=self)
        dialog.tags_changed.connect(self._on_header_tags_changed)
        dialog.exec()
        self.recording_header.refresh_tags()

    def _on_banner_tags_updated(self, tags):
        if self._current_session:
            self._current_session["tags"] = tags
            self.recording_header.refresh_tags()
        self.recordings_list.refresh()

    def _on_banner_tags_dismissed(self):
        pass

    def _on_recordings_list_tags_changed(self, directory, tags):
        if self._current_session and self._current_session.get("directory") == directory:
            self._current_session["tags"] = tags
            self.recording_header.refresh_tags()

    def _open_manage_tags_dialog(self):
        from app.ui.tag_manager_dialog import TagManagerDialog
        recordings_dir = self.config.get("output", "directory")
        dlg = TagManagerDialog(recordings_dir=recordings_dir, parent=self)
        dlg.tags_changed.connect(self._on_global_tags_changed)
        dlg.exec()

    def _on_global_tags_changed(self):
        if self._current_session and self._current_session.get("directory"):
            self._current_session["tags"] = tag_manager.get_recording_tags(self._current_session["directory"])
            self.recording_header.refresh_tags()
        self.recordings_list.refresh()

    def _maybe_auto_summarize(self):
        if not self.config.get("general", "auto_transcribe"):
            return
        if not self.config.get("ai", "auto_summarize"):
            return
        if self.config.get("ai", "provider") == "none":
            return
        if not getattr(self, '_transcript', None):
            return
        self._run_summarize()

    def _regenerate_summary(self):
        if not getattr(self, '_transcript', None):
            return
        self._run_summarize()

    def _ai_provider_configured(self):
        return self.config.get("ai", "provider") != "none"

    def _run_summarize(self):
        from app.ai.summarizer import build_summary_prompt, build_action_items_prompt, parse_action_items
        from app.ai.provider_factory import create_provider
        from PyQt6.QtCore import QThread, pyqtSignal

        if self._summarize_worker is not None and self._summarize_worker.isRunning():
            return

        ai_config = self.config.data.get("ai", {})
        try:
            provider = create_provider(ai_config)
        except Exception as e:
            self.status_label.setText(f"AI provider error: {e}")
            return
        if provider is None:
            return

        self.summary_panel.set_loading()
        self.action_items_panel.set_loading()

        class SummarizeWorker(QThread):
            summary_ready = pyqtSignal(str)
            actions_ready = pyqtSignal(list)
            error = pyqtSignal(str)

            def __init__(self, provider, segments, speaker_names, notes="", instruction=""):
                super().__init__()
                self._provider = provider
                self._segments = segments
                self._names = speaker_names
                self._notes = notes
                self._instruction = instruction

            def run(self):
                try:
                    max_chars = self._provider.max_context_chars
                    summary_prompt = build_summary_prompt(
                        self._segments, self._names, self._notes, self._instruction,
                        max_transcript_chars=max_chars,
                    )
                    summary = self._provider.complete(summary_prompt)
                    self.summary_ready.emit(summary)

                    actions_prompt = build_action_items_prompt(
                        self._segments, self._names, self._notes, self._instruction,
                        max_transcript_chars=max_chars,
                    )
                    actions_response = self._provider.complete(actions_prompt)
                    actions = parse_action_items(actions_response)
                    self.actions_ready.emit(actions)
                except Exception as e:
                    self.error.emit(str(e))

        speaker_names = self.transcript_viewer._speaker_names
        notes = self.notes_panel.get_text()
        instruction = self.summary_panel.get_instruction()
        self._summarize_worker = SummarizeWorker(
            provider, self._transcript.segments, speaker_names, notes, instruction
        )
        self._summarize_worker.summary_ready.connect(self._on_summary_ready)
        self._summarize_worker.actions_ready.connect(self._on_actions_ready)
        self._summarize_worker.error.connect(self._on_summarize_error)
        self._summarize_worker.start()

    def _on_summarize_error(self, error_msg):
        self.status_label.setText(f"AI error: {error_msg}")
        self.summary_panel.set_error()
        self.action_items_panel.set_error()

    def _on_summary_ready(self, summary):
        self.summary_panel.set_summary(summary)
        if self._current_session:
            path = Path(self._current_session["directory"]) / "summary.md"
            try:
                atomic_write_text(path, summary)
            except OSError:
                self.status_label.setText("Failed to save summary.")
                return
            self._export_transcript()

    def _on_actions_ready(self, items):
        self.action_items_panel.set_items(items)
        self._on_action_items_changed(items)

    def _on_action_items_changed(self, items):
        if self._current_session:
            path = Path(self._current_session["directory"]) / "action_items.json"
            try:
                atomic_write_json(path, items, indent=2)
            except OSError:
                self.status_label.setText("Failed to save action items.")
                return
            self._export_transcript()

    def _should_hide_to_tray(self):
        """Whether the close button's "minimize instead" choice should fully
        hide to the tray rather than leave a normal taskbar-minimized window.

        The minimize button no longer consults this at all — it always
        performs an ordinary Windows minimize. The tray is a deliberate
        close-time destination, and only when nothing is in flight: while
        recording or transcribing the activity pill needs a minimized (not
        hidden) window to stand in for.
        """
        busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
        return (
            busy_state is None
            and bool(self.config.get("general", "close_to_tray"))
            and self.tray.is_supported()
        )

    def changeEvent(self, event):
        """Minimize is left alone — it always minimizes to the taskbar.

        The only window-state work here is on the way back up: a compact bar
        or pill that stands in for a minimized window is dismissed when the
        user restores from the taskbar, so they land on a clean full UI.
        A strip opened alongside the window from View > Show Compact Strip
        isn't a minimized form and stays put.
        """
        if event.type() == QEvent.Type.WindowStateChange:
            minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
            if not minimized and self._strip_is_minimized_form:
                self._strip_is_minimized_form = False
                self.compact_strip_action.setChecked(False)
            self._update_activity_visibility()
            if minimized:
                if self.recorder.state != RecordingState.IDLE:
                    if hasattr(self, "tray") and self.tray.is_supported():
                        self.tray.notify_meeting(
                            "Recording in Progress",
                            "TalkTrack is continuing to record in the background."
                        )
                elif self._calendar_banner_session is not None and self.calendar_banner.isVisible():
                    if hasattr(self, "tray") and self.tray.is_supported():
                        self.tray.notify_meeting(
                            "Save Confirmation Pending",
                            "TalkTrack is waiting for calendar tag / save confirmation in the background."
                        )
                elif self._transcription_busy():
                    if hasattr(self, "tray") and self.tray.is_supported():
                        self.tray.notify_meeting(
                            "Transcription in Progress",
                            "TalkTrack is finishing transcription in the background."
                        )
        super().changeEvent(event)

    def closeEvent(self, event):
        if not self._really_quit:
            outcome = self._confirm_exit()
            if outcome == "minimize":
                event.ignore()
                if self._should_hide_to_tray():
                    # showMinimized() would re-enter changeEvent synchronously
                    # to do this same hide — but showMinimized()'s own
                    # internal "ensure the widget is visible" follow-up runs
                    # immediately after and silently undoes that hide()
                    # before control even returns here (confirmed via
                    # diagnostic logging: the window ends up isVisible=True
                    # right after showMinimized() despite changeEvent's
                    # hide() having taken effect in between). Hide directly
                    # instead of going through showMinimized() at all.
                    self.hide()
                    # There's otherwise zero visible feedback that anything
                    # happened — the tray icon was already showing before
                    # this click. changeEvent's own hint is one-time-ever
                    # and may already be spent, so this explicit, occasional
                    # action gets its own reminder every time, independent
                    # of that gate.
                    self.tray.show_hint_balloon()
                else:
                    self.showMinimized()
                return
            if outcome != "quit":
                event.ignore()
                return
            self._really_quit = True

        # Blocks _start_transcription, so stopping the recorder below can't
        # auto-spawn a new worker while the event loop is exiting.
        self._closing = True
        self._pending_transcriptions.clear()

        if self._gain_save_timer.isActive():
            self._gain_save_timer.stop()
            self._flush_gain_to_config()
        if self._meeting_poll_timer.isActive():
            self._meeting_poll_timer.stop()
        if self._preflight_poll_timer.isActive():
            self._preflight_poll_timer.stop()
        self._com_poller.stop()
        self._activity_widget.close()
        if hasattr(self, "meeting_toast"):
            self.meeting_toast.close()
        if hasattr(self, "mic_monitor"):
            self.mic_monitor.stop()
        if hasattr(self, "mic_monitor_2"):
            self.mic_monitor_2.stop()
        if hasattr(self, "system_monitor"):
            self._stop_system_monitor()
        if self.recorder.state != RecordingState.IDLE:
            self.recorder.stop_recording()
        self._shutdown_workers()
        self.config.save()
        if hasattr(self, "tray"):
            self.tray.hide()
        event.accept()

    def _update_activity_visibility(self):
        self._update_compact_strip_state()
        busy = self._transcription_busy()
        job_name = None
        elapsed = None
        if busy:
            session = getattr(self._transcription_worker, "session", None)
            if session:
                from app.ui.recording_header import _display_name_from_metadata
                job_name = _display_name_from_metadata(session)
            start_time = getattr(self.transcript_viewer, "_progress_start_time", None)
            if start_time is not None:
                elapsed = time.monotonic() - start_time
        self.recording_controls.set_transcribing(
            busy, self._current_transcription_percent,
            name=job_name, elapsed_seconds=elapsed,
            queued=len(self._pending_transcriptions),
        )
        self.recordings_list.set_transcribing(transcribing_directories(
            [self._transcription_worker, self._diarization_worker,
             self._simple_diarize_worker],
            self._pending_transcriptions,
        ))
        busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
        # Compact/pill mode is now a genuinely minimized window, so without
        # the strip check both floating widgets would stack on screen while
        # recording — and the strip already renders the busy states itself.
        should_show = (
            busy_state is not None
            and (self.isMinimized() or self.isHidden())
            and not self.compact_strip.isVisible()
        )
        if should_show:
            elapsed = (
                int(self.recorder.get_elapsed_time())
                if busy_state in ("recording", "paused") else None
            )
            percent = (
                self._current_transcription_percent
                if busy_state == "transcribing" else None
            )
            if not self._activity_widget.isVisible():
                x, y = self._activity_widget_position()
                self._activity_widget.show_at(x, y)
            self._activity_widget.set_activity(busy_state, elapsed, percent)
        elif self._activity_widget.isVisible():
            self._activity_widget.hide()

    def _on_compact_strip_toggled(self, checked):
        if checked:
            x, y = self._compact_strip_position()
            self.compact_strip.move(x, y)
            self._update_compact_strip_state()
            self.compact_strip.show()
        else:
            self.compact_strip.hide()
            # Toggled off by hand (View menu) rather than by restoring the
            # window: the strip has stopped being this window's minimized
            # stand-in either way.
            self._strip_is_minimized_form = False
        self.config.set("ui", "compact_strip_visible", checked)

    def _current_presentation(self):
        if not self.compact_strip.isVisible() or not self._strip_is_minimized_form:
            return "full"
        return "pill" if self.compact_strip.variant() == "pill" else "compact_bar"

    def _advance_presentation(self):
        """Double-click anywhere in the chain: shrink one step and wrap.

        full -> compact_bar -> pill -> full, with ui.double_click_target
        choosing which of the two shrunken states a double-click from the
        full window lands on.
        """
        state = next_presentation(
            self._current_presentation(),
            self.config.get("ui", "double_click_target"),
        )
        if state == "full":
            self._switch_to_full_ui()
        else:
            self._switch_to_compact_bar(variant="pill" if state == "pill" else "full")

    def _switch_to_compact_bar(self, variant="full"):
        """Swap the full window for the floating compact strip.

        The window is *minimized*, not hidden, so it keeps its taskbar entry
        — the app can't be lost if the strip ends up off-screen or behind
        another window. Routed through the same checkable action the View
        menu uses, so both stay in sync and the choice persists.
        """
        self.compact_strip.set_variant(variant)
        self.compact_strip_action.setChecked(True)
        # After setChecked: _on_compact_strip_toggled clears the flag when it
        # runs for an unchecking, and setting it first would be undone by a
        # re-entrant toggle.
        self._strip_is_minimized_form = True
        self.showMinimized()

    def _switch_to_full_ui(self):
        """Swap back to the full window, dismissing the strip."""
        self._strip_is_minimized_form = False
        self.compact_strip_action.setChecked(False)
        self._restore_from_tray()

    def _compact_strip_position(self):
        saved = self.config.get("ui", "compact_strip_position")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            return saved[0], saved[1]
        screen = QApplication.primaryScreen()
        if screen is None:
            return 0, 0
        geo = screen.availableGeometry()
        return geo.center().x() - 350, geo.top() + 24

    def _on_compact_strip_moved(self, x, y):
        self.config.set("ui", "compact_strip_position", [x, y])

    def _on_compact_strip_variant_changed(self, variant):
        self.config.set("ui", "strip_variant", variant)

    def _update_compact_strip_state(self):
        from app.integrations.meeting_detector import IDLE as _MEETING_IDLE
        meeting_active = getattr(self, "_meeting_detector", None) is not None \
            and self._meeting_detector.state != _MEETING_IDLE
        state = resolve_compact_strip_state(
            self.recorder.state, self._mic_muted, self._transcription_busy(),
            self._compact_strip_done, meeting_active=meeting_active,
        )
        self.compact_strip.set_state(state)

    def _on_compact_strip_mic_level(self, audio_chunk):
        pct = int(db_to_fraction(compute_rms_db(audio_chunk)) * 100)
        self.compact_strip.update_meters(pct, self.compact_strip.sys_meter.value())

    def _on_compact_strip_system_level(self, audio_chunk):
        pct = int(db_to_fraction(compute_rms_db(audio_chunk)) * 100)
        self.compact_strip.update_meters(self.compact_strip.mic_meter.value(), pct)

    def _activity_widget_position(self):
        saved = self.config.get("ui", "activity_widget_position")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            return saved[0], saved[1]
        screen = QApplication.primaryScreen()
        if screen is None:
            return 0, 0
        geo = screen.availableGeometry()
        return geo.right() - 150, geo.top() + 20

    def _on_activity_widget_moved(self, x, y):
        self.config.set("ui", "activity_widget_position", [x, y])

    def _shutdown_workers(self):
        """Stop background QThreads before the event loop exits.

        A QThread destroyed while running aborts the process. Transcription
        cancels cooperatively; diarization/summarize/chat block in native or
        network code, so after a bounded wait terminate() is the last resort —
        risky in general, but the process is exiting anyway.
        """
        if hasattr(self, "_batch_monitor_timer") and self._batch_monitor_timer.isActive():
            self._batch_monitor_timer.stop()

        # Finalizing is writing the user's audio to disk. Interrupting it
        # loses the recording outright, so it gets a generous wait of its
        # own instead of joining the terminate-after-5s list below. The
        # real cost is seconds (~6s for a 20-minute call).
        finalize_worker = self.recorder.finalize_worker()
        if finalize_worker is not None and finalize_worker.isRunning():
            self.status_label.setText("Saving the recording before closing...")
            if not finalize_worker.wait(60000):
                logger.warning("Finalize worker still running at exit — abandoning it")
        # Writes metadata.json for a finalize that completed but whose
        # signal the dying event loop will never deliver.
        self.recorder.finish_pending_finalize()

        if self._transcription_worker is not None and self._transcription_worker.isRunning():
            self._transcription_worker.cancel()
        if self._batch_worker is not None and self._batch_worker.isRunning():
            self._batch_worker.cancel()
        workers = [
            self._transcription_worker,
            self._diarization_worker,
            self._simple_diarize_worker,
            self._summarize_worker,
            self._batch_worker,
            self.chat_panel.active_worker(),
            self.recordings_list.active_search_worker(),
        ] + list(self._calendar_lookup_workers)
        for worker in workers:
            if worker is None or not worker.isRunning():
                continue
            if not worker.wait(5000):
                worker.terminate()
                worker.wait(1000)

    def _confirm_exit(self):
        """Show the close-confirmation dialog.

        Returns "quit", "minimize", or "cancel".
        """
        if self.recorder.state != RecordingState.IDLE:
            body = (
                "A recording is in progress. Closing will stop and save it — "
                "minimize instead to keep recording in the background."
            )
        elif self._calendar_banner_session is not None and self.calendar_banner.isVisible():
            body = (
                "TalkTrack is waiting for tag/save confirmation on the finished recording. "
                "Close TalkTrack, or minimize to keep running in the background?"
            )
        elif self._transcription_busy():
            body = (
                "Transcription is currently in progress. Closing will cancel saving the transcript — "
                "minimize instead to finish in the background."
            )
        else:
            body = "Close TalkTrack, or minimize it to keep it running in the background?"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Close TalkTrack?")
        box.setText(body)
        close_btn = box.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        minimize_btn = box.addButton("Minimize", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(minimize_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is close_btn:
            return "quit"
        if clicked is minimize_btn:
            return "minimize"
        return "cancel"
