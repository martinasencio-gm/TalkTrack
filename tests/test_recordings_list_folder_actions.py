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


class TestRecordingsListFolderActions(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_open_folder_opens_given_directory(self):
        widget = RecordingsList(self.recordings_dir)
        with patch("app.ui.recordings_list.os.startfile") as mock_start:
            widget._open_folder(str(self.recordings_dir))
        mock_start.assert_called_once_with(str(self.recordings_dir))


if __name__ == "__main__":
    unittest.main()
