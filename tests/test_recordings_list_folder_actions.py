import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class _FakeConfig:
    def __init__(self, transcripts_dir):
        self._transcripts_dir = transcripts_dir

    def get(self, *keys):
        assert keys == ("transcripts", "directory")
        return self._transcripts_dir


class TestRecordingsListFolderActions(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()
        self.transcripts_dir = Path(self.tmp) / "transcripts"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_construction_without_config_does_not_crash(self):
        widget = RecordingsList(self.recordings_dir)
        self.assertIsNone(widget.config)

    def test_open_folder_opens_given_directory(self):
        widget = RecordingsList(self.recordings_dir)
        with patch("app.ui.recordings_list.os.startfile") as mock_start:
            widget._open_folder(str(self.recordings_dir))
        mock_start.assert_called_once_with(str(self.recordings_dir))

    def test_open_transcripts_folder_uses_config_directory(self):
        config = _FakeConfig(str(self.transcripts_dir))
        widget = RecordingsList(self.recordings_dir, config=config)
        with patch("app.ui.recordings_list.os.startfile") as mock_start:
            widget._open_transcripts_folder()
        mock_start.assert_called_once_with(str(self.transcripts_dir))
        self.assertTrue(self.transcripts_dir.exists())

    def test_open_transcripts_folder_without_config_is_a_noop(self):
        widget = RecordingsList(self.recordings_dir)
        with patch("app.ui.recordings_list.os.startfile") as mock_start:
            widget._open_transcripts_folder()
        mock_start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
