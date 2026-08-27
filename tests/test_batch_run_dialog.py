import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from app.ui.batch_run_dialog import BatchRunDialog, MODE_IN_APP, MODE_DETACHED

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestBatchRunDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_dialog_zero_queued(self):
        dialog = BatchRunDialog(queued_count=0)
        self.assertFalse(hasattr(dialog, "_in_app_radio"))

    def test_window_title_is_run_batch_processing(self):
        dialog = BatchRunDialog(queued_count=0)
        self.assertEqual(dialog.windowTitle(), "Run Batch Processing")

    def test_dialog_queued_defaults_and_options(self):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda sec, key: {
            ("diarization", "hf_token"): "hf_test_token",
            ("diarization", "enabled"): True,
        }.get((sec, key))

        dialog = BatchRunDialog(queued_count=3, config=mock_config)
        self.assertEqual(dialog.execution_mode(), MODE_IN_APP)
        self.assertTrue(dialog.diarize_enabled())
        self.assertIsNone(dialog.limit())

        # Select detached mode
        dialog._detached_radio.setChecked(True)
        self.assertEqual(dialog.execution_mode(), MODE_DETACHED)

        # Select limit
        dialog._limit_radio.setChecked(True)
        dialog._limit_spinbox.setValue(2)
        self.assertEqual(dialog.limit(), 2)

    def test_dialog_diarize_disabled_without_token(self):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda sec, key: {
            ("diarization", "hf_token"): "",
            ("diarization", "enabled"): True,
        }.get((sec, key))

        dialog = BatchRunDialog(queued_count=2, config=mock_config)
        self.assertFalse(dialog.diarize_enabled())
        self.assertFalse(dialog._diarize_cb.isEnabled())

    def test_summarize_group_disabled_without_a_provider(self):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda sec, key: {
            ("diarization", "hf_token"): "hf",
            ("diarization", "enabled"): False,
            ("ai", "provider"): "none",
        }.get((sec, key))

        dialog = BatchRunDialog(queued_count=2, config=mock_config)
        self.assertFalse(dialog._summarize_cb.isEnabled())
        self.assertFalse(dialog.summarize_enabled())

    def test_summarize_group_enabled_with_a_cloud_provider(self):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda sec, key: {
            ("diarization", "hf_token"): "hf",
            ("diarization", "enabled"): False,
            ("ai", "provider"): "claude",
        }.get((sec, key))

        dialog = BatchRunDialog(queued_count=2, config=mock_config)
        self.assertTrue(dialog._summarize_cb.isEnabled())
        # Opt-in: unchecked by default (it spends against the API key).
        self.assertFalse(dialog._summarize_cb.isChecked())
        self.assertFalse(dialog.summarize_enabled())
        dialog._summarize_cb.setChecked(True)
        self.assertTrue(dialog.summarize_enabled())

    def test_zero_queued_dialog_has_no_summarize_accessor_crash(self):
        dialog = BatchRunDialog(queued_count=0)
        # No groups are built, but the accessor is still safe to call.
        self.assertFalse(dialog.summarize_enabled())


if __name__ == "__main__":
    unittest.main()
