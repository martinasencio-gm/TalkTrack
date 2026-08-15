import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowRecordingFilesChanged(unittest.TestCase):
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

    def test_clears_view_when_current_session_files_changed(self):
        window = self._make_window()
        window._current_session = {"directory": "/r1"}
        window._transcript = object()

        window._on_recording_files_changed("/r1")

        self.assertIsNone(window._current_session)
        self.assertIsNone(window._transcript)
        self.assertEqual(window.status_label.text(), "Recording updated.")

    def test_ignores_other_sessions(self):
        window = self._make_window()
        window._current_session = {"directory": "/r1"}
        window._transcript = "keep-me"

        window._on_recording_files_changed("/r2")

        self.assertEqual(window._current_session, {"directory": "/r1"})
        self.assertEqual(window._transcript, "keep-me")

    def test_noop_when_no_current_session(self):
        window = self._make_window()
        window._current_session = None

        window._on_recording_files_changed("/r1")  # must not raise

        self.assertIsNone(window._current_session)


if __name__ == "__main__":
    unittest.main()
