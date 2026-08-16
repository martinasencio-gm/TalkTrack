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
from app.utils.transcript_export import export_path_for

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class _FakeConfig:
    """(section, key) -> value stub; these tests build a bare RecordingsList
    with no MainWindow, so there is no real Config to reuse."""

    def __init__(self, transcripts_dir):
        self._transcripts_dir = transcripts_dir

    def get(self, section, key):
        assert (section, key) == ("transcripts", "directory")
        return self._transcripts_dir


class TestRecordingsListBadges(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _widget(self):
        return RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

    def _make_session(self, name, with_audio, with_transcript, with_export=None):
        """with_transcript writes transcript.json inside the recording folder;
        with_export writes the Markdown export in the transcripts folder and
        defaults to matching with_transcript (the normal, in-sync case)."""
        d = self.recordings_dir / name
        d.mkdir()
        started_at = "2026-08-14T10:00:00"
        audio_files = {}
        if with_audio:
            audio_path = d / "combined_audio.wav"
            audio_path.write_text("wav", encoding="utf-8")
            audio_files["combined"] = str(audio_path)
        if with_transcript:
            (d / "transcript.json").write_text("{}", encoding="utf-8")
        if with_export is None:
            with_export = with_transcript
        if with_export:
            export_path_for(name, started_at, str(self.transcripts_dir)).write_text(
                "# exported", encoding="utf-8"
            )
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": started_at,
            "duration": 60,
            "audio_files": audio_files,
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def _badge_texts(self, widget, metadata):
        row = widget._build_row_widget(metadata)
        return {label.text() for label in row.findChildren(QLabel)}

    def test_shows_both_badges_when_audio_and_transcript_exist(self):
        widget = self._widget()
        metadata = self._make_session("both", with_audio=True, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Audio", texts)
        self.assertIn("Transcribed", texts)

    def test_shows_only_audio_badge_when_no_transcript(self):
        widget = self._widget()
        metadata = self._make_session("audio_only", with_audio=True, with_transcript=False)
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Audio", texts)
        self.assertNotIn("Transcribed", texts)

    def test_shows_only_transcribed_badge_when_no_audio(self):
        widget = self._widget()
        metadata = self._make_session("transcript_only", with_audio=False, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("Audio", texts)
        self.assertIn("Transcribed", texts)

    def test_shows_neither_badge_when_both_deleted(self):
        widget = self._widget()
        metadata = self._make_session("neither", with_audio=False, with_transcript=False)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("Audio", texts)
        self.assertNotIn("Transcribed", texts)

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
        self.assertNotIn("Audio", texts)

    def test_no_transcribed_badge_when_transcript_json_has_no_export(self):
        """The pill reports the file the user can open in the transcripts
        folder. transcript.json alone is not enough."""
        widget = self._widget()
        metadata = self._make_session(
            "no_export", with_audio=True, with_transcript=True, with_export=False
        )
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Audio", texts)
        self.assertNotIn("Transcribed", texts)

    def test_transcribed_badge_when_export_survives_a_folder_delete(self):
        """After a "Recordings only" delete the folder is gone but the export
        remains; a row rebuilt from stale metadata must still say Transcribed."""
        widget = self._widget()
        metadata = self._make_session(
            "kept_export", with_audio=False, with_transcript=False, with_export=True
        )
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Transcribed", texts)

    def test_no_transcribed_badge_without_config(self):
        """A RecordingsList with no config cannot resolve the transcripts
        folder — degrade to no badge rather than raising in the row builder."""
        widget = RecordingsList(self.recordings_dir)
        metadata = self._make_session("no_config", with_audio=True, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("Transcribed", texts)


if __name__ == "__main__":
    unittest.main()
