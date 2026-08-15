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
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = True
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window._update_activity_visibility()
                mock_instance.hide.assert_called_once_with()
            finally:
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_shows_when_minimized_and_recording(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = False
            from app.main_window import MainWindow
            from app.recording.recorder import RecordingState
            window = MainWindow()
            try:
                window.recorder._state = RecordingState.RECORDING
                window.isMinimized = lambda: True
                window._update_activity_visibility()
                mock_instance.show_at.assert_called_once()
                mock_instance.set_activity.assert_called_once()
                args, _ = mock_instance.set_activity.call_args
                self.assertEqual(args[0], "recording")
            finally:
                # Reset the forced RECORDING state before teardown: closeEvent()
                # calls recorder.stop_recording() for any non-IDLE state, and this
                # recorder never went through a real start_recording() (no _capture
                # object) -- that would raise and surface a blocking
                # QMessageBox.critical() dialog via _on_error(), which is
                # pre-existing behavior unrelated to this test or Task 3.
                window.recorder._state = RecordingState.IDLE
                window._really_quit = True
                window.close()

    def test_update_activity_visibility_does_not_show_when_idle(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            mock_instance.isVisible.return_value = False
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                window.isMinimized = lambda: True
                window._update_activity_visibility()
                mock_instance.show_at.assert_not_called()
                mock_instance.set_activity.assert_not_called()
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

    def test_change_event_minimize_while_idle_still_hides_to_tray(self):
        with patch("app.main_window.ActivityIndicator") as MockActivityIndicator:
            mock_instance = MockActivityIndicator.return_value
            from app.main_window import MainWindow
            from PyQt6.QtCore import QEvent, Qt
            window = MainWindow()
            try:
                if not (window.tray.is_supported()):
                    self.skipTest("System tray not available on this runner")
                window.config.set("general", "minimize_to_tray", True)
                window.setWindowState(Qt.WindowState.WindowMinimized)
                event = QEvent(QEvent.Type.WindowStateChange)
                window.changeEvent(event)
                self.assertTrue(window.isHidden())
            finally:
                window._really_quit = True
                window.close()


if __name__ == "__main__":
    unittest.main()
