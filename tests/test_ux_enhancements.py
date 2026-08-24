import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from main import build_bug_report_url
from app.ui.delete_scope_dialog import DeleteScopeDialog, DELETE_BOTH, DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS
from app.ui.compact_strip import CompactStrip
from app.ui.transcript_viewer import TranscriptViewer
from app.utils.config import Config
from app.ui.settings_dialog import SettingsDialog

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestUXEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_bug_report_url_targets_user_repo(self):
        url = build_bug_report_url("test error")
        self.assertIn("https://github.com/martinasencio-gm/TalkTrack/issues/new?", url)

    def test_delete_scope_dialog_everything_text(self):
        dialog = DeleteScopeDialog(count=1)
        self.assertIn("Everything", dialog._both_radio.text())
        self.assertEqual(dialog.selected_scope(), DELETE_BOTH)

    def test_compact_strip_pill_status_labels(self):
        strip = CompactStrip()
        strip.set_state("idle")
        self.assertEqual(strip.pill_status_label.text(), "Ready")
        strip.set_state("armed")
        self.assertEqual(strip.pill_status_label.text(), "Call Active")
        strip.set_state("recording")
        self.assertEqual(strip.pill_status_label.text(), "REC")
        strip.set_state("paused")
        self.assertEqual(strip.pill_status_label.text(), "PAUSED")
        strip.set_state("muted")
        self.assertEqual(strip.pill_status_label.text(), "MUTED")
        strip.set_state("transcribing")
        self.assertEqual(strip.pill_status_label.text(), "Transcribing…")
        strip.set_state("done")
        self.assertEqual(strip.pill_status_label.text(), "Done")

    def test_double_click_target_settings_persistence(self):
        config = Config()
        dialog = SettingsDialog(config)
        dialog.double_click_target_combo.setCurrentIndex(
            dialog.double_click_target_combo.findData("pill")
        )
        dialog._apply_settings()
        self.assertEqual(config.get("ui", "double_click_target"), "pill")

        dialog.double_click_target_combo.setCurrentIndex(
            dialog.double_click_target_combo.findData("compact_bar")
        )
        dialog._apply_settings()
        self.assertEqual(config.get("ui", "double_click_target"), "compact_bar")

    def test_close_to_tray_settings_persistence(self):
        config = Config()
        dialog = SettingsDialog(config)
        dialog.close_to_tray_cb.setChecked(True)
        dialog._apply_settings()
        self.assertTrue(config.get("general", "close_to_tray"))

        dialog.close_to_tray_cb.setChecked(False)
        dialog._apply_settings()
        self.assertFalse(config.get("general", "close_to_tray"))

    def test_transcript_viewer_show_loading(self):
        viewer = TranscriptViewer()
        viewer.show_loading("Processing...")
        self.assertEqual(viewer.scroll_area.widget(), viewer._segments_container)


if __name__ == "__main__":
    unittest.main()
