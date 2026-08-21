"""Tests for BatchProcessInfoDialog UI."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.batch.process_monitor import BatchProcessInfo
from app.ui.batch_process_info_dialog import BatchProcessInfoDialog

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestBatchProcessInfoDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_dialog_renders_info(self):
        info = BatchProcessInfo(
            pid=4321,
            create_time=1000.0,
            is_in_app=False,
            cmdline=["pythonw.exe", "batch_transcribe.py", "--limit", "5"],
            name="pythonw.exe",
        )
        dialog = BatchProcessInfoDialog(info)
        self.assertIn("4321", dialog._pid_label.text())
        self.assertIn("Detached Background Process", dialog._type_label.text())
        self.assertIn("--limit 5", dialog._args_label.text())

    def test_open_log_clicked(self):
        info = BatchProcessInfo(pid=4321, create_time=1000.0)
        dialog = BatchProcessInfoDialog(info)

        with patch("app.ui.batch_process_info_dialog.open_batch_log") as mock_open_log:
            dialog._on_open_log_clicked()
            mock_open_log.assert_called_once()

    def test_end_process_cancelled_by_user(self):
        info = BatchProcessInfo(pid=4321, create_time=1000.0)
        dialog = BatchProcessInfoDialog(info)

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch("app.ui.batch_process_info_dialog.terminate_batch_process") as mock_term:
            dialog._on_end_clicked()
            mock_term.assert_not_called()

    def test_end_process_confirmed_by_user(self):
        info = BatchProcessInfo(pid=4321, create_time=1000.0)
        dialog = BatchProcessInfoDialog(info)

        terminated_pids = []
        dialog.process_terminated.connect(terminated_pids.append)

        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("PyQt6.QtWidgets.QMessageBox.information"), \
             patch("app.ui.batch_process_info_dialog.terminate_batch_process", return_value=True) as mock_term:
            dialog._on_end_clicked()
            mock_term.assert_called_once_with(4321, in_app_worker=None)
            self.assertEqual(terminated_pids, [4321])

    def test_multiple_processes_rendered_and_terminated(self):
        p1 = BatchProcessInfo(pid=1001, create_time=1000.0, is_in_app=False, cmdline=["python", "batch_transcribe.py"])
        p2 = BatchProcessInfo(pid=1002, create_time=1000.0, is_in_app=True)
        dialog = BatchProcessInfoDialog([p1, p2])

        self.assertEqual(len(dialog.processes), 2)
        self.assertIn("2 Batch Jobs", dialog._title_label.text())
        self.assertIn(1001, dialog._process_cards)
        self.assertIn(1002, dialog._process_cards)

        # Terminate one
        terminated_pids = []
        dialog.process_terminated.connect(terminated_pids.append)
        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("app.ui.batch_process_info_dialog.terminate_batch_process", return_value=True) as mock_term:
            dialog._on_end_clicked_proc(p1)
            mock_term.assert_called_once_with(1001, in_app_worker=None)
            self.assertEqual(terminated_pids, [1001])
            self.assertEqual(len(dialog.processes), 1)


if __name__ == "__main__":
    unittest.main()
