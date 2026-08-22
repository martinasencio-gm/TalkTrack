"""Tests for TagManagerDialog."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.ui.tag_manager_dialog import TagManagerDialog
from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestTagManagerDialog(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.sess = self.recordings_dir / "rec_1"
        self.sess.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": "rec_1",
            "directory": str(self.sess),
            "name": "Rec 1",
            "tags": ["Meeting"],
        }
        (self.sess / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        self.tags_file = Path(self.tmp) / "tags.json"
        tag_manager.create_tag("Meeting", color="#89b4fa", tags_file=self.tags_file)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dialog_loads_tags_and_counts(self):
        dlg = TagManagerDialog(recordings_dir=self.recordings_dir, tags_file=self.tags_file)
        self.assertGreater(dlg.table.rowCount(), 0)

        found_meeting = False
        for row in range(dlg.table.rowCount()):
            name = dlg.table.item(row, 1).text()
            if name == "Meeting":
                count_str = dlg.table.item(row, 2).text()
                self.assertIn("1 recording", count_str)
                found_meeting = True
                break
        self.assertTrue(found_meeting)


if __name__ == "__main__":
    unittest.main()
