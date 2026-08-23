"""The transcript column's placeholder used to be one plain sentence for
every empty case, including "no recording selected at all". Per the design
handoff's NOTIFICATIONS.md, that case gets its own richer "Nothing
selected" state (icon, title, subtitle, "Open the last one" button) while
a selected-but-not-yet-transcribed recording keeps the plain placeholder.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication, QLabel

from app.ui.transcript_viewer import TranscriptViewer

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestTranscriptViewerEmptyState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_construction_shows_nothing_selected_state(self):
        viewer = TranscriptViewer()
        # The rich state is a QWidget wrapping icon/title/subtitle/button,
        # not the plain QLabel used for "selected, awaiting transcription".
        self.assertNotIsInstance(viewer._placeholder, QLabel)

    def test_clear_default_shows_plain_placeholder(self):
        viewer = TranscriptViewer()
        viewer.clear()
        self.assertIsInstance(viewer._placeholder, QLabel)

    def test_clear_nothing_selected_shows_rich_state(self):
        viewer = TranscriptViewer()
        viewer.clear(nothing_selected=True)
        self.assertNotIsInstance(viewer._placeholder, QLabel)

    def test_open_last_button_emits_signal(self):
        viewer = TranscriptViewer()
        received = []
        viewer.open_last_requested.connect(lambda: received.append(True))
        for child in viewer._placeholder.findChildren(object):
            if hasattr(child, "text") and callable(child.text) and child.text() == "Open the last one":
                child.click()
                break
        self.assertEqual(received, [True])


if __name__ == "__main__":
    unittest.main()
