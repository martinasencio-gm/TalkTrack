import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import time
import unittest
from unittest import mock

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
        self.assertEqual(snapshot, {"audio_apps": [], "mic_pids": set(),
                                    "render_peaks": {}, "app_devices": {}})

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

    def test_get_snapshot_returns_cached_when_queue_read_raises(self):
        """get_snapshot() returns cached snapshot when queue.get_nowait() raises EOFError."""
        poller = self._make(_fake_worker_blocks_forever)
        poller.start()
        # Set a known cached value
        poller._cached_snapshot = {"audio_apps": ["TestApp"], "mic_pids": {999}}
        # Mock queue to raise EOFError (simulating corrupted pipe mid-read)
        with mock.patch.object(poller._queue, "get_nowait", side_effect=EOFError("pipe closed")):
            snapshot = poller.get_snapshot()
        # Should return the cached snapshot, not raise
        self.assertEqual(snapshot["audio_apps"], ["TestApp"])
        self.assertEqual(snapshot["mic_pids"], {999})

    def test_get_snapshot_returns_cached_when_respawn_raises(self):
        """get_snapshot() returns cached snapshot when start() raises during respawn."""
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        # Wait for worker to die
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        # Set a known cached value
        poller._cached_snapshot = {"audio_apps": ["CachedApp"], "mic_pids": {777}}
        # Bypass backoff window
        poller._last_restart_ts = time.monotonic() - 10.0
        # Mock start() to raise OSError (simulating resource exhaustion)
        with mock.patch.object(poller, "start", side_effect=OSError("Resource temporarily unavailable")):
            snapshot = poller.get_snapshot()
        # Should return the cached snapshot, not raise
        self.assertEqual(snapshot["audio_apps"], ["CachedApp"])
        self.assertEqual(snapshot["mic_pids"], {777})

    def test_queue_is_recreated_on_each_start_not_reused_across_respawns(self):
        """Reproduces the wedge scenario: a worker generation whose put()
        drained the queue's internal semaphore (e.g. died mid-put after a
        native crash) must not permanently block every future respawn's
        put_nowait(). Proven by observing that a *second* start() (a
        respawn) produces a queue that a fresh put/get still flows through,
        which is only possible if start() built a brand new Queue rather
        than reusing the one whose semaphore was left drained.
        """
        poller = self._make(_fake_worker_blocks_forever)
        poller.start()
        first_queue = poller._queue

        # Simulate the crash-mid-put scenario: acquire the put semaphore by
        # putting a value, and do NOT get() it, and do NOT let anything else
        # get() it either. Then fill it again the way a stuck queue would
        # reject a second put.
        first_queue.put_nowait({"audio_apps": ["stale"], "mic_pids": set()})
        with self.assertRaises(queue.Full):
            first_queue.put_nowait({"audio_apps": ["should not fit"], "mic_pids": set()})

        # A respawn (as triggered by get_snapshot() after the worker dies)
        # must swap in a fresh queue rather than reusing the wedged one.
        poller.start()
        second_queue = poller._queue
        self.assertIsNot(second_queue, first_queue)

        # Prove it's not just a different object but an actually-usable,
        # unwedged channel: fresh data flows through it end-to-end.
        # put_nowait() hands off to a background feeder thread that flushes
        # asynchronously, so poll for the result rather than reading once
        # immediately (avoids a race against that thread).
        second_queue.put_nowait({"audio_apps": ["fresh"], "mic_pids": {42}})
        snapshot = _wait_until(
            lambda: poller.get_snapshot() if poller.get_snapshot()["audio_apps"] else None
        )
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["audio_apps"], ["fresh"])
        self.assertEqual(snapshot["mic_pids"], {42})

    def test_stop_then_get_snapshot_does_not_respawn(self):
        """stop() must leave the poller in a state where a later
        get_snapshot() (e.g. a QTimer tick racing shutdown) does not treat
        the clean shutdown as a crash and spawn a fresh live worker.
        """
        poller = self._make(_fake_worker_blocks_forever)
        poller.start()
        poller.stop()
        self.assertIsNone(poller._process)

        snapshot = poller.get_snapshot()

        self.assertIsNone(poller._process)
        self.assertEqual(snapshot, {"audio_apps": [], "mic_pids": set(),
                                    "render_peaks": {}, "app_devices": {}})

    def test_failed_respawn_respects_backoff_on_next_call(self):
        """Failed respawn respects backoff window on next get_snapshot() call."""
        poller = self._make(_fake_worker_dies_immediately)
        poller.start()
        # Wait for worker to die
        _wait_until(lambda: True if not poller._process.is_alive() else None)
        # Bypass backoff window so first respawn attempt will be tried
        poller._last_restart_ts = time.monotonic() - 10.0

        # Mock start() to raise and track call count
        call_count = [0]
        def mock_start_raises():
            call_count[0] += 1
            raise OSError("Resource temporarily unavailable")

        with mock.patch.object(poller, "start", side_effect=mock_start_raises):
            # First call: respawn attempt fails, _last_restart_ts updated
            poller.get_snapshot()
            self.assertEqual(call_count[0], 1)

            # Second call immediately after (within 5s backoff): respawn NOT attempted
            poller.get_snapshot()
            # start() should NOT be called a second time due to backoff
            self.assertEqual(call_count[0], 1)


if __name__ == "__main__":
    unittest.main()
