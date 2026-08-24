"""Tests that MainWindow._update_preflight actually feeds real state into
the capture bar's PreflightWidget and "CAPTURING" sources block. Before this,
set_verdict()/set_capturing() had zero callers anywhere in the app — the
verdict always showed hardcoded "Ready" placeholders regardless of
device/mismatch/token state.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication

from app.ui.recording_controls import RecordingControls
from app.ui.transcript_viewer import TranscriptViewer
from app.utils.preflight_status import READY, WARNING, BLOCKED
from app.utils.mic_level_tracker import MicLevelTracker

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class _StubSourceSelector:
    """Stands in for the real (device-enumerating) SourceSelector so this
    test exercises MainWindow's wiring, not Windows audio devices."""

    def __init__(self):
        self.mic = 1
        self.mic_name = "fifine SC3"
        self.source_name = "All system audio"
        self.per_app = True
        self.app_pids = [100]
        self.loopback = 10
        self.mic_mismatch = None
        self.output_mismatch = None
        self.conferencing_blocked = False

    def get_selected_mic(self):
        return self.mic

    def get_selected_mic_name(self):
        return self.mic_name

    def get_selected_source_name(self):
        return self.source_name

    def is_per_app_mode(self):
        return self.per_app

    def get_selected_app_pids(self):
        return self.app_pids

    def get_selected_loopback(self):
        return self.loopback

    def is_conferencing_blocked(self):
        return self.conferencing_blocked


class _StubConfig:
    def __init__(self, hf_token="", engine="pyannote"):
        self._hf_token = hf_token
        self._engine = engine

    def get(self, section, key):
        if (section, key) == ("diarization", "engine"):
            return self._engine
        if (section, key) == ("diarization", "hf_token"):
            return self._hf_token
        raise KeyError((section, key))


class TestUpdatePreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self, hf_token=""):
        from app.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.recording_controls = RecordingControls()
        window.transcript_viewer = TranscriptViewer()
        window.source_selector = _StubSourceSelector()
        window.config = _StubConfig(hf_token=hf_token)
        window._mic_level_tracker = MicLevelTracker()
        window.compact_strip = SimpleNamespace(set_subtitle=lambda text: None)
        return window

    def test_all_clear_is_ready_verdict(self):
        window = self._make_window()
        window._update_preflight()

        preflight = window.recording_controls.preflight
        self.assertIn("Ready", preflight.verdict_title.text())
        self.assertEqual(
            window.recording_controls.capturing_mic_name.text(), "fifine SC3"
        )
        self.assertEqual(
            window.recording_controls.capturing_call_name.text(), "All system audio"
        )

    def test_no_mic_selected_blocks(self):
        window = self._make_window()
        window.source_selector.mic = None
        window._update_preflight()

        self.assertIn(
            "No microphone",
            window.recording_controls.preflight.verdict_title.text(),
        )

    def test_conferencing_app_blocks_the_call_check(self):
        window = self._make_window()
        window.source_selector.conferencing_blocked = True
        window._update_preflight()

        self.assertIn(
            "blocks per-app capture",
            window.recording_controls.preflight.verdict_title.text(),
        )

    def test_mic_mismatch_surfaces_the_app_name(self):
        window = self._make_window()
        window.source_selector.mic_mismatch = {"app": "Microsoft Teams", "device": "Jabra"}
        window._update_preflight()

        self.assertIn(
            "Microsoft Teams",
            window.recording_controls.preflight.verdict_title.text(),
        )

    def test_diarization_enabled_without_token_warns(self):
        window = self._make_window(hf_token="")
        window.transcript_viewer.set_diarization_available(True)
        window.transcript_viewer.set_diarization_enabled(True)
        window._update_preflight()

        self.assertIn(
            "HuggingFace",
            window.recording_controls.preflight.verdict_title.text(),
        )

    def test_no_source_selected_shows_no_source_in_capturing_block(self):
        window = self._make_window()
        window.source_selector.source_name = None
        window._update_preflight()

        self.assertEqual(
            window.recording_controls.capturing_call_name.text(), "No source"
        )

    def test_quiet_mic_warns_the_verdict(self):
        window = self._make_window()
        fake_now = [0.0]
        window._mic_level_tracker = MicLevelTracker(clock=lambda: fake_now[0])
        import numpy as np
        quiet_chunk = np.full(160, 0.001, dtype=np.float32)  # well under -40 dB
        window._mic_level_tracker.ingest(quiet_chunk)
        fake_now[0] += 2.0  # past the tracker's min-sample window
        window._mic_level_tracker.ingest(quiet_chunk)

        window._update_preflight()

        self.assertEqual(
            window.recording_controls.preflight.verdict_title.text(),
            "Mic is very quiet",
        )
        self.assertIn(
            "fifine SC3", window.recording_controls.preflight.verdict_subtitle.text()
        )

    def test_fresh_tracker_with_no_samples_does_not_warn(self):
        """No samples yet (app just opened) must read as ready, not quiet."""
        window = self._make_window()
        window._update_preflight()

        self.assertIn("Ready", window.recording_controls.preflight.verdict_title.text())

    def test_sources_button_opens_the_dialog(self):
        from app.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.recording_controls = RecordingControls()
        opened = []
        window.source_selector = SimpleNamespace(
            show=lambda: opened.append("show"),
            raise_=lambda: opened.append("raise"),
            activateWindow=lambda: opened.append("activate"),
        )
        window._open_source_selector()
        self.assertEqual(opened, ["show", "raise", "activate"])

    def test_capturing_block_click_opens_the_dialog(self):
        window_controls = RecordingControls()
        clicks = []
        window_controls.sources_clicked.connect(lambda: clicks.append(1))
        window_controls.capturing_block.clicked.emit()
        self.assertEqual(clicks, [1])


if __name__ == "__main__":
    unittest.main()
