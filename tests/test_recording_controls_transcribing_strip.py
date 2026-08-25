"""The transcribing strip's label was hardcoded to "Transcribing <name>"
even while a diarization job (not a transcription job) was the one
actually running — so the percent/elapsed/left figures were real, but the
verb describing them was wrong. `set_transcribing` now takes a phase_label
so callers can say "Identifying speakers" while diarization is busy.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.recording_controls import RecordingControls

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestTranscribingStripPhaseLabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_defaults_to_transcribing_label(self):
        controls = RecordingControls()
        controls.set_transcribing(True, percent=50, name="recording_1")
        self.assertIn("Transcribing", controls.transcribing_label.text())
        self.assertIn("recording_1", controls.transcribing_label.text())

    def test_custom_phase_label_replaces_transcribing(self):
        controls = RecordingControls()
        controls.set_transcribing(
            True, percent=16, name="recording_1", phase_label="Identifying speakers"
        )
        text = controls.transcribing_label.text()
        self.assertIn("Identifying speakers", text)
        self.assertIn("recording_1", text)
        self.assertNotIn("Transcribing", text)

    def test_custom_phase_label_without_name(self):
        controls = RecordingControls()
        controls.set_transcribing(True, percent=16, phase_label="Identifying speakers")
        self.assertEqual(controls.transcribing_label.text(), "Identifying speakers…")


if __name__ == "__main__":
    unittest.main()
