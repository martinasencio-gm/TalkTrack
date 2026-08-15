"""Tests for the Close/Minimize/Cancel close-confirmation dialog."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _click(box, text=None, standard=None):
    if standard is not None:
        btn = box.button(standard)
    else:
        btn = next(b for b in box.buttons() if b.text().replace("&", "") == text)
    btn.click()


def _fake_exec_clicking(text=None, standard=None):
    """A QMessageBox.exec replacement that clicks a button instead of
    blocking on a real event loop (there is nothing to click it from under
    the offscreen platform)."""
    def _exec(box_self):
        _click(box_self, text=text, standard=standard)
        return 0
    return _exec


class TestConfirmExitDialog(unittest.TestCase):
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

    def test_close_button_returns_quit(self):
        window = self._make_window()
        with patch.object(QMessageBox, "exec", new=_fake_exec_clicking(text="Close")):
            self.assertEqual(window._confirm_exit(), "quit")

    def test_minimize_button_returns_minimize(self):
        window = self._make_window()
        with patch.object(QMessageBox, "exec", new=_fake_exec_clicking(text="Minimize")):
            self.assertEqual(window._confirm_exit(), "minimize")

    def test_cancel_button_returns_cancel(self):
        window = self._make_window()
        with patch.object(QMessageBox, "exec",
                           new=_fake_exec_clicking(standard=QMessageBox.StandardButton.Cancel)):
            self.assertEqual(window._confirm_exit(), "cancel")


class TestCloseEventBranching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_quit_outcome_proceeds_to_real_shutdown(self):
        from app.main_window import MainWindow
        window = MainWindow()
        with patch.object(window, "_confirm_exit", return_value="quit"):
            window.close()
        self.assertTrue(window._really_quit)

    def test_minimize_outcome_ignores_close_and_minimizes_without_quitting(self):
        from app.main_window import MainWindow
        window = MainWindow()
        with patch.object(window, "_confirm_exit", return_value="minimize"), \
             patch.object(window, "showMinimized") as mock_minimize:
            window.close()
        mock_minimize.assert_called_once()
        self.assertFalse(window._really_quit)
        window._really_quit = True
        window.close()

    def test_cancel_outcome_leaves_window_open(self):
        from app.main_window import MainWindow
        window = MainWindow()
        with patch.object(window, "_confirm_exit", return_value="cancel"):
            window.close()
        self.assertFalse(window._really_quit)
        window._really_quit = True
        window.close()


if __name__ == "__main__":
    unittest.main()
