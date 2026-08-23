"""Smoke tests: Call Notes / Summary / Action Items boxes gained a vertical
resize grip, and it tracks the same show/hide lifecycle as its target."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.action_items_panel import ActionItemsPanel
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


class TestActionItemsPanelResizable(unittest.TestCase):
    def test_grip_shown_with_items_hidden_otherwise(self):
        panel = ActionItemsPanel()
        self.assertTrue(panel._resize_grip.isHidden())
        panel.set_items([{"task": "follow up", "assignee": "", "deadline": ""}])
        self.assertFalse(panel._resize_grip.isHidden())
        panel.clear()
        self.assertTrue(panel._resize_grip.isHidden())


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
