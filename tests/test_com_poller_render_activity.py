# tests/test_com_poller_render_activity.py
import queue
import unittest

from app.utils.com_session_worker import ComSessionPoller


class _FakeQueue:
    """Hands out prepared snapshots, then behaves as empty."""

    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def get_nowait(self):
        if not self._snapshots:
            raise queue.Empty
        return self._snapshots.pop(0)


class TestPollerRenderActivity(unittest.TestCase):
    """The poller folds each worker snapshot's render peaks into a short
    activity history, so the source selector can prefer the endpoint that
    is actually playing over the (often wrong) Windows default."""

    def _poller(self, snapshots):
        poller = ComSessionPoller(worker_target=lambda *a: None)
        poller._queue = _FakeQueue(snapshots)
        return poller

    def _outputs(self, *names):
        return [{"index": 20 + i, "name": n} for i, n in enumerate(names)]

    def test_no_opinion_before_any_snapshot(self):
        poller = self._poller([])
        self.assertIsNone(
            poller.active_output_index(self._outputs("Speakers (Realtek)")))

    def test_identifies_the_rendering_endpoint(self):
        poller = self._poller([{
            "audio_apps": [], "mic_pids": set(),
            "render_peaks": {"Speakers (AnkerWork B600 Video Bar)": 0.0,
                             "Speakers (Realtek(R) Audio)": 0.42},
        }])
        poller.get_snapshot()
        outs = self._outputs("Speakers (AnkerWork B600 Video Bar)",
                             "Speakers (Realtek(R) Audio)")
        self.assertEqual(poller.active_output_index(outs), 21)

    def test_survives_a_snapshot_without_render_peaks(self):
        # Snapshots from an older/partly-failed worker lack the key.
        poller = self._poller([{"audio_apps": [], "mic_pids": set()}])
        poller.get_snapshot()
        self.assertIsNone(
            poller.active_output_index(self._outputs("Speakers (Realtek)")))

    def test_activity_survives_a_quiet_snapshot(self):
        # A pause between sentences must not drop the endpoint back to
        # "nothing is playing" — that would flap the default selection.
        poller = self._poller([
            {"audio_apps": [], "mic_pids": set(),
             "render_peaks": {"Speakers (Realtek(R) Audio)": 0.42}},
            {"audio_apps": [], "mic_pids": set(),
             "render_peaks": {"Speakers (Realtek(R) Audio)": 0.0}},
        ])
        poller.get_snapshot()
        poller.get_snapshot()
        outs = self._outputs("Speakers (Realtek(R) Audio)")
        self.assertEqual(poller.active_output_index(outs), 20)

    def test_default_snapshot_exposes_render_peaks(self):
        poller = ComSessionPoller(worker_target=lambda *a: None)
        self.assertEqual(poller.get_snapshot()["render_peaks"], {})


if __name__ == "__main__":
    unittest.main()
