"""Smoke tests that PreflightWidget's verdict icon actually gets a rendered
pixmap. Before this, verdict_icon was a QLabel() created and never given a
pixmap - the design's vendored SVGs never rendered in the capture bar.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.preflight import PreflightWidget

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestPreflightIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_verdict_icon_renders_on_construction(self):
        widget = PreflightWidget()
        pixmap = widget.verdict_icon.pixmap()
        self.assertFalse(pixmap.isNull())

    def test_set_verdict_updates_the_icon_pixmap(self):
        widget = PreflightWidget()
        before = widget.verdict_icon.pixmap().toImage()
        widget.set_verdict("blocked", "Recording will be silent", "Fix it")
        after = widget.verdict_icon.pixmap().toImage()
        self.assertNotEqual(before, after)

    def test_set_verdict_updates_title_and_subtitle_text(self):
        widget = PreflightWidget()
        widget.set_verdict("warning", "Mic is very quiet", "Check it before you record")
        self.assertEqual(widget.verdict_title.text(), "Mic is very quiet")
        self.assertEqual(widget.verdict_subtitle.text(), "Check it before you record")


if __name__ == "__main__":
    unittest.main()
