import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from app.ui.source_selector import SourceSelector

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSourceSelectorComPoller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_selector(self):
        poller = MagicMock()
        poller.get_snapshot.return_value = {"audio_apps": [], "mic_pids": set()}
        selector = SourceSelector(config=None, com_poller=poller)
        return selector, poller

    def test_refresh_app_list_reads_from_poller_not_pycaw(self):
        selector, poller = self._make_selector()
        if selector.app_list is None:
            self.skipTest("Per-app UI not available on this Windows version")
        poller.get_snapshot.return_value = {
            "audio_apps": [{"pids": [111], "name": "Zoom",
                             "process_name": "Zoom.exe", "active": True}],
            "mic_pids": set(),
        }
        selector._refresh_app_list()
        self.assertEqual(selector.app_list.count(), 1)
        self.assertIn("Zoom", selector.app_list.item(0).text())

    def test_set_recording_active_updates_poller_interval(self):
        selector, poller = self._make_selector()
        if selector._auto_refresh_timer is None:
            self.skipTest("Per-app UI not available on this Windows version")
        selector.set_recording_active(True)
        poller.set_interval.assert_called_with(2.0)
        selector.set_recording_active(False)
        poller.set_interval.assert_called_with(5.0)

    def test_auto_refresh_timer_uses_relaxed_interval(self):
        selector, poller = self._make_selector()
        if selector._auto_refresh_timer is None:
            self.skipTest("Per-app UI not available on this Windows version")
        self.assertEqual(selector._auto_refresh_timer.interval(), 5000)


if __name__ == "__main__":
    unittest.main()
