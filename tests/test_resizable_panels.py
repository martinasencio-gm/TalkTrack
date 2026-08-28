"""Smoke tests: Call Notes / Summary boxes gained a vertical resize grip,
and it tracks the same show/hide lifecycle as its target."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.notes_panel import NotesPanel
from app.ui.summary_panel import SummaryPanel
from app.ui.vertical_resize_grip import VerticalResizeGrip

_app = QApplication.instance() or QApplication([])


class TestNotesPanelResizable(unittest.TestCase):
    def test_editor_has_fixed_starting_height(self):
        panel = NotesPanel()
        self.assertEqual(panel.editor.height(), 160)


class TestSummaryPanelResizable(unittest.TestCase):
    def test_grip_shown_with_summary_hidden_otherwise(self):
        panel = SummaryPanel()
        self.assertTrue(panel._resize_grip.isHidden())
        panel.set_summary("hello world")
        self.assertFalse(panel._resize_grip.isHidden())
        panel.clear()
        self.assertTrue(panel._resize_grip.isHidden())

    def test_grip_hidden_during_loading_and_restored_on_error_with_prior_text(self):
        panel = SummaryPanel()
        panel.set_summary("existing summary")
        panel.set_loading()
        self.assertTrue(panel._resize_grip.isHidden())
        panel.set_error()
        self.assertFalse(panel._resize_grip.isHidden())


class TestSummaryPanelDelete(unittest.TestCase):
    def test_delete_button_tracks_summary_visibility(self):
        panel = SummaryPanel()
        self.assertTrue(panel._delete_btn.isHidden())
        panel.set_summary("a summary")
        self.assertFalse(panel._delete_btn.isHidden())
        panel.set_loading()
        self.assertTrue(panel._delete_btn.isHidden())
        panel.set_error()  # prior text -> summary restored, delete back
        self.assertFalse(panel._delete_btn.isHidden())
        panel.clear()
        self.assertTrue(panel._delete_btn.isHidden())

    def test_delete_button_emits_delete_requested(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        seen = []
        panel.delete_requested.connect(lambda: seen.append(True))
        panel._delete_btn.click()
        self.assertEqual(seen, [True])


class TestSummaryPanelMeta(unittest.TestCase):
    def test_set_meta_shows_model_and_time(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        panel.set_meta("claude/claude-sonnet-4-6", 4.2)
        self.assertFalse(panel._meta_label.isHidden())
        text = panel._meta_label.text()
        self.assertIn("claude/claude-sonnet-4-6", text)
        self.assertIn("4.2s", text)

    def test_set_meta_formats_minutes(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        panel.set_meta("local-model", 95)
        self.assertIn("1m 35s", panel._meta_label.text())

    def test_empty_meta_hides_label(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        panel.set_meta("m", 1.0)
        panel.set_meta()
        self.assertTrue(panel._meta_label.isHidden())

    def test_loading_hides_meta(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        panel.set_meta("m", 1.0)
        panel.set_loading()
        self.assertTrue(panel._meta_label.isHidden())

    def test_clear_hides_meta(self):
        panel = SummaryPanel()
        panel.set_summary("a summary")
        panel.set_meta("m", 1.0)
        panel.clear()
        self.assertTrue(panel._meta_label.isHidden())


class TestVerticalResizeGripIntegration(unittest.TestCase):
    def test_drag_resizes_target_within_bounds(self):
        panel = NotesPanel()
        grip = VerticalResizeGrip(panel.editor, min_height=80, max_height=300)
        grip._start_height = panel.editor.height()
        grip._drag_start_y = 0
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt as QtNS

        event = QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(0, 500), QPointF(0, 500),
            QtNS.MouseButton.NoButton, QtNS.MouseButton.NoButton,
            QtNS.KeyboardModifier.NoModifier,
        )
        grip.mouseMoveEvent(event)
        self.assertEqual(panel.editor.height(), 300)  # clamped to max


if __name__ == "__main__":
    unittest.main()
