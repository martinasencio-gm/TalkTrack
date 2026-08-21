"""Tests for batch run logging helpers."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.batch.logging_setup import (
    get_latest_log,
    open_batch_log,
    open_batch_logs_folder,
    prune_old_logs,
)


class TestBatchLoggingSetup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_get_latest_log_empty_dir(self):
        self.assertIsNone(get_latest_log(directory=self.log_dir))

    def test_get_latest_log_nonexistent_dir(self):
        non_existent = self.log_dir / "does_not_exist"
        self.assertIsNone(get_latest_log(directory=non_existent))

    def test_get_latest_log_returns_newest(self):
        (self.log_dir / "batch_20260101_100000.log").write_text("old", encoding="utf-8")
        (self.log_dir / "batch_20260102_120000.log").write_text("newer", encoding="utf-8")
        (self.log_dir / "batch_20260103_090000.log").write_text("newest", encoding="utf-8")

        latest = get_latest_log(directory=self.log_dir)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.name, "batch_20260103_090000.log")

    def test_open_batch_logs_folder_invokes_startfile(self):
        with patch("os.startfile") as mock_startfile:
            res = open_batch_logs_folder(directory=self.log_dir)
            self.assertEqual(res, self.log_dir)
            if sys.platform == "win32":
                mock_startfile.assert_called_once_with(str(self.log_dir))

    def test_open_batch_log_with_specific_file(self):
        log_file = self.log_dir / "batch_20260101_100000.log"
        log_file.write_text("log content", encoding="utf-8")

        with patch("os.startfile") as mock_startfile:
            res = open_batch_log(log_path=log_file, directory=self.log_dir)
            self.assertEqual(res, log_file)
            if sys.platform == "win32":
                mock_startfile.assert_called_once_with(str(log_file))

    def test_open_batch_log_falls_back_to_latest(self):
        log_file = self.log_dir / "batch_20260101_100000.log"
        log_file.write_text("log content", encoding="utf-8")

        with patch("os.startfile") as mock_startfile:
            res = open_batch_log(directory=self.log_dir)
            self.assertEqual(res, log_file)
            if sys.platform == "win32":
                mock_startfile.assert_called_once_with(str(log_file))

    def test_open_batch_log_falls_back_to_folder_when_no_files(self):
        with patch("os.startfile") as mock_startfile:
            res = open_batch_log(directory=self.log_dir)
            self.assertEqual(res, self.log_dir)
            if sys.platform == "win32":
                mock_startfile.assert_called_once_with(str(self.log_dir))


if __name__ == "__main__":
    unittest.main()
