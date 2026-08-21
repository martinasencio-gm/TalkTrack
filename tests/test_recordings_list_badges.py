"""Tests for the Audio/Transcribed presence badges on each recordings-list row."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QLabel

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListBadges(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _widget(self):
        return RecordingsList(self.recordings_dir)

    def _make_session(self, name, with_audio, with_transcript):
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
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": "2026-08-14T10:00:00",
            "duration": 60,
            "audio_files": audio_files,
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def _badge_texts(self, widget, metadata):
        row = widget._build_row_widget(metadata)
        return {label.text() for label in row.findChildren(QLabel)}

    def _badge_tooltips(self, widget, metadata):
        row = widget._build_row_widget(metadata)
        return {label.toolTip() for label in row.findChildren(QLabel) if label.toolTip()}

    def test_shows_both_badges_when_audio_and_transcript_exist(self):
        widget = self._widget()
        metadata = self._make_session("both", with_audio=True, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertIn("\U0001f3b5", texts)
        self.assertIn("\U0001f4dd", texts)
        tooltips = self._badge_tooltips(widget, metadata)
        self.assertIn("Audio recording available", tooltips)
        self.assertIn("Transcript available", tooltips)

    def test_shows_only_audio_badge_when_no_transcript(self):
        widget = self._widget()
        metadata = self._make_session("audio_only", with_audio=True, with_transcript=False)
        texts = self._badge_texts(widget, metadata)
        self.assertIn("\U0001f3b5", texts)
        self.assertNotIn("\U0001f4dd", texts)

    def test_shows_only_transcribed_badge_when_no_audio(self):
        widget = self._widget()
        metadata = self._make_session("transcript_only", with_audio=False, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("\U0001f3b5", texts)
        self.assertIn("\U0001f4dd", texts)

    def test_shows_neither_badge_when_both_deleted(self):
        widget = self._widget()
        metadata = self._make_session("neither", with_audio=False, with_transcript=False)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("\U0001f3b5", texts)
        self.assertNotIn("\U0001f4dd", texts)

    def test_stale_audio_files_entry_pointing_at_deleted_file_is_not_a_badge(self):
        # audio_files can list a path whose file is already gone (e.g. between
        # a recordings-only delete and metadata.json being rewritten) — the
        # badge must reflect the disk, not the dict.
        widget = self._widget()
        d = self.recordings_dir / "stale"
        d.mkdir()
        metadata = {
            "directory": str(d),
            "name": "stale",
            "started_at": "2026-08-14T10:00:00",
            "duration": 60,
            "audio_files": {"combined": str(d / "gone.wav")},
        }
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("\U0001f3b5", texts)

    def test_transcribed_badge_reflects_transcript_json_only_recording(self):
        """A recording made before transcript.md started shipping alongside
        transcript.json (or one whose .md was otherwise never written) still
        shows Transcribed — the pill keys off transcript.json."""
        widget = self._widget()
        d = self.recordings_dir / "json_only"
        d.mkdir()
        (d / "transcript.json").write_text("{}", encoding="utf-8")
        metadata = {
            "directory": str(d), "name": "json_only",
            "started_at": "2026-08-14T10:00:00", "duration": 60, "audio_files": {},
        }
        texts = self._badge_texts(widget, metadata)
        self.assertIn("\U0001f4dd", texts)


if __name__ == "__main__":
    unittest.main()
