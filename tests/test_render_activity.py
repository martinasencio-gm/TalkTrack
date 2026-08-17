# tests/test_render_activity.py
import unittest

from app.utils import render_activity as ra


class TestUpdateActivity(unittest.TestCase):
    def test_records_endpoints_above_the_silence_floor(self):
        hist = ra.update_activity({}, {"Speakers (Realtek)": 0.4}, now=100.0)
        self.assertEqual(hist, {"Speakers (Realtek)": (100.0, 0.4)})

    def test_ignores_endpoints_at_the_silence_floor(self):
        # Idle endpoints report a tiny non-zero peak; treating those as
        # "rendering" would make every device look active.
        hist = ra.update_activity({}, {"Speakers (Realtek)": 0.0}, now=100.0)
        self.assertEqual(hist, {})

    def test_keeps_the_previous_timestamp_while_silent(self):
        hist = {"A": (100.0, 0.4)}
        hist = ra.update_activity(hist, {"A": 0.0}, now=105.0)
        self.assertEqual(hist, {"A": (100.0, 0.4)})

    def test_refreshes_timestamp_and_peak_when_loud_again(self):
        hist = {"A": (100.0, 0.4)}
        hist = ra.update_activity(hist, {"A": 0.9}, now=105.0)
        self.assertEqual(hist, {"A": (105.0, 0.9)})

    def test_drops_entries_older_than_the_window(self):
        hist = {"Stale": (10.0, 0.9), "Fresh": (100.0, 0.2)}
        hist = ra.update_activity(hist, {}, now=120.0, window=30.0)
        self.assertEqual(list(hist), ["Fresh"])


class TestMostActive(unittest.TestCase):
    def test_returns_none_when_nothing_rendered(self):
        self.assertIsNone(ra.most_active({}, now=100.0))

    def test_prefers_the_most_recently_active_endpoint(self):
        # Recency beats loudness: a notification chime on one device
        # shouldn't outrank the device carrying the meeting.
        hist = {"Chime": (95.0, 0.95), "Meeting": (99.0, 0.30)}
        self.assertEqual(ra.most_active(hist, now=100.0), "Meeting")

    def test_breaks_recency_ties_on_peak(self):
        hist = {"Quiet": (99.0, 0.10), "Loud": (99.0, 0.60)}
        self.assertEqual(ra.most_active(hist, now=100.0), "Loud")

    def test_ignores_entries_outside_the_window(self):
        hist = {"Old": (10.0, 0.9)}
        self.assertIsNone(ra.most_active(hist, now=100.0, window=30.0))


class TestPickOutputIndex(unittest.TestCase):
    def _outputs(self, *names):
        return [{"index": 20 + i, "name": n} for i, n in enumerate(names)]

    def test_maps_an_active_endpoint_to_its_device_index(self):
        outs = self._outputs("Speakers (AnkerWork B600 Video Bar)",
                             "Speakers (Realtek(R) Audio)")
        hist = {"Speakers (Realtek(R) Audio)": (99.0, 0.4)}
        self.assertEqual(ra.pick_output_index(hist, outs, now=100.0), 21)

    def test_returns_none_when_the_active_endpoint_is_not_capturable(self):
        # Rendering to a device the user hid, or one with no loopback:
        # the caller must fall back rather than pick something arbitrary.
        outs = self._outputs("Speakers (AnkerWork B600 Video Bar)")
        hist = {"Headphones (Bluetooth)": (99.0, 0.4)}
        self.assertIsNone(ra.pick_output_index(hist, outs, now=100.0))

    def test_skips_active_endpoints_that_are_not_outputs(self):
        # Capture endpoints expose peak meters too, and the user's own mic
        # is loud during a meeting. Letting the mic win the recency race
        # would report "no opinion" and silently fall back to the default.
        outs = self._outputs("Speakers (Realtek(R) Audio)")
        hist = {"Microphone Array (Intel)": (99.0, 0.90),
                "Speakers (Realtek(R) Audio)": (99.0, 0.10)}
        self.assertEqual(ra.pick_output_index(hist, outs, now=100.0), 20)

    def test_returns_none_when_nothing_is_rendering(self):
        outs = self._outputs("Speakers (Realtek(R) Audio)")
        self.assertIsNone(ra.pick_output_index({}, outs, now=100.0))


if __name__ == "__main__":
    unittest.main()
