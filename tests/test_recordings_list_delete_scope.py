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
    RecordingsList, _delete_audio_files, _delete_transcription_files
)
from app.ui.delete_scope_dialog import DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS, DELETE_BOTH

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestDeleteAudioFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.directory = Path(self.tmp) / "session"
        self.directory.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content=""):
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_removes_audio_files_and_clears_metadata(self):
        combined = self._write("combined_audio.wav", "fake wav")
        metadata = {
            "directory": str(self.directory),
            "name": "Test",
            "audio_files": {"combined": combined},
        }
        meta_path = self.directory / "metadata.json"
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        _delete_audio_files(str(self.directory), metadata)

        self.assertFalse(Path(combined).exists())
        updated = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["audio_files"], {})
        self.assertEqual(updated["name"], "Test")

    def test_leaves_transcript_files_untouched(self):
        combined = self._write("combined_audio.wav", "fake wav")
        self._write("transcript.json", "{}")
        metadata = {
            "directory": str(self.directory),
            "audio_files": {"combined": combined},
        }
        (self.directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        _delete_audio_files(str(self.directory), metadata)

        self.assertTrue((self.directory / "transcript.json").exists())

    def test_missing_audio_path_is_a_noop(self):
        metadata = {
            "directory": str(self.directory),
            "audio_files": {"combined": str(self.directory / "gone.wav")},
        }
        (self.directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        _delete_audio_files(str(self.directory), metadata)  # must not raise

        updated = json.loads((self.directory / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["audio_files"], {})


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

    def test_scope_recordings_emits_files_changed_not_deleted(self):
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir)
        about_to_delete = []
        files_changed = []
        deleted = []
        widget.about_to_delete.connect(about_to_delete.append)
        widget.recording_files_changed.connect(files_changed.append)
        widget.recording_deleted.connect(deleted.append)

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse((self.session_dir / "combined_audio.wav").exists())
        self.assertTrue((self.session_dir / "transcript.json").exists())
        self.assertTrue(self.session_dir.exists())
        self.assertEqual(about_to_delete, [str(self.session_dir)])
        self.assertEqual(files_changed, [str(self.session_dir)])
        self.assertEqual(deleted, [])

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


if __name__ == "__main__":
    unittest.main()
