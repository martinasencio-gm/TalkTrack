"""Local-model wiring in the settings dialog: CPU wheel index on install,
and the effective path/name resolution for the Local provider."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.utils.config import Config

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestLocalProviderInstallIndex(unittest.TestCase):
    """Critical 1: installing the `local` provider must pass the prebuilt CPU
    wheel index through to install_package so llama-cpp-python is not built
    from source."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def _dialog(self):
        from app.ui.settings_dialog import SettingsDialog
        return SettingsDialog(Config())

    def test_local_install_passes_cpu_wheel_index(self):
        from app.utils import package_installer
        dialog = self._dialog()
        fake_install = MagicMock(return_value=(True, "ok"))
        with patch.object(package_installer, "is_package_installed", return_value=False), \
             patch.object(package_installer, "install_package", fake_install), \
             patch("app.ui.settings_dialog.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes), \
             patch("app.ui.settings_dialog.QApplication.processEvents"):
            ok = dialog._install_provider_package("local")
        self.assertTrue(ok)
        args, _ = fake_install.call_args
        self.assertEqual(args[0], "llama-cpp-python>=0.3.0")
        self.assertEqual(args[1], "https://abetlen.github.io/llama-cpp-python/whl/cpu")

    def test_api_provider_install_passes_no_extra_index(self):
        from app.utils import package_installer
        dialog = self._dialog()
        fake_install = MagicMock(return_value=(True, "ok"))
        with patch.object(package_installer, "is_package_installed", return_value=False), \
             patch.object(package_installer, "install_package", fake_install), \
             patch("app.ui.settings_dialog.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes), \
             patch("app.ui.settings_dialog.QApplication.processEvents"):
            dialog._install_provider_package("claude")
        args, _ = fake_install.call_args
        self.assertEqual(args[0], "anthropic>=0.40.0")
        self.assertIsNone(args[1])


class TestEffectiveLocalModelPathAndName(unittest.TestCase):
    """Minor 6: a non-empty custom GGUF path wins and clears the catalog key
    so a stale name can't drive n_ctx for an unrelated model."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def _dialog(self):
        from app.ui.settings_dialog import SettingsDialog
        return SettingsDialog(Config())

    def test_custom_path_clears_catalog_name(self):
        dialog = self._dialog()
        dialog.model_catalog_widget.set_selected_key("qwen2.5-3b")
        dialog.ai_local_path.setText("C:/models/some-custom.gguf")
        path, name = dialog._effective_local_model_path_and_name()
        self.assertEqual(path, "C:/models/some-custom.gguf")
        self.assertEqual(name, "")

    def test_catalog_name_resolves_to_path_when_no_custom_path(self):
        dialog = self._dialog()
        dialog.ai_local_path.setText("")
        dialog.model_catalog_widget.set_selected_key("qwen2.5-3b")
        path, name = dialog._effective_local_model_path_and_name()
        self.assertEqual(name, "qwen2.5-3b")
        self.assertTrue(path.endswith("Qwen2.5-3B-Instruct-Q4_K_M.gguf"))

    def test_nothing_selected_returns_empty_pair(self):
        dialog = self._dialog()
        dialog.ai_local_path.setText("")
        dialog.model_catalog_widget.set_selected_key("")
        self.assertEqual(dialog._effective_local_model_path_and_name(), ("", ""))


if __name__ == "__main__":
    unittest.main()
