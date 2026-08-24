"""Tests for the double-click shrink chain (pure logic, no Qt)."""
import unittest


class TestNextPresentation(unittest.TestCase):
    def test_full_lands_on_the_configured_target(self):
        from app.ui.window_presentation import next_presentation
        self.assertEqual(next_presentation("full", "compact_bar"), "compact_bar")
        self.assertEqual(next_presentation("full", "pill"), "pill")

    def test_compact_bar_shrinks_to_the_pill(self):
        from app.ui.window_presentation import next_presentation
        # The chain itself is fixed; the target only picks the entry point.
        self.assertEqual(next_presentation("compact_bar", "compact_bar"), "pill")
        self.assertEqual(next_presentation("compact_bar", "pill"), "pill")

    def test_pill_wraps_back_to_the_full_window(self):
        from app.ui.window_presentation import next_presentation
        self.assertEqual(next_presentation("pill", "compact_bar"), "full")
        self.assertEqual(next_presentation("pill", "pill"), "full")

    def test_the_compact_bar_target_cycles_through_all_three(self):
        from app.ui.window_presentation import next_presentation
        seen = []
        state = "full"
        for _ in range(3):
            state = next_presentation(state, "compact_bar")
            seen.append(state)
        self.assertEqual(seen, ["compact_bar", "pill", "full"])

    def test_the_pill_target_skips_the_compact_bar(self):
        from app.ui.window_presentation import next_presentation
        # With "pill" chosen, the compact bar is reachable only from the
        # pill's own expand button, not from the double-click cycle.
        seen = []
        state = "full"
        for _ in range(2):
            state = next_presentation(state, "pill")
            seen.append(state)
        self.assertEqual(seen, ["pill", "full"])

    def test_an_unrecognized_target_falls_back_to_the_compact_bar(self):
        from app.ui.window_presentation import next_presentation
        # A hand-edited settings.json must not strand the gesture.
        self.assertEqual(next_presentation("full", "sidebar"), "compact_bar")
        self.assertEqual(next_presentation("full", None), "compact_bar")
        self.assertEqual(next_presentation("full", ""), "compact_bar")

    def test_an_unrecognized_current_state_restores_the_full_window(self):
        from app.ui.window_presentation import next_presentation
        # Nothing should be able to leave the app with no way back.
        self.assertEqual(next_presentation("tray", "compact_bar"), "full")
        self.assertEqual(next_presentation(None, "compact_bar"), "full")


class TestCloseToTrayMigration(unittest.TestCase):
    def _merged(self):
        return {"general": {"close_to_tray": False}}

    def test_a_brand_new_install_is_untouched(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        merged = self._merged()
        self.assertFalse(
            apply_close_to_tray_migration(None, merged)["general"]["close_to_tray"])

    def test_legacy_tray_behavior_migrates(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        saved = {"general": {"minimize_behavior": "tray"}}
        self.assertTrue(
            apply_close_to_tray_migration(saved, self._merged())["general"]["close_to_tray"])

    def test_the_legacy_boolean_also_migrates(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        saved = {"general": {"minimize_to_tray": True}}
        self.assertTrue(
            apply_close_to_tray_migration(saved, self._merged())["general"]["close_to_tray"])

    def test_other_legacy_behaviors_do_not_migrate(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        for behavior in ("taskbar", "compact_bar"):
            saved = {"general": {"minimize_behavior": behavior}}
            self.assertFalse(
                apply_close_to_tray_migration(saved, self._merged())["general"]["close_to_tray"],
                behavior,
            )

    def test_an_already_migrated_config_is_respected(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        # Their explicit choice wins over anything the legacy keys imply.
        saved = {"general": {"close_to_tray": False, "minimize_behavior": "tray"}}
        self.assertFalse(
            apply_close_to_tray_migration(saved, self._merged())["general"]["close_to_tray"])

    def test_a_config_predating_the_legacy_keys_is_untouched(self):
        from app.utils.config_migration import apply_close_to_tray_migration
        saved = {"general": {"user_name": "sam"}}
        self.assertFalse(
            apply_close_to_tray_migration(saved, self._merged())["general"]["close_to_tray"])


if __name__ == "__main__":
    unittest.main()
