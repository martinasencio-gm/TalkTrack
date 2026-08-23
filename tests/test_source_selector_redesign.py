"""Item 6 of the UI redesign review: Sources dialog header, mode cards,
blocked-conferencing-app rows, footer verdict, and the recording-lock
notice. See docs/superpowers/specs for the Screen 3 spec this follows.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from app.ui.source_selector import SourceSelector

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSourceSelectorRedesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_selector(self, snapshot=None):
        poller = MagicMock()
        poller.get_snapshot.return_value = snapshot or {
            "audio_apps": [], "mic_pids": set(),
        }
        selector = SourceSelector(config=None, com_poller=poller)
        self.addCleanup(selector.deleteLater)
        return selector, poller

    def test_header_shows_title_and_subtitle(self):
        selector, _ = self._make_selector()
        self.assertEqual(selector.findChild(QLabel, "sourcesHeaderTitle").text(),
                          "Audio Sources")
        self.assertTrue(selector.findChild(QLabel, "sourcesHeaderSubtitle").text())

    def test_lock_notice_hidden_by_default(self):
        selector, _ = self._make_selector()
        self.assertFalse(selector._recording_lock_notice.isVisible())

    def test_set_enabled_false_shows_lock_notice(self):
        selector, _ = self._make_selector()
        selector.show()
        selector.set_enabled(False)
        self.assertTrue(selector._recording_lock_notice.isVisible())
        selector.set_enabled(True)
        self.assertFalse(selector._recording_lock_notice.isVisible())

    def test_mode_card_click_selects_its_radio(self):
        selector, _ = self._make_selector()
        if selector.mode_group is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector.radio_per_app.setChecked(True)
        self.assertFalse(selector.radio_legacy.isChecked())
        # Simulate clicking the legacy card by driving the radio directly
        # the same way _ModeCard.mousePressEvent does.
        legacy_card = selector.radio_legacy.parent()
        while legacy_card is not None and legacy_card.objectName() != "sourceModeCard":
            legacy_card = legacy_card.parent()
        self.assertIsNotNone(legacy_card)
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QPointF
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        legacy_card.mousePressEvent(event)
        self.assertTrue(selector.radio_legacy.isChecked())

    def test_conferencing_app_row_is_disabled_with_reason(self):
        selector, _ = self._make_selector()
        if selector.app_list is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector._com_poller.get_snapshot.return_value = {
            "audio_apps": [{"pids": [111], "name": "Microsoft Teams",
                             "process_name": "Teams.exe", "active": True}],
            "mic_pids": set(),
        }
        selector._refresh_app_list()
        item = selector.app_list.item(0)
        self.assertIn("blocks per-app capture", item.text())
        self.assertFalse(bool(item.flags() & Qt.ItemFlag.ItemIsEnabled))

    def test_non_conferencing_app_row_stays_enabled(self):
        selector, _ = self._make_selector()
        if selector.app_list is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector._com_poller.get_snapshot.return_value = {
            "audio_apps": [{"pids": [222], "name": "Spotify",
                             "process_name": "Spotify.exe", "active": True}],
            "mic_pids": set(),
        }
        selector._refresh_app_list()
        item = selector.app_list.item(0)
        self.assertNotIn("blocks per-app capture", item.text())
        self.assertTrue(bool(item.flags() & Qt.ItemFlag.ItemIsEnabled))

    def test_footer_verdict_reflects_no_source_selected(self):
        selector, _ = self._make_selector()
        selector._update_verdict()
        self.assertIn("No app or system audio", selector._verdict.verdict_title.text())

    def test_footer_verdict_blocked_when_conferencing_app_checked(self):
        selector, _ = self._make_selector()
        if selector.app_list is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector._com_poller.get_snapshot.return_value = {
            "audio_apps": [{"pids": [111], "name": "Microsoft Teams",
                             "process_name": "Teams.exe", "active": True}],
            "mic_pids": set(),
        }
        selector._refresh_app_list()
        # Force-check the disabled row the way a restored saved config would
        # (programmatic check bypasses ItemIsUserCheckable/ItemIsEnabled).
        selector.app_list.item(0).setCheckState(Qt.CheckState.Checked)
        selector._update_verdict()
        self.assertIn("blocks per-app capture", selector._verdict.verdict_title.text())


if __name__ == "__main__":
    unittest.main()
