"""Window geometry: restored (clamped to connected screens) on launch,
saved on quit, and a maximized window kept maximized when it comes back
from the compact strip."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self, ui_overrides=None):
        from app.main_window import MainWindow
        window = MainWindow()
        # Never touch the real settings.json from a test.
        window.config.save = lambda: None
        if ui_overrides:
            for k, v in ui_overrides.items():
                window.config.set("ui", k, v)

        def _close():
            window._really_quit = True
            if hasattr(window, "_com_session_poller") and window._com_session_poller:
                window._com_session_poller.stop()
            window.close()
        self.addCleanup(_close)
        return window

    def test_restore_applies_the_helper_result_via_move_and_resize(self):
        from app.utils.screen_utils import fit_geometry_to_screens
        window = self._make_window()
        saved = [15, 25, 1300, 850]
        window.config.set("ui", "window_geometry", saved)
        screens = [
            (g.x(), g.y(), g.width(), g.height())
            for g in (s.availableGeometry() for s in QApplication.screens())
        ]
        ex, ey, ew, eh = fit_geometry_to_screens(
            *saved, screens,
            min_size=(window.minimumWidth(), window.minimumHeight()),
        )
        with patch.object(window, "resize") as resize, \
             patch.object(window, "move") as move:
            window._restore_window_geometry()
        resize.assert_called_once_with(ew, eh)
        move.assert_called_once_with(ex, ey)

    def test_restore_clamps_a_rect_from_an_unplugged_monitor(self):
        window = self._make_window()
        window.config.set("ui", "window_geometry", [12000, 300, 700, 600])
        with patch.object(window, "resize") as resize, \
             patch.object(window, "move") as move:
            window._restore_window_geometry()
        geo = QApplication.primaryScreen().availableGeometry()
        mx, my = move.call_args[0]
        rw, rh = resize.call_args[0]
        self.assertGreaterEqual(mx, geo.x())
        self.assertLessEqual(mx + rw, geo.x() + geo.width())
        self.assertGreaterEqual(my, geo.y())
        self.assertLessEqual(my + rh, geo.y() + geo.height())

    def test_restore_ignores_a_malformed_saved_value(self):
        window = self._make_window()
        window.resize(1360, 860)
        window.config.set("ui", "window_geometry", "nonsense")
        window._restore_window_geometry()  # must not raise
        self.assertEqual((window.width(), window.height()), (1360, 860))

    def test_saved_maximized_state_is_applied_once_on_show(self):
        window = self._make_window()
        window._restore_maximized = True
        window.showEvent(QShowEvent())
        self.assertTrue(window._maximized_applied)

    def test_save_writes_rect_and_maximized_flag(self):
        window = self._make_window()
        with patch.object(QApplication, "platformName", return_value="windows"):
            window.resize(1234, 777)
            window.move(50, 60)
            window._save_window_geometry()
        self.assertEqual(window.config.get("ui", "window_geometry"), [50, 60, 1234, 777])
        self.assertFalse(window.config.get("ui", "window_maximized"))

    def test_save_is_skipped_headless(self):
        window = self._make_window()
        window.config.set("ui", "window_geometry", None)
        window._save_window_geometry()  # offscreen platform -> no-op
        self.assertIsNone(window.config.get("ui", "window_geometry"))

    def test_maximized_window_returns_maximized_from_the_strip(self):
        window = self._make_window()
        window.show()
        window.showMaximized()
        self.assertTrue(window.isMaximized())
        window._switch_to_compact_bar()
        self.assertTrue(window._was_maximized_before_strip)
        window._switch_to_full_ui()
        self.assertTrue(window.isMaximized())
        self.assertFalse(window._was_maximized_before_strip)

    def test_non_maximized_window_returns_normal_from_the_strip(self):
        window = self._make_window()
        window.show()
        window._switch_to_compact_bar()
        self.assertFalse(window._was_maximized_before_strip)
        window._switch_to_full_ui()
        self.assertFalse(window.isMaximized())


if __name__ == "__main__":
    unittest.main()
