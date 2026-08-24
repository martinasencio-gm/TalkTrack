"""The minimize button minimizes; double-click shrinks.

Previously changeEvent hijacked WindowMinimized and swapped in the compact
strip or hid to the tray depending on a settings combo, so the standard
Windows gesture did something non-standard. Now minimize always minimizes to
the taskbar and the shrink chain (full -> compact bar -> pill -> full) lives
entirely on double-click.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowMinimize(unittest.TestCase):
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

    def _minimize(self, window):
        """What clicking the minimize button amounts to."""
        window.setWindowState(Qt.WindowState.WindowMinimized)

    def test_minimizing_never_shows_the_strip(self):
        for target in ("compact_bar", "pill"):
            with self.subTest(target=target):
                window = self._make_window()
                window.config.set("ui", "double_click_target", target)
                self._minimize(window)
                self.assertFalse(window.compact_strip.isVisible())

    def test_minimizing_leaves_the_window_minimized_not_hidden(self):
        # Hidden means no taskbar entry — that's the tray, and only the close
        # button goes there now.
        window = self._make_window()
        window.config.set("general", "close_to_tray", True)
        window.show()
        self._minimize(window)
        self.assertTrue(window.isMinimized())
        self.assertFalse(window.isHidden())

    def test_double_click_shrinks_to_the_compact_bar_and_keeps_the_taskbar_entry(self):
        window = self._make_window()
        window.config.set("ui", "double_click_target", "compact_bar")

        window._advance_presentation()

        self.assertTrue(window.compact_strip.isVisible())
        self.assertEqual(window.compact_strip.variant(), "full")
        self.assertTrue(window.isMinimized())
        self.assertFalse(window.isHidden())

    def test_the_chain_runs_compact_bar_then_pill_then_full(self):
        window = self._make_window()
        window.config.set("ui", "double_click_target", "compact_bar")

        window._advance_presentation()
        self.assertEqual(window._current_presentation(), "compact_bar")

        window._advance_presentation()
        self.assertEqual(window._current_presentation(), "pill")
        self.assertEqual(window.compact_strip.variant(), "pill")

        window._advance_presentation()
        self.assertEqual(window._current_presentation(), "full")
        self.assertFalse(window.compact_strip.isVisible())

    def test_the_pill_target_skips_the_compact_bar(self):
        window = self._make_window()
        window.config.set("ui", "double_click_target", "pill")

        window._advance_presentation()
        self.assertEqual(window.compact_strip.variant(), "pill")

        window._advance_presentation()
        self.assertFalse(window.compact_strip.isVisible())

    def test_restoring_from_the_taskbar_dismisses_the_strip(self):
        window = self._make_window()
        window._advance_presentation()
        self.assertTrue(window.compact_strip.isVisible())

        window.setWindowState(Qt.WindowState.WindowNoState)

        self.assertFalse(window.compact_strip.isVisible())
        self.assertFalse(window.compact_strip_action.isChecked())

    def test_a_strip_opened_from_the_view_menu_survives_un_minimizing(self):
        # That strip is a free-floating panel, not the window's minimized
        # stand-in, so restoring the window must leave it alone.
        window = self._make_window()
        window.compact_strip_action.setChecked(True)
        self.assertTrue(window.compact_strip.isVisible())

        self._minimize(window)
        window.setWindowState(Qt.WindowState.WindowNoState)

        self.assertTrue(window.compact_strip.isVisible())
        self.assertTrue(window.compact_strip_action.isChecked())

    def test_the_activity_widget_stays_hidden_while_the_strip_is_up(self):
        # Compact mode is a minimized window now, so without the strip check
        # both floating widgets would stack on screen while recording.
        from app.recording.recorder import RecordingState
        window = self._make_window()
        window._advance_presentation()

        window.recorder._set_state(RecordingState.RECORDING)
        window._update_activity_visibility()

        self.assertFalse(window._activity_widget.isVisible())
        self.assertTrue(window.compact_strip.isVisible())


if __name__ == "__main__":
    unittest.main()
