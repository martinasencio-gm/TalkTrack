"""Tests for the per-run "Summarize" checkbox in the transcript header."""
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


class TestSummarizeCheckbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        from app.ui.transcript_viewer import TranscriptViewer
        self.viewer = TranscriptViewer()
        self.addCleanup(self.viewer.deleteLater)

    def test_unchecked_and_disabled_by_default(self):
        # No provider announced yet, so the box is inert until told otherwise.
        self.assertFalse(self.viewer.summarize_enabled())
        self.assertFalse(self.viewer.summarize_cb.isEnabled())

    def test_checked_with_a_provider_reports_enabled(self):
        self.viewer.set_summarize_available(True)
        self.viewer.set_summarize_enabled(True)
        self.assertTrue(self.viewer.summarize_enabled())

    def test_without_a_provider_a_checked_box_still_reports_disabled(self):
        # The saved preference can be True from a session that had a
        # provider configured. Reporting it as enabled would queue a
        # summary job with nothing to run it.
        self.viewer.set_summarize_available(False)
        self.viewer.set_summarize_enabled(True)
        self.assertFalse(self.viewer.summarize_enabled())
        self.assertFalse(self.viewer.summarize_cb.isEnabled())

    def test_programmatic_set_does_not_emit(self):
        # Syncing from config must not look like a user change, or loading
        # the window would write the setting straight back.
        seen = []
        self.viewer.summarize_toggled.connect(seen.append)
        self.viewer.set_summarize_available(True)
        self.viewer.set_summarize_enabled(True)
        self.assertEqual(seen, [])

    def test_user_toggle_emits(self):
        seen = []
        self.viewer.set_summarize_available(True)
        self.viewer.summarize_toggled.connect(seen.append)
        self.viewer.summarize_cb.setChecked(True)
        self.assertEqual(seen, [True])

    def test_available_toggle_preserves_checked_state(self):
        # Opening/closing Settings re-announces availability; that must not
        # silently clear a checked box.
        self.viewer.set_summarize_available(True)
        self.viewer.set_summarize_enabled(True)
        self.viewer.set_summarize_available(True)
        self.assertTrue(self.viewer.summarize_cb.isChecked())


class TestMainWindowWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        from app.main_window import MainWindow
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window._really_quit = True
        self.window.close()

    def test_toggling_the_checkbox_persists_the_preference(self):
        from unittest.mock import patch
        original = self.window.config.get("ai", "auto_summarize")
        with patch.object(self.window.config, "save"):
            self.window._on_summarize_toggled(not original)
            self.assertEqual(
                self.window.config.get("ai", "auto_summarize"), not original
            )
            self.window._on_summarize_toggled(original)

    def test_sync_disables_the_box_without_a_provider(self):
        from unittest.mock import patch
        with patch.object(self.window, "_ai_provider_configured", return_value=False):
            self.window._sync_summarize_control()
        self.assertFalse(self.window.transcript_viewer.summarize_cb.isEnabled())

    def test_sync_pushes_saved_preference_into_the_box(self):
        from unittest.mock import patch
        with patch.object(self.window, "_ai_provider_configured", return_value=True), \
             patch.object(self.window.config, "save"):
            self.window.config.set("ai", "auto_summarize", True)
            self.window._sync_summarize_control()
        self.assertTrue(self.window.transcript_viewer.summarize_cb.isChecked())

    def test_auto_summarize_skipped_when_the_box_is_unchecked(self):
        from unittest.mock import patch
        self.window._transcript = object()
        with patch.object(self.window, "_ai_provider_configured", return_value=True), \
             patch.object(self.window.transcript_viewer, "summarize_enabled", return_value=False), \
             patch.object(self.window.config, "get", return_value=True), \
             patch.object(self.window, "_run_summarize") as run:
            self.window._maybe_auto_summarize()
        run.assert_not_called()

    def test_auto_summarize_runs_when_the_box_is_checked(self):
        from unittest.mock import patch
        self.window._transcript = object()

        def _cfg(section, key=None):
            if (section, key) == ("general", "auto_transcribe"):
                return True
            return None

        with patch.object(self.window, "_ai_provider_configured", return_value=True), \
             patch.object(self.window.transcript_viewer, "summarize_enabled", return_value=True), \
             patch.object(self.window.config, "get", side_effect=_cfg), \
             patch.object(self.window, "_run_summarize") as run:
            self.window._maybe_auto_summarize()
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
