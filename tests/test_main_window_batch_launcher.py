import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowBatchLauncher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            window.close()

        self.addCleanup(_close)
        return window

    def test_open_batch_run_dialog_starts_in_app_batch(self):
        window = self._make_window()
        mock_job = MagicMock()

        with patch("app.batch.worklist.build_worklist", return_value=[mock_job]), \
             patch("app.ui.batch_run_dialog.BatchRunDialog.exec", return_value=True), \
             patch.object(window, "_start_in_app_batch") as mock_start:
            window._open_batch_run_dialog()
            mock_start.assert_called_once()

    def test_open_batch_run_dialog_launches_detached_batch(self):
        window = self._make_window()
        mock_job = MagicMock()

        with patch("app.batch.worklist.build_worklist", return_value=[mock_job]), \
             patch("app.ui.batch_run_dialog.BatchRunDialog.exec", return_value=True), \
             patch("app.ui.batch_run_dialog.BatchRunDialog.execution_mode", return_value="detached"), \
             patch.object(window, "_launch_detached_batch") as mock_detached:
            window._open_batch_run_dialog()
            mock_detached.assert_called_once()

    def test_start_in_app_batch_creates_and_starts_worker(self):
        window = self._make_window()

        with patch("app.batch.worker.BatchRunnerWorker.start") as mock_start:
            window._start_in_app_batch()
            self.assertIsNotNone(window._batch_worker)
            mock_start.assert_called_once()

    def test_batch_worker_lifecycle_in_transcription_busy_and_shutdown(self):
        window = self._make_window()
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        window._batch_worker = mock_worker

        self.assertTrue(window._transcription_busy())

        with patch.object(mock_worker, "wait", return_value=True):
            window._shutdown_workers()
            mock_worker.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
