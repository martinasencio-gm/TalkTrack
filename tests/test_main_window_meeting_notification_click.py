import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from app.integrations.meeting_detector import Decision

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowMeetingNotificationClick(unittest.TestCase):
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

    def test_suggest_start_shows_toast_and_notifies_tray(self):
        window = self._make_window()
        with patch.object(window.meeting_toast, "show_start") as mock_show, \
             patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "notify_meeting") as mock_notify:
            window._handle_meeting_decision(
                Decision("suggest_start", "Team Sync"), {"timestamp": 0}
            )
        mock_show.assert_called_once()
        mock_notify.assert_called_once()
        self.assertEqual(window._pending_meeting_notification, "start")

    def test_suggest_end_shows_toast_and_notifies_tray(self):
        window = self._make_window()
        with patch.object(window.meeting_toast, "show_end") as mock_show, \
             patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "notify_meeting") as mock_notify:
            window._handle_meeting_decision(
                Decision("suggest_end", "Team Sync"), {"timestamp": 0}
            )
        mock_show.assert_called_once()
        mock_notify.assert_called_once()
        self.assertEqual(window._pending_meeting_notification, "end")

    def test_clicking_start_notification_starts_recording_and_clears_toast(self):
        window = self._make_window()
        window._pending_meeting_notification = "start"
        with patch.object(window, "_on_meeting_start_accepted") as mock_accept, \
             patch.object(window.meeting_toast, "hide_and_clear") as mock_hide:
            window._on_tray_message_clicked()
        mock_hide.assert_called_once()
        mock_accept.assert_called_once()
        self.assertIsNone(window._pending_meeting_notification)

    def test_clicking_end_notification_restores_window(self):
        window = self._make_window()
        window._pending_meeting_notification = "end"
        with patch.object(window, "_restore_from_tray") as mock_restore:
            window._on_tray_message_clicked()
        mock_restore.assert_called_once()
        self.assertIsNone(window._pending_meeting_notification)

    def test_click_with_no_pending_notification_is_a_noop(self):
        window = self._make_window()
        self.assertIsNone(window._pending_meeting_notification)
        with patch.object(window, "_on_meeting_start_accepted") as mock_accept, \
             patch.object(window, "_restore_from_tray") as mock_restore:
            window._on_tray_message_clicked()
        mock_accept.assert_not_called()
        mock_restore.assert_not_called()

    def test_toast_hidden_when_recording_starts(self):
        from app.recording.recorder import RecordingState
        window = self._make_window()
        window.meeting_toast.show_start("Daily Standup", 0)
        self.assertFalse(window.meeting_toast.isHidden())
        # Simulate recording state changed to RECORDING
        window._on_state_changed(RecordingState.RECORDING)
        self.assertTrue(window.meeting_toast.isHidden())

    def test_suggest_start_ignored_when_recording_active(self):
        from app.recording.recorder import RecordingState
        window = self._make_window()
        window.recorder._state = RecordingState.RECORDING
        try:
            with patch.object(window.meeting_toast, "show_start") as mock_show:
                window._handle_meeting_decision(
                    Decision("suggest_start", "Planning"), {"timestamp": 0}
                )
                mock_show.assert_not_called()
        finally:
            window.recorder._state = RecordingState.IDLE


    def test_toast_record_accepted_starts_recording(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_on_meeting_start_accepted") as mock_accept:
            window = self._make_window()
            window.meeting_toast.record_accepted.emit()
        mock_accept.assert_called_once()

    def test_toast_dismissed_calls_dismiss(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_on_meeting_start_dismissed") as mock_dismiss:
            window = self._make_window()
            window.meeting_toast.dismissed.emit()
        mock_dismiss.assert_called_once()

    def test_toast_end_chosen_calls_end_handler(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_on_meeting_end_chosen") as mock_end:
            window = self._make_window()
            window.meeting_toast.end_chosen.emit("stop")
        mock_end.assert_called_once_with("stop")

    def test_calendar_banner_tag_requested_calls_handler(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_on_calendar_tag_requested") as mock_tag:
            window = self._make_window()
            event = {"subject": "Stand-up", "start": "09:00", "end": "09:15"}
            window.calendar_banner.tag_requested.emit(event)
        mock_tag.assert_called_once_with(event)

    def test_calendar_banner_dismissed_calls_handler(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_on_calendar_dismissed") as mock_dismiss:
            window = self._make_window()
            window.calendar_banner.dismissed.emit()
        mock_dismiss.assert_called_once()


if __name__ == "__main__":
    unittest.main()
