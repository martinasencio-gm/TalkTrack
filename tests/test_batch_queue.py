"""Tests for the per-recording batch-processing tag.

The tag lives in each recording's own metadata.json rather than in
settings.json, so it travels with the folder and can't go stale when a
recording is deleted outside the app.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path


def _write_metadata(directory, data):
    path = Path(directory) / "metadata.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _read_metadata(directory):
    return json.loads((Path(directory) / "metadata.json").read_text(encoding="utf-8"))


class TestReaders(unittest.TestCase):
    def test_absent_keys_mean_not_queued(self):
        from app.utils.batch_queue import is_queued, attempts
        self.assertFalse(is_queued({"name": "call"}))
        self.assertEqual(attempts({"name": "call"}), 0)

    def test_reads_the_flag_and_the_counter(self):
        from app.utils.batch_queue import is_queued, attempts
        meta = {"batch_pending": True, "batch_attempts": 2}
        self.assertTrue(is_queued(meta))
        self.assertEqual(attempts(meta), 2)

    def test_tolerates_junk_values(self):
        from app.utils.batch_queue import is_queued, attempts
        # Hand-edited metadata.json is a real possibility; a bad value must
        # not take the whole worklist scan down.
        self.assertFalse(is_queued({"batch_pending": "yes please"}))
        self.assertEqual(attempts({"batch_attempts": "three"}), 0)
        self.assertFalse(is_queued(None))
        self.assertEqual(attempts(None), 0)


class TestSetQueued(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_queues_a_recording(self):
        from app.utils.batch_queue import set_queued
        _write_metadata(self.dir, {"name": "Sync"})
        self.assertTrue(set_queued(self.dir, True))
        self.assertTrue(_read_metadata(self.dir)["batch_pending"])

    def test_preserves_the_other_metadata(self):
        from app.utils.batch_queue import set_queued
        _write_metadata(self.dir, {"name": "Sync", "duration": 91.5, "capture_mode": "legacy"})
        set_queued(self.dir, True)
        meta = _read_metadata(self.dir)
        self.assertEqual(meta["name"], "Sync")
        self.assertEqual(meta["duration"], 91.5)
        self.assertEqual(meta["capture_mode"], "legacy")

    def test_unqueueing_drops_the_flag(self):
        from app.utils.batch_queue import set_queued
        _write_metadata(self.dir, {"batch_pending": True})
        set_queued(self.dir, False)
        self.assertNotIn("batch_pending", _read_metadata(self.dir))

    def test_queueing_resets_the_attempt_counter(self):
        from app.utils.batch_queue import set_queued
        # Re-queuing by hand is the user's way of saying "try this again",
        # so a recording parked at the attempt limit must become eligible.
        _write_metadata(self.dir, {"batch_pending": False, "batch_attempts": 3})
        set_queued(self.dir, True)
        self.assertEqual(_read_metadata(self.dir).get("batch_attempts", 0), 0)

    def test_missing_metadata_is_reported_not_raised(self):
        from app.utils.batch_queue import set_queued
        self.assertFalse(set_queued(self.dir, True))
        self.assertFalse((Path(self.dir) / "metadata.json").exists())

    def test_corrupt_metadata_is_reported_not_raised(self):
        from app.utils.batch_queue import set_queued
        (Path(self.dir) / "metadata.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(set_queued(self.dir, True))

    def test_leaves_no_temp_file_behind(self):
        from app.utils.batch_queue import set_queued
        _write_metadata(self.dir, {"name": "Sync"})
        set_queued(self.dir, True)
        self.assertEqual(
            [n for n in os.listdir(self.dir) if n.endswith(".tmp")], [],
        )


class TestFailureAccounting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_records_a_failure(self):
        from app.utils.batch_queue import record_failure
        _write_metadata(self.dir, {"batch_pending": True})
        self.assertEqual(record_failure(self.dir), 1)
        self.assertEqual(_read_metadata(self.dir)["batch_attempts"], 1)

    def test_failures_accumulate(self):
        from app.utils.batch_queue import record_failure
        _write_metadata(self.dir, {"batch_pending": True})
        record_failure(self.dir)
        record_failure(self.dir)
        self.assertEqual(record_failure(self.dir), 3)

    def test_a_failed_recording_stays_queued(self):
        from app.utils.batch_queue import record_failure
        # It has to stay queued to be retried; the attempt limit, not the
        # flag, is what eventually stops it consuming every run.
        _write_metadata(self.dir, {"batch_pending": True})
        record_failure(self.dir)
        self.assertTrue(_read_metadata(self.dir)["batch_pending"])

    def test_clear_drops_both_keys(self):
        from app.utils.batch_queue import clear
        _write_metadata(self.dir, {"name": "Sync", "batch_pending": True, "batch_attempts": 2})
        self.assertTrue(clear(self.dir))
        meta = _read_metadata(self.dir)
        self.assertNotIn("batch_pending", meta)
        self.assertNotIn("batch_attempts", meta)
        self.assertEqual(meta["name"], "Sync")

    def test_clear_on_an_untagged_recording_is_a_no_op(self):
        from app.utils.batch_queue import clear
        _write_metadata(self.dir, {"name": "Sync"})
        self.assertTrue(clear(self.dir))
        self.assertEqual(_read_metadata(self.dir), {"name": "Sync"})


if __name__ == "__main__":
    unittest.main()
