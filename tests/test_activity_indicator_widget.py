import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from app.ui.activity_indicator import ActivityIndicator

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class _FakeLeftButtonEvent:
    def button(self):
        return Qt.MouseButton.LeftButton


class TestActivityIndicatorWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_set_activity_recording_starts_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        self.assertTrue(widget._pulse_timer.isActive())
        widget.close()

    def test_set_activity_paused_stops_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        widget.set_activity("paused", elapsed_seconds=5)
        self.assertFalse(widget._pulse_timer.isActive())
        widget.close()

    def test_set_activity_transcribing_stops_pulse_timer(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=5)
        widget.set_activity("transcribing", progress_percent=50)
        self.assertFalse(widget._pulse_timer.isActive())
        widget.close()

    def test_show_at_clamps_to_screen_geometry(self):
        widget = ActivityIndicator()
        widget.show_at(-5000, -5000)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.assertGreaterEqual(widget.x(), geo.left())
        self.assertGreaterEqual(widget.y(), geo.top())
        widget.close()

    def test_show_at_makes_widget_visible(self):
        widget = ActivityIndicator()
        widget.show_at(100, 100)
        self.assertTrue(widget.isVisible())
        widget.close()

    def test_click_without_drag_emits_restore_requested(self):
        widget = ActivityIndicator()
        received = []
        widget.restore_requested.connect(lambda: received.append(True))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 0
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(received, [True])
        widget.close()

    def test_drag_past_threshold_emits_position_changed(self):
        widget = ActivityIndicator()
        received = []
        widget.position_changed.connect(lambda x, y: received.append((x, y)))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 20
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(len(received), 1)
        widget.close()

    def test_drag_at_exactly_threshold_emits_restore_not_position(self):
        widget = ActivityIndicator()
        restored = []
        moved = []
        widget.restore_requested.connect(lambda: restored.append(True))
        widget.position_changed.connect(lambda x, y: moved.append((x, y)))
        widget._press_pos = QPoint(10, 10)
        widget._press_widget_pos = widget.pos()
        widget._moved_distance = 4
        widget.mouseReleaseEvent(_FakeLeftButtonEvent())
        self.assertEqual(restored, [True])
        self.assertEqual(moved, [])
        widget.close()

    def test_pulse_phase_preserved_across_same_state_calls(self):
        widget = ActivityIndicator()
        widget.set_activity("recording", elapsed_seconds=1)
        # Simulate a pulse tick by toggling the dot visibility
        widget._dot_visible = False
        # Refresh with the same state (e.g., updating elapsed time)
        widget.set_activity("recording", elapsed_seconds=2)
        # Verify pulse phase was preserved (dot still hidden)
        self.assertFalse(widget._dot_visible)
        widget.close()


if __name__ == "__main__":
    unittest.main()
