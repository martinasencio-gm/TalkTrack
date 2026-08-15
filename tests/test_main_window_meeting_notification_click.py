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
            window.close()
        self.addCleanup(_close)
        return window

    def test_suggest_start_marks_pending_and_notifies_tray(self):
        window = self._make_window()
        with patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "notify_meeting") as mock_notify:
            window._handle_meeting_decision(
                Decision("suggest_start", "Team Sync"), {"timestamp": 0}
            )
        self.assertEqual(window._pending_meeting_notification, "start")
        mock_notify.assert_called_once()

    def test_suggest_end_marks_pending_and_notifies_tray(self):
        window = self._make_window()
        with patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "notify_meeting") as mock_notify:
            window._handle_meeting_decision(
                Decision("suggest_end", "Team Sync"), {"timestamp": 0}
            )
        self.assertEqual(window._pending_meeting_notification, "end")
        mock_notify.assert_called_once()

    def test_clicking_start_notification_starts_recording_and_clears_banner(self):
        window = self._make_window()
        window._pending_meeting_notification = "start"
        with patch.object(window, "_on_meeting_start_accepted") as mock_accept, \
             patch.object(window.meeting_banner, "hide_and_clear") as mock_hide:
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


if __name__ == "__main__":
    unittest.main()
