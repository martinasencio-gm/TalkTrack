"""RecordingControls' recording-state row used to show a bare
QLabel("[ Meters ]") placeholder with no data ever fed into it. This
confirms it now hosts a real LevelMeter wired up to receive audio chunks
(the actual dB math is already covered by tests/test_level_meter.py).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

import numpy as np
from PyQt6.QtWidgets import QApplication

from app.ui.level_meter import LevelMeter
from app.ui.recording_controls import RecordingControls

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestRecordingControlsLiveMeters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_no_more_placeholder_label(self):
        controls = RecordingControls()
        self.assertFalse(hasattr(controls, "meters_placeholder"))

    def test_live_meters_is_a_real_level_meter(self):
        controls = RecordingControls()
        self.assertIsInstance(controls.live_meters, LevelMeter)

    def test_live_meters_reacts_to_audio_chunks(self):
        controls = RecordingControls()
        loud = np.full(256, 0.9, dtype=np.float32)
        controls.live_meters.update_mic_level(loud)
        self.assertGreater(controls.live_meters._mic_bar._level, 0.0)

    def test_rec_source_block_click_emits_sources_clicked(self):
        controls = RecordingControls()
        clicked = []
        controls.sources_clicked.connect(lambda: clicked.append(True))
        controls.rec_source_block.clicked.emit()
        self.assertEqual(clicked, [True])

    def test_live_meters_not_hidden_by_default(self):
        controls = RecordingControls()
        self.assertFalse(controls.live_meters.isHidden())


if __name__ == "__main__":
    unittest.main()
