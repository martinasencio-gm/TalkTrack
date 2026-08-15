import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.delete_scope_dialog import (
    DeleteScopeDialog, DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS, DELETE_BOTH
)

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestDeleteScopeDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_defaults_to_both(self):
        dialog = DeleteScopeDialog(count=1)
        self.assertEqual(dialog.selected_scope(), DELETE_BOTH)

    def test_selecting_recordings_only(self):
        dialog = DeleteScopeDialog(count=1)
        dialog._recordings_radio.setChecked(True)
        self.assertEqual(dialog.selected_scope(), DELETE_RECORDINGS)

    def test_selecting_transcriptions_only(self):
        dialog = DeleteScopeDialog(count=1)
        dialog._transcriptions_radio.setChecked(True)
        self.assertEqual(dialog.selected_scope(), DELETE_TRANSCRIPTIONS)

    def test_singular_title_for_one_recording(self):
        dialog = DeleteScopeDialog(count=1)
        self.assertEqual(dialog.windowTitle(), "Delete Recording")

    def test_plural_title_for_multiple_recordings(self):
        dialog = DeleteScopeDialog(count=3)
        self.assertEqual(dialog.windowTitle(), "Delete Recordings")


if __name__ == "__main__":
    unittest.main()
