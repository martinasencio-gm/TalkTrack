import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.batch.launcher import find_pythonw_executable, launch_detached_batch


class TestBatchLauncher(unittest.TestCase):
    def test_find_pythonw_executable_finds_venv(self):
        with patch.object(Path, "exists", return_value=True):
            exe = find_pythonw_executable(repo_root="/fake/repo")
            self.assertTrue(exe.endswith("pythonw.exe") or exe.endswith("python.exe"))

    @patch("app.batch.launcher.subprocess.Popen")
    @patch("app.batch.launcher.find_pythonw_executable", return_value="pythonw.exe")
    @patch.object(Path, "exists", return_value=True)
    def test_launch_detached_batch_default_args(self, mock_exists, mock_find, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        proc = launch_detached_batch(repo_root="/fake/repo", until="07:00")
        self.assertEqual(proc.pid, 1234)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "pythonw.exe")
        self.assertTrue(cmd[1].endswith("batch_transcribe.py"))
        self.assertIn("--until", cmd)
        self.assertIn("07:00", cmd)

    @patch("app.batch.launcher.subprocess.Popen")
    @patch("app.batch.launcher.find_pythonw_executable", return_value="pythonw.exe")
    @patch.object(Path, "exists", return_value=True)
    def test_launch_detached_batch_with_options(self, mock_exists, mock_find, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        launch_detached_batch(
            repo_root="/fake/repo",
            until="2026-08-18T18:00",
            diarize=True,
            limit=5,
        )

        cmd = mock_popen.call_args[0][0]
        self.assertIn("--diarize", cmd)
        self.assertIn("--limit", cmd)
        self.assertIn("5", cmd)

    @patch("app.batch.launcher.subprocess.Popen")
    @patch("app.batch.launcher.find_pythonw_executable", return_value="pythonw.exe")
    @patch.object(Path, "exists", return_value=True)
    def test_launch_detached_batch_no_diarize(self, mock_exists, mock_find, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        launch_detached_batch(
            repo_root="/fake/repo",
            until="2026-08-18T18:00",
            diarize=False,
        )

        cmd = mock_popen.call_args[0][0]
        self.assertIn("--no-diarize", cmd)


if __name__ == "__main__":
    unittest.main()
