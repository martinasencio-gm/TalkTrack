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

    def test_mic_mismatch_detected_and_cleared_on_match(self):
        selector = self._make_selector()
        # Initial state: mic index 1 (Realtek) is selected
        self.assertEqual(selector.get_selected_mic(), 1)
        self.assertIsNone(selector.mic_mismatch)

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
        received = []
        selector.mismatch_changed.connect(lambda: received.append(True))
        selector.check_device_mismatches(app_devices)

        self.assertEqual(selector.mic_mismatch, {
            "app": "Microsoft Teams",
            "device": "Headset Microphone (Jabra Evolve2 65)",
        })
        self.assertEqual(received, [True])

        # Switching to the Jabra mic clears the mismatch
        selector.mic_combo.setCurrentIndex(2)
        self.assertEqual(selector.get_selected_mic(), 2)
        selector.check_device_mismatches()
        self.assertIsNone(selector.mic_mismatch)

    def test_mic_matching_is_not_a_mismatch(self):
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
        self.assertIsNone(selector.mic_mismatch)

    def test_output_mismatch_detected_in_legacy_mode(self):
        selector = self._make_selector()
        # Switch to legacy mode (All system audio)
        if selector.mode_group:
            selector.radio_legacy.setChecked(True)

        self.assertEqual(selector.get_selected_loopback(), 10)
        self.assertIsNone(selector.output_mismatch)

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

        self.assertEqual(selector.output_mismatch, {
            "app": "Zoom",
            "device": "Headphones (Jabra Evolve2 65)",
        })

    def test_output_mismatch_not_checked_in_per_app_mode(self):
        """Per-app capture taps the target process's own stream, so what
        endpoint it renders to doesn't matter — only legacy loopback cares."""
        selector = self._make_selector()
        if selector.mode_group:
            selector.radio_per_app.setChecked(True)

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
        self.assertIsNone(selector.output_mismatch)

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

        self.assertIsNone(selector.mic_mismatch)
        self.assertIsNone(selector.output_mismatch)


if __name__ == "__main__":
    unittest.main()
