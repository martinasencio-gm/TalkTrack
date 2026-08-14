import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowBatchTranscript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            # Skip the exit-confirmation dialog: on this machine, its nested
            # event loop lets the source selector's audio-session refresh
            # timer fire mid-teardown, which crashes (access violation) deep
            # in pycaw/comtypes COM cleanup — unrelated to the code under
            # test. Setting _really_quit bypasses that dialog entirely.
            window._really_quit = True
            window.close()

        self.addCleanup(_close)
        return window

    def test_on_transcribe_selected_calls_start_transcription_per_recording_with_audio(self):
        window = self._make_window()
        recordings = [
            {"directory": "/r1", "audio_files": {"combined": "/r1/combined_audio.wav"}},
            {"directory": "/r2", "audio_files": {"mic": "/r2/mic_audio.wav"}},
        ]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        self.assertEqual(mock_start.call_count, 2)
        mock_start.assert_any_call("/r1/combined_audio.wav", session=recordings[0])
        mock_start.assert_any_call("/r2/mic_audio.wav", session=recordings[1])

    def test_on_transcribe_selected_skips_recordings_with_no_audio_files(self):
        window = self._make_window()
        recordings = [{"directory": "/r1", "audio_files": {}}]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        mock_start.assert_not_called()

    def test_on_transcribe_selected_prefers_combined_over_system_over_mic(self):
        window = self._make_window()
        recordings = [{
            "directory": "/r1",
            "audio_files": {
                "mic": "/r1/mic_audio.wav",
                "system": "/r1/system_audio.wav",
                "combined": "/r1/combined_audio.wav",
            },
        }]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        mock_start.assert_called_once_with("/r1/combined_audio.wav", session=recordings[0])

    def test_on_export_selected_calls_export_transcript_per_recording(self):
        window = self._make_window()
        recordings = [{"directory": "/r1"}, {"directory": "/r2"}]
        with patch.object(window, "_export_transcript") as mock_export:
            window._on_export_selected(recordings)
        self.assertEqual(mock_export.call_count, 2)
        mock_export.assert_any_call(recordings[0])
        mock_export.assert_any_call(recordings[1])


if __name__ == "__main__":
    unittest.main()
