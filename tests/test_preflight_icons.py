"""Smoke tests that PreflightWidget's verdict and check icons actually get
a rendered pixmap. Before this, verdict_icon and the three check icons were
QLabel()s created and never given a pixmap - the design's 46 vendored SVGs
never rendered anywhere in the capture bar.
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

    def test_check_icons_render_on_construction(self):
        widget = PreflightWidget()
        for icon in (widget.voice_icon, widget.call_icon, widget.transcription_icon):
            self.assertFalse(icon.pixmap().isNull())

    def test_set_verdict_updates_the_icon_pixmap(self):
        widget = PreflightWidget()
        before = widget.verdict_icon.pixmap().toImage()
        widget.set_verdict("blocked", "Recording will be silent", "Fix it")
        after = widget.verdict_icon.pixmap().toImage()
        self.assertNotEqual(before, after)

    def test_update_checks_updates_each_check_icon(self):
        widget = PreflightWidget()
        before = widget.voice_icon.pixmap().toImage()
        widget.update_checks(
            "blocked", "No microphone selected",
            "ready", "Ready",
            "ready", "Ready",
        )
        after = widget.voice_icon.pixmap().toImage()
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
