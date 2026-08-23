"""Tests for the recordings list's batch-queue helpers."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMenu

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


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


class TestAddBatchQueueActionsExcludesTranscribing(unittest.TestCase):
    """A recording actively being transcribed must not be offered for the
    batch queue — it'll already have a transcript by the time any batch run
    would reach it, and the tag is auto-cleared on completion anyway (see
    MainWindow._display_final_transcript)."""

    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _widget(self):
        from app.ui.recordings_list import RecordingsList
        return RecordingsList(self.recordings_dir)

    def _action_labels(self, menu):
        return [a.text() for a in menu.actions()]

    def test_excludes_a_directory_currently_transcribing(self):
        widget = self._widget()
        widget.set_transcribing({"a"})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [{"directory": "a"}])
        self.assertEqual(self._action_labels(menu), [])

    def test_offers_queueing_for_a_directory_not_transcribing(self):
        widget = self._widget()
        widget.set_transcribing({"a"})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [{"directory": "b"}])
        self.assertIn("Queue for Batch Transcription", self._action_labels(menu))

    def test_mixed_selection_only_offers_the_non_transcribing_one(self):
        widget = self._widget()
        widget.set_transcribing({"a"})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [{"directory": "a"}, {"directory": "b"}])
        self.assertIn("Queue for Batch Transcription", self._action_labels(menu))

    def test_already_queued_and_transcribing_still_offers_removal(self):
        # Removing from the queue (or running it now) is still useful even
        # while transcription is in progress — only the *offer to queue* is
        # suppressed.
        widget = self._widget()
        widget.set_transcribing({"a"})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [{"directory": "a", "batch_pending": True}])
        labels = self._action_labels(menu)
        self.assertIn("Remove from Batch Queue", labels)
        self.assertIn("Process Batch Queue Now...", labels)
        self.assertNotIn("Queue for Batch Transcription", labels)


if __name__ == "__main__":
    unittest.main()
