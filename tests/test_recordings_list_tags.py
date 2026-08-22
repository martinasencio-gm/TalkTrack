"""Tests for tag badges, tag filtering, and tag assignment in RecordingsList."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList
from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListTags(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp)
        self.tags_file = self.recordings_dir / "tags.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, name, tags=None, started_at="2026-08-21T10:00:00"):
        d = self.recordings_dir / name.replace(" ", "_")
        d.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": name.replace(" ", "_"),
            "directory": str(d),
            "name": name,
            "started_at": started_at,
            "duration": 60,
            "audio_files": {},
            "tags": tags or [],
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def test_filter_matches_on_tag(self):
        self._make_session("Call A", tags=["Backend", "Bug"])
        self._make_session("Call B", tags=["Frontend", "Design"])
        widget = RecordingsList(self.recordings_dir)
        widget.refresh()

        widget._on_filter_changed("backend")

        visible = [
            widget.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"]
            for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        ]
        self.assertEqual(visible, ["Call A"])

    def test_toggle_tag_on_recordings(self):
        meta1 = self._make_session("Rec 1", tags=["Sprint"])
        meta2 = self._make_session("Rec 2", tags=[])

        widget = RecordingsList(self.recordings_dir)
        widget.refresh()

        # Add tag "Review" to both
        widget._toggle_tag_on_recordings([meta1, meta2], "Review", True)

        m1 = json.loads((Path(meta1["directory"]) / "metadata.json").read_text(encoding="utf-8"))
        m2 = json.loads((Path(meta2["directory"]) / "metadata.json").read_text(encoding="utf-8"))

        self.assertIn("Review", m1["tags"])
        self.assertIn("Sprint", m1["tags"])
        self.assertIn("Review", m2["tags"])

        # Remove tag "Review" from both
        widget._toggle_tag_on_recordings([meta1, meta2], "Review", False)

        m1_after = json.loads((Path(meta1["directory"]) / "metadata.json").read_text(encoding="utf-8"))
        m2_after = json.loads((Path(meta2["directory"]) / "metadata.json").read_text(encoding="utf-8"))

        self.assertNotIn("Review", m1_after["tags"])
        self.assertNotIn("Review", m2_after["tags"])
        self.assertIn("Sprint", m1_after["tags"])


if __name__ == "__main__":
    unittest.main()
