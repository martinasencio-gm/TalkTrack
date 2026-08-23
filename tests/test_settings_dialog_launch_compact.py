"""Item 3 of the compact-mode UI work: a settings checkbox to launch
straight into the compact strip instead of the full window."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from app.ui.settings_dialog import SettingsDialog
from app.utils import config as config_module
from app.utils.config import Config

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestLaunchInCompactModeSetting(unittest.TestCase):
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

    def test_defaults_to_unchecked(self):
        config = Config()
        dialog = SettingsDialog(config)
        self.assertFalse(dialog.launch_compact_cb.isChecked())

    def test_checking_and_saving_persists_to_config(self):
        config = Config()
        dialog = SettingsDialog(config)
        dialog.launch_compact_cb.setChecked(True)
        dialog._save_and_close()
        self.assertTrue(config.get("general", "launch_in_compact_mode"))

    def test_dialog_reflects_a_previously_saved_true_value(self):
        config = Config()
        config.set("general", "launch_in_compact_mode", True)
        dialog = SettingsDialog(config)
        self.assertTrue(dialog.launch_compact_cb.isChecked())


if __name__ == "__main__":
    unittest.main()
