"""Tests for choosing which recordings a batch run will process."""
import json
import os
import tempfile
import unittest
from pathlib import Path


def _make_recording(root, name, metadata=None, audio="combined_audio.wav",
                    transcript=False):
    directory = Path(root) / name
    directory.mkdir(parents=True)
    if metadata is not None:
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if audio:
        (directory / audio).write_bytes(b"RIFF")
    if transcript:
        (directory / "transcript.json").write_text("{}", encoding="utf-8")
    return directory


QUEUED = {"batch_pending": True}


class TestBuildWorklist(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def names(self, **kwargs):
        from app.batch.worklist import build_worklist
        return [Path(job.directory).name for job in build_worklist(self.root, **kwargs)]

    def test_selects_only_tagged_recordings(self):
        _make_recording(self.root, "recording_20260101_000000", QUEUED)
        _make_recording(self.root, "recording_20260102_000000", {"name": "not queued"})
        self.assertEqual(self.names(), ["recording_20260101_000000"])

    def test_orders_oldest_first(self):
        # Oldest first so a run that hits the cutoff has cleared the
        # longest-waiting backlog rather than the newest arrivals.
        for name in ("recording_20260305_090000",
                     "recording_20260101_000000",
                     "recording_20260202_120000"):
            _make_recording(self.root, name, QUEUED)
        self.assertEqual(self.names(), ["recording_20260101_000000",
                                        "recording_20260202_120000",
                                        "recording_20260305_090000"])

    def test_skips_recordings_at_the_attempt_limit(self):
        from app.utils.batch_queue import MAX_ATTEMPTS
        _make_recording(self.root, "recording_20260101_000000",
                        {"batch_pending": True, "batch_attempts": MAX_ATTEMPTS})
        _make_recording(self.root, "recording_20260102_000000",
                        {"batch_pending": True, "batch_attempts": MAX_ATTEMPTS - 1})
        self.assertEqual(self.names(), ["recording_20260102_000000"])

    def test_skips_recordings_with_no_audio(self):
        # The audio is the whole input; without it there is nothing to do
        # and the runner would only burn an attempt discovering that.
        _make_recording(self.root, "recording_20260101_000000", QUEUED, audio=None)
        self.assertEqual(self.names(), [])

    def test_skips_recordings_with_no_metadata(self):
        # Crash-recovered folders have audio but no metadata.json, so they
        # cannot carry a tag in the first place.
        _make_recording(self.root, "recording_20260101_000000", metadata=None)
        self.assertEqual(self.names(), [])

    def test_skips_corrupt_metadata(self):
        directory = _make_recording(self.root, "recording_20260101_000000", QUEUED)
        (directory / "metadata.json").write_text("{ truncated", encoding="utf-8")
        self.assertEqual(self.names(), [])

    def test_ignores_loose_files_in_the_recordings_folder(self):
        Path(self.root, "notes.txt").write_text("hi", encoding="utf-8")
        _make_recording(self.root, "recording_20260101_000000", QUEUED)
        self.assertEqual(self.names(), ["recording_20260101_000000"])

    def test_missing_recordings_directory_yields_nothing(self):
        from app.batch.worklist import build_worklist
        self.assertEqual(build_worklist(os.path.join(self.root, "nope")), [])

    def test_limit_caps_the_run(self):
        for name in ("recording_20260101_000000", "recording_20260102_000000",
                     "recording_20260103_000000"):
            _make_recording(self.root, name, QUEUED)
        self.assertEqual(len(self.names(limit=2)), 2)

    def test_already_transcribed_recordings_are_still_processed_when_tagged(self):
        # Tagging is an explicit instruction; re-transcribing (a better
        # model, or diarization this time) is a legitimate reason to tag.
        _make_recording(self.root, "recording_20260101_000000", QUEUED, transcript=True)
        self.assertEqual(self.names(), ["recording_20260101_000000"])


    def test_rebases_stale_audio_paths_onto_the_real_folder(self):
        from app.batch.worklist import build_worklist
        directory = _make_recording(
            self.root, "recording_20260101_000000",
            {"batch_pending": True,
             "directory": "D:/old/recording_20260101_000000",
             "audio_files": {
                 "combined": "D:/old/recording_20260101_000000/combined_audio.wav",
                 "system": None,
             }},
        )
        job = build_worklist(self.root)[0]
        # Recordings live under Documents, which users move (and OneDrive
        # re-homes); the absolute paths baked into metadata.json go stale
        # while the files sit right there next to it.
        self.assertEqual(
            Path(job.session["audio_files"]["combined"]),
            directory / "combined_audio.wav",
        )
        self.assertIsNone(job.session["audio_files"]["system"])

    def test_audio_path_prefers_combined_then_system_then_mic(self):
        from app.batch.worklist import build_worklist
        directory = _make_recording(
            self.root, "recording_20260101_000000",
            {"batch_pending": True,
             "audio_files": {"mic": "mic_audio.wav", "combined": "combined_audio.wav"}},
        )
        (directory / "mic_audio.wav").write_bytes(b"RIFF")
        self.assertEqual(
            Path(build_worklist(self.root)[0].audio_path),
            directory / "combined_audio.wav",
        )


class TestJob(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_job_carries_the_session_metadata(self):
        from app.batch.worklist import build_worklist
        _make_recording(self.root, "recording_20260101_000000",
                        {"batch_pending": True, "name": "Bi-Weekly Sync"})
        job = build_worklist(self.root)[0]
        self.assertEqual(job.session["name"], "Bi-Weekly Sync")

    def test_job_session_directory_points_at_the_folder(self):
        from app.batch.worklist import build_worklist
        directory = _make_recording(self.root, "recording_20260101_000000", QUEUED)
        job = build_worklist(self.root)[0]
        # The pipeline and every writer key off session["directory"]; the
        # stored metadata.json may carry a stale path from another machine.
        self.assertEqual(Path(job.session["directory"]), directory)

    def test_job_label_prefers_the_friendly_name(self):
        from app.batch.worklist import build_worklist
        _make_recording(self.root, "recording_20260101_000000",
                        {"batch_pending": True, "name": "Bi-Weekly Sync"})
        self.assertEqual(build_worklist(self.root)[0].label, "Bi-Weekly Sync")

    def test_job_label_falls_back_to_the_folder_name(self):
        from app.batch.worklist import build_worklist
        _make_recording(self.root, "recording_20260101_000000", QUEUED)
        self.assertEqual(build_worklist(self.root)[0].label, "recording_20260101_000000")


if __name__ == "__main__":
    unittest.main()
