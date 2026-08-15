import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import time
import unittest

from PyQt6.QtWidgets import QApplication

from app.utils.single_instance import SingleInstanceGuard

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


if __name__ == "__main__":
    unittest.main()
