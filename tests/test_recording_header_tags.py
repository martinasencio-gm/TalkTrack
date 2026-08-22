"""Tests for RecordingHeader tag chips, removal, and signals."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.ui.recording_header import RecordingHeader
from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingHeaderTags(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.session_dir = Path(self.tmp) / "rec_1"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.meta = {
            "id": "rec_1",
            "directory": str(self.session_dir),
            "name": "Test Call",
            "tags": ["Meeting", "Follow-up"],
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(self.meta), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_recording_loads_tags(self):
        header = RecordingHeader()
        header.set_recording(self.meta)

        self.assertEqual(header.tags_layout.count(), 2)
        chip1 = header.tags_layout.itemAt(0).widget()
        chip2 = header.tags_layout.itemAt(1).widget()

        self.assertEqual(chip1.tag_name, "Meeting")
        self.assertEqual(chip2.tag_name, "Follow-up")

    def test_remove_tag_updates_metadata_and_emits_signal(self):
        header = RecordingHeader()
        header.set_recording(self.meta)

        emitted = []
        header.tags_changed.connect(emitted.append)

        header._on_remove_tag("Meeting")

        self.assertEqual(header.tags_layout.count(), 1)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0], ["Follow-up"])

        meta_saved = json.loads((self.session_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_saved["tags"], ["Follow-up"])


if __name__ == "__main__":
    unittest.main()
