"""Task 5: collapsing/expanding the Transcript or Inspector column, or an
Inspector section header, persists to config; a fresh MainWindow restores
from it. See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
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


class TestOuterColumnCollapsePersists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_collapsing_transcript_persists_transcript_collapsed(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))

    def test_expanding_transcript_persists_false(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()  # collapse
        window.splitter2.toggle_collapse()  # expand
        self.assertFalse(window.config.get("ui", "transcript_collapsed"))

    def test_collapsing_inspector_persists_inspector_collapsed(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

    def test_fresh_window_restores_a_collapsed_transcript_column(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))

        window2 = _make_window(self)
        self.assertTrue(window2.splitter2.is_collapsed())

    def test_fresh_window_restores_a_collapsed_inspector_column(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

        window2 = _make_window(self)
        self.assertTrue(window2.splitter1.is_collapsed())


class TestInspectorSectionCollapsePersists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_collapsing_speakers_section_persists_only_that_key(self):
        window = _make_window(self)
        window.inspector.speakers_section.set_expanded(True)   # already True by default; no-op
        window.inspector.speakers_section.set_expanded(False)  # then collapse it explicitly

        self.assertFalse(window.config.get("ui", "speakers_section_expanded"))
        self.assertEqual(window.config.get("ui", "notes_section_expanded"), True)
        self.assertEqual(window.config.get("ui", "summary_section_expanded"), True)

    def test_fresh_window_restores_section_expand_state(self):
        window = _make_window(self)
        window.inspector.notes_section.set_expanded(True)
        self.assertTrue(window.config.get("ui", "notes_section_expanded"))

        window2 = _make_window(self)
        self.assertTrue(window2.inspector.notes_section.is_expanded())
        # Untouched sections still restore from their own (equally True) default.
        self.assertTrue(window2.inspector.speakers_section.is_expanded())


if __name__ == "__main__":
    unittest.main()
