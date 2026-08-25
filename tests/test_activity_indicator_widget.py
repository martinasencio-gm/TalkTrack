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

    def test_set_activity_recording_starts_pulse_animation(self):
        from PyQt6.QtCore import QPropertyAnimation
        widget = ActivityIndicator()
        widget.show()
        widget.set_activity("recording", elapsed_seconds=5)
        self.assertEqual(widget._mark_pulse_anim.state(), QPropertyAnimation.State.Running)
        self.assertFalse(widget.pill_btn_stop.isHidden())
        self.assertFalse(widget.pill_btn_pause.isHidden())
        self.assertFalse(widget.pill_mic_meter.isHidden())
        widget.close()

    def test_set_activity_paused_stops_pulse_animation(self):
        from PyQt6.QtCore import QPropertyAnimation
        widget = ActivityIndicator()
        widget.show()
        widget.set_activity("recording", elapsed_seconds=5)
        widget.set_activity("paused", elapsed_seconds=5)
        self.assertEqual(widget._mark_pulse_anim.state(), QPropertyAnimation.State.Stopped)
        self.assertEqual(widget.pill_status_label.text(), "PAUSED")
        widget.close()

    def test_set_activity_transcribing_shows_progress(self):
        widget = ActivityIndicator()
        widget.show()
        widget.set_activity("transcribing", progress_percent=50)
        self.assertEqual(widget.pill_status_label.text(), "Transcribing 50%")
        self.assertTrue(widget.pill_btn_stop.isHidden())
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

    def test_click_stop_emits_stop_requested(self):
        widget = ActivityIndicator()
        received = []
        widget.stop_requested.connect(lambda: received.append(True))
        widget.set_activity("recording")
        widget.pill_btn_stop.click()
        self.assertEqual(received, [True])
        widget.close()

    def test_click_pause_emits_pause_requested(self):
        widget = ActivityIndicator()
        received = []
        widget.pause_requested.connect(lambda: received.append(True))
        widget.set_activity("recording")
        widget.pill_btn_pause.click()
        self.assertEqual(received, [True])
        widget.close()

    def test_click_resume_from_paused_emits_resume_requested(self):
        widget = ActivityIndicator()
        received = []
        widget.resume_requested.connect(lambda: received.append(True))
        widget.set_activity("paused")
        widget.pill_btn_pause.click()
        self.assertEqual(received, [True])
        widget.close()

    def test_update_meters(self):
        widget = ActivityIndicator()
        widget.update_meters(45, 80)
        self.assertEqual(widget.pill_mic_meter.value(), 45)
        self.assertEqual(widget.pill_sys_meter.value(), 80)
        widget.close()


if __name__ == "__main__":
    unittest.main()
