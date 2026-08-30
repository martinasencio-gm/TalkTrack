"""Task 7: user-resized column widths are saved as a fraction of the active
screen's available width, and restored the same way on the next launch.
Drives the flush/restore methods directly rather than simulating a real
mouse drag — QSplitter.splitterMoved wiring itself is a one-line connect,
not worth flaky drag simulation to cover.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestFlushSplitterFraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_flush_splitter2_writes_library_and_transcript_fractions(self):
        window = _make_window(self)
        window.splitter2.resize(1000, 600)
        window.splitter2.setSizes([300, 700])

        window._flush_splitter2_fraction()

        fractions = window.config.get("ui", "panel_fractions")
        self.assertIsNotNone(fractions["library"])
        self.assertIsNotNone(fractions["transcript"])

    def test_flush_splitter1_writes_only_inspector_fraction(self):
        window = _make_window(self)
        window.splitter1.resize(1400, 600)
        window.splitter1.setSizes([1000, 400])

        window._flush_splitter1_fraction()

        fractions = window.config.get("ui", "panel_fractions")
        self.assertIsNotNone(fractions["inspector"])

    def test_flush_with_fewer_than_two_sizes_does_not_raise(self):
        window = _make_window(self)
        window.splitter1.setSizes([100])  # degenerate — must not crash
        window._flush_splitter1_fraction()  # no assertion needed; just must not raise


class TestRestorePanelFractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_restore_applies_a_previously_saved_fraction(self):
        window = _make_window(self)
        window.splitter2.resize(1000, 600)
        window.splitter2.setSizes([300, 700])
        window._flush_splitter2_fraction()
        saved_fractions = window.config.get("ui", "panel_fractions")

        window2 = _make_window(self)
        window2.config.set("ui", "panel_fractions", saved_fractions)
        window2.splitter2.resize(1000, 600)
        window2._restore_panel_fractions()

        # setSizes() normalizes proportionally to actual widget width, so
        # assert the ratio rather than exact pixels.
        sizes = window2.splitter2.sizes()
        self.assertGreater(sizes[1], sizes[0])  # transcript still bigger than library

    def test_restore_with_no_saved_fractions_does_not_raise(self):
        window = _make_window(self)
        window._restore_panel_fractions()  # all-None defaults; must not raise

    def test_collapsed_transcript_column_expands_to_its_saved_width(self):
        # Finding #1: a column that starts collapsed at launch must still
        # expand to its saved fraction-derived width on the first click of
        # the expand arrow, not to whatever clamped size Qt reported while
        # the window was still hidden. Also covers finding #4: this pins a
        # specific numeric expectation instead of a loose ordering check.
        from app.utils.config import Config
        from app.ui.panel_fractions import resolve_pane_size
        from app.utils.screen_utils import get_active_screen

        seed_config = Config()
        fractions = dict(seed_config.get("ui", "panel_fractions") or {})
        fractions["transcript"] = 0.45
        seed_config.set("ui", "panel_fractions", fractions)
        seed_config.set("ui", "transcript_collapsed", True)

        window = _make_window(self)
        self.assertTrue(window.splitter2.is_collapsed())

        screen = get_active_screen(window)
        screen_width = screen.availableGeometry().width() if screen else 0
        expected = resolve_pane_size(0.45, screen_width, 776)

        # _make_window() never shows the window, so without this the
        # splitter's own total width stays at construction-time's tiny
        # hidden geometry, and toggle_collapse()'s restore step clamps to
        # *that* regardless of what _expanded_size holds. A real user only
        # ever clicks the expand arrow after the window is actually on
        # screen and has gone through a real layout pass, so show() here
        # (offscreen platform; no real display needed) is what makes this
        # test representative rather than resizing the nested splitter
        # directly, which fights its parent layout and produces unstable
        # sizes.
        window.show()
        QApplication.processEvents()

        window.splitter2.toggle_collapse()  # expands, since it starts collapsed

        self.assertFalse(window.splitter2.is_collapsed())
        self.assertLessEqual(abs(window.splitter2.sizes()[1] - expected), 2)

    def test_collapsed_inspector_column_expands_to_its_saved_width(self):
        from app.utils.config import Config
        from app.ui.panel_fractions import resolve_pane_size
        from app.utils.screen_utils import get_active_screen

        seed_config = Config()
        fractions = dict(seed_config.get("ui", "panel_fractions") or {})
        # Above InspectorWidget's own setMinimumWidth(322) floor (app/ui/
        # inspector.py) so that floor can't mask a wrong restored value —
        # 0.3 of an 800px test screen (240px) sits below it and would
        # legitimately clamp up to 322 regardless of this fix.
        fractions["inspector"] = 0.5
        seed_config.set("ui", "panel_fractions", fractions)
        seed_config.set("ui", "inspector_collapsed", True)

        window = _make_window(self)
        self.assertTrue(window.splitter1.is_collapsed())

        screen = get_active_screen(window)
        screen_width = screen.availableGeometry().width() if screen else 0
        expected = resolve_pane_size(0.5, screen_width, 322)

        # See the matching comment in the transcript-column test above.
        window.show()
        QApplication.processEvents()

        window.splitter1.toggle_collapse()  # expands, since it starts collapsed

        self.assertFalse(window.splitter1.is_collapsed())
        self.assertLessEqual(abs(window.splitter1.sizes()[1] - expected), 2)


class TestSplitterMovedIsWired(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_dragging_splitter2_starts_the_debounce_timer(self):
        window = _make_window(self)
        self.assertFalse(window._panel_fraction_timer2.isActive())
        window.splitter2.splitterMoved.emit(300, 1)
        self.assertTrue(window._panel_fraction_timer2.isActive())

    def test_dragging_splitter1_starts_the_debounce_timer(self):
        window = _make_window(self)
        self.assertFalse(window._panel_fraction_timer1.isActive())
        window.splitter1.splitterMoved.emit(1000, 1)
        self.assertTrue(window._panel_fraction_timer1.isActive())


if __name__ == "__main__":
    unittest.main()
