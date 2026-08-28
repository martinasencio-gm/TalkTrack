import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowActivityWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_activity_widget_created_and_wired_in_init(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                MockActivityIndicator.assert_called_once_with()
                self.assertIs(window._activity_widget, mock_instance)
                mock_instance.restore_requested.connect.assert_called_once_with(
                    window._restore_from_tray
                )
                mock_instance.position_changed.connect.assert_called_once_with(
                    window._on_activity_widget_moved
                )
                self.assertIsNone(window._current_transcription_percent)
            finally:
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_hides_when_visible_but_not_minimized(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            window.show()
            window._update_activity_visibility()
            self.assertFalse(window._activity_widget.isVisible())
        finally:
            window._really_quit = True
            window.close()

    def test_update_activity_visibility_shows_when_minimized_and_recording(self):
        from app.main_window import MainWindow
        from app.recording.recorder import RecordingState
        window = MainWindow()
        try:
            window.recorder._state = RecordingState.RECORDING
            window.isMinimized = lambda: True
            window._update_activity_visibility()
            self.assertTrue(window._activity_widget.isVisible())
            self.assertTrue(window._activity_widget.pill_btn_stop.isVisible())
            self.assertTrue(window._activity_widget.pill_btn_pause.isVisible())
        finally:
            window.recorder._state = RecordingState.IDLE
            window._really_quit = True
            window.close()

    def test_update_activity_visibility_does_not_show_when_idle(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            window.isMinimized = lambda: True
            window._update_activity_visibility()
            self.assertFalse(window._activity_widget.isVisible())
        finally:
            window._really_quit = True
            window.close()

    def test_on_activity_widget_moved_saves_config(self):
        with patch("app.main_window.ActivityIndicator"):
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window._on_activity_widget_moved(200, 300)
                self.assertEqual(
                    window.config.get("ui", "activity_widget_position"), [200, 300]
                )
            finally:
                window._really_quit = True
                window.close()

    def test_activity_widget_position_falls_back_to_default(self):
        with patch("app.main_window.ActivityIndicator"):
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                x, y = window._activity_widget_position()
                self.assertIsInstance(x, int)
                self.assertIsInstance(y, int)
            finally:
                window._really_quit = True
                window.close()

    def test_close_event_closes_activity_widget(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            window._really_quit = True
            window.close()
            mock_instance.close.assert_called_once_with()

    def test_start_diarization_resets_stale_percent_and_updates_widget(self):
        with patch("app.main_window.DiarizationWorker") as MockDiarizationWorker:
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window.isMinimized = lambda: True
                # A just-finished transcription leaves a stale percent behind
                # and must not be carried over and shown as if it were live —
                # diarization's own progress_percent signal (wired below)
                # replaces it once the new job actually reports progress.
                window._current_transcription_percent = 87
                window._start_diarization(
                    transcript_result=object(),
                    session={"audio_files": {"combined": "/fake/path.wav"}},
                )
                self.assertIsNone(window._current_transcription_percent)
                MockDiarizationWorker.return_value.start.assert_called_once()
            finally:
                window._really_quit = True
                window.close()

    def test_start_diarization_wires_progress_percent_to_the_bar(self):
        # Diarization used to show only status text ("Running speaker
        # diarization...") with no percent, unlike transcription's real
        # progress bar — pyannote's per-chunk hook now drives the same bar.
        with patch("app.main_window.DiarizationWorker") as MockDiarizationWorker:
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window._start_diarization(
                    transcript_result=object(),
                    session={"audio_files": {"combined": "/fake/path.wav"}},
                )
                worker = MockDiarizationWorker.return_value
                worker.progress_percent.connect.assert_any_call(
                    window.transcript_viewer.set_progress_percent
                )
                worker.progress_percent.connect.assert_any_call(
                    window._on_transcription_percent
                )
            finally:
                window._really_quit = True
                window.close()

    def test_start_simple_diarization_resets_stale_percent_and_updates_widget(self):
        with patch("app.main_window.SimpleDiarizeWorker") as MockSimpleDiarizeWorker:
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window.isMinimized = lambda: True
                window._current_transcription_percent = 87
                window._start_simple_diarization(
                    transcript_result=object(),
                    session={"audio_files": {}},
                    mic_path="/fake/mic.wav",
                    sys_path="/fake/sys.wav",
                )
                self.assertIsNone(window._current_transcription_percent)
                MockSimpleDiarizeWorker.return_value.start.assert_called_once()
            finally:
                window._really_quit = True
                window.close()

    def test_change_event_minimize_while_idle_still_hides_to_tray(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            from PyQt6.QtCore import QEvent, Qt
            window = MainWindow()
            try:
                if not (window.tray.is_supported()):
                    self.skipTest("System tray not available on this runner")
                window.config.set("general", "close_to_tray", True)
                window.setWindowState(Qt.WindowState.WindowMinimized)
                event = QEvent(QEvent.Type.WindowStateChange)
                window.changeEvent(event)
                self.assertTrue(window.isHidden())
            finally:
                window._really_quit = True
                window.close()

    def test_cancel_transcription_cancels_diarization_worker(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            mock_diarizer = MagicMock()
            mock_diarizer.isRunning.return_value = True
            mock_diarizer.transcript_result = MagicMock()
            mock_diarizer.session = {"directory": "/fake/dir"}
            window._diarization_worker = mock_diarizer

            window._cancel_transcription()
            mock_diarizer.cancel.assert_called_once()

            # Calling cancelled handler displays base transcript and clears worker
            with patch.object(window, "_display_final_transcript") as mock_display, \
                 patch.object(window, "_process_pending_transcriptions") as mock_process:
                window._on_diarization_cancelled()
                mock_display.assert_called_once()
                mock_process.assert_called_once()
                self.assertIsNone(window._diarization_worker)
        finally:
            window._really_quit = True
            window.close()

    def test_activity_visibility_labels_diarization_as_identifying_speakers(self):
        # A stale, no-longer-running TranscriptionWorker from the same
        # recording must not paint the strip as "Transcribing" while a
        # DiarizationWorker is the one actually busy.
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            stale_transcription = MagicMock()
            stale_transcription.isRunning.return_value = False
            stale_transcription.session = {"directory": "/fake/recording_1"}
            window._transcription_worker = stale_transcription

            busy_diarizer = MagicMock()
            busy_diarizer.isRunning.return_value = True
            busy_diarizer.session = {"directory": "/fake/recording_1"}
            window._diarization_worker = busy_diarizer

            with patch.object(window.recording_controls, "set_transcribing") as mock_set:
                window._update_activity_visibility()
                self.assertEqual(
                    mock_set.call_args.kwargs.get("phase_label"),
                    "Identifying speakers",
                )
                self.assertEqual(
                    mock_set.call_args.kwargs.get("name"), "recording_1"
                )
        finally:
            window._diarization_worker = None
            window._really_quit = True
            window.close()

    def test_recording_state_populates_capturing_sources(self):
        from app.main_window import MainWindow
        from app.recording.recorder import RecordingState
        window = MainWindow()
        try:
            window.source_selector.get_selected_mic_name = lambda: "Test Mic"
            window.source_selector.get_selected_source_name = lambda: "Test App"
            window._on_state_changed(RecordingState.RECORDING)
            self.assertEqual(window.recording_controls.rec_mic_name.text(), "Test Mic")
            self.assertEqual(window.recording_controls.rec_call_name.text(), "Test App")
        finally:
            window._really_quit = True
            window.close()


class TestMainWindowAiActivity(unittest.TestCase):
    """AI summary / action-item generation drives the same progress
    surfaces as transcription, without entering _transcription_busy()."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def _running_worker(self):
        w = MagicMock()
        w.isRunning.return_value = True
        return w

    def test_ai_busy_reflects_a_running_summarize_worker(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            self.assertFalse(window._ai_busy())
            window._summarize_worker = self._running_worker()
            self.assertTrue(window._ai_busy())
            # ...but never the transcription-pipeline gate.
            self.assertFalse(window._transcription_busy())
        finally:
            window._summarize_worker = None
            window._really_quit = True
            window.close()

    def test_current_phase_label_is_generating_summary_when_ai_busy(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            window._summarize_worker = self._running_worker()
            self.assertEqual(window._current_phase_label(), "Generating summary")
        finally:
            window._summarize_worker = None
            window._really_quit = True
            window.close()

    def test_transcription_worker_outranks_ai_label(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            tw = self._running_worker()
            tw.session = {"directory": "/fake/rec"}
            window._transcription_worker = tw
            window._summarize_worker = self._running_worker()
            self.assertEqual(window._current_phase_label(), "Transcribing")
        finally:
            window._transcription_worker = None
            window._summarize_worker = None
            window._really_quit = True
            window.close()

    def test_update_activity_visibility_feeds_ai_label_to_the_strip(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            window._current_session = {"directory": "/fake/rec", "name": "Client call"}
            window._summarize_worker = self._running_worker()
            window._ai_start_time = None
            with patch.object(window.recording_controls, "set_transcribing") as mock_set, \
                 patch.object(window.recordings_list, "set_summarizing") as mock_sum:
                window._update_activity_visibility()
            self.assertTrue(mock_set.call_args.args[0])  # visual_busy
            self.assertEqual(
                mock_set.call_args.kwargs.get("phase_label"), "Generating summary"
            )
            self.assertEqual(mock_set.call_args.kwargs.get("name"), "Client call")
            self.assertIsNone(mock_set.call_args.args[1])  # no percent for AI
            mock_sum.assert_called_once_with({"/fake/rec"})
        finally:
            window._summarize_worker = None
            window._really_quit = True
            window.close()

    def test_end_ai_phase_clears_state_and_stops_tick(self):
        from app.main_window import MainWindow
        window = MainWindow()
        try:
            window._ai_start_time = 123.0
            window._ai_tick.start()
            window._end_ai_phase()
            self.assertIsNone(window._ai_start_time)
            self.assertFalse(window._ai_tick.isActive())
        finally:
            window._really_quit = True
            window.close()


if __name__ == "__main__":
    unittest.main()
