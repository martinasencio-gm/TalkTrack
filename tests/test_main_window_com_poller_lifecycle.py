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


class TestMainWindowComPollerLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_com_poller_started_in_init(self):
        with patch("app.main_window.ComSessionPoller") as MockPoller:
            mock_instance = MockPoller.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            try:
                MockPoller.assert_called_once_with(main_pid=os.getpid())
                mock_instance.start.assert_called_once_with()
                self.assertIs(window._com_poller, mock_instance)
            finally:
                window._really_quit = True
                window.close()

    def test_com_poller_stopped_on_close(self):
        with patch("app.main_window.ComSessionPoller") as MockPoller:
            mock_instance = MockPoller.return_value
            from app.main_window import MainWindow
            window = MainWindow()
            window._really_quit = True
            window.close()
            mock_instance.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
