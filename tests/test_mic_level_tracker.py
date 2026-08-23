"""Tests for app.utils.mic_level_tracker — the rolling peak-dB tracker that
feeds the pre-flight bar's "quiet mic" check. Pure Python/numpy, no Qt and
no real audio device."""
import unittest

import numpy as np

from app.utils.mic_level_tracker import MicLevelTracker, peak_db, DB_FLOOR


def _chunk(amplitude, n=160):
    return np.full(n, amplitude, dtype=np.float32)


class TestPeakDb(unittest.TestCase):
    def test_silence_is_floor(self):
        self.assertEqual(peak_db(_chunk(0.0)), DB_FLOOR)

    def test_full_scale_is_near_zero_db(self):
        self.assertAlmostEqual(peak_db(_chunk(1.0)), 0.0, places=3)

    def test_empty_chunk_is_floor(self):
        self.assertEqual(peak_db(np.array([], dtype=np.float32)), DB_FLOOR)

    def test_quieter_amplitude_is_lower_db(self):
        self.assertLess(peak_db(_chunk(0.01)), peak_db(_chunk(0.5)))


class TestMicLevelTracker(unittest.TestCase):
    def _tracker(self, **kwargs):
        self.now = 0.0
        return MicLevelTracker(clock=lambda: self.now, **kwargs)

    def test_no_samples_yet_returns_none(self):
        tracker = self._tracker()
        self.assertIsNone(tracker.peak_db_over_window())

    def test_too_soon_after_first_sample_returns_none(self):
        tracker = self._tracker(min_sample_seconds=1.5)
        tracker.ingest(_chunk(0.5))
        self.now += 1.0  # still under min_sample_seconds
        self.assertIsNone(tracker.peak_db_over_window())

    def test_reports_loudest_peak_after_min_sample_window(self):
        tracker = self._tracker(min_sample_seconds=1.5)
        tracker.ingest(_chunk(0.01))
        self.now += 0.5
        tracker.ingest(_chunk(0.5))  # the loud one
        self.now += 0.5
        tracker.ingest(_chunk(0.02))
        self.now += 1.0  # now past min_sample_seconds
        reading = tracker.peak_db_over_window()
        self.assertAlmostEqual(reading, peak_db(_chunk(0.5)), places=3)

    def test_old_samples_fall_out_of_the_window(self):
        tracker = self._tracker(window_seconds=2.0, min_sample_seconds=0.0)
        tracker.ingest(_chunk(0.9))  # loud, but will age out
        self.now += 3.0
        tracker.ingest(_chunk(0.01))  # quiet, recent
        reading = tracker.peak_db_over_window()
        self.assertAlmostEqual(reading, peak_db(_chunk(0.01)), places=3)

    def test_reset_clears_state(self):
        tracker = self._tracker(min_sample_seconds=0.0)
        tracker.ingest(_chunk(0.5))
        tracker.reset()
        self.assertIsNone(tracker.peak_db_over_window())


if __name__ == "__main__":
    unittest.main()
