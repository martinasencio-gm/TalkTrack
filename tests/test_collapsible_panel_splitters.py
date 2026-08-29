"""Task 4: the flat 3-pane QSplitter was replaced by two nested
CollapsibleSplitters so the Transcript and Inspector columns can collapse
independently. See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.collapsible_splitter import CollapsibleSplitter

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


class TestNestedSplitterStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_splitter1_and_splitter2_are_collapsible_splitters(self):
        window = _make_window(self)
        self.assertIsInstance(window.splitter1, CollapsibleSplitter)
        self.assertIsInstance(window.splitter2, CollapsibleSplitter)

    def test_splitter1_holds_splitter2_and_inspector(self):
        window = _make_window(self)
        self.assertIs(window.splitter1.widget(0), window.splitter2)
        self.assertIs(window.splitter1.widget(1), window.inspector)

    def test_splitter2_holds_library_and_transcript(self):
        window = _make_window(self)
        self.assertIs(window.splitter2.widget(0), window.library_panel)
        self.assertIs(window.splitter2.widget(1), window.transcript_panel)

    def test_main_window_has_no_flat_splitter_attribute(self):
        window = _make_window(self)
        self.assertFalse(hasattr(window, "splitter"))

    def test_collapsing_splitter2_zeroes_the_transcript_pane(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertEqual(window.splitter2.sizes()[1], 0)

    def test_collapsing_splitter1_zeroes_the_inspector_pane(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertEqual(window.splitter1.sizes()[1], 0)


if __name__ == "__main__":
    unittest.main()
