import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListBatchTranscript(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_recording(self, name, transcribed):
        directory = self.recordings_dir / name
        directory.mkdir()
        metadata = {
            "id": name,
            "directory": str(directory),
            "name": name,
            "started_at": "2026-08-14T10:00:00",
            "duration": 5.0,
            "audio_files": {"combined": str(directory / "combined_audio.wav")},
        }
        with open(directory / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)
        if transcribed:
            with open(directory / "transcript.json", "w", encoding="utf-8") as f:
                json.dump({"segments": []}, f)
        return metadata

    def _select_all(self, widget):
        widget.list_widget.selectAll()
        return widget.list_widget.selectedItems()

    def test_selected_untranscribed_returns_only_recordings_without_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_untranscribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_a")

    def test_selected_transcribed_returns_only_recordings_with_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_transcribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_b")

    def test_transcribe_selected_requested_emits_untranscribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.transcribe_selected_requested.connect(seen.append)
        untranscribed = widget._selected_untranscribed(items)
        widget.transcribe_selected_requested.emit(untranscribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_a")

    def test_export_selected_requested_emits_transcribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.export_selected_requested.connect(seen.append)
        transcribed = widget._selected_transcribed(items)
        widget.export_selected_requested.emit(transcribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_b")

    def test_selected_untranscribed_empty_when_all_transcribed(self):
        self._make_recording("rec_a", transcribed=True)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_untranscribed(items), [])

    def test_selected_transcribed_empty_when_none_transcribed(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=False)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_transcribed(items), [])


if __name__ == "__main__":
    unittest.main()
