"""Tests for app.utils.device_mismatch — pure detection of conferencing-app
device mismatches, extracted (Qt-free) from what SourceSelector.
check_device_mismatches did before the UI redesign reduced it to `pass`.
"""
import unittest

from app.utils.device_mismatch import find_active_conferencing_app, compute_device_mismatches

CONFERENCING_APPS = {"Microsoft Teams", "Zoom", "Webex", "GoToMeeting", "Google Meet", "Discord"}


class TestFindActiveConferencingApp(unittest.TestCase):
    def test_finds_conferencing_app_using_mic(self):
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams", "mic": "Jabra", "output": None,
                "process_name": "ms-teams.exe", "pids": [100],
            }
        }
        result = find_active_conferencing_app(app_devices, CONFERENCING_APPS)
        self.assertEqual(result["app"], "Microsoft Teams")

    def test_ignores_non_conferencing_background_apps(self):
        app_devices = {
            "M365Copilot": {
                "app": "M365Copilot", "mic": "Intel Mic", "output": "HP Dock",
                "process_name": "M365Copilot.exe", "pids": [999],
            }
        }
        self.assertIsNone(find_active_conferencing_app(app_devices, CONFERENCING_APPS))

    def test_ignores_conferencing_app_using_neither_mic_nor_output(self):
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams", "mic": None, "output": None,
                "process_name": "ms-teams.exe", "pids": [100],
            }
        }
        self.assertIsNone(find_active_conferencing_app(app_devices, CONFERENCING_APPS))

    def test_matches_by_process_name_when_app_display_name_differs(self):
        app_devices = {
            "some-key": {
                "app": "", "mic": "Jabra", "output": None,
                "process_name": "ms-teams.exe", "pids": [100],
            }
        }
        result = find_active_conferencing_app(app_devices, CONFERENCING_APPS)
        self.assertIsNotNone(result)


class TestComputeDeviceMismatches(unittest.TestCase):
    def test_no_app_devices_means_no_mismatch(self):
        result = compute_device_mismatches(
            current_mic_name="Realtek", current_output_name="Speakers",
            output_check_active=True, app_devices={}, conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertEqual(result, {"mic": None, "output": None})

    def test_mic_mismatch_detected(self):
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams",
                "mic": "Headset Microphone (Jabra Evolve2 65)",
                "output": None, "process_name": "ms-teams.exe", "pids": [100],
            }
        }
        result = compute_device_mismatches(
            current_mic_name="Microphone Array (Realtek(R) Audio)",
            current_output_name=None, output_check_active=False,
            app_devices=app_devices, conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertEqual(result["mic"], {
            "app": "Microsoft Teams",
            "device": "Headset Microphone (Jabra Evolve2 65)",
        })
        self.assertIsNone(result["output"])

    def test_mic_matching_name_is_not_a_mismatch(self):
        app_devices = {
            "Microsoft Teams": {
                "app": "Microsoft Teams",
                "mic": "Headset Microphone (Jabra Evolve2 65)",
                "output": None, "process_name": "ms-teams.exe", "pids": [100],
            }
        }
        result = compute_device_mismatches(
            current_mic_name="Headset Microphone (Jabra Evolve2 65)",
            current_output_name=None, output_check_active=False,
            app_devices=app_devices, conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertIsNone(result["mic"])

    def test_output_mismatch_only_checked_when_active(self):
        app_devices = {
            "Zoom": {
                "app": "Zoom", "mic": None,
                "output": "Headphones (Jabra Evolve2 65)",
                "process_name": "Zoom.exe", "pids": [200],
            }
        }
        inactive = compute_device_mismatches(
            current_mic_name=None, current_output_name="Speakers (Realtek(R) Audio)",
            output_check_active=False, app_devices=app_devices,
            conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertIsNone(inactive["output"])

        active = compute_device_mismatches(
            current_mic_name=None, current_output_name="Speakers (Realtek(R) Audio)",
            output_check_active=True, app_devices=app_devices,
            conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertEqual(active["output"], {
            "app": "Zoom", "device": "Headphones (Jabra Evolve2 65)",
        })

    def test_ignores_non_conferencing_background_apps(self):
        app_devices = {
            "M365Copilot": {
                "app": "M365Copilot",
                "mic": "Microphone Array (Intel Smart Sound)",
                "output": "Headphones (HP USB-C Dock)",
                "process_name": "M365Copilot.exe", "pids": [999],
            }
        }
        result = compute_device_mismatches(
            current_mic_name="Microphone Array (Realtek(R) Audio)",
            current_output_name="Speakers (Realtek(R) Audio)",
            output_check_active=True, app_devices=app_devices,
            conferencing_app_names=CONFERENCING_APPS,
        )
        self.assertEqual(result, {"mic": None, "output": None})


if __name__ == "__main__":
    unittest.main()
