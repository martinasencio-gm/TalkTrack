"""Tests for TranscriptViewer when audio is missing or deleted."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.ui.transcript_viewer import TranscriptViewer

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestTranscriptViewerMissingAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _sample_transcript(self):
        return TranscriptResult(
            segments=[
                TranscriptSegment(start=0.0, end=4.0, text="First segment", speaker="SPEAKER_00"),
                TranscriptSegment(start=4.5, end=8.0, text="Second segment", speaker="SPEAKER_01"),
            ],
            language="en",
            duration=8.0,
        )

    def test_display_transcript_without_audio_hides_segment_play_buttons(self):
        viewer = TranscriptViewer()
        viewer.set_audio_path(None)
        transcript = self._sample_transcript()
        viewer.display_transcript(transcript)

        self.assertEqual(len(viewer._segment_widgets), 2)
        for widget in viewer._segment_widgets:
            self.assertTrue(widget.play_btn.isHidden())

        self.assertFalse(viewer.play_all_btn.isEnabled())
        self.assertFalse(viewer.continue_from_cb.isEnabled())

    def test_main_window_selected_recording_with_deleted_audio_hides_play_buttons(self):
        from app.main_window import MainWindow
        import json

        session_dir = self.tmp_dir / "recording_20260101_100000"
        session_dir.mkdir()
        metadata = {
            "directory": str(session_dir),
            "audio_files": {
                "combined": str(session_dir / "combined_audio.wav"),
                "system": str(session_dir / "system_audio.wav"),
                "mic": str(session_dir / "mic_audio.wav"),
            },
        }
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        transcript_data = {
            "segments": [
                {"start": 0.0, "end": 4.0, "text": "Segment 1", "speaker": "SPEAKER_00"},
            ],
            "language": "en",
            "duration": 4.0,
        }
        (session_dir / "transcript.json").write_text(json.dumps(transcript_data), encoding="utf-8")

        # Audio files do NOT exist on disk (they were deleted)
        window = MainWindow()

        def _close():
            window._really_quit = True
            window.close()

        self.addCleanup(_close)

        window._on_recording_selected(metadata)

        self.assertIsNone(window.transcript_viewer._audio_path)
        self.assertEqual(len(window.transcript_viewer._segment_widgets), 1)
        self.assertTrue(window.transcript_viewer._segment_widgets[0].play_btn.isHidden())
        self.assertFalse(window.transcript_viewer.play_all_btn.isEnabled())

    def test_display_transcript_with_valid_audio_shows_play_buttons(self):
        valid_audio = self.tmp_dir / "valid_audio.wav"
        valid_audio.write_bytes(b"RIFF dummy wav data")

        viewer = TranscriptViewer()
        viewer.set_audio_path(str(valid_audio))
        transcript = self._sample_transcript()
        viewer.display_transcript(transcript)

        self.assertEqual(len(viewer._segment_widgets), 2)
        for widget in viewer._segment_widgets:
            self.assertFalse(widget.play_btn.isHidden())

        self.assertTrue(viewer.play_all_btn.isEnabled())
        self.assertTrue(viewer.continue_from_cb.isEnabled())

    def test_set_audio_path_after_display_updates_existing_widgets(self):
        viewer = TranscriptViewer()
        transcript = self._sample_transcript()
        viewer.display_transcript(transcript)

        # Initially no audio
        for widget in viewer._segment_widgets:
            self.assertTrue(widget.play_btn.isHidden())

        # Audio created/loaded
        valid_audio = self.tmp_dir / "valid_audio.wav"
        valid_audio.write_bytes(b"RIFF dummy wav data")
        viewer.set_audio_path(str(valid_audio))

        for widget in viewer._segment_widgets:
            self.assertFalse(widget.play_btn.isHidden())
        self.assertTrue(viewer.play_all_btn.isEnabled())

        # Audio deleted
        viewer.set_audio_path(None)
        for widget in viewer._segment_widgets:
            self.assertTrue(widget.play_btn.isHidden())
        self.assertFalse(viewer.play_all_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
