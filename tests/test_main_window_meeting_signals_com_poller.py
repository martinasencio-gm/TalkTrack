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


class TestMainWindowMeetingSignalsComPoller(unittest.TestCase):
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

    def test_poll_meeting_signals_uses_poller_snapshot(self):
        window = self._make_window()
        window.config.data["meeting_detection"] = {"mode": "manual", "apps": ["Zoom"],
                                                     "use_mic_capture": True,
                                                     "use_calendar": False,
                                                     "use_window_title": False}
        fake_snapshot = {
            "audio_apps": [{"pids": [1], "name": "Zoom", "process_name": "Zoom.exe",
                             "active": True}],
            "mic_pids": {1},
        }
        window._com_poller.get_snapshot = MagicMock(return_value=fake_snapshot)
        with patch("app.main_window.meeting_signals.probe") as mock_probe:
            mock_probe.return_value = {"timestamp": 0, "audio_apps": [], "mic_capture_apps": [],
                                        "meeting_titles": [], "calendar_event": None}
            window._poll_meeting_signals()
            self.assertTrue(mock_probe.called)
            _, kwargs = mock_probe.call_args
            self.assertEqual(kwargs["_audio_apps_fn"](), fake_snapshot["audio_apps"])
            self.assertEqual(kwargs["_mic_pids_fn"](), {1})

    def test_maybe_tag_detected_meeting_saves_calendar_event(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            window = self._make_window()
            session = {
                "id": "rec1",
                "directory": tmpdir,
                "started_at": "2026-08-20T12:00:00",
                "stopped_at": "2026-08-20T12:05:00",
            }
            window._current_session = session
            with patch("app.main_window.QInputDialog.getText", return_value=("", False)), \
                 patch("app.main_window.get_current_user_name", return_value="Martin Asencio"):
                window._maybe_tag_detected_meeting(session, "Jane Doe")
            self.assertEqual(window._calendar_attendees, ["Jane Doe", "Martin Asencio"])
            event_file = Path(tmpdir) / "calendar_event.json"
            self.assertTrue(event_file.exists())
            import json
            with open(event_file, "r") as f:
                data = json.load(f)
            self.assertEqual(data["subject"], "Jane Doe")
            self.assertEqual(data["attendees"], ["Jane Doe", "Martin Asencio"])
            self.assertEqual(data["organizer"], "Martin Asencio")

    def test_generic_app_name_is_not_tagged(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            window = self._make_window()
            session = {
                "id": "rec1",
                "directory": tmpdir,
                "started_at": "2026-08-20T12:00:00",
                "stopped_at": "2026-08-20T12:05:00",
            }
            window._current_session = session
            window._maybe_tag_detected_meeting(session, "ms-teams")
            event_file = Path(tmpdir) / "calendar_event.json"
            self.assertFalse(event_file.exists())


if __name__ == "__main__":
    unittest.main()
