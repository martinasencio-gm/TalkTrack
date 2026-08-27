"""Tests for the recordings list's batch-queue helpers."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
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


class TestQueuedBatchTooltip(unittest.TestCase):
    def test_legacy_flag_reads_as_transcription(self):
        from app.ui.recordings_list import queued_batch_tooltip
        # batch_pending with no batch_ops resolves to ["transcription"].
        self.assertEqual(
            queued_batch_tooltip({"directory": "a", "batch_pending": True}),
            "Queued for batch: Transcription",
        )

    def test_lists_the_queued_ops_in_canonical_order(self):
        from app.ui.recordings_list import queued_batch_tooltip
        meta = {"directory": "a", "batch_pending": True,
                "batch_ops": ["summarization", "transcription"]}
        self.assertEqual(
            queued_batch_tooltip(meta),
            "Queued for batch: Transcription, Summarization",
        )

    def test_diarization_is_shown_as_speaker_recognition(self):
        from app.ui.recordings_list import queued_batch_tooltip
        meta = {"directory": "a", "batch_pending": True,
                "batch_ops": ["transcription", "diarization"]}
        self.assertEqual(
            queued_batch_tooltip(meta),
            "Queued for batch: Transcription, Speaker Recognition",
        )

    def test_not_queued_falls_back_to_the_plain_caption(self):
        from app.ui.recordings_list import queued_batch_tooltip
        self.assertEqual(
            queued_batch_tooltip({"directory": "a"}),
            "Queued for batch transcription",
        )


class TestBatchOpsMenuState(unittest.TestCase):
    """The pure (checked, enabled) helper behind the context sub-menu."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _rec(self, name, ops=None, transcript=False):
        directory = self.root / name
        directory.mkdir()
        meta = {"directory": str(directory)}
        if ops is not None:
            meta["batch_pending"] = True
            meta["batch_ops"] = list(ops)
        if transcript:
            (directory / "transcript.json").write_text("{}", encoding="utf-8")
        return meta

    def test_empty_selection_is_an_empty_map(self):
        from app.ui.recordings_list import batch_ops_menu_state
        self.assertEqual(batch_ops_menu_state([], True, True), {})
        self.assertEqual(batch_ops_menu_state(None, True, True), {})

    def test_transcription_is_always_enabled_and_reflects_the_selection(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state([self._rec("a")], True, True)
        self.assertEqual(state["transcription"], (False, True))
        state = batch_ops_menu_state([self._rec("b", ops=["transcription"])], True, True)
        self.assertEqual(state["transcription"], (True, True))

    def test_downstream_ops_disabled_without_a_transcript_or_transcription(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state([self._rec("a")], True, True)
        self.assertFalse(state["diarization"][1])
        self.assertFalse(state["summarization"][1])

    def test_downstream_ops_enabled_once_a_transcript_exists(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state([self._rec("a", transcript=True)], True, True)
        self.assertTrue(state["diarization"][1])
        self.assertTrue(state["summarization"][1])

    def test_downstream_ops_enabled_once_transcription_is_queued(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state(
            [self._rec("a", ops=["transcription"])], True, True)
        self.assertTrue(state["diarization"][1])
        self.assertTrue(state["summarization"][1])

    def test_speaker_recognition_needs_an_hf_token(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state(
            [self._rec("a", transcript=True)], False, True)
        self.assertFalse(state["diarization"][1])
        self.assertTrue(state["summarization"][1])

    def test_summarization_needs_an_ai_provider(self):
        from app.ui.recordings_list import batch_ops_menu_state
        state = batch_ops_menu_state(
            [self._rec("a", transcript=True)], True, False)
        self.assertTrue(state["diarization"][1])
        self.assertFalse(state["summarization"][1])

    def test_checked_only_when_every_selected_recording_carries_the_op(self):
        from app.ui.recordings_list import batch_ops_menu_state
        metas = [self._rec("a", ops=["transcription", "summarization"], transcript=True),
                 self._rec("b", ops=["transcription"], transcript=True)]
        state = batch_ops_menu_state(metas, True, True)
        self.assertTrue(state["transcription"][0])
        self.assertFalse(state["summarization"][0])


class _MenuCase(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _widget(self):
        from app.ui.recordings_list import RecordingsList
        w = RecordingsList(self.recordings_dir)
        w.set_batch_capabilities(True, True)
        return w

    def _rec(self, name, ops=None, transcript=False):
        directory = self.recordings_dir / name
        directory.mkdir(exist_ok=True)
        meta = {"name": name, "directory": str(directory)}
        payload = dict(meta)
        if ops is not None:
            payload["batch_pending"] = True
            payload["batch_ops"] = list(ops)
        (directory / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")
        if transcript:
            (directory / "transcript.json").write_text(
                json.dumps({"segments": []}), encoding="utf-8")
        return payload

    def _labels(self, menu):
        return [a.text() for a in menu.actions()]

    def _submenu(self, menu, title="Batch Transcription/Summarization"):
        for a in menu.actions():
            if a.text() == title and a.menu() is not None:
                return a.menu()
        return None


class TestAddBatchQueueActions(_MenuCase):
    def test_excludes_a_directory_currently_transcribing(self):
        widget = self._widget()
        widget.set_transcribing({str(self.recordings_dir / "a")})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("a")])
        self.assertEqual(self._labels(menu), [])

    def test_offers_the_sub_menu_for_a_directory_not_transcribing(self):
        widget = self._widget()
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("b")])
        self.assertIn("Batch Transcription/Summarization", self._labels(menu))
        sub = self._submenu(menu)
        self.assertEqual(
            [a.text() for a in sub.actions()],
            ["Transcription", "Speaker Recognition", "Summarization"],
        )

    def test_sub_menu_actions_are_checkable(self):
        widget = self._widget()
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("b", ops=["transcription"])])
        sub = self._submenu(menu)
        transcription = sub.actions()[0]
        self.assertTrue(transcription.isCheckable())
        self.assertTrue(transcription.isChecked())

    def test_downstream_actions_disabled_without_a_transcript(self):
        widget = self._widget()
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("b")])
        sub = self._submenu(menu)
        by_label = {a.text(): a for a in sub.actions()}
        self.assertFalse(by_label["Speaker Recognition"].isEnabled())
        self.assertFalse(by_label["Summarization"].isEnabled())

    def test_capabilities_gate_the_downstream_actions(self):
        widget = self._widget()
        widget.set_batch_capabilities(False, False)
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("b", transcript=True)])
        sub = self._submenu(menu)
        by_label = {a.text(): a for a in sub.actions()}
        self.assertFalse(by_label["Speaker Recognition"].isEnabled())
        self.assertFalse(by_label["Summarization"].isEnabled())

    def test_already_queued_and_transcribing_still_offers_run_now(self):
        widget = self._widget()
        widget.set_transcribing({str(self.recordings_dir / "a")})
        menu = QMenu()
        widget._add_batch_queue_actions(
            menu, [self._rec("a", ops=["transcription"])])
        labels = self._labels(menu)
        # The recording is queued, so it still shows in the sub-menu (to be
        # changed / removed) and "Process Batch Queue Now..." is offered.
        self.assertIn("Batch Transcription/Summarization", labels)
        self.assertIn("Process Batch Queue Now...", labels)

    def test_nothing_offered_when_selection_is_empty_of_targets(self):
        widget = self._widget()
        widget.set_transcribing({str(self.recordings_dir / "a")})
        menu = QMenu()
        widget._add_batch_queue_actions(menu, [self._rec("a")])
        self.assertEqual(self._labels(menu), [])


class TestToggleBatchOp(_MenuCase):
    def _ops_on_disk(self, name):
        from app.utils import batch_queue
        meta = json.loads(
            (self.recordings_dir / name / "metadata.json").read_text(encoding="utf-8"))
        return batch_queue.queued_ops(meta)

    def test_checking_transcription_queues_the_recording(self):
        widget = self._widget()
        rec = self._rec("a")
        widget._toggle_batch_op([rec], "transcription", True)
        self.assertEqual(self._ops_on_disk("a"), ["transcription"])

    def test_unchecking_the_last_op_removes_it_from_the_queue(self):
        from app.utils import batch_queue
        widget = self._widget()
        rec = self._rec("a", ops=["transcription"])
        widget._toggle_batch_op([rec], "transcription", False)
        meta = json.loads(
            (self.recordings_dir / "a" / "metadata.json").read_text(encoding="utf-8"))
        self.assertFalse(batch_queue.is_queued(meta))

    def test_adding_summarization_keeps_canonical_order(self):
        widget = self._widget()
        rec = self._rec("a", ops=["transcription"])
        widget._toggle_batch_op([rec], "summarization", True)
        self.assertEqual(self._ops_on_disk("a"), ["transcription", "summarization"])

    def test_checking_transcription_over_an_existing_transcript_prompts(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        widget = self._widget()
        rec = self._rec("a", transcript=True)
        with patch("PyQt6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No) as q:
            widget._toggle_batch_op([rec], "transcription", True)
            q.assert_called_once()
        self.assertEqual(self._ops_on_disk("a"), [])

    def test_checking_transcription_proceeds_on_yes(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        widget = self._widget()
        rec = self._rec("a", transcript=True)
        with patch("PyQt6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes) as q:
            widget._toggle_batch_op([rec], "transcription", True)
            q.assert_called_once()
        self.assertEqual(self._ops_on_disk("a"), ["transcription"])

    def test_summarization_over_an_existing_transcript_never_prompts(self):
        from unittest.mock import patch
        widget = self._widget()
        rec = self._rec("a", transcript=True)
        with patch("PyQt6.QtWidgets.QMessageBox.question") as q:
            widget._toggle_batch_op([rec], "summarization", True)
            q.assert_not_called()
        self.assertEqual(self._ops_on_disk("a"), ["summarization"])


if __name__ == "__main__":
    unittest.main()
