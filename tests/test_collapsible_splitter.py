import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from app.ui.collapsible_splitter import CollapsibleSplitter

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestCollapsibleSplitter(unittest.TestCase):
    def _make(self, total_width=600):
        _get_app()
        splitter = CollapsibleSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        right = QWidget()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.resize(total_width, 400)
        splitter.setSizes([total_width * 2 // 3, total_width // 3])
        return splitter

    def test_toggle_collapses_right_pane_to_zero(self):
        splitter = self._make()
        splitter.toggle_collapse()
        sizes = splitter.sizes()
        self.assertEqual(sizes[1], 0)
        self.assertTrue(splitter.is_collapsed())

    def test_toggle_twice_restores_original_width(self):
        splitter = self._make()
        original = splitter.sizes()
        splitter.toggle_collapse()
        splitter.toggle_collapse()
        self.assertEqual(splitter.sizes()[1], original[1])
        self.assertFalse(splitter.is_collapsed())

    def test_collapse_changed_emits_correct_bool(self):
        splitter = self._make()
        seen = []
        splitter.collapse_changed.connect(seen.append)
        splitter.toggle_collapse()
        splitter.toggle_collapse()
        self.assertEqual(seen, [True, False])

    def test_set_collapsed_true_on_fresh_splitter(self):
        splitter = self._make()
        splitter.set_collapsed(True)
        self.assertEqual(splitter.sizes()[1], 0)
        self.assertTrue(splitter.is_collapsed())

    def test_set_collapsed_false_is_a_noop_on_fresh_splitter(self):
        splitter = self._make()
        original = splitter.sizes()
        splitter.set_collapsed(False)
        self.assertEqual(splitter.sizes(), original)
        self.assertFalse(splitter.is_collapsed())

    def test_expanding_after_restored_collapse_uses_a_sane_width(self):
        # set_collapsed(True) on a fresh splitter has no prior expanded width
        # recorded - expanding afterward must not restore to 0.
        splitter = self._make()
        splitter.set_collapsed(True)
        splitter.toggle_collapse()
        self.assertGreater(splitter.sizes()[1], 0)


if __name__ == "__main__":
    unittest.main()
