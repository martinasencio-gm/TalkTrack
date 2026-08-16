import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QDialog

from app.ui.recordings_list import (
    RecordingsList, _delete_transcription_files, _delete_audio_files,
    _has_any_audio, _has_any_transcript, _rmtree_robust
)
from app.ui.delete_scope_dialog import DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS, DELETE_BOTH

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
        for name in ("transcript.json", "transcript.md", "transcript.txt", "summary.md",
                     "action_items.json", "speaker_names.json"):
            (self.directory / name).write_text("x", encoding="utf-8")
        (self.directory / "combined_audio.wav").write_text("wav", encoding="utf-8")
        (self.directory / "notes.txt").write_text("my notes", encoding="utf-8")

        _delete_transcription_files(str(self.directory))

        for name in ("transcript.json", "transcript.md", "transcript.txt", "summary.md",
                     "action_items.json", "speaker_names.json"):
            self.assertFalse((self.directory / name).exists(), name)
        self.assertTrue((self.directory / "combined_audio.wav").exists())
        self.assertTrue((self.directory / "notes.txt").exists())

    def test_missing_files_are_a_noop(self):
        _delete_transcription_files(str(self.directory))  # must not raise


class TestDeleteAudioFiles(unittest.TestCase):
    """"Recording audio only" removes just the audio tracks — everything
    transcript-derived (transcript.json/.md, summary, action items, speaker
    names, notes, chat history, calendar tag, embeddings) survives."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.directory = Path(self.tmp) / "session"
        self.directory.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_standard_audio_tracks(self):
        for name in ("mic_audio.wav", "system_audio.wav", "combined_audio.wav"):
            (self.directory / name).write_text("wav", encoding="utf-8")

        _delete_audio_files(str(self.directory))

        for name in ("mic_audio.wav", "system_audio.wav", "combined_audio.wav"):
            self.assertFalse((self.directory / name).exists(), name)

    def test_removes_dual_mic_raw_temps_and_mp3_tracks(self):
        (self.directory / "mic1_raw.wav").write_text("wav", encoding="utf-8")
        (self.directory / "mic2_raw.wav").write_text("wav", encoding="utf-8")
        (self.directory / "combined_audio.mp3").write_text("mp3", encoding="utf-8")

        _delete_audio_files(str(self.directory))

        self.assertFalse((self.directory / "mic1_raw.wav").exists())
        self.assertFalse((self.directory / "mic2_raw.wav").exists())
        self.assertFalse((self.directory / "combined_audio.mp3").exists())

    def test_keeps_transcript_derived_and_other_session_files(self):
        keep = (
            "transcript.json", "transcript.md", "summary.md", "action_items.json",
            "speaker_names.json", "notes.txt", "chat_history.json",
            "calendar_event.json", "embeddings.npz", "metadata.json",
        )
        for name in keep:
            (self.directory / name).write_text("x", encoding="utf-8")
        (self.directory / "combined_audio.wav").write_text("wav", encoding="utf-8")

        _delete_audio_files(str(self.directory))

        for name in keep:
            self.assertTrue((self.directory / name).exists(), name)

    def test_missing_files_are_a_noop(self):
        _delete_audio_files(str(self.directory))  # must not raise


class TestHasAnyAudioAndTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.directory = Path(self.tmp) / "session"
        self.directory.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_has_any_audio_true_when_a_track_exists(self):
        (self.directory / "combined_audio.wav").write_text("wav", encoding="utf-8")
        self.assertTrue(_has_any_audio(str(self.directory)))

    def test_has_any_audio_false_when_empty(self):
        self.assertFalse(_has_any_audio(str(self.directory)))

    def test_has_any_transcript_true_for_json(self):
        (self.directory / "transcript.json").write_text("{}", encoding="utf-8")
        self.assertTrue(_has_any_transcript(str(self.directory)))

    def test_has_any_transcript_true_for_markdown_only(self):
        (self.directory / "transcript.md").write_text("# t", encoding="utf-8")
        self.assertTrue(_has_any_transcript(str(self.directory)))

    def test_has_any_transcript_false_when_neither_exists(self):
        self.assertFalse(_has_any_transcript(str(self.directory)))


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

    def _make_session(self, with_transcript_md=False):
        audio_path = str(self.session_dir / "combined_audio.wav")
        (self.session_dir / "combined_audio.wav").write_text("wav", encoding="utf-8")
        (self.session_dir / "transcript.json").write_text("{}", encoding="utf-8")
        if with_transcript_md:
            (self.session_dir / "transcript.md").write_text("# t", encoding="utf-8")
        metadata = {
            "directory": str(self.session_dir),
            "name": "Test",
            "audio_files": {"combined": audio_path},
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def test_scope_recordings_keeps_the_folder_when_a_transcript_survives(self):
        """"Recording audio only" removes audio but keeps the session alive
        as a transcript-only entry when transcript.json/.md remains."""
        metadata = self._make_session(with_transcript_md=True)
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

        self.assertTrue(self.session_dir.exists())
        self.assertFalse((self.session_dir / "combined_audio.wav").exists())
        self.assertTrue((self.session_dir / "transcript.json").exists())
        self.assertTrue((self.session_dir / "transcript.md").exists())
        self.assertTrue((self.session_dir / "embeddings.npz").exists())
        self.assertTrue((self.session_dir / "chat_history.json").exists())
        self.assertEqual(about_to_delete, [str(self.session_dir)])
        self.assertEqual(files_changed, [str(self.session_dir)])
        self.assertEqual(deleted, [])

    def test_scope_recordings_removes_the_folder_when_no_transcript_survives(self):
        """No transcript.json/.md left behind means nothing the entry is
        for — the degenerate case removes the whole folder."""
        audio_path = str(self.session_dir / "combined_audio.wav")
        (self.session_dir / "combined_audio.wav").write_text("wav", encoding="utf-8")
        metadata = {
            "directory": str(self.session_dir), "name": "Test",
            "audio_files": {"combined": audio_path},
        }
        widget = RecordingsList(self.recordings_dir)
        deleted = []
        files_changed = []
        widget.recording_deleted.connect(deleted.append)
        widget.recording_files_changed.connect(files_changed.append)

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse(self.session_dir.exists())
        self.assertEqual(deleted, [str(self.session_dir)])
        self.assertEqual(files_changed, [])

    def test_scope_transcriptions_keeps_the_folder_when_audio_survives(self):
        metadata = self._make_session(with_transcript_md=True)
        widget = RecordingsList(self.recordings_dir)
        about_to_delete = []
        files_changed = []
        widget.about_to_delete.connect(about_to_delete.append)
        widget.recording_files_changed.connect(files_changed.append)

        widget._perform_delete(metadata, DELETE_TRANSCRIPTIONS)

        self.assertTrue(self.session_dir.exists())
        self.assertTrue((self.session_dir / "combined_audio.wav").exists())
        self.assertFalse((self.session_dir / "transcript.json").exists())
        self.assertFalse((self.session_dir / "transcript.md").exists())
        self.assertEqual(about_to_delete, [])
        self.assertEqual(files_changed, [str(self.session_dir)])

    def test_scope_transcriptions_removes_the_folder_when_no_audio_survives(self):
        """A prior recordings-only delete already removed the audio — a
        transcriptions-only delete now has nothing left to keep the folder
        for, so it removes the folder instead of leaving an empty shell."""
        (self.session_dir / "transcript.json").write_text("{}", encoding="utf-8")
        (self.session_dir / "transcript.md").write_text("# t", encoding="utf-8")
        metadata = {"directory": str(self.session_dir), "name": "Test", "audio_files": {}}
        widget = RecordingsList(self.recordings_dir)
        deleted = []
        files_changed = []
        widget.recording_deleted.connect(deleted.append)
        widget.recording_files_changed.connect(files_changed.append)

        widget._perform_delete(metadata, DELETE_TRANSCRIPTIONS)

        self.assertFalse(self.session_dir.exists())
        self.assertEqual(deleted, [str(self.session_dir)])
        self.assertEqual(files_changed, [])

    def test_scope_both_removes_directory_and_emits_recording_deleted(self):
        metadata = self._make_session(with_transcript_md=True)
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


class TestRmtreeRobustMissingDirectory(unittest.TestCase):
    """Regression coverage for #73: a nonexistent path made shutil.rmtree
    raise FileNotFoundError, which the retry loop misread as a lock —
    burning up to 1.5s of UI-thread sleeps and popping a modal for a
    directory that was already gone (e.g. removed externally between a
    bulk-delete selection and this call)."""

    def test_returns_promptly_without_raising(self):
        tmp = tempfile.mkdtemp()
        shutil.rmtree(tmp, ignore_errors=True)
        missing = Path(tmp) / "definitely_not_here"

        start = time.time()
        _rmtree_robust(str(missing))  # must not raise
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()
