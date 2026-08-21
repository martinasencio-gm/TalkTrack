"""Tests for SegmentWidget audio availability and play button visibility."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from PyQt6.QtWidgets import QApplication

from app.transcription.transcriber import TranscriptSegment
from app.ui.segment_widget import SegmentWidget

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSegmentWidgetAudioAvailability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_segment_widget_has_audio_true_shows_play_button(self):
        seg = TranscriptSegment(start=0.0, end=5.0, text="Hello world", speaker="SPEAKER_00")
        widget = SegmentWidget(index=0, segment=seg, has_audio=True)
        self.assertFalse(widget.play_btn.isHidden())

    def test_segment_widget_has_audio_false_hides_play_button(self):
        seg = TranscriptSegment(start=0.0, end=5.0, text="Hello world", speaker="SPEAKER_00")
        widget = SegmentWidget(index=0, segment=seg, has_audio=False)
        self.assertTrue(widget.play_btn.isHidden())

    def test_set_has_audio_toggles_play_button_visibility(self):
        seg = TranscriptSegment(start=0.0, end=5.0, text="Hello world", speaker="SPEAKER_00")
        widget = SegmentWidget(index=0, segment=seg, has_audio=True)
        self.assertFalse(widget.play_btn.isHidden())

        widget.set_has_audio(False)
        self.assertTrue(widget.play_btn.isHidden())

        widget.set_has_audio(True)
        self.assertFalse(widget.play_btn.isHidden())

    def test_set_has_audio_false_resets_playing_state_if_active(self):
        seg = TranscriptSegment(start=0.0, end=5.0, text="Hello world", speaker="SPEAKER_00")
        widget = SegmentWidget(index=0, segment=seg, has_audio=True)
        widget.set_playing(True)
        self.assertTrue(widget._playing)

        widget.set_has_audio(False)
        self.assertFalse(widget._playing)
        self.assertTrue(widget.play_btn.isHidden())


if __name__ == "__main__":
    unittest.main()
