import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestWorkerThreadPriority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            window.close()
        self.addCleanup(_close)
        return window

    def test_start_transcription_uses_low_priority(self):
        window = self._make_window()
        with patch("app.main_window.TranscriptionWorker") as MockWorker:
            mock_instance = MockWorker.return_value
            window._start_transcription("/some/audio.wav", session={"directory": "/r1"})
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)

    def test_start_diarization_uses_low_priority(self):
        window = self._make_window()
        session = {"directory": "/r1", "audio_files": {"combined": "/r1/combined_audio.wav"}}
        transcript_result = MagicMock()
        with patch("app.main_window.DiarizationWorker") as MockWorker, \
             patch.object(window, "config") as mock_config:
            mock_config.get.side_effect = lambda *keys: {
                ("diarization", "hf_token"): "fake-token",
                ("diarization", "min_speakers"): None,
                ("diarization", "max_speakers"): None,
            }.get(keys, None)
            mock_instance = MockWorker.return_value
            window._start_diarization(transcript_result, session)
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)

    def test_start_simple_diarization_uses_low_priority(self):
        window = self._make_window()
        transcript_result = MagicMock()
        with patch("app.main_window.SimpleDiarizeWorker") as MockWorker:
            mock_instance = MockWorker.return_value
            window._start_simple_diarization(
                transcript_result, {"directory": "/r1"}, "/r1/mic_audio.wav", "/r1/system_audio.wav"
            )
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)


if __name__ == "__main__":
    unittest.main()
