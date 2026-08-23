"""CompactStrip is built but was never wired into MainWindow (item #10 of
the UI redesign review). This covers the View-menu toggle, the seven
signal connections, and state derivation — not the widget's own visuals
(see test_compact_strip.py for those).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowCompactStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            if hasattr(window, "_meeting_signals_timer"):
                window._meeting_signals_timer.stop()
            if hasattr(window, "_com_session_poller") and window._com_session_poller:
                window._com_session_poller.stop()
            window.close()
        self.addCleanup(_close)
        return window

    def test_compact_strip_starts_hidden(self):
        window = self._make_window()
        self.assertFalse(window.compact_strip.isVisible())
        self.assertFalse(window.compact_strip_action.isChecked())

    def test_view_menu_action_shows_and_hides_strip(self):
        window = self._make_window()
        window.compact_strip_action.setChecked(True)
        self.assertTrue(window.compact_strip.isVisible())
        window.compact_strip_action.setChecked(False)
        self.assertFalse(window.compact_strip.isVisible())

    def test_toggle_persists_visibility_to_config(self):
        window = self._make_window()
        window.compact_strip_action.setChecked(True)
        self.assertTrue(window.config.get("ui", "compact_strip_visible"))
        window.compact_strip_action.setChecked(False)
        self.assertFalse(window.config.get("ui", "compact_strip_visible"))

    def test_drag_persists_position_to_config(self):
        window = self._make_window()
        window._on_compact_strip_moved(222, 333)
        self.assertEqual(window.config.get("ui", "compact_strip_position"), [222, 333])

    def test_record_requested_starts_recording(self):
        # Qt captures the bound method at connect() time (inside __init__),
        # so patching the instance attribute after construction wouldn't
        # affect the already-connected slot — patch the class before the
        # window (and its signal wiring) is built.
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_start_recording") as mock_start:
            window = self._make_window()
            window.compact_strip.record_requested.emit()
        mock_start.assert_called_once()

    def test_stop_requested_stops_recording(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_stop_recording") as mock_stop:
            window = self._make_window()
            window.compact_strip.stop_requested.emit()
        mock_stop.assert_called_once()

    def test_pause_and_resume_requested_both_map_to_toggle_pause(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_toggle_pause") as mock_toggle:
            window = self._make_window()
            window.compact_strip.pause_requested.emit()
            window.compact_strip.resume_requested.emit()
        self.assertEqual(mock_toggle.call_count, 2)

    def test_cancel_requested_cancels_transcription(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_cancel_transcription") as mock_cancel:
            window = self._make_window()
            window.compact_strip.cancel_requested.emit()
        mock_cancel.assert_called_once()

    def test_expand_and_open_transcript_both_restore_from_tray(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_restore_from_tray") as mock_restore:
            window = self._make_window()
            window.compact_strip.expand_requested.emit()
            window.compact_strip.open_transcript_requested.emit()
        self.assertEqual(mock_restore.call_count, 2)

    def test_mute_toggle_updates_compact_strip_state(self):
        from app.recording.recorder import RecordingState
        window = self._make_window()
        window.recorder._set_state(RecordingState.RECORDING)
        window._toggle_mute()
        self.assertEqual(window.compact_strip.current_state, "muted")

    def test_transcription_finished_marks_done_until_next_recording(self):
        from app.recording.recorder import RecordingState
        from app.transcription.transcriber import TranscriptResult
        window = self._make_window()
        window._current_session = {"directory": "", "name": "x"}
        result = TranscriptResult(segments=[], language="en", duration=0.0)
        window._display_final_transcript(result, session=window._current_session)
        self.assertTrue(window._compact_strip_done)
        self.assertEqual(window.compact_strip.current_state, "done")

        window.recorder._set_state(RecordingState.RECORDING)
        self.assertFalse(window._compact_strip_done)
        self.assertEqual(window.compact_strip.current_state, "recording")


if __name__ == "__main__":
    unittest.main()
