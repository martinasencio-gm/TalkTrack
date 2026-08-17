import json
import logging
import os
import sys
import webbrowser
from pathlib import Path
from datetime import datetime

import sounddevice as sd

logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QMenuBar, QStatusBar, QMessageBox, QLabel, QInputDialog,
    QFrame
)
from PyQt6.QtCore import Qt, QTimer, QEvent, QThread
from PyQt6.QtGui import QAction

from app.utils.atomic_io import atomic_write_json, atomic_write_text
from app.utils.config import Config
from app.utils import transcript_export
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
from app.ui.collapsible_section import CollapsibleSection
from app.ui.settings_dialog import SettingsDialog
from app.ui.status_panel import SystemStatusDialog
from app.ui.tray_icon import TrayIcon
from app.ui.activity_indicator import ActivityIndicator, resolve_activity_state
from app.ui.recording_header import RecordingHeader, match_event_by_subject
from app.ui.waveform_display import WaveformDisplay
from app.ui.about_dialog import AboutDialog, BMAC_URL
from app.ui.summary_panel import SummaryPanel
from app.ui.action_items_panel import ActionItemsPanel
from app.ui.chat_panel import ChatPanel
from app.ai.chat import build_chat_context
from app.ui.calendar_banner import CalendarSuggestionBanner
from app.ui.meeting_banner import MeetingBanner
from app.integrations.meeting_detector import MeetingDetector
from app.utils import meeting_signals
from app.utils.com_session_worker import ComSessionPoller
from app.ui.calendar_lookup_worker import CalendarLookupWorker
from app.ui.import_timestamp_dialog import ImportTimestampDialog
from app.recording.import_session import build_import_metadata, needs_conversion

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

        self.setWindowTitle("TalkTrack - Call Recorder, Transcriber & AI Summarizer")
        self.setMinimumSize(1000, 700)
        self.resize(1260, 800)

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
                "System tray not available; minimize-to-tray is disabled."
            )

        self._current_transcription_percent = None
        self._activity_widget = ActivityIndicator()
        self._activity_widget.restore_requested.connect(self._restore_from_tray)
        self._activity_widget.position_changed.connect(self._on_activity_widget_moved)

        QTimer.singleShot(500, self._check_startup_status)

    def _setup_menu(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        open_recordings_action = QAction("&Open Recordings Folder", self)
        open_recordings_action.triggered.connect(self._open_recordings_folder)
        file_menu.addAction(open_recordings_action)

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        status_action = QAction("&System Status...", self)
        status_action.triggered.connect(self._show_system_status)
        help_menu.addAction(status_action)

        diarization_setup_action = QAction("&Diarization Setup...", self)
        diarization_setup_action.triggered.connect(self._show_diarization_setup)
        help_menu.addAction(diarization_setup_action)

        shortcut_action = QAction("Add to Start &Menu...", self)
        shortcut_action.triggered.connect(self._install_start_menu_shortcut)
        help_menu.addAction(shortcut_action)

        help_menu.addSeparator()

        log_action = QAction("Open &Log File", self)
        log_action.triggered.connect(self._open_log_file)
        help_menu.addAction(log_action)

        report_action = QAction("&Report a Bug...", self)
        report_action.triggered.connect(self._report_bug)
        help_menu.addAction(report_action)

        help_menu.addSeparator()

        support_action = QAction("Support TalkTrack", self)
        support_action.triggered.connect(lambda: webbrowser.open(BMAC_URL))
        help_menu.addAction(support_action)

        help_menu.addSeparator()

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

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
        main_layout = QHBoxLayout(central)

        # Main splitter: left (controls) | right (tabs)
        splitter = CollapsibleSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)

        # Left panel: controls at top, sources collapsible, recordings below
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_panel.setFixedWidth(400)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)

        # Recording controls (buttons row + timer/meters row)
        self.recording_controls = RecordingControls()
        left_layout.addWidget(self.recording_controls)

        # Meters + waveform share a bordered region so recording state can
        # accent the whole capture area at once (see _on_state_changed) —
        # otherwise the only sign a recording is live is a small blinking dot.
        self.capture_region = QFrame()
        self.capture_region.setObjectName("captureRegion")
        capture_layout = QVBoxLayout(self.capture_region)
        capture_layout.setContentsMargins(4, 4, 4, 4)
        capture_layout.setSpacing(4)

        self.meters_panel = MetersPanel()
        self.meters_panel.setObjectName("metersPanel")
        self.meters_panel.set_gain(self.config.get("audio", "mic_gain"))
        self.meters_panel.gain_changed.connect(self._on_gain_changed)
        capture_layout.addWidget(self.meters_panel)

        # Idle-time level monitors. Off by default every launch — user
        # enables them via the Test Mic button in recording_controls.
        # The system monitor uses WASAPI loopback regardless of capture mode
        # (per-app capture is heavier and not worth spinning up for preview).
        self.mic_monitor = MicMonitor(
            sample_rate=self.config.get("audio", "sample_rate"),
            level_callback=self.meters_panel.update_mic_level,
        )
        # Second monitor for dual-mic mode. Both feed the same meter
        # callback so the UI behaves like recording: whichever mic produced
        # the most recent chunk drives the bar (no true sum — matches
        # DualAudioCapture's shared _mic_level_callback semantics).
        self.mic_monitor_2 = MicMonitor(
            sample_rate=self.config.get("audio", "sample_rate"),
            level_callback=self.meters_panel.update_mic_level,
        )
        self.system_monitor = None  # LoopbackStream, created per-test

        # Waveform display (hidden until recording starts)
        self.waveform = WaveformDisplay(
            seconds=5,
            sample_rate=self.config.get("audio", "sample_rate"),
        )
        capture_layout.addWidget(self.waveform)

        left_layout.addWidget(self.capture_region)

        # Audio sources (collapsible). Stretch is toggled dynamically below.
        self.source_selector = SourceSelector(config=self.config, com_poller=self._com_poller)
        left_layout.addWidget(self.source_selector)

        # Recordings list wrapped in a CollapsibleSection
        recordings_dir = self.config.get("output", "directory")
        self.recordings_list = RecordingsList(recordings_dir)
        self._recordings_section = CollapsibleSection("Recordings", accent="#cba6f7")
        self._recordings_section.content_layout().addWidget(self.recordings_list)
        self._recordings_section.set_expanded(
            not self.config.get("ui", "recordings_collapsed")
        )
        left_layout.addWidget(self._recordings_section, 1)

        # Trailing spacer absorbs space only when both sections are collapsed —
        # otherwise the expanded section claims its stretch factor uncontested.
        left_layout.addStretch(0)
        self._left_layout = left_layout
        self._left_spacer_index = left_layout.count() - 1

        self.source_selector._section.toggled.connect(self._update_left_panel_stretch)
        self._recordings_section.toggled.connect(self._update_left_panel_stretch)
        self._recordings_section.toggled.connect(
            lambda expanded: self.config.set("ui", "recordings_collapsed", not expanded)
        )
        self._update_left_panel_stretch()

        splitter.addWidget(left_panel)

        # Right panel: tabs for transcript and notes
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        # Recording header (above tabs)
        self.recording_header = RecordingHeader()
        right_layout.addWidget(self.recording_header)

        self.calendar_banner = CalendarSuggestionBanner()
        self.calendar_banner.tag_requested.connect(self._on_calendar_tag_requested)
        self.calendar_banner.dismissed.connect(self._on_calendar_dismissed)
        right_layout.addWidget(self.calendar_banner)

        self.meeting_banner = MeetingBanner()
        self.meeting_banner.start_accepted.connect(self._on_meeting_start_accepted)
        self.meeting_banner.start_dismissed.connect(self._on_meeting_start_dismissed)
        self.meeting_banner.end_chosen.connect(self._on_meeting_end_chosen)
        right_layout.addWidget(self.meeting_banner)

        self.tabs = QTabWidget()

        # Transcript tab
        self.transcript_viewer = TranscriptViewer(config=self.config)
        self.tabs.addTab(self.transcript_viewer, "Transcript")

        # Notes tab
        self.notes_panel = NotesPanel()
        self.tabs.addTab(self.notes_panel, "Notes")

        # Summary tab
        self.summary_panel = SummaryPanel()
        self.tabs.addTab(self.summary_panel, "Summary")

        # Action Items tab
        self.action_items_panel = ActionItemsPanel()
        self.tabs.addTab(self.action_items_panel, "Action Items")

        # Chat tab
        self.chat_panel = ChatPanel()
        self.tabs.addTab(self.chat_panel, "Chat")

        right_layout.addWidget(self.tabs)
        splitter.addWidget(right_panel)

        splitter.setSizes([400, 860])
        main_layout.addWidget(splitter)

        self._main_splitter = splitter
        self._left_panel = left_panel
        margins = main_layout.contentsMargins()
        self._collapsed_window_width = (
            left_panel.width() + splitter.handleWidth()
            + margins.left() + margins.right()
        )
        self._min_window_width = self.minimumWidth()
        self._expanded_window_width = self.width()
        splitter.about_to_toggle.connect(self._apply_collapsed_window_size)
        splitter.collapse_changed.connect(self._on_right_panel_collapse_changed)
        if self.config.get("ui", "right_panel_collapsed"):
            splitter.set_collapsed(True)

    def _apply_collapsed_window_size(self, collapsing):
        if collapsing:
            self._expanded_window_width = self.width()
            self.setMinimumWidth(self._collapsed_window_width)
            self.resize(self._collapsed_window_width, self.height())
        else:
            self.setMinimumWidth(self._min_window_width)
            self.resize(self._expanded_window_width, self.height())
        # resize() doesn't propagate to child layouts synchronously - the
        # splitter must already report its new width when toggle_collapse()
        # reads sizes() right after this signal returns.
        QApplication.processEvents()

    def _on_right_panel_collapse_changed(self, collapsed):
        self.config.set("ui", "right_panel_collapsed", collapsed)

    def _update_left_panel_stretch(self, *_):
        audio_expanded = self.source_selector._section.is_expanded()
        rec_expanded = self._recordings_section.is_expanded()
        self._left_layout.setStretchFactor(
            self.source_selector, 1 if audio_expanded else 0
        )
        self._left_layout.setStretchFactor(
            self._recordings_section, 1 if rec_expanded else 0
        )
        self._left_layout.setStretch(
            self._left_spacer_index, 0 if (audio_expanded or rec_expanded) else 1
        )

    def _setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.status_label = QLabel("Ready")
        self.statusbar.addWidget(self.status_label)

    def _connect_signals(self):
        # Recording controls
        self.recording_controls.record_clicked.connect(self._start_recording)
        self.recording_controls.pause_clicked.connect(self._toggle_pause)
        self.recording_controls.stop_clicked.connect(self._stop_recording)
        self.recording_controls.mute_clicked.connect(self._toggle_mute)
        self.recording_controls.test_mic_toggled.connect(self._on_test_mic_toggled)

        # Recorder signals
        self.recorder.state_changed.connect(self._on_state_changed)
        self.recorder.time_updated.connect(self.recording_controls.update_time)
        self.recorder.time_updated.connect(self._on_recording_tick)
        self.recorder.recording_finished.connect(self._on_recording_finished)
        self.recorder.recording_discarded.connect(self._on_recording_discarded)
        self.recorder.error_occurred.connect(self._on_error)
        self.recorder.mic_level.connect(self.meters_panel.update_mic_level)
        self.recorder.mic_level.connect(self.waveform.append_audio)
        self.recorder.system_level.connect(self.meters_panel.update_system_level)
        self.recorder.system_level.connect(self.waveform.append_system_audio)

        # Transcript
        self.transcript_viewer.transcribe_requested.connect(self._start_transcription)
        self.transcript_viewer.cancel_requested.connect(self._cancel_transcription)

        # Recordings list
        self.recordings_list.recording_selected.connect(self._on_recording_selected)
        self.recordings_list.about_to_delete.connect(self._on_recording_about_to_delete)
        self.recordings_list.recording_deleted.connect(self._on_recording_deleted)
        self.recordings_list.recording_files_changed.connect(self._on_recording_files_changed)
        self.recordings_list.search_result_selected.connect(self._on_search_result_selected)
        self.recordings_list.import_requested.connect(self._on_import_requested)
        self.recordings_list.transcribe_selected_requested.connect(self._on_transcribe_selected)
        self.recordings_list.export_selected_requested.connect(self._on_export_selected)

        # Mic device change: restart monitor on new device if it's running
        self.source_selector.mic_changed.connect(self._on_mic_device_changed)

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

        # Transcript editing
        self.transcript_viewer.transcript_changed.connect(self._save_transcript)
        self.transcript_viewer.speaker_names_changed.connect(self._save_speaker_names)

        # Summary / action items
        self.summary_panel.regenerate_requested.connect(self._regenerate_summary)
        self.action_items_panel.regenerate_requested.connect(self._regenerate_summary)
        self.action_items_panel.items_changed.connect(self._on_action_items_changed)

    def _start_recording(self):
        self._silent_capture_warned = False

        mic = self.source_selector.get_selected_mic()
        mic2 = self.source_selector.get_selected_mic2()
        capture_mode = self.source_selector.get_capture_mode()
        app_pids = self.source_selector.get_selected_app_pids()
        loopback = self.source_selector.get_selected_loopback()

        # Validate: need at least one audio source
        if mic is None and mic2 is None and loopback is None and not app_pids:
            QMessageBox.warning(
                self, "No Audio Source",
                "Please select at least one audio source "
                "(microphone, system audio, or app)."
            )
            return

        # Validate: per-app mode needs at least one app checked
        if capture_mode == "per_app" and not app_pids:
            QMessageBox.warning(
                self, "No Apps Selected",
                "Select at least one app to capture, "
                "or switch to 'Capture all system audio' mode."
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

    def _on_mic_device_changed(self, device_index):
        """Mic dropdown changed. If the monitor is running, move it to the new device."""
        if self.mic_monitor.is_active:
            self.mic_monitor.start(device_index)

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
        self.recorder.stop_recording()
        self.status_label.setText("Stopping...")

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
            return
        if action == "suggest_start":
            self.meeting_banner.show_start(
                decision.meeting_name, self._meeting_elapsed(snapshot))
            if hasattr(self, "tray") and self.tray.is_supported():
                self._pending_meeting_notification = "start"
                self.tray.notify_meeting(
                    "Meeting detected",
                    f"{decision.meeting_name or 'A meeting'} is running — "
                    "click here to record it."
                )
        elif action == "start":
            self.status_label.setText("Meeting detected — auto-recording...")
            self._start_recording()
        elif action == "suggest_end":
            self.meeting_banner.show_end(
                decision.meeting_name, self.recorder.get_elapsed_time())
            if hasattr(self, "tray") and self.tray.is_supported():
                self._pending_meeting_notification = "end"
                self.tray.notify_meeting(
                    "Meeting ended",
                    "TalkTrack is still recording — click here to stop or pause."
                )
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
            self._on_meeting_start_accepted()
        elif kind == "end":
            self._restore_from_tray()

    def _on_meeting_start_dismissed(self):
        self._meeting_detector.dismiss_start()

    def _on_meeting_end_chosen(self, action):
        self._meeting_detector.choose_end(action)
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

        self.capture_region.setProperty("recording", state == RecordingState.RECORDING)
        self.capture_region.style().unpolish(self.capture_region)
        self.capture_region.style().polish(self.capture_region)
        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(state, int(self.recorder.get_elapsed_time()))

        if state == RecordingState.RECORDING:
            self.source_selector.set_recording_active(True)
            if not self.waveform.isVisible():
                self.waveform.start()
            else:
                self.waveform._paint_timer.start()
            if self._last_meeting_snapshot and self._meeting_detector.state not in (
                    "recording", "paused_by_detection"):
                self._meeting_detector.note_recording_started(self._last_meeting_snapshot)
        elif state == RecordingState.PAUSED:
            self.waveform._paint_timer.stop()
        elif state == RecordingState.IDLE:
            self.source_selector.set_recording_active(False)
            self.waveform.stop()
            self.recording_controls.reset_timer()
            self.meters_panel.reset()
            self._mic_muted = False
            self.waveform.set_mic_muted(False)
            self._current_capture_failures = {}
            self.source_selector.mark_capture_failures({})
            self._meeting_detector.note_recording_stopped()
            self.meeting_banner.hide_and_clear()

        self._update_activity_visibility()

    def _on_recording_tick(self, seconds):
        if hasattr(self, "tray") and self.tray.is_supported():
            self.tray.set_state(self.recorder.state, int(seconds))
        self._check_silent_capture(seconds)
        self._update_activity_visibility()

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

        # Switch to transcript tab
        self.tabs.setCurrentWidget(self.transcript_viewer)

        # Update recording header
        self.recording_header.set_recording(session)

        # Auto-start transcription if enabled, audio available, long enough
        duration = session.get("duration", 0)
        min_duration = self.config.get("transcription", "min_duration")
        auto_transcribe = self.config.get("general", "auto_transcribe")
        if not auto_transcribe:
            if audio_for_transcript:
                self.status_label.setText(
                    "Recording saved — auto-transcribe disabled. "
                    "Use Transcribe button to transcribe manually."
                )
        elif audio_for_transcript and duration >= min_duration:
            self._start_transcription(audio_for_transcript)
        elif audio_for_transcript:
            self.status_label.setText(
                f"Recording too short ({duration:.0f}s < {min_duration}s) — "
                "skipping auto-transcription. Use Transcribe button to transcribe manually."
            )

        self._maybe_lookup_calendar(session)

    def _transcription_busy(self):
        return (
            (self._transcription_worker is not None and self._transcription_worker.isRunning())
            or (self._diarization_worker is not None and self._diarization_worker.isRunning())
            or (self._simple_diarize_worker is not None and self._simple_diarize_worker.isRunning())
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

        # With separate mic and system tracks on disk, transcribe each one
        # instead of the mix: Whisper never sees the doubled copy of remote
        # speech that bleed puts into combined_audio.wav, and the You/Remote
        # labels come from which file a segment was read out of.
        tracks = dual_track_plan(
            session,
            self.config.get("diarization", "enabled"),
            self.config.get("diarization", "hf_token"),
        )

        self._current_transcription_percent = None
        self._transcription_worker = TranscriptionWorker(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            device=device,
            tracks=tracks,
        )
        self._transcription_worker.session = session
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
        diarization_enabled = self.config.get("diarization", "enabled")
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
        if not session or not session.get("directory"):
            return
        directory = Path(session["directory"])
        names = {}
        names_path = directory / "speaker_names.json"
        if names_path.exists():
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    names = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        try:
            atomic_write_json(directory / "transcript.json",
                              result.to_dict(speaker_names=names),
                              indent=2, ensure_ascii=False)
            atomic_write_text(directory / "transcript.txt",
                              result.to_text(speaker_names=names))
        except OSError:
            self.status_label.setText("Failed to save transcript.")
            return

        self._export_transcript(session)

    def _export_transcript(self, session=None):
        """Best-effort LLM-readable Markdown export for a session, reading
        everything fresh from disk. Deliberately does not touch
        self.transcript_viewer / self.notes_panel — the caller in
        _on_recording_selected runs this for a session that is no longer
        the one those widgets currently display."""
        session = session if session is not None else self._current_session
        if not session or not session.get("directory"):
            return
        directory = Path(session["directory"])

        transcript_path = directory / "transcript.json"
        if not transcript_path.exists():
            return  # nothing transcribed yet — nothing useful to export
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        speaker_names = {}
        names_path = directory / "speaker_names.json"
        if names_path.exists():
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    speaker_names = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        calendar_event, _ = self._load_calendar_event(session)

        notes = ""
        notes_path = directory / "notes.txt"
        if notes_path.exists():
            try:
                notes = notes_path.read_text(encoding="utf-8")
            except OSError:
                pass

        summary_markdown = None
        summary_path = directory / "summary.md"
        if summary_path.exists():
            try:
                summary_markdown = summary_path.read_text(encoding="utf-8")
            except OSError:
                pass

        action_items = None
        actions_path = directory / "action_items.json"
        if actions_path.exists():
            try:
                with open(actions_path, "r", encoding="utf-8") as f:
                    action_items = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        transcript_export.export_transcript(
            session, transcript_data, speaker_names, calendar_event,
            notes, summary_markdown, action_items,
        )

    def _load_calendar_event(self, session):
        """Load calendar_event.json for a session, if present.

        Returns (calendar_event: dict|None, attendees: list[str]). Shared by
        _display_final_transcript (just-finished-transcribing path) and
        _on_recording_selected (browse-to-past-recording path) so both show
        a previously saved calendar tag, not just the former.
        """
        calendar_event = None
        attendees = []
        if not session or not session.get("directory"):
            return calendar_event, attendees
        calendar_path = Path(session["directory"]) / "calendar_event.json"
        if calendar_path.exists():
            try:
                with open(calendar_path, "r", encoding="utf-8") as f:
                    calendar_event = json.load(f)
                attendees = calendar_event.get("attendees", [])
            except (json.JSONDecodeError, OSError):
                pass
        return calendar_event, attendees

    def _display_final_transcript(self, result, session=None):
        result.merge_adjacent_same_speaker()

        if session is None:
            session = self._current_session

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
        self._maybe_auto_summarize()

        # Update chat panel context
        self._update_chat_context()

        self._process_pending_transcriptions()

    def _on_transcription_error(self, error_msg):
        self.transcript_viewer.hide_progress()
        self.status_label.setText("Transcription failed.")
        if self._is_hidden_to_tray():
            self._flag_error_notification()
        else:
            QMessageBox.warning(self, "Transcription Error", error_msg)
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
            self.transcript_viewer.clear()
            self.recording_header.clear()
            self.summary_panel.clear()
            self.action_items_panel.clear()
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
            self.transcript_viewer.clear()
            self.recording_header.clear()
            self.summary_panel.clear()
            self.action_items_panel.clear()
            self.status_label.setText("Recording updated.")

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

        audio_files = metadata.get("audio_files", {})
        audio_path = audio_files.get("combined") or audio_files.get("system") or audio_files.get("mic")
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

        # Switch to transcript tab
        self.tabs.setCurrentWidget(self.transcript_viewer)

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

    def _on_error(self, error_msg):
        self.status_label.setText(f"Error: {error_msg}")
        if self._is_hidden_to_tray():
            self._flag_error_notification()
        else:
            QMessageBox.critical(self, "Error", error_msg)

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
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._success_pending = False
        self._error_pending = False
        self.tray.set_overlay(None)
        self._update_activity_visibility()

    def _quit_from_tray(self):
        self.close()

    def _open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            # Update recordings list with potentially new directory
            self.recordings_list.recordings_dir = Path(self.config.get("output", "directory"))
            self.recordings_list.refresh()
            # Refresh devices in case hidden devices changed
            self.source_selector.refresh_devices()
            # Update mic2 visibility in case mic_count changed
            self.source_selector.update_mic_count(self.config.get("audio", "mic_count"))

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
                "right-click the taskbar icon and choose Pin to taskbar.\n\n"
                "You can also do this later from Help > Add to Start Menu.",
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

    def _report_bug(self):
        from main import build_bug_report_url
        webbrowser.open(build_bug_report_url())

    def _install_start_menu_shortcut(self):
        """Create a Start Menu shortcut for proper taskbar icon."""
        try:
            from app.utils.start_menu import needs_shortcut, create_shortcut, shortcut_path
            app_dir = Path(__file__).parent.parent

            if not needs_shortcut(app_dir):
                QMessageBox.information(
                    self, "Start Menu Shortcut",
                    f"Shortcut already exists:\n{shortcut_path()}"
                )
                return

            reply = QMessageBox.question(
                self,
                "Add to Start Menu",
                "This will create a TalkTrack shortcut in your Start Menu.\n\n"
                f"Location:\n{shortcut_path()}\n\n"
                "This also helps Windows show the correct taskbar icon.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            create_shortcut(app_dir)
            QMessageBox.information(
                self, "Start Menu Shortcut",
                "Shortcut created! TalkTrack is now in the Start Menu.\n\n"
                "The taskbar icon should update next time you launch the app."
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Start Menu Shortcut",
                f"Could not create shortcut:\n{e}"
            )

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

    def _maybe_lookup_calendar(self, session):
        """Kick off an off-thread Outlook calendar lookup for this session,
        if the feature is enabled. Best-effort — no-op on any failure,
        never surfaces an error to the user (see outlook_calendar.py)."""
        if not self.config.get("calendar", "enabled"):
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
            return
        from datetime import datetime
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            return

        self._dispatch_calendar_lookup(session, started_dt, stopped_dt)

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
                                  for_rename=False):
        worker = CalendarLookupWorker(started_dt, stopped_dt)
        worker.session = session
        worker.manual = manual
        worker.for_rename = for_rename
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
        event_to_save["start"] = event["start"].isoformat()
        event_to_save["end"] = event["end"].isoformat()
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
        """Whether a minimize right now should fully hide to the tray rather
        than leave a normal taskbar-minimized window (e.g. so the activity
        pill has something to replace while busy)."""
        busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
        return (
            busy_state is None
            and self.config.get("general", "minimize_to_tray")
            and self.tray.is_supported()
        )

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                if self._should_hide_to_tray():
                    self.setWindowState(Qt.WindowState.WindowNoState)
                    self.hide()
                    if self.config.get("general", "show_tray_hint"):
                        self.tray.show_hint_balloon()
                        self.config.set("general", "show_tray_hint", False)
                    event.accept()
                    return
            self._update_activity_visibility()
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
        self._com_poller.stop()
        self._activity_widget.close()
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
        self.recordings_list.set_transcribing(transcribing_directories(
            [self._transcription_worker, self._diarization_worker,
             self._simple_diarize_worker],
            self._pending_transcriptions,
        ))
        busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
        should_show = busy_state is not None and (self.isMinimized() or self.isHidden())
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
        if self._transcription_worker is not None and self._transcription_worker.isRunning():
            self._transcription_worker.cancel()
        workers = [
            self._transcription_worker,
            self._diarization_worker,
            self._simple_diarize_worker,
            self._summarize_worker,
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
