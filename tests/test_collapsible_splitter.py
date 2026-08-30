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
        # Mirrors production: MainWindow's left panel is a fixed-width pane
        # (setFixedWidth), so only the right pane actually resizes. A left
        # widget without that constraint lets QSplitter free-stretch it to
        # fill any leftover space, which production never does.
        #
        # Production also shrinks/grows the containing window on
        # about_to_toggle before toggle_collapse() reads sizes() - without
        # that, a fixed-width left pane can never let the right pane reach
        # zero, since QSplitter always hands leftover space back to the only
        # resizable pane. Emulate that here by resizing the splitter itself.
        _get_app()
        left_width = total_width * 2 // 3
        splitter = CollapsibleSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setFixedWidth(left_width)
        right = QWidget()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.resize(total_width, 400)
        splitter.setSizes([left_width, total_width - left_width])

        def _on_about_to_toggle(collapsing):
            handle = splitter.handleWidth()
            if collapsing:
                splitter.resize(left_width + handle, 400)
            else:
                splitter.resize(total_width, 400)
            QApplication.processEvents()

        splitter.about_to_toggle.connect(_on_about_to_toggle)
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

    def test_dragging_a_collapsed_pane_open_desyncs_collapsed_flag_without_reconciler(self):
        # Documents the bug this reconciler fixes: a drag that reopens a
        # collapsed pane bypasses toggle_collapse() entirely, so without the
        # splitterMoved reconciliation, _collapsed would be stuck True even
        # though the pane is now visibly open (finding #3).
        total_width = 600
        splitter = self._make(total_width=total_width)
        splitter.toggle_collapse()
        self.assertTrue(splitter.is_collapsed())
        self.assertEqual(splitter.sizes()[1], 0)

        # _make()'s about_to_toggle hookup shrinks the splitter's own width
        # to force a literal fixed-width left pane down to zero -- a test
        # harness detail, not something production does (MainWindow wires
        # no listener to about_to_toggle, since its real left pane isn't a
        # hard setFixedWidth). Widen back to the original full width to
        # model production's actual geometry — an unchanged window width,
        # collapsed pane sitting at zero — before simulating the drag.
        splitter.resize(total_width, 400)
        QApplication.processEvents()

        # Simulate the drag reopening the pane directly (bypassing
        # toggle_collapse()), then fire the same signal Qt emits after a
        # real drag.
        total = sum(splitter.sizes())
        splitter.setSizes([total - 200, 200])
        splitter.splitterMoved.emit(total - 200, 1)

        self.assertGreater(splitter.sizes()[1], 0)
        self.assertFalse(splitter.is_collapsed())

    def test_reconciler_ignores_a_zero_size_move_while_already_expanded(self):
        # Sanity check that the reconciler doesn't misfire on ordinary moves
        # while already expanded (nothing to reconcile there).
        splitter = self._make()
        self.assertFalse(splitter.is_collapsed())
        splitter.splitterMoved.emit(300, 1)
        self.assertFalse(splitter.is_collapsed())


if __name__ == "__main__":
    unittest.main()
