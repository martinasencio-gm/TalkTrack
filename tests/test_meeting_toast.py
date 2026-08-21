import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication, QPushButton

from app.ui.meeting_toast import MeetingNotificationToast

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMeetingNotificationToast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_show_start_displays_content_and_buttons(self):
        toast = MeetingNotificationToast()
        toast.show_start("Design Sync", 0)

        self.assertIn("Design Sync", toast._title_label.text())
        self.assertIn("Design Sync started just now - record it?", toast._body_label.text())

        # Buttons in button row: stretch + Dismiss + Record
        button_texts = [w.text() for w in toast._button_container.findChildren(QPushButton)]
        self.assertIn("Record", button_texts)
        self.assertIn("Dismiss", button_texts)

    def test_clicking_record_emits_signal_and_hides(self):
        toast = MeetingNotificationToast()
        toast.show_start("All Hands", 30)

        mock_slot = MagicMock()
        toast.record_accepted.connect(mock_slot)

        # Find and click record button
        record_btn = toast.findChild(QPushButton, "toastRecordBtn")
        self.assertIsNotNone(record_btn)
        record_btn.click()

        mock_slot.assert_called_once()
        self.assertTrue(toast.isHidden())

    def test_clicking_dismiss_emits_signal_and_hides(self):
        toast = MeetingNotificationToast()
        toast.show_start("Weekly Catchup", 0)

        mock_slot = MagicMock()
        toast.dismissed.connect(mock_slot)

        dismiss_btn = toast.findChild(QPushButton, "toastDismissBtn")
        self.assertIsNotNone(dismiss_btn)
        dismiss_btn.click()

        mock_slot.assert_called_once()
        self.assertTrue(toast.isHidden())

    def test_show_end_displays_end_actions(self):
        toast = MeetingNotificationToast()
        toast.show_end("Weekly Catchup", 600)

        self.assertEqual(toast._title_label.text(), "Meeting Ended")
        self.assertIn("stop recording?", toast._body_label.text())

        mock_slot = MagicMock()
        toast.end_chosen.connect(mock_slot)

        stop_btn = toast.findChild(QPushButton, "toastRecordBtn")
        self.assertIsNotNone(stop_btn)
        self.assertEqual(stop_btn.text(), "Stop & save")
        stop_btn.click()

        mock_slot.assert_called_once_with("stop")
        self.assertTrue(toast.isHidden())


if __name__ == "__main__":
    unittest.main()
