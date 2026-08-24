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

    def test_close_to_tray_shows_hint_balloon_every_time(self):
        """Regression test: with close_to_tray on, showMinimized() hides the
        window entirely with no other visible feedback, so the user has no
        way to tell it worked. The balloon must fire on every explicit
        Minimize click, not just once ever (that gate belongs to the
        separate normal-minimize hint in changeEvent)."""
        from app.main_window import MainWindow
        window = MainWindow()
        window.config.set("general", "close_to_tray", True)
        window.config.set("general", "show_tray_hint", False)
        with patch.object(window, "_confirm_exit", return_value="minimize"), \
             patch.object(window, "showMinimized"), \
             patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "show_hint_balloon") as mock_balloon:
            window.close()
        mock_balloon.assert_called_once()
        window._really_quit = True
        window.close()

    def test_close_to_tray_does_not_go_through_showMinimized(self):
        """Regression test for the root cause: showMinimized() re-enters
        changeEvent synchronously to hide the window, but showMinimized()'s
        own internal "ensure the widget is visible" follow-up runs right
        after and silently undoes that hide() before control even returns —
        confirmed live via diagnostic logging (window ended up isVisible=True
        immediately after showMinimized() despite changeEvent's hide() having
        taken effect in between). The tray-hide path must hide() directly and
        never call showMinimized() at all."""
        from app.main_window import MainWindow
        window = MainWindow()
        window.config.set("general", "close_to_tray", True)
        with patch.object(window, "_confirm_exit", return_value="minimize"), \
             patch.object(window, "showMinimized") as mock_minimize, \
             patch.object(window.tray, "is_supported", return_value=True), \
             patch.object(window.tray, "show_hint_balloon"):
            window.close()
        mock_minimize.assert_not_called()
        self.assertFalse(window.isVisible())
        window._really_quit = True
        window.close()

    def test_minimize_without_tray_hiding_does_not_show_balloon(self):
        from app.main_window import MainWindow
        window = MainWindow()
        window.config.set("general", "close_to_tray", False)
        with patch.object(window, "_confirm_exit", return_value="minimize"), \
             patch.object(window, "showMinimized"), \
             patch.object(window.tray, "show_hint_balloon") as mock_balloon:
            window.close()
        mock_balloon.assert_not_called()
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
