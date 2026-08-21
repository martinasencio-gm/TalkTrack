"""Tests for batch process monitor discovery, duration formatting, and termination."""
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from app.batch.process_monitor import (
    BatchProcessInfo,
    find_running_batch_processes,
    format_duration,
    terminate_batch_process,
)


class TestBatchProcessMonitor(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(45), "00:45")
        self.assertEqual(format_duration(75), "01:15")
        self.assertEqual(format_duration(3665), "01:01:05")

    def test_batch_process_info_properties(self):
        start_ts = time.time() - 125
        info = BatchProcessInfo(
            pid=1234,
            create_time=start_ts,
            is_in_app=False,
            cmdline=["pythonw.exe", "c:\\src\\batch_transcribe.py", "--until", "23:59", "--diarize"],
            name="pythonw.exe",
        )
        self.assertGreaterEqual(info.elapsed_seconds, 120)
        self.assertEqual(info.process_type_label, "Detached Background Process")
        self.assertIn("--until 23:59 --diarize", info.arguments_summary)
        self.assertNotEqual(info.formatted_start_time, "Unknown")

    def test_batch_process_info_in_app(self):
        start_ts = time.time() - 30
        info = BatchProcessInfo(
            pid=os.getpid(),
            create_time=start_ts,
            is_in_app=True,
            name="TalkTrack (In-App)",
        )
        self.assertEqual(info.process_type_label, "In-App Worker Thread")
        self.assertEqual(info.arguments_summary, "Running inside TalkTrack")

    def test_find_running_batch_processes_detects_in_app_worker(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True

        with patch("psutil.process_iter", return_value=[]):
            procs = find_running_batch_processes(in_app_worker=mock_worker, in_app_start_time=1000.0)
            self.assertEqual(len(procs), 1)
            self.assertTrue(procs[0].is_in_app)
            self.assertEqual(procs[0].pid, os.getpid())
            self.assertEqual(procs[0].create_time, 1000.0)

    def test_find_running_batch_processes_detects_external_process(self):
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 5678,
            "name": "python.exe",
            "cmdline": ["python.exe", "C:\\path\\batch_transcribe.py", "--diarize"],
            "create_time": 2000.0,
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            procs = find_running_batch_processes(in_app_worker=None)
            self.assertEqual(len(procs), 1)
            self.assertFalse(procs[0].is_in_app)
            self.assertEqual(procs[0].pid, 5678)
            self.assertEqual(procs[0].create_time, 2000.0)

    def test_terminate_in_app_worker(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True

        res = terminate_batch_process(os.getpid(), in_app_worker=mock_worker)
        self.assertTrue(res)
        mock_worker.cancel.assert_called_once()

    def test_terminate_external_process(self):
        mock_proc = MagicMock()
        with patch("psutil.Process", return_value=mock_proc):
            res = terminate_batch_process(9999, in_app_worker=None)
            self.assertTrue(res)
            mock_proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
