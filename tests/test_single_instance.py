import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.utils.single_instance import SingleInstanceGuard, sweep_orphaned_processes

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestSingleInstanceGuard(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        patcher = patch("app.utils.single_instance.SERVER_NAME", f"TalkTrackTest_{os.getpid()}")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_guard(self):
        guard = SingleInstanceGuard(self.tmp)

        def _cleanup():
            if guard._server is not None:
                guard._server.close()
            guard._lock_file.unlock()
        self.addCleanup(_cleanup)
        return guard

    def test_first_guard_acquires_lock(self):
        guard = self._make_guard()
        self.assertTrue(guard.try_acquire())

    def test_second_guard_on_same_dir_fails_to_acquire(self):
        first = self._make_guard()
        self.assertTrue(first.try_acquire())

        second = self._make_guard()
        self.assertFalse(second.try_acquire())

    def test_notify_running_instance_triggers_show_requested(self):
        first = self._make_guard()
        self.assertTrue(first.try_acquire())
        received = []
        first.show_requested.connect(lambda: received.append(True))

        second = self._make_guard()
        self.assertFalse(second.try_acquire())
        self.assertTrue(second.notify_running_instance())

        app = _get_app()
        for _ in range(100):
            app.processEvents()
            if received:
                break
            time.sleep(0.02)
        self.assertTrue(received)

    def test_notify_running_instance_returns_false_when_nobody_listening(self):
        # Nothing has started a server under SERVER_NAME in this test.
        guard = self._make_guard()
        self.assertFalse(guard.notify_running_instance())


class TestSweepOrphanedProcesses(unittest.TestCase):
    """sweep_orphaned_processes() runs once this process has already won
    the single-instance lock, so anything else out there still running the
    same script is stale by definition — except this process's own venv
    launcher parent, which shares the identical command line."""

    def setUp(self):
        self.script = os.path.abspath(__file__)

    def _make_proc(self, pid, cmdline):
        proc = MagicMock()
        proc.info = {"pid": pid, "cmdline": cmdline}
        return proc

    @patch("app.utils.single_instance.os.getpid", return_value=100)
    @patch("app.utils.single_instance.psutil.Process")
    @patch("app.utils.single_instance.psutil.process_iter")
    def test_terminates_other_process_running_same_script(
        self, mock_iter, mock_process_cls, mock_getpid
    ):
        mock_process_cls.return_value.ppid.return_value = 50
        other = self._make_proc(200, ["pythonw.exe", self.script])
        mock_iter.return_value = [other]

        killed = sweep_orphaned_processes(self.script)

        other.terminate.assert_called_once()
        other.wait.assert_called_once()
        self.assertEqual(killed, [200])

    @patch("app.utils.single_instance.os.getpid", return_value=100)
    @patch("app.utils.single_instance.psutil.Process")
    @patch("app.utils.single_instance.psutil.process_iter")
    def test_skips_own_pid_and_own_parent_pid(
        self, mock_iter, mock_process_cls, mock_getpid
    ):
        mock_process_cls.return_value.ppid.return_value = 50
        self_proc = self._make_proc(100, ["pythonw.exe", self.script])
        parent_proc = self._make_proc(50, ["pythonw.exe", self.script])
        mock_iter.return_value = [self_proc, parent_proc]

        killed = sweep_orphaned_processes(self.script)

        self_proc.terminate.assert_not_called()
        parent_proc.terminate.assert_not_called()
        self.assertEqual(killed, [])

    @patch("app.utils.single_instance.os.getpid", return_value=100)
    @patch("app.utils.single_instance.psutil.Process")
    @patch("app.utils.single_instance.psutil.process_iter")
    def test_ignores_processes_running_a_different_script(
        self, mock_iter, mock_process_cls, mock_getpid
    ):
        mock_process_cls.return_value.ppid.return_value = 50
        unrelated = self._make_proc(300, ["notepad.exe", "C:\\some\\other\\file.txt"])
        mock_iter.return_value = [unrelated]

        killed = sweep_orphaned_processes(self.script)

        unrelated.terminate.assert_not_called()
        self.assertEqual(killed, [])

    @patch("app.utils.single_instance.os.getpid", return_value=100)
    @patch("app.utils.single_instance.psutil.Process")
    @patch("app.utils.single_instance.psutil.process_iter")
    def test_kills_process_that_ignores_terminate(
        self, mock_iter, mock_process_cls, mock_getpid
    ):
        import psutil as real_psutil

        mock_process_cls.return_value.ppid.return_value = 50
        stubborn = self._make_proc(200, ["pythonw.exe", self.script])
        stubborn.wait.side_effect = real_psutil.TimeoutExpired(200)
        mock_iter.return_value = [stubborn]

        killed = sweep_orphaned_processes(self.script)

        stubborn.terminate.assert_called_once()
        stubborn.kill.assert_called_once()
        self.assertEqual(killed, [200])


if __name__ == "__main__":
    unittest.main()
