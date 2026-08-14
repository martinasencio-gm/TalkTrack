import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest

from app.utils.com_session_worker import ComSessionPoller


def _fake_worker_blocks_forever(result_queue, interval, stop_event, main_pid):
    stop_event.wait()


def _fake_worker_puts_once(result_queue, interval, stop_event, main_pid):
    result_queue.put({"audio_apps": ["FakeApp"], "mic_pids": {123}})
    stop_event.wait()


def _fake_worker_dies_immediately(result_queue, interval, stop_event, main_pid):
    return


def _fake_worker_reports_interval(result_queue, interval, stop_event, main_pid):
    while not stop_event.is_set():
        result_queue.put({"audio_apps": [], "mic_pids": set(),
                           "interval_seen": interval.value})
        stop_event.wait(0.05)


def _wait_until(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


class TestComSessionPoller(unittest.TestCase):
    def setUp(self):
        self._poller = None

    def tearDown(self):
        if self._poller is not None:
            self._poller.stop()

    def _make(self, worker_target):
        self._poller = ComSessionPoller(main_pid=os.getpid(), worker_target=worker_target)
        return self._poller

    def test_default_snapshot_before_any_result(self):
        poller = self._make(_fake_worker_blocks_forever)
        poller.start()
        snapshot = poller.get_snapshot()
        self.assertEqual(snapshot, {"audio_apps": [], "mic_pids": set()})

    def test_returns_queued_snapshot_once_worker_reports(self):
        poller = self._make(_fake_worker_puts_once)
        poller.start()
        snapshot = _wait_until(
            lambda: poller.get_snapshot() if poller.get_snapshot()["audio_apps"] else None
        )
        self.assertEqual(snapshot["audio_apps"], ["FakeApp"])
        self.assertEqual(snapshot["mic_pids"], {123})

    def test_dead_worker_is_respawned_after_backoff_elapses(self):
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        first_pid = poller._process.pid
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        self.assertFalse(poller._process.is_alive())

        # Bypass the backoff window to simulate enough time having passed.
        poller._last_restart_ts = time.monotonic() - 10.0
        poller.get_snapshot()

        respawned = _wait_until(lambda: poller._process if poller._process.pid != first_pid else None)
        self.assertIsNotNone(respawned)
        self.assertTrue(poller._process.is_alive())

    def test_dead_worker_is_not_respawned_within_backoff_window(self):
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        dead_process = poller._process

        # Simulate a restart having *just* happened.
        poller._last_restart_ts = time.monotonic()
        poller.get_snapshot()

        self.assertIs(poller._process, dead_process)

    def test_set_interval_updates_shared_value_worker_reads(self):
        poller = self._make(_fake_worker_reports_interval)
        poller.start()
        poller.set_interval(2.5)
        snapshot = _wait_until(
            lambda: poller.get_snapshot() if poller.get_snapshot().get("interval_seen") == 2.5 else None
        )
        self.assertIsNotNone(snapshot)


if __name__ == "__main__":
    unittest.main()
