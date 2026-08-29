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


class TestInspectorCollapsedMigration(unittest.TestCase):
    def _merged(self, inspector_collapsed=False):
        return {"ui": {"inspector_collapsed": inspector_collapsed}}

    def test_fresh_install_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        result = apply_inspector_collapsed_migration(None, self._merged())
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_legacy_key_true_is_copied_across(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": True}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], True)

    def test_legacy_key_false_is_copied_across(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": False}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=True))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_already_migrated_is_left_alone(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": True, "inspector_collapsed": False}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_no_legacy_key_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"theme": "dark"}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_no_ui_section_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"general": {}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)


if __name__ == "__main__":
    unittest.main()
