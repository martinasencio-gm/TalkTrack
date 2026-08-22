"""Tests for TagPromptBanner post-recording prompt component."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.ui.tag_prompt_banner import TagPromptBanner
from app.utils import tag_manager

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestTagPromptBanner(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.session_dir = Path(self.tmp) / "rec_new"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.meta = {
            "id": "rec_new",
            "directory": str(self.session_dir),
            "name": "New Finished Recording",
            "tags": [],
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(self.meta), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_show_prompt_and_toggle_tag(self):
        banner = TagPromptBanner()
        banner.show_prompt(self.meta)

        self.assertFalse(banner.isHidden())

        emitted = []
        banner.tags_updated.connect(emitted.append)

        # Toggle tag
        banner._toggle_tag("Sprint")

        self.assertIn("Sprint", banner._assigned_tags)
        self.assertEqual(len(emitted), 1)
        self.assertIn("Sprint", emitted[0])

        meta_saved = json.loads((self.session_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertIn("Sprint", meta_saved["tags"])

        # Toggle again to remove
        banner._toggle_tag("Sprint")
        self.assertNotIn("Sprint", banner._assigned_tags)

    def test_dismiss_banner(self):
        banner = TagPromptBanner()
        banner.show_prompt(self.meta)

        dismissed = []
        banner.dismissed.connect(lambda: dismissed.append(True))

        banner._on_done()

        self.assertTrue(banner.isHidden())
        self.assertEqual(len(dismissed), 1)


if __name__ == "__main__":
    unittest.main()
