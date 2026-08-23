"""Tests that MainWindow._update_preflight actually feeds real state into
the capture bar's PreflightWidget. Before this, update_checks()/set_verdict()
had zero callers anywhere in the app — the verdict always showed hardcoded
"Ready" placeholders regardless of device/mismatch/token state.
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
        self.per_app = True
        self.app_pids = [100]
        self.loopback = 10
        self.mic_mismatch = None
        self.output_mismatch = None
        self.conferencing_blocked = False

    def get_selected_mic(self):
        return self.mic

    def is_per_app_mode(self):
        return self.per_app

    def get_selected_app_pids(self):
        return self.app_pids

    def get_selected_loopback(self):
        return self.loopback

    def is_conferencing_blocked(self):
        return self.conferencing_blocked


class _StubConfig:
    def __init__(self, hf_token=""):
        self._hf_token = hf_token

    def get(self, section, key):
        assert (section, key) == ("diarization", "hf_token")
        return self._hf_token


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
        return window

    def test_all_clear_is_ready_verdict(self):
        window = self._make_window()
        window._update_preflight()

        preflight = window.recording_controls.preflight
        self.assertEqual(preflight.voice_val.text(), "Ready")
        self.assertEqual(preflight.call_val.text(), "Ready")
        self.assertEqual(preflight.transcription_val.text(), "Ready")
        self.assertIn("Ready", preflight.verdict_title.text())

    def test_no_mic_selected_blocks(self):
        window = self._make_window()
        window.source_selector.mic = None
        window._update_preflight()

        self.assertIn("No microphone", window.recording_controls.preflight.voice_val.text())

    def test_conferencing_app_blocks_the_call_check(self):
        window = self._make_window()
        window.source_selector.conferencing_blocked = True
        window._update_preflight()

        self.assertIn(
            "blocks per-app capture",
            window.recording_controls.preflight.call_val.text(),
        )

    def test_mic_mismatch_surfaces_the_app_name(self):
        window = self._make_window()
        window.source_selector.mic_mismatch = {"app": "Microsoft Teams", "device": "Jabra"}
        window._update_preflight()

        self.assertIn("Microsoft Teams", window.recording_controls.preflight.voice_val.text())

    def test_diarization_enabled_without_token_warns_transcription_check(self):
        window = self._make_window(hf_token="")
        window.transcript_viewer.set_diarization_available(True)
        window.transcript_viewer.set_diarization_enabled(True)
        window._update_preflight()

        self.assertIn(
            "HuggingFace",
            window.recording_controls.preflight.transcription_val.text(),
        )

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


if __name__ == "__main__":
    unittest.main()
