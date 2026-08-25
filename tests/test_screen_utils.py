import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QWidget, QDialog

from app.utils.screen_utils import (
    get_active_screen,
    center_on_active_screen,
    position_corner_on_active_screen,
    ensure_within_screens,
)

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestScreenUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_get_active_screen_returns_screen(self):
        screen = get_active_screen()
        self.assertIsNotNone(screen)

    def test_get_active_screen_with_visible_widget(self):
        widget = QWidget()
        widget.show()
        screen = get_active_screen(widget)
        self.assertIsNotNone(screen)
        widget.close()

    def test_center_on_active_screen_positions_within_geometry(self):
        dialog = QDialog()
        dialog.resize(300, 200)
        center_on_active_screen(dialog)
        screen = get_active_screen()
        geo = screen.availableGeometry()

        self.assertGreaterEqual(dialog.x(), geo.left())
        self.assertGreaterEqual(dialog.y(), geo.top())
        self.assertLessEqual(dialog.x() + dialog.width(), geo.right() + 1)
        self.assertLessEqual(dialog.y() + dialog.height(), geo.bottom() + 1)
        dialog.close()

    def test_center_on_active_screen_with_parent(self):
        parent = QWidget()
        parent.setGeometry(100, 100, 600, 400)
        parent.show()

        child = QDialog(parent)
        child.resize(200, 100)
        center_on_active_screen(child, parent)

        # Child should be centered over parent
        expected_x = parent.geometry().x() + (parent.geometry().width() - child.width()) // 2
        expected_y = parent.geometry().y() + (parent.geometry().height() - child.height()) // 2
        self.assertEqual(child.x(), expected_x)
        self.assertEqual(child.y(), expected_y)

        child.close()
        parent.close()

    def test_position_corner_bottom_right(self):
        widget = QWidget()
        widget.resize(200, 100)
        position_corner_on_active_screen(widget, corner="bottom-right", margin=20)
        screen = get_active_screen()
        geo = screen.availableGeometry()

        self.assertEqual(widget.x(), geo.right() - widget.width() - 20)
        self.assertEqual(widget.y(), geo.bottom() - widget.height() - 20)
        widget.close()

    def test_position_corner_top_right(self):
        widget = QWidget()
        widget.resize(200, 100)
        position_corner_on_active_screen(widget, corner="top-right", margin=15)
        screen = get_active_screen()
        geo = screen.availableGeometry()

        self.assertEqual(widget.x(), geo.right() - widget.width() - 15)
        self.assertEqual(widget.y(), geo.top() + 15)
        widget.close()

    def test_ensure_within_screens_valid_coordinates(self):
        screen = get_active_screen()
        geo = screen.availableGeometry()
        valid_x = geo.center().x()
        valid_y = geo.center().y()

        clamped_x, clamped_y = ensure_within_screens(valid_x, valid_y, width=100, height=50)
        self.assertEqual(clamped_x, valid_x)
        self.assertEqual(clamped_y, valid_y)

    def test_ensure_within_screens_recovers_offscreen_coordinates(self):
        # Far outside any monitor (e.g. disconnected 4K display at x=99999, y=99999)
        clamped_x, clamped_y = ensure_within_screens(99999, 99999, width=200, height=60)
        screen = get_active_screen()
        geo = screen.availableGeometry()

        # Should recover to inside the available geometry
        self.assertGreaterEqual(clamped_x, geo.left())
        self.assertLessEqual(clamped_x + 200, geo.right() + 1)
        self.assertGreaterEqual(clamped_y, geo.top())
        self.assertLessEqual(clamped_y + 60, geo.bottom() + 1)

    def test_dialogs_have_stay_on_top_hint(self):
        from PyQt6.QtCore import Qt
        from app.ui.about_dialog import AboutDialog
        from app.ui.delete_scope_dialog import DeleteScopeDialog
        from app.ui.import_timestamp_dialog import ImportTimestampDialog
        from datetime import datetime

        about = AboutDialog()
        self.assertTrue(bool(about.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        about.close()

        delete = DeleteScopeDialog(1)
        self.assertTrue(bool(delete.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        delete.close()

        import_dlg = ImportTimestampDialog(datetime.now())
        self.assertTrue(bool(import_dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        import_dlg.close()


if __name__ == "__main__":
    unittest.main()
