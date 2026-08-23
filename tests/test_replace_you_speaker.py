import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.batch.pipeline import BatchSettings, _Workers, run_job
from app.batch.worklist import Job
from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.ui.settings_dialog import SettingsDialog
from app.ui.transcript_viewer import TranscriptViewer
from app.utils import config as config_module
from app.utils.config import Config
from app.utils.session_io import load_speaker_names, write_transcript

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSettingsDialogReplaceYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self._patchers = [
            patch.object(config_module, "CONFIG_DIR", tmp_path),
            patch.object(config_module, "CONFIG_FILE", tmp_path / "settings.json"),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def test_settings_dialog_loads_and_saves_replace_you(self):
        config = Config()
        config.set("general", "replace_you_with_name", True)
        config.set("general", "user_name", "Martin A.")

        dialog = SettingsDialog(config)
        self.assertTrue(dialog.replace_you_cb.isChecked())
        self.assertEqual(dialog.user_name_edit.text(), "Martin A.")

        dialog.replace_you_cb.setChecked(False)
        dialog.user_name_edit.setText("Alice")
        dialog._apply_settings()

        self.assertFalse(config.get("general", "replace_you_with_name"))
        self.assertEqual(config.get("general", "user_name"), "Alice")


class TestSessionIoReplaceYou(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.session = {"directory": str(self.dir), "name": "TestSession"}
        self.config = Config()

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_speaker_names_without_replace_flag(self):
        self.config.set("general", "replace_you_with_name", False)
        self.assertEqual(load_speaker_names(self.session, self.config), {})

    def test_load_speaker_names_with_replace_flag(self):
        self.config.set("general", "replace_you_with_name", True)
        self.config.set("general", "user_name", "Martin")
        names = load_speaker_names(self.session, self.config)
        self.assertEqual(names.get("You"), "Martin")

    def test_load_speaker_names_preserves_existing_override(self):
        self.config.set("general", "replace_you_with_name", True)
        self.config.set("general", "user_name", "Martin")
        (self.dir / "speaker_names.json").write_text('{"You": "CustomName"}', encoding="utf-8")
        names = load_speaker_names(self.session, self.config)
        self.assertEqual(names.get("You"), "CustomName")

    def test_write_transcript_saves_speaker_names_json(self):
        res = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Hello", speaker="You")],
            duration=1.0,
        )
        write_transcript(self.session, res, speaker_names={"You": "Martin"})
        self.assertTrue((self.dir / "speaker_names.json").exists())
        saved_names = json.loads((self.dir / "speaker_names.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_names, {"You": "Martin"})


class TestTranscriptViewerReplaceYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self.config = Config()
        self.config.set("general", "replace_you_with_name", True)
        self.config.set("general", "user_name", "Martin")
        self.viewer = TranscriptViewer(config=self.config)

    def test_display_transcript_maps_you_to_user_name(self):
        res = TranscriptResult(
            segments=[
                TranscriptSegment(start=0.0, end=1.0, text="My turn", speaker="You"),
                TranscriptSegment(start=1.0, end=2.0, text="Their turn", speaker="Remote"),
            ],
            duration=2.0,
        )
        self.viewer.display_transcript(res)
        self.assertEqual(self.viewer._speaker_names.get("You"), "Martin")


class TestBatchPipelineReplaceYou(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.audio_file = self.dir / "combined_audio.wav"
        self.audio_file.write_bytes(b"RIFF dummy wav data")
        self.session = {
            "directory": str(self.dir),
            "name": "BatchSession",
            "audio_files": {"combined": str(self.audio_file)},
        }

    def tearDown(self):
        self._tmp.cleanup()

    def test_batch_settings_from_config(self):
        config = Config()
        config.set("general", "replace_you_with_name", True)
        config.set("general", "user_name", "Martin")
        settings = BatchSettings.from_config(config)
        self.assertTrue(settings.replace_you_with_name)
        self.assertEqual(settings.user_name, "Martin")

    def test_process_job_writes_speaker_names_when_not_diarized(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class _BatchFakeWorker(QObject):
            progress = pyqtSignal(str)
            finished = pyqtSignal(object)
            error = pyqtSignal(str)

            def __init__(self, result):
                super().__init__()
                self.result = result

            def run(self):
                self.finished.emit(self.result)

        settings = BatchSettings(
            model_size="base",
            diarize=False,
            replace_you_with_name=True,
            user_name="Martin",
        )
        job = Job(
            directory=str(self.dir),
            session=self.session,
            label="BatchSession",
            audio_path=str(self.audio_file),
        )

        fake_res = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Speaking", speaker="You")],
            duration=1.0,
        )

        fake_worker = _BatchFakeWorker(fake_res)
        workers = _Workers(transcription=lambda *a, **k: fake_worker)
        outcome = run_job(job, settings, workers=workers)

        self.assertTrue(outcome.ok)
        names_file = self.dir / "speaker_names.json"
        self.assertTrue(names_file.exists())
        names = json.loads(names_file.read_text(encoding="utf-8"))
        self.assertEqual(names, {"You": "Martin"})


class TestMainWindowReplaceYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        self.session_dir = self.recordings_dir / "rec_1"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.metadata = {
            "id": "rec_1",
            "directory": str(self.session_dir),
            "name": "Quick Sync",
            "tags": [],
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_window(self):
        from app.main_window import MainWindow
        with patch.object(MainWindow, "_check_startup_status"):
            window = MainWindow()
        window.config.set("output", "directory", str(self.recordings_dir))
        window.config.set("diarization", "hf_token", "dummy_token")

        def _close():
            window._really_quit = True
            window.close()

        self.addCleanup(_close)
        return window

    def test_main_window_display_final_transcript_replaces_you(self):
        win = self._make_window()
        win.config.set("general", "replace_you_with_name", True)
        win.config.set("general", "user_name", "Martin")
        win._current_session = self.metadata

        res = TranscriptResult(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Hello", speaker="You")],
            duration=1.0,
        )
        win._display_final_transcript(res, session=self.metadata)

        names_file = self.session_dir / "speaker_names.json"
        self.assertTrue(names_file.exists())
        names = json.loads(names_file.read_text(encoding="utf-8"))
        self.assertEqual(names.get("You"), "Martin")
        self.assertEqual(win.transcript_viewer._speaker_names.get("You"), "Martin")

