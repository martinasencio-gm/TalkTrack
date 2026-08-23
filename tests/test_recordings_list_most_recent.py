"""Tests for RecordingsList.most_recent_recording(), which backs the
transcript column's "Open the last one" empty-state button.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestMostRecentRecording(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, name):
        d = self.recordings_dir / name
        d.mkdir()
        metadata = {
            "directory": str(d), "name": name,
            "started_at": "2026-08-14T10:00:00", "duration": 60, "audio_files": {},
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def test_no_recordings_returns_none(self):
        widget = RecordingsList(self.recordings_dir)
        self.assertIsNone(widget.most_recent_recording())

    def test_returns_the_newest_by_folder_name(self):
        # Folder names sort newest-last alphabetically here (recording_1 <
        # recording_2), and refresh() lists reverse=True, so "recording_2"
        # is what should come back.
        self._make_session("recording_1")
        self._make_session("recording_2")
        widget = RecordingsList(self.recordings_dir)
        result = widget.most_recent_recording()
        self.assertEqual(result["name"], "recording_2")

    def test_survives_an_active_search(self):
        # A live search only flips _showing_search_results — it must not
        # blank out self._recordings, or "open the last one" would break
        # while the user is mid-search.
        self._make_session("recording_1")
        widget = RecordingsList(self.recordings_dir)
        widget._showing_search_results = True
        self.assertIsNotNone(widget.most_recent_recording())


if __name__ == "__main__":
    unittest.main()
