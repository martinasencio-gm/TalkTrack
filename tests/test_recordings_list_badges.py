"""Tests for the row status track + pills on each recordings-list row.

Line 2 of a row carries an always-present 3-step stage track (Recorded ▸
Transcribed ▸ Summarized, icon-only) plus independent colored pills for
work in progress (transcribing / summarizing) and batch queue state.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QLabel

from app.ui.recordings_list import RecordingsList, has_summary

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class _RowMixin:
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _widget(self):
        return RecordingsList(self.recordings_dir)

    def _make_session(self, name, with_audio=False, with_transcript=False,
                      with_summary=False):
        d = self.recordings_dir / name
        d.mkdir()
        audio_files = {}
        if with_audio:
            audio_path = d / "combined_audio.wav"
            audio_path.write_text("wav", encoding="utf-8")
            audio_files["combined"] = str(audio_path)
        if with_transcript:
            (d / "transcript.json").write_text("{}", encoding="utf-8")
            (d / "transcript.md").write_text("# t", encoding="utf-8")
        if with_summary:
            (d / "summary.md").write_text("# s", encoding="utf-8")
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": "2026-08-14T10:00:00",
            "duration": 60,
            "audio_files": audio_files,
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def _stage(self, widget, metadata):
        """{'recorded': bool, 'transcribed': bool, 'summarized': bool} from the
        stage-track icon labels' `reached` property."""
        row = widget._build_row_widget(metadata)
        out = {}
        for key, obj in (
            ("recorded", "recordingStageRecorded"),
            ("transcribed", "recordingStageTranscribed"),
            ("summarized", "recordingStageSummarized"),
        ):
            labels = [l for l in row.findChildren(QLabel) if l.objectName() == obj]
            self.assertEqual(len(labels), 1, f"{obj} must appear exactly once")
            out[key] = bool(labels[0].property("reached"))
        return out

    def _pill_names(self, widget, metadata):
        row = widget._build_row_widget(metadata)
        return {
            l.objectName()
            for l in row.findChildren(QLabel)
            if l.objectName().startswith("recordingBadge")
        }


class TestStageTrack(_RowMixin, unittest.TestCase):
    def test_all_three_steps_present_even_for_a_bare_recording(self):
        widget = self._widget()
        metadata = self._make_session("bare")
        stage = self._stage(widget, metadata)
        self.assertEqual(stage, {"recorded": False, "transcribed": False,
                                 "summarized": False})

    def test_audio_only(self):
        widget = self._widget()
        metadata = self._make_session("a", with_audio=True)
        self.assertEqual(
            self._stage(widget, metadata),
            {"recorded": True, "transcribed": False, "summarized": False},
        )

    def test_audio_and_transcript(self):
        widget = self._widget()
        metadata = self._make_session("at", with_audio=True, with_transcript=True)
        self.assertEqual(
            self._stage(widget, metadata),
            {"recorded": True, "transcribed": True, "summarized": False},
        )

    def test_full_lifecycle(self):
        widget = self._widget()
        metadata = self._make_session("full", with_audio=True,
                                      with_transcript=True, with_summary=True)
        self.assertEqual(
            self._stage(widget, metadata),
            {"recorded": True, "transcribed": True, "summarized": True},
        )

    def test_transcript_only_after_audio_delete(self):
        widget = self._widget()
        metadata = self._make_session("to", with_audio=False,
                                      with_transcript=True, with_summary=True)
        self.assertEqual(
            self._stage(widget, metadata),
            {"recorded": False, "transcribed": True, "summarized": True},
        )

    def test_stage_track_reads_disk_not_stale_audio_files_dict(self):
        widget = self._widget()
        d = self.recordings_dir / "stale"
        d.mkdir()
        metadata = {
            "directory": str(d), "name": "stale",
            "started_at": "2026-08-14T10:00:00", "duration": 60,
            "audio_files": {"combined": str(d / "gone.wav")},
        }
        self.assertFalse(self._stage(widget, metadata)["recorded"])

    def test_has_summary_helper(self):
        d = self.recordings_dir / "hs"
        d.mkdir()
        self.assertFalse(has_summary({"directory": str(d)}))
        (d / "summary.md").write_text("x", encoding="utf-8")
        self.assertTrue(has_summary({"directory": str(d)}))


class TestRowPills(_RowMixin, unittest.TestCase):
    def test_no_progress_pills_by_default(self):
        widget = self._widget()
        metadata = self._make_session("plain", with_audio=True, with_transcript=True)
        self.assertEqual(self._pill_names(widget, metadata), set())

    def test_transcribing_pill_when_dir_is_in_progress(self):
        widget = self._widget()
        metadata = self._make_session("t", with_audio=True)
        widget.set_transcribing({metadata["directory"]})
        self.assertIn("recordingBadgeWorking", self._pill_names(widget, metadata))

    def test_summarizing_pill_when_dir_is_in_progress(self):
        widget = self._widget()
        metadata = self._make_session("s", with_audio=True, with_transcript=True)
        widget.set_summarizing({metadata["directory"]})
        self.assertIn("recordingBadgeSummarizing", self._pill_names(widget, metadata))

    def test_summarizing_pill_absent_for_other_dirs(self):
        widget = self._widget()
        metadata = self._make_session("s2", with_audio=True, with_transcript=True)
        widget.set_summarizing({"C:/somewhere/else"})
        self.assertNotIn("recordingBadgeSummarizing", self._pill_names(widget, metadata))

    def test_queued_pill_unchanged(self):
        widget = self._widget()
        metadata = self._make_session("q", with_audio=True)
        metadata["batch_pending"] = True
        self.assertIn("recordingBadgeQueued", self._pill_names(widget, metadata))

    def test_transcribing_and_summarizing_can_coexist_with_stage_track(self):
        widget = self._widget()
        metadata = self._make_session("both", with_audio=True, with_transcript=True)
        widget.set_transcribing({metadata["directory"]})
        widget.set_summarizing({metadata["directory"]})
        names = self._pill_names(widget, metadata)
        self.assertIn("recordingBadgeWorking", names)
        self.assertIn("recordingBadgeSummarizing", names)
        # stage track still there
        self.assertTrue(self._stage(widget, metadata)["transcribed"])


if __name__ == "__main__":
    unittest.main()
