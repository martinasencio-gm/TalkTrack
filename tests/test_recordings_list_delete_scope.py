import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QDialog

from app.ui.recordings_list import (
    RecordingsList, _delete_transcription_files,
    _delete_exported_transcript
)
from app.ui.delete_scope_dialog import DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS, DELETE_BOTH
from app.utils.transcript_export import export_path_for


class _FakeConfig:
    """Minimal (section, key) -> value stub — these tests build a bare
    RecordingsList with no MainWindow, so there's no real Config to reuse."""

    def __init__(self, transcripts_dir):
        self._transcripts_dir = transcripts_dir

    def get(self, section, key):
        assert (section, key) == ("transcripts", "directory")
        return self._transcripts_dir

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestDeleteTranscriptionFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.directory = Path(self.tmp) / "session"
        self.directory.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_all_transcript_derived_files(self):
        for name in ("transcript.json", "transcript.txt", "summary.md",
                     "action_items.json", "speaker_names.json"):
            (self.directory / name).write_text("x", encoding="utf-8")
        (self.directory / "combined_audio.wav").write_text("wav", encoding="utf-8")
        (self.directory / "notes.txt").write_text("my notes", encoding="utf-8")

        _delete_transcription_files(str(self.directory))

        for name in ("transcript.json", "transcript.txt", "summary.md",
                     "action_items.json", "speaker_names.json"):
            self.assertFalse((self.directory / name).exists(), name)
        self.assertTrue((self.directory / "combined_audio.wav").exists())
        self.assertTrue((self.directory / "notes.txt").exists())

    def test_missing_files_are_a_noop(self):
        _delete_transcription_files(str(self.directory))  # must not raise


class TestDeleteExportedTranscript(unittest.TestCase):
    """The Markdown export copy in the separate transcripts/ folder (see
    transcript_export.py) is a distinct file from transcript.json inside the
    recording's own directory — deleting one never touches the other."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.session_dir = Path(self.tmp) / "recordings" / "session1"
        self.session_dir.mkdir(parents=True)
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.metadata = {"directory": str(self.session_dir), "started_at": ""}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_the_export_copy_when_present(self):
        self.transcripts_dir.mkdir()
        export_path = export_path_for("session1", "", str(self.transcripts_dir))
        export_path.write_text("# exported", encoding="utf-8")

        _delete_exported_transcript(self.metadata, str(self.transcripts_dir))

        self.assertFalse(export_path.exists())

    def test_missing_export_copy_is_a_noop(self):
        _delete_exported_transcript(self.metadata, str(self.transcripts_dir))  # must not raise

    def test_falsy_transcripts_dir_is_a_noop(self):
        _delete_exported_transcript(self.metadata, None)  # must not raise
        _delete_exported_transcript(self.metadata, "")  # must not raise


class TestPerformDeleteScopes(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()
        self.session_dir = self.recordings_dir / "session1"
        self.session_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self):
        audio_path = str(self.session_dir / "combined_audio.wav")
        (self.session_dir / "combined_audio.wav").write_text("wav", encoding="utf-8")
        (self.session_dir / "transcript.json").write_text("{}", encoding="utf-8")
        metadata = {
            "directory": str(self.session_dir),
            "name": "Test",
            "audio_files": {"combined": audio_path},
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def test_scope_recordings_removes_the_whole_folder(self):
        """"Recordings only" deletes the recording's directory outright.
        Anything the app dropped in there — embeddings.npz, chat_history.json,
        stray chunk WAVs — goes with it; only the exported Markdown in the
        separate transcripts/ folder survives (covered below)."""
        metadata = self._make_session()
        (self.session_dir / "embeddings.npz").write_text("npz", encoding="utf-8")
        (self.session_dir / "chat_history.json").write_text("[]", encoding="utf-8")
        widget = RecordingsList(self.recordings_dir)
        about_to_delete = []
        files_changed = []
        deleted = []
        widget.about_to_delete.connect(about_to_delete.append)
        widget.recording_files_changed.connect(files_changed.append)
        widget.recording_deleted.connect(deleted.append)

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse(self.session_dir.exists())
        self.assertEqual(about_to_delete, [str(self.session_dir)])
        self.assertEqual(deleted, [str(self.session_dir)])
        self.assertEqual(files_changed, [])

    def test_scope_transcriptions_does_not_emit_about_to_delete(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir)
        about_to_delete = []
        files_changed = []
        widget.about_to_delete.connect(about_to_delete.append)
        widget.recording_files_changed.connect(files_changed.append)

        widget._perform_delete(metadata, DELETE_TRANSCRIPTIONS)

        self.assertTrue((self.session_dir / "combined_audio.wav").exists())
        self.assertFalse((self.session_dir / "transcript.json").exists())
        self.assertEqual(about_to_delete, [])
        self.assertEqual(files_changed, [str(self.session_dir)])

    def test_scope_both_removes_directory_and_emits_recording_deleted(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir)
        deleted = []
        files_changed = []
        widget.recording_deleted.connect(deleted.append)
        widget.recording_files_changed.connect(files_changed.append)

        widget._perform_delete(metadata, DELETE_BOTH)

        self.assertFalse(self.session_dir.exists())
        self.assertEqual(deleted, [str(self.session_dir)])
        self.assertEqual(files_changed, [])

    def test_delete_recording_dialog_cancel_leaves_files_untouched(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir)
        with patch("app.ui.recordings_list.DeleteScopeDialog") as MockDialog:
            MockDialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            widget._delete_recording(metadata)
        self.assertTrue((self.session_dir / "combined_audio.wav").exists())
        self.assertTrue((self.session_dir / "transcript.json").exists())


class TestPerformDeleteRemovesExportedTranscript(unittest.TestCase):
    """Regression coverage for the reported bug: deleting a recording's
    transcript left the Markdown export copy in the separate transcripts/
    folder behind, since _perform_delete never knew that file existed."""

    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()
        self.session_dir = self.recordings_dir / "session1"
        self.session_dir.mkdir()
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()
        self.export_path = export_path_for("session1", "", str(self.transcripts_dir))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self):
        audio_path = str(self.session_dir / "combined_audio.wav")
        (self.session_dir / "combined_audio.wav").write_text("wav", encoding="utf-8")
        (self.session_dir / "transcript.json").write_text("{}", encoding="utf-8")
        self.export_path.write_text("# exported", encoding="utf-8")
        metadata = {
            "directory": str(self.session_dir),
            "name": "Test",
            "audio_files": {"combined": audio_path},
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def test_scope_transcriptions_removes_the_export_copy_too(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

        widget._perform_delete(metadata, DELETE_TRANSCRIPTIONS)

        self.assertFalse((self.session_dir / "transcript.json").exists())
        self.assertFalse(self.export_path.exists())

    def test_scope_both_removes_the_export_copy_too(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

        widget._perform_delete(metadata, DELETE_BOTH)

        self.assertFalse(self.session_dir.exists())
        self.assertFalse(self.export_path.exists())

    def test_scope_recordings_leaves_the_export_copy_alone(self):
        """The export is the durable artifact: deleting the recording folder
        is exactly how a user keeps the transcript and reclaims the audio."""
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse(self.session_dir.exists())
        self.assertTrue(self.export_path.exists())

    def test_scope_transcriptions_without_config_does_not_raise(self):
        """RecordingsList built with no config (e.g. some existing tests) -
        can't know the transcripts dir, so this must degrade to a no-op
        rather than fail the whole delete."""
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir)

        widget._perform_delete(metadata, DELETE_TRANSCRIPTIONS)  # must not raise

        self.assertFalse((self.session_dir / "transcript.json").exists())
        self.assertTrue(self.export_path.exists())


if __name__ == "__main__":
    unittest.main()
