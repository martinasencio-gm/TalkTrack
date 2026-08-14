import unittest

from app.utils.config_migration import apply_meeting_detection_migration


class TestMeetingDetectionMigration(unittest.TestCase):
    def _merged(self, mode="suggest", threshold=5):
        return {
            "general": {"auto_record": False, "auto_record_threshold": 5},
            "meeting_detection": {"mode": mode, "threshold_seconds": threshold},
        }

    def test_fresh_config_keeps_suggest_default(self):
        # No saved file at all -> brand new user -> the default stands.
        result = apply_meeting_detection_migration(None, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "suggest")

    def test_existing_user_with_auto_record_off_becomes_off(self):
        # The important case: someone who deliberately disabled auto-record must
        # NOT silently inherit the new "suggest" default via _deep_merge.
        saved = {"general": {"auto_record": False}}
        result = apply_meeting_detection_migration(saved, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "off")

    def test_existing_user_with_auto_record_on_becomes_auto(self):
        saved = {"general": {"auto_record": True}}
        result = apply_meeting_detection_migration(saved, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "auto")

    def test_threshold_carries_over(self):
        saved = {"general": {"auto_record": True, "auto_record_threshold": 12}}
        merged = self._merged()
        merged["general"]["auto_record_threshold"] = 12
        result = apply_meeting_detection_migration(saved, merged)
        self.assertEqual(result["meeting_detection"]["threshold_seconds"], 12)

    def test_already_migrated_config_is_untouched(self):
        saved = {"general": {"auto_record": True},
                 "meeting_detection": {"mode": "off"}}
        merged = self._merged(mode="off")
        result = apply_meeting_detection_migration(saved, merged)
        self.assertEqual(result["meeting_detection"]["mode"], "off")

    def test_saved_without_auto_record_keeps_default(self):
        result = apply_meeting_detection_migration({"general": {}}, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "suggest")

    def test_saved_without_general_section_keeps_default(self):
        result = apply_meeting_detection_migration({"ui": {}}, self._merged())
        self.assertEqual(result["meeting_detection"]["mode"], "suggest")


if __name__ == "__main__":
    unittest.main()
