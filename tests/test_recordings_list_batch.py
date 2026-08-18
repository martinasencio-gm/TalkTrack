"""Tests for the recordings list's batch-queue helpers (pure logic only)."""
import unittest


class TestPartitionByQueueState(unittest.TestCase):
    def test_splits_a_mixed_selection(self):
        from app.ui.recordings_list import partition_by_queue_state
        queued_one = {"directory": "a", "batch_pending": True}
        plain = {"directory": "b"}
        unqueued, queued = partition_by_queue_state([queued_one, plain])
        self.assertEqual(unqueued, [plain])
        self.assertEqual(queued, [queued_one])

    def test_ignores_entries_without_a_directory(self):
        from app.ui.recordings_list import partition_by_queue_state
        # Search-result rows carry a different shape entirely.
        unqueued, queued = partition_by_queue_state(
            [None, {}, {"recording_id": "x"}, {"directory": "a"}])
        self.assertEqual(unqueued, [{"directory": "a"}])
        self.assertEqual(queued, [])

    def test_empty_selection(self):
        from app.ui.recordings_list import partition_by_queue_state
        self.assertEqual(partition_by_queue_state([]), ([], []))
        self.assertEqual(partition_by_queue_state(None), ([], []))

    def test_a_cleared_tag_reads_as_unqueued(self):
        from app.ui.recordings_list import partition_by_queue_state
        # What the batch runner leaves behind after a successful run.
        unqueued, queued = partition_by_queue_state([{"directory": "a", "batch_attempts": 0}])
        self.assertEqual(len(unqueued), 1)
        self.assertEqual(queued, [])


if __name__ == "__main__":
    unittest.main()
