import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.ui.source_selector import SourceSelector
from app.utils.audio_devices import device_names_match, find_matching_device_index

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestDeviceNamesMatch(unittest.TestCase):
    def test_exact_and_case_insensitive_match(self):
        self.assertTrue(device_names_match(
            "Microphone (Jabra Evolve2 65)",
            "microphone (jabra evolve2 65)"
        ))

    def test_api_tag_differences(self):
        self.assertTrue(device_names_match(
            "Headset Microphone (Jabra Evolve2 65) (MME)",
            "Headset Microphone (Jabra Evolve2 65)"
        ))
        self.assertTrue(device_names_match(
            "Speakers (Realtek(R) Audio) (WASAPI Loopback)",
            "Speakers (Realtek(R) Audio)"
        ))

    def test_mme_truncation_prefix_match(self):
        self.assertTrue(device_names_match(
            "Microphone Array (Realtek(R) Au",
            "Microphone Array (Realtek(R) Audio)"
        ))

    def test_hardware_token_matching(self):
        self.assertTrue(device_names_match(
            "Jabra Evolve2 65",
            "Headset Microphone (Jabra Evolve2 65 Audio)"
        ))
        self.assertTrue(device_names_match(
            "Yeti Nano Microphone",
            "Microphone (Yeti Nano)"
        ))

    def test_distinct_hardware_does_not_match(self):
        self.assertFalse(device_names_match(
            "Microphone Array (Realtek(R) Audio)",
            "Headset Microphone (Jabra Evolve2 65)"
        ))
        self.assertFalse(device_names_match(
            "Speakers (Realtek(R) Audio)",
            "DELL S2725QS (Intel(R) Display Audio)"
        ))

    def test_empty_or_none_handling(self):
        self.assertFalse(device_names_match("", "Jabra"))
        self.assertFalse(device_names_match(None, "Realtek"))
        self.assertFalse(device_names_match("", ""))


class TestFindMatchingDeviceIndex(unittest.TestCase):
    def setUp(self):
        self.devices = [
            {"index": 1, "name": "Microphone Array (Realtek(R) Audio)", "hostapi": "MME"},
            {"index": 2, "name": "Headset Microphone (Jabra Evolve2 65)", "hostapi": "MME"},
            {"index": 5, "name": "Yeti Nano", "hostapi": "Windows WASAPI"},
        ]

    def test_finds_matching_device_index(self):
        self.assertEqual(
            find_matching_device_index("Headset Microphone (Jabra Evolve2 65)", self.devices),
            2
        )
        self.assertEqual(
            find_matching_device_index("Yeti Nano (USB Audio)", self.devices),
            5
        )

    def test_returns_none_if_unmatched(self):
        self.assertIsNone(find_matching_device_index("AirPods Pro", self.devices))
        self.assertIsNone(find_matching_device_index(None, self.devices))


class TestSourceSelectorDeviceMismatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self.poller = MagicMock()
        self.poller.get_snapshot.return_value = {
            "audio_apps": [], "mic_pids": set(), "render_peaks": {}, "app_devices": {}
        }
        self.mock_inputs = [
            {"index": 1, "name": "Microphone Array (Realtek(R) Audio)", "channels": 2, "sample_rate": 48000, "hostapi": "MME"},
            {"index": 2, "name": "Headset Microphone (Jabra Evolve2 65)", "channels": 1, "sample_rate": 48000, "hostapi": "MME"},
        ]
        self.mock_outputs = [
            {"index": 10, "name": "Speakers (Realtek(R) Audio)", "channels": 2, "sample_rate": 48000, "hostapi": "WASAPI"},
            {"index": 11, "name": "Headphones (Jabra Evolve2 65)", "channels": 2, "sample_rate": 48000, "hostapi": "WASAPI"},
        ]

    def _make_selector(self):
        with patch("app.ui.source_selector.get_input_devices", return_value=self.mock_inputs), \
             patch("app.ui.source_selector.get_system_audio_devices", return_value=self.mock_outputs), \
             patch("app.ui.source_selector.get_default_mic", return_value=1), \
             patch("app.ui.source_selector.get_default_output", return_value=10):
            selector = SourceSelector(config=None, com_poller=self.poller)
            selector.show()
            return selector

    def test_mic_mismatch_banner_and_switch_action(self):
        selector = self._make_selector()
        # Initial state: mic index 1 (Realtek) is selected
        self.assertEqual(selector.get_selected_mic(), 1)
        self.assertFalse(selector._mic_mismatch_banner.isVisible())

        # Teams is using Jabra mic
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams",
                "mic": "Headset Microphone (Jabra Evolve2 65)",
                "output": None,
                "process_name": "ms-teams.exe",
                "pids": [100]
            }
        }
        selector.check_device_mismatches(app_devices)

        # Mismatch banner should be shown
        self.assertTrue(selector._mic_mismatch_banner.isVisible())
        self.assertIn("Microsoft Teams is using \"Headset Microphone (Jabra Evolve2 65)\" mic",
                      selector._mic_mismatch_label.text())

        # Click switch button
        selector._mic_mismatch_btn.click()

        # Should switch mic selection to index 2 (Jabra) and hide banner
        self.assertEqual(selector.get_selected_mic(), 2)
        self.assertFalse(selector._mic_mismatch_banner.isVisible())

    def test_mic_matching_hides_banner(self):
        selector = self._make_selector()
        # Set selected mic to Jabra
        selector.mic_combo.setCurrentIndex(2)
        self.assertEqual(selector.get_selected_mic(), 2)

        # Teams is also using Jabra mic
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams",
                "mic": "Headset Microphone (Jabra Evolve2 65)",
                "output": None,
                "process_name": "ms-teams.exe",
                "pids": [100]
            }
        }
        selector.check_device_mismatches(app_devices)
        self.assertFalse(selector._mic_mismatch_banner.isVisible())

    def test_output_mismatch_banner_and_switch_in_legacy_mode(self):
        selector = self._make_selector()
        # Switch to legacy mode (All system audio)
        if selector.mode_group:
            selector.radio_legacy.setChecked(True)

        self.assertEqual(selector.get_selected_loopback(), 10)
        self.assertFalse(selector._output_mismatch_banner.isVisible())

        # Zoom is outputting to Jabra headphones
        app_devices = {
            "Zoom": {
                "app": "Zoom",
                "mic": None,
                "output": "Headphones (Jabra Evolve2 65)",
                "process_name": "Zoom.exe",
                "pids": [200]
            }
        }
        selector.check_device_mismatches(app_devices)

        # Output mismatch banner should be visible
        self.assertTrue(selector._output_mismatch_banner.isVisible())
        self.assertIn("Zoom is outputting to \"Headphones (Jabra Evolve2 65)\"",
                      selector._output_mismatch_label.text())

        # Click switch output button
        selector._output_mismatch_btn.click()

        # Should switch loopback selection to index 11 (Jabra) and hide banner
        self.assertEqual(selector.get_selected_loopback(), 11)
        self.assertFalse(selector._output_mismatch_banner.isVisible())

    def test_ignores_non_conferencing_background_apps(self):
        """Background apps like M365Copilot or explorer should NOT trigger mismatch warnings."""
        selector = self._make_selector()
        app_devices = {
            "M365Copilot": {
                "app": "M365Copilot",
                "mic": "Microphone Array (Intel Smart Sound)",
                "output": "Headphones (HP USB-C Dock)",
                "process_name": "M365Copilot.exe",
                "pids": [999]
            }
        }
        selector.check_device_mismatches(app_devices)

        # Neither banner should be shown
        self.assertFalse(selector._mic_mismatch_banner.isVisible())
        self.assertFalse(selector._output_mismatch_banner.isVisible())


if __name__ == "__main__":
    unittest.main()
