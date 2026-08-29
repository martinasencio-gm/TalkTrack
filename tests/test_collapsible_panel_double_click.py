"""Task 6: opening a recording always shows both outer columns for that
viewing session, without overwriting the user's saved collapse preference.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
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


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestExpandPanelsForRecordingView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_expands_a_collapsed_transcript_column(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter2.is_collapsed())

    def test_expands_a_collapsed_inspector_column(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.splitter1.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter1.is_collapsed())

    def test_does_not_persist_the_expand(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()  # persists transcript_collapsed=True
        window.splitter1.toggle_collapse()  # persists inspector_collapsed=True
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

        window._expand_panels_for_recording_view()

        # Visually open now, but the saved preference must be untouched —
        # a fresh launch should still come up collapsed.
        self.assertFalse(window.splitter2.is_collapsed())
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

    def test_is_a_noop_when_already_expanded(self):
        window = _make_window(self)
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()  # must not raise or toggle anything

        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

    def test_do_on_recording_selected_calls_the_expand_helper(self):
        from unittest.mock import patch
        window = _make_window(self)
        metadata = {
            "directory": window.config.get("output", "directory"),
            "audio_files": {},
        }
        with patch.object(window, "_expand_panels_for_recording_view") as mock_expand:
            window._do_on_recording_selected(metadata)
        mock_expand.assert_called_once()


if __name__ == "__main__":
    unittest.main()
