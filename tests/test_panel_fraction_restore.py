"""Pure fraction<->pixel math for the collapsible-panel proportional resize
feature (docs/superpowers/specs/2026-08-29-collapsible-panels-design.md).
No Qt involved — MainWindow wires these into splitter setSizes()/sizes()
calls, which is covered separately by tests/test_collapsible_panel_resize_persistence.py.
"""
import unittest

from app.ui.panel_fractions import fraction_for_size, resolve_pane_size, resolve_splitter_sizes


class TestFractionForSize(unittest.TestCase):
    def test_computes_fraction_of_screen_width(self):
        self.assertAlmostEqual(fraction_for_size(480, 1920), 0.25)

    def test_zero_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, 0))

    def test_negative_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, -1))

    def test_none_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, None))


class TestResolvePaneSize(unittest.TestCase):
    def test_none_fraction_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(None, 1920, 322), 322)

    def test_fraction_scales_to_current_screen_width(self):
        self.assertEqual(resolve_pane_size(0.25, 1920, 322), 480)

    def test_zero_screen_width_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(0.25, 0, 322), 322)

    def test_negative_screen_width_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(0.25, -100, 322), 322)

    def test_result_is_floored_at_one_pixel(self):
        self.assertEqual(resolve_pane_size(0.0001, 100, 322), 1)


class TestResolveSplitterSizes(unittest.TestCase):
    def test_all_none_fractions_return_defaults(self):
        fractions = {"library": None, "transcript": None}
        sizes = resolve_splitter_sizes(fractions, ["library", "transcript"], 1920, [262, 776])
        self.assertEqual(sizes, [262, 776])

    def test_set_fraction_overrides_its_slot(self):
        fractions = {"library": 0.2, "transcript": None}
        sizes = resolve_splitter_sizes(fractions, ["library", "transcript"], 1000, [262, 776])
        self.assertEqual(sizes, [200, 776])

    def test_none_key_always_uses_default(self):
        # splitter1's pane 0 is splitter2 itself; its width is never saved
        # as its own fraction — only the paired "inspector" slot is.
        fractions = {"inspector": 0.3}
        sizes = resolve_splitter_sizes(fractions, [None, "inspector"], 1000, [1038, 322])
        self.assertEqual(sizes, [1038, 300])

    def test_missing_key_in_fractions_dict_falls_back_to_default(self):
        sizes = resolve_splitter_sizes({}, ["library", "transcript"], 1920, [262, 776])
        self.assertEqual(sizes, [262, 776])

    def test_round_trip_save_then_restore_recovers_the_same_size(self):
        original_size = 540
        screen_width = 1920
        fraction = fraction_for_size(original_size, screen_width)
        restored_size = resolve_pane_size(fraction, screen_width, 322)
        self.assertEqual(restored_size, original_size)


if __name__ == "__main__":
    unittest.main()
