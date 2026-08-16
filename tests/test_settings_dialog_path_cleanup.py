import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from app.ui.settings_dialog import SettingsDialog, _clean_path
from app.utils import config as config_module
from app.utils.config import Config

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestCleanPath(unittest.TestCase):
    """QFileDialog.getExistingDirectory() returns forward-slash paths even on
    Windows, and a pasted "Copy as path" value arrives wrapped in quotes.
    Both need cleaning up before they're stored or displayed."""

    def test_forward_slashes_become_native_separators(self):
        self.assertEqual(
            _clean_path("C:/Users/test/Documents/talktrack/recordings"),
            os.path.normpath("C:/Users/test/Documents/talktrack/recordings"))

    def test_strips_surrounding_double_quotes(self):
        self.assertEqual(
            _clean_path('"C:\\Users\\test\\recordings"'),
            os.path.normpath("C:\\Users\\test\\recordings"))

    def test_strips_surrounding_single_quotes(self):
        self.assertEqual(
            _clean_path("'C:\\Users\\test\\recordings'"),
            os.path.normpath("C:\\Users\\test\\recordings"))

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(
            _clean_path("  C:\\Users\\test  "),
            os.path.normpath("C:\\Users\\test"))

    def test_empty_string_is_left_alone(self):
        self.assertEqual(_clean_path(""), "")

    def test_already_clean_path_is_unchanged(self):
        clean = os.path.normpath("C:\\Users\\test\\recordings")
        self.assertEqual(_clean_path(clean), clean)


class TestSettingsDialogPathFields(unittest.TestCase):
    """End-to-end: a browse pick and a dirty pre-existing config value both
    end up clean in the field and in what gets saved back."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self._patchers = [
            patch.object(config_module, "CONFIG_DIR", tmp_path),
            patch.object(config_module, "CONFIG_FILE", tmp_path / "settings.json"),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def test_load_cleans_a_quoted_value_already_in_config(self):
        config = Config()
        config.set("output", "directory", '"C:\\Users\\test\\recordings"')
        dialog = SettingsDialog(config)
        self.assertEqual(dialog.output_dir_edit.text(),
                          os.path.normpath("C:\\Users\\test\\recordings"))

    def test_browse_normalizes_forward_slashes_from_qfiledialog(self):
        config = Config()
        dialog = SettingsDialog(config)
        with patch("app.ui.settings_dialog.QFileDialog.getExistingDirectory",
                    return_value="C:/Users/test/Documents/talktrack/recordings"):
            dialog._browse_output_dir()
        self.assertEqual(
            dialog.output_dir_edit.text(),
            os.path.normpath("C:/Users/test/Documents/talktrack/recordings"))

    def test_save_settings_persists_cleaned_path(self):
        config = Config()
        dialog = SettingsDialog(config)
        dialog.output_dir_edit.setText("C:/Users/test/recordings")
        dialog._save_and_close()
        self.assertEqual(config.get("output", "directory"),
                          os.path.normpath("C:/Users/test/recordings"))


if __name__ == "__main__":
    unittest.main()
