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

    def test_batch_indicator_updates_on_poll(self):
        from app.batch.process_monitor import BatchProcessInfo
        window = self._make_window()

        # No processes -> indicator hidden
        with patch("app.batch.process_monitor.find_running_batch_processes", return_value=[]):
            window._poll_batch_processes()
            self.assertTrue(window.batch_indicator.isHidden())

        # Running process -> indicator visible
        proc_info = BatchProcessInfo(pid=7777, create_time=1000.0, is_in_app=False)
        with patch("app.batch.process_monitor.find_running_batch_processes", return_value=[proc_info]):
            window._poll_batch_processes()
            self.assertFalse(window.batch_indicator.isHidden())
            self.assertIn("7777", window.batch_indicator.text())

    def test_show_batch_process_info_opens_dialog(self):
        from app.batch.process_monitor import BatchProcessInfo
        window = self._make_window()
        proc_info = BatchProcessInfo(pid=8888, create_time=1000.0, is_in_app=False)

        with patch("app.batch.process_monitor.find_running_batch_processes", return_value=[proc_info]), \
             patch("app.ui.batch_process_info_dialog.BatchProcessInfoDialog.exec") as mock_exec:
            window._show_batch_process_info()
            mock_exec.assert_called_once()

    def test_batch_btn_hides_when_batch_process_is_running(self):
        from app.batch.process_monitor import BatchProcessInfo
        window = self._make_window()
        # Mock queued recording
        window.recordings_list._recordings = [{"directory": "test_rec", "batch_pending": True}]

        # When no batch process is running, batch button should be visible (not hidden)
        with patch("app.batch.process_monitor.find_running_batch_processes", return_value=[]):
            window._poll_batch_processes()
            self.assertFalse(window.recordings_list.batch_btn.isHidden())
            self.assertEqual(window.recordings_list.batch_btn.text(), "Run Batch (1)")

        # When batch process is running, batch button should hide
        proc_info = BatchProcessInfo(pid=9999, create_time=1000.0, is_in_app=False)
        with patch("app.batch.process_monitor.find_running_batch_processes", return_value=[proc_info]):
            window._poll_batch_processes()
            self.assertTrue(window.recordings_list.batch_btn.isHidden())

    def test_launch_detached_batch_enqueues_notification(self):
        window = self._make_window()
        mock_proc = MagicMock(pid=1234)

        with patch("app.batch.launcher.launch_detached_batch", return_value=mock_proc), \
             patch.object(window, "_poll_batch_processes"), \
             patch.object(window.notification_region, "enqueue") as mock_enqueue:
            window._launch_detached_batch()
            mock_enqueue.assert_called_once()
            args, kwargs = mock_enqueue.call_args
            self.assertIn("1234", kwargs.get("text", "") or args[1])

    def test_batch_finished_enqueues_notification(self):
        window = self._make_window()
        with patch.object(window.notification_region, "enqueue") as mock_enqueue:
            window._on_batch_finished(processed=3, failed=0, deferred=0)
            mock_enqueue.assert_called_once()
            args, kwargs = mock_enqueue.call_args
            self.assertIn("3 recording(s)", kwargs.get("text", "") or args[1])

    def test_open_batch_logs_actions(self):
        window = self._make_window()

        with patch("app.batch.logging_setup.open_batch_logs_folder") as mock_open_folder:
            window._open_batch_logs_folder()
            mock_open_folder.assert_called_once()

        with patch("app.batch.logging_setup.open_batch_log") as mock_open_log:
            window._open_batch_log_file()
            mock_open_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
