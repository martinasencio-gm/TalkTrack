"""Tests for confirmation prompts when requesting transcription or batch queueing
for recordings that already have an existing transcription."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestTranscriptionOverwriteConfirmation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _create_recording(self, name, has_transcript=True):
        session_dir = self.tmp_dir / name
        session_dir.mkdir(parents=True, exist_ok=True)
        audio_path = session_dir / "combined_audio.wav"
        audio_path.write_bytes(b"RIFF dummy wav data")

        metadata = {
            "name": name,
            "directory": str(session_dir),
            "audio_files": {"combined": str(audio_path)},
        }
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        if has_transcript:
            (session_dir / "transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")

        return metadata

    def test_viewer_transcribe_requested_prompts_and_aborts_on_no(self):
        from app.main_window import MainWindow
        window = MainWindow()
        self.addCleanup(lambda: (setattr(window, "_really_quit", True), window.close()))

        rec = self._create_recording("rec1", has_transcript=True)
        window._current_session = rec

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as mock_q, \
             patch.object(window, "_start_transcription") as mock_start:
            window._on_viewer_transcribe_requested(rec["audio_files"]["combined"])
            mock_q.assert_called_once()
            mock_start.assert_not_called()

    def test_viewer_transcribe_requested_prompts_and_proceeds_on_yes(self):
        from app.main_window import MainWindow
        window = MainWindow()
        self.addCleanup(lambda: (setattr(window, "_really_quit", True), window.close()))

        rec = self._create_recording("rec1", has_transcript=True)
        window._current_session = rec

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as mock_q, \
             patch.object(window, "_start_transcription") as mock_start:
            window._on_viewer_transcribe_requested(rec["audio_files"]["combined"])
            mock_q.assert_called_once()
            mock_start.assert_called_once_with(rec["audio_files"]["combined"], session=rec)

    def test_viewer_transcribe_requested_without_existing_transcript_does_not_prompt(self):
        from app.main_window import MainWindow
        window = MainWindow()
        self.addCleanup(lambda: (setattr(window, "_really_quit", True), window.close()))

        rec = self._create_recording("rec1", has_transcript=False)
        window._current_session = rec

        with patch("PyQt6.QtWidgets.QMessageBox.question") as mock_q, \
             patch.object(window, "_start_transcription") as mock_start:
            window._on_viewer_transcribe_requested(rec["audio_files"]["combined"])
            mock_q.assert_not_called()
            mock_start.assert_called_once_with(rec["audio_files"]["combined"], session=rec)

    def test_recordings_list_queue_transcription_prompts_and_aborts_on_no(self):
        from app.ui.recordings_list import RecordingsList
        from app.utils import batch_queue

        list_widget = RecordingsList(recordings_dir=self.tmp_dir)
        self.addCleanup(list_widget.deleteLater)

        rec = self._create_recording("rec_transcribed", has_transcript=True)

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as mock_q:
            list_widget._toggle_batch_op([rec], "transcription", True)
            mock_q.assert_called_once()

        self.assertFalse(batch_queue.is_queued(rec))

    def test_recordings_list_queue_transcription_prompts_and_proceeds_on_yes(self):
        from app.ui.recordings_list import RecordingsList
        from app.utils import batch_queue

        list_widget = RecordingsList(recordings_dir=self.tmp_dir)
        self.addCleanup(list_widget.deleteLater)

        rec = self._create_recording("rec_transcribed", has_transcript=True)

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as mock_q:
            list_widget._toggle_batch_op([rec], "transcription", True)
            mock_q.assert_called_once()

        updated_meta = json.loads((Path(rec["directory"]) / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(batch_queue.is_queued(updated_meta))
        self.assertEqual(batch_queue.queued_ops(updated_meta), ["transcription"])

    def test_recordings_list_queue_transcription_untranscribed_does_not_prompt(self):
        from app.ui.recordings_list import RecordingsList
        from app.utils import batch_queue

        list_widget = RecordingsList(recordings_dir=self.tmp_dir)
        self.addCleanup(list_widget.deleteLater)

        rec = self._create_recording("rec_untranscribed", has_transcript=False)

        with patch("PyQt6.QtWidgets.QMessageBox.question") as mock_q:
            list_widget._toggle_batch_op([rec], "transcription", True)
            mock_q.assert_not_called()

        updated_meta = json.loads((Path(rec["directory"]) / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(batch_queue.is_queued(updated_meta))

    def test_recordings_list_queue_summarization_over_transcript_does_not_prompt(self):
        from app.ui.recordings_list import RecordingsList
        from app.utils import batch_queue

        list_widget = RecordingsList(recordings_dir=self.tmp_dir)
        self.addCleanup(list_widget.deleteLater)

        rec = self._create_recording("rec_transcribed", has_transcript=True)

        with patch("PyQt6.QtWidgets.QMessageBox.question") as mock_q:
            list_widget._toggle_batch_op([rec], "summarization", True)
            mock_q.assert_not_called()

        updated_meta = json.loads((Path(rec["directory"]) / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(batch_queue.queued_ops(updated_meta), ["summarization"])


if __name__ == "__main__":
    unittest.main()
