"""Tests for SegmentWidget logic."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest

from app.transcription.transcriber import TranscriptSegment

_app = None


def _get_app():
    global _app
    if _app is None:
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
    return _app


class TestSegmentWidgetHelpers(unittest.TestCase):

    def test_format_time(self):
        from app.ui.segment_widget import _format_time
        self.assertEqual(_format_time(0), "00:00:00")
        self.assertEqual(_format_time(65), "00:01:05")
        self.assertEqual(_format_time(3661), "01:01:01")

    def test_display_speaker_with_name(self):
        from app.ui.segment_widget import _display_speaker
        self.assertEqual(
            _display_speaker("SPEAKER_00", {"SPEAKER_00": "Alice"}),
            "Alice"
        )

    def test_display_speaker_without_name(self):
        from app.ui.segment_widget import _display_speaker
        self.assertEqual(
            _display_speaker("SPEAKER_00", {}),
            "SPEAKER_00"
        )

    def test_display_speaker_empty(self):
        from app.ui.segment_widget import _display_speaker
        self.assertEqual(_display_speaker("", {}), "")

    def test_display_speaker_empty_name_value(self):
        from app.ui.segment_widget import _display_speaker
        self.assertEqual(
            _display_speaker("SPEAKER_00", {"SPEAKER_00": ""}),
            "SPEAKER_00"
        )


class TestSegmentWidgetIconsCwdIndependent(unittest.TestCase):
    """SegmentWidget's play/stop/edit icons used to be loaded via
    QIcon("resources/icons/...") — a path resolved relative to the
    process's current working directory, not this file's location. A
    Start Menu shortcut or Task Scheduler launch with a different cwd
    silently produced blank buttons (QIcon just returns a null icon on a
    missing file, no exception). Regression-tested here by actually
    chdir-ing away from the repo root before constructing the widget.
    """

    def setUp(self):
        _get_app()
        self._orig_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp()
        os.chdir(self._tmp)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def test_icons_load_regardless_of_cwd(self):
        from app.ui.segment_widget import SegmentWidget

        segment = TranscriptSegment(start=0.0, end=1.0, text="hello", speaker="SPEAKER_00")
        widget = SegmentWidget(0, segment, has_audio=True)

        self.assertFalse(widget.play_btn.icon().isNull())
        self.assertFalse(widget.edit_affordance.icon().isNull())

        widget.set_playing(True)
        self.assertFalse(widget.play_btn.icon().isNull())


if __name__ == "__main__":
    unittest.main()
