"""SettingsDialog gained an optional initial_tab parameter so the
InspectorWidget's "Connect a provider" button can open Settings already on
the AI Assistant tab instead of dumping the user on General.
"""
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


class TestSettingsDialogInitialTab(unittest.TestCase):
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

    def test_no_initial_tab_defaults_to_first_tab(self):
        config = Config()
        dialog = SettingsDialog(config)
        self.assertEqual(dialog.tabs.currentIndex(), 0)

    def test_initial_tab_selects_named_tab(self):
        config = Config()
        dialog = SettingsDialog(config, initial_tab="AI Assistant")
        expected = None
        for i in range(dialog.tabs.count()):
            if dialog.tabs.tabText(i) == "AI Assistant":
                expected = i
                break
        self.assertIsNotNone(expected)
        self.assertEqual(dialog.tabs.currentIndex(), expected)

    def test_unknown_initial_tab_leaves_default_selection(self):
        config = Config()
        dialog = SettingsDialog(config, initial_tab="Nonexistent Tab")
        self.assertEqual(dialog.tabs.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
