"""Unit tests for autotagging by matching previous recording names."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestAutoTagByName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        self.tags_file = Path(self.tmp) / "tags.json"
        tag_manager.create_tag("Weekly Sync", color="#89b4fa", tags_file=self.tags_file)
        tag_manager.create_tag("Important", color="#f38ba8", tags_file=self.tags_file)

        # Create past recording with name and tag
        self.past_dir = self.recordings_dir / "rec_past"
        self.past_dir.mkdir(parents=True, exist_ok=True)
        meta_past = {
            "id": "rec_past",
            "directory": str(self.past_dir),
            "name": "Team Standup",
            "tags": ["Weekly Sync", "Important"],
        }
        (self.past_dir / "metadata.json").write_text(json.dumps(meta_past), encoding="utf-8")

        # Create current recording without tags
        self.curr_dir = self.recordings_dir / "rec_curr"
        self.curr_dir.mkdir(parents=True, exist_ok=True)
        self.meta_curr = {
            "id": "rec_curr",
            "directory": str(self.curr_dir),
            "name": "Untitled Recording",
            "tags": [],
        }
        (self.curr_dir / "metadata.json").write_text(json.dumps(self.meta_curr), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_window(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_check_startup_status"):
            window = MainWindow()
        window.config.set("output", "directory", str(self.recordings_dir))
        window.config.set("general", "auto_tag_by_name", True)
        window.config.set("general", "prompt_tags_after_recording", False)
        window.config.set("diarization", "hf_token", "dummy_token")
        window.recordings_list.recordings_dir = self.recordings_dir

        def _close():
            window._really_quit = True
            window.close()

        self.addCleanup(_close)
        return window

    def test_autotag_on_rename_untagged_recording(self):
        window = self._make_window()
        window._current_session = dict(self.meta_curr)

        # Rename to "Team Standup"
        window._on_recording_renamed("Team Standup")

        # Verify current session got autotagged
        self.assertEqual(window._current_session["tags"], ["Weekly Sync", "Important"])
        meta_saved = json.loads((self.curr_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_saved["tags"], ["Weekly Sync", "Important"])

    def test_suggest_retag_when_already_tagged_and_accepted(self):
        # Give current recording existing tag "Different"
        self.meta_curr["tags"] = ["Different"]
        (self.curr_dir / "metadata.json").write_text(json.dumps(self.meta_curr), encoding="utf-8")

        window = self._make_window()
        window._current_session = dict(self.meta_curr)

        # Mock user clicking "Yes" on retag suggestion dialog
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window._on_recording_renamed("Team Standup")

        # Verify tags updated to matching
        self.assertEqual(window._current_session["tags"], ["Weekly Sync", "Important"])
        meta_saved = json.loads((self.curr_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_saved["tags"], ["Weekly Sync", "Important"])

    def test_suggest_retag_when_already_tagged_and_declined(self):
        # Give current recording existing tag "Different"
        self.meta_curr["tags"] = ["Different"]
        (self.curr_dir / "metadata.json").write_text(json.dumps(self.meta_curr), encoding="utf-8")

        window = self._make_window()
        window._current_session = dict(self.meta_curr)

        # Mock user clicking "No" on retag suggestion dialog
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            window._on_recording_renamed("Team Standup")

        # Verify tags remained "Different"
        self.assertEqual(window._current_session["tags"], ["Different"])
        meta_saved = json.loads((self.curr_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_saved["tags"], ["Different"])

    def test_autotag_on_recording_finished(self):
        window = self._make_window()
        session = dict(self.meta_curr)
        session["name"] = "Team Standup"
        (self.curr_dir / "metadata.json").write_text(json.dumps(session), encoding="utf-8")

        window._on_recording_finished(session)

        self.assertEqual(session["tags"], ["Weekly Sync", "Important"])
        meta_saved = json.loads((self.curr_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_saved["tags"], ["Weekly Sync", "Important"])


if __name__ == "__main__":
    unittest.main()
