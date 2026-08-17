# tests/test_transcribing_indicator.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from app.transcription.job_status import transcribing_directories


class _FakeWorker:
    def __init__(self, session, running=True):
        self.session = session
        self._running = running

    def isRunning(self):
        return self._running


def session(directory):
    return {"directory": directory}


class TestTranscribingDirectories(unittest.TestCase):
    """Which recordings the list should mark as in-progress. Workers carry
    their session bound at creation, so a job keeps pointing at its own
    recording even after the user selects a different one."""

    def test_running_worker_marks_its_session(self):
        dirs = transcribing_directories([_FakeWorker(session("C:/rec/a"))], [])
        self.assertEqual(dirs, {"C:/rec/a"})

    def test_finished_worker_marks_nothing(self):
        dirs = transcribing_directories(
            [_FakeWorker(session("C:/rec/a"), running=False)], [])
        self.assertEqual(dirs, set())

    def test_absent_workers_are_skipped(self):
        # The three worker slots are None until their stage runs.
        dirs = transcribing_directories([None, _FakeWorker(session("C:/rec/a"))], [])
        self.assertEqual(dirs, {"C:/rec/a"})

    def test_queued_jobs_count_as_in_progress(self):
        # A queued recording is going to be transcribed without further
        # input, so the row should say so rather than looking untouched.
        dirs = transcribing_directories([], [("audio.wav", session("C:/rec/b"))])
        self.assertEqual(dirs, {"C:/rec/b"})

    def test_combines_running_and_queued(self):
        dirs = transcribing_directories(
            [_FakeWorker(session("C:/rec/a"))],
            [("audio.wav", session("C:/rec/b"))],
        )
        self.assertEqual(dirs, {"C:/rec/a", "C:/rec/b"})

    def test_same_recording_across_stages_appears_once(self):
        # Transcription and diarization both run for one recording.
        a = session("C:/rec/a")
        dirs = transcribing_directories([_FakeWorker(a), _FakeWorker(a)], [])
        self.assertEqual(dirs, {"C:/rec/a"})

    def test_worker_without_a_session_is_ignored(self):
        # Re-transcribing from the viewer can run with no session bound.
        dirs = transcribing_directories([_FakeWorker(None)], [])
        self.assertEqual(dirs, set())

    def test_session_without_a_directory_is_ignored(self):
        dirs = transcribing_directories([_FakeWorker({})], [])
        self.assertEqual(dirs, set())

    def test_nothing_running_or_queued(self):
        self.assertEqual(transcribing_directories([], []), set())


if __name__ == "__main__":
    unittest.main()
