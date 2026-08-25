import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.ui.tag_recording_dialog import TagRecordingDialog, _CreateTagPill
from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestTagRecordingDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.tags_file = self.root / "tags.json"
        self.recordings_dir = self.root / "recordings"
        self.recordings_dir.mkdir()

        # Create dummy session
        self.session_dir = self.recordings_dir / "recording_1"
        self.session_dir.mkdir()
        self.metadata = {
            "id": "1",
            "name": "Eugen Gitin",
            "duration": 476,
            "directory": str(self.session_dir),
            "tags": ["Gresham"],
        }
        # Pre-populate tags
        tag_manager.create_tag("Gresham", color="#89b4fa", tags_file=self.tags_file)
        tag_manager.add_tag_to_recording(self.session_dir, "Gresham", tags_file=self.tags_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_button_creates_and_assigns_new_tag(self):
        dialog = TagRecordingDialog(
            self.metadata, self.recordings_dir, tags_file=self.tags_file
        )
        dialog.filter_input.setText("Acme Corp")
        self.assertTrue(dialog.add_btn.isEnabled())

        # Click add button
        dialog.add_btn.click()

        # Tag should be assigned to recording and created globally
        self.assertIn("Acme Corp", dialog._assigned)
        all_tags = [t["name"] for t in tag_manager.load_all_tags(tags_file=self.tags_file)]
        self.assertIn("Acme Corp", all_tags)
        self.assertEqual(dialog.filter_input.text(), "")
        dialog.close()

    def test_return_pressed_creates_and_assigns_new_tag(self):
        dialog = TagRecordingDialog(
            self.metadata, self.recordings_dir, tags_file=self.tags_file
        )
        dialog.filter_input.setText("New Tag Via Enter")
        dialog.filter_input.returnPressed.emit()

        self.assertIn("New Tag Via Enter", dialog._assigned)
        all_tags = [t["name"] for t in tag_manager.load_all_tags(tags_file=self.tags_file)]
        self.assertIn("New Tag Via Enter", all_tags)
        dialog.close()

    def test_create_pill_appears_and_creates_tag_on_click(self):
        dialog = TagRecordingDialog(
            self.metadata, self.recordings_dir, tags_file=self.tags_file
        )
        dialog.filter_input.setText("BrandNewTag")

        # Find _CreateTagPill in _all_flow
        create_pills = [
            dialog._all_flow.itemAt(i).widget()
            for i in range(dialog._all_flow.count())
            if isinstance(dialog._all_flow.itemAt(i).widget(), _CreateTagPill)
        ]
        self.assertEqual(len(create_pills), 1)
        create_pill = create_pills[0]
        self.assertEqual(create_pill.tag_name, "BrandNewTag")

        # Click the create pill
        create_pill.clicked.emit("BrandNewTag")

        self.assertIn("BrandNewTag", dialog._assigned)
        all_tags = [t["name"] for t in tag_manager.load_all_tags(tags_file=self.tags_file)]
        self.assertIn("BrandNewTag", all_tags)
        dialog.close()

    def test_accept_auto_commits_unsubmitted_text(self):
        dialog = TagRecordingDialog(
            self.metadata, self.recordings_dir, tags_file=self.tags_file
        )
        dialog.filter_input.setText("DoneTag")
        dialog.accept()

        self.assertIn("DoneTag", dialog._assigned)
        all_tags = [t["name"] for t in tag_manager.load_all_tags(tags_file=self.tags_file)]
        self.assertIn("DoneTag", all_tags)
        self.assertIn("DoneTag", self.metadata.get("tags", []))
        dialog.close()


if __name__ == "__main__":
    unittest.main()
