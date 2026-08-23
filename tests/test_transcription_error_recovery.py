"""Tests for the transcription-error UI recovery path.

_on_transcription_error referenced TranscriptViewer.current_recording_id and
self._current_session_id, neither of which exist anywhere in the codebase —
every transcription failure for the displayed recording raised AttributeError
before the "Transcription Failed" notification could even be shown.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication

from app.transcription.transcriber import TranscriptResult, TranscriptSegment
from app.ui.transcript_viewer import TranscriptViewer
from app.ui.inspector import InspectorWidget

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _sample_transcript():
    return TranscriptResult(
        segments=[
            TranscriptSegment(start=0.0, end=4.0, text="First segment", speaker="SPEAKER_00"),
        ],
        language="en",
        duration=4.0,
    )


class TestTranscriptViewerShowEmptyState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_show_empty_state_true_reverts_to_placeholder(self):
        viewer = TranscriptViewer()
        viewer.display_transcript(_sample_transcript())
        self.assertEqual(len(viewer._segment_widgets), 1)

        viewer.show_empty_state(True)

        self.assertEqual(len(viewer._segment_widgets), 0)
        self.assertFalse(viewer.copy_all_btn.isEnabled())
        self.assertFalse(viewer.play_all_btn.isEnabled())

    def test_show_empty_state_false_is_a_no_op(self):
        viewer = TranscriptViewer()
        viewer.display_transcript(_sample_transcript())

        viewer.show_empty_state(False)

        self.assertEqual(len(viewer._segment_widgets), 1)


class _StubNotificationRegion:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)


class _StubRecordingControls:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


class _StubRecordingsList:
    def __init__(self):
        self.refreshed = False

    def refresh_status(self):
        self.refreshed = True


class TestOnTranscriptionErrorRecovery(unittest.TestCase):
    """Exercises MainWindow._on_transcription_error directly, wiring only
    the real collaborators it touches (TranscriptViewer, InspectorWidget) and
    stubbing the rest. Full MainWindow() construction runs real device/COM
    enumeration — slow, and per .claude/rules/per-app-audio-capture.md a
    comtypes proxy crash there can take the whole process down natively, so
    it's the wrong tool for a pure UI-recovery-logic test."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _make_window(self):
        from app.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window._current_session = None
        window._transcription_worker = None
        window._transcription_busy = False
        window.transcript_viewer = TranscriptViewer()
        window.inspector = InspectorWidget()
        window.recording_controls = _StubRecordingControls()
        window.notification_region = _StubNotificationRegion()
        window.recordings_list = _StubRecordingsList()
        window._process_pending_transcriptions = lambda: None
        return window

    def _session(self, name):
        session_dir = self.tmp_dir / name
        session_dir.mkdir()
        return {"directory": str(session_dir), "name": name}

    def test_error_for_displayed_session_does_not_raise_and_clears_viewer(self):
        window = self._make_window()
        session = self._session("recording_current")
        window._current_session = session
        window.transcript_viewer.display_transcript(_sample_transcript())

        window._transcription_worker = SimpleNamespace(session=session)

        window._on_transcription_error("boom")  # must not raise

        self.assertEqual(len(window.transcript_viewer._segment_widgets), 0)
        self.assertEqual(len(window.notification_region.enqueued), 1)
        self.assertIn("boom", window.notification_region.enqueued[0]["text"])

    def test_error_for_background_session_leaves_displayed_transcript_alone(self):
        window = self._make_window()
        current = self._session("recording_current")
        background = self._session("recording_background")
        window._current_session = current
        window.transcript_viewer.display_transcript(_sample_transcript())

        window._transcription_worker = SimpleNamespace(session=background)

        window._on_transcription_error("boom")  # must not raise

        self.assertEqual(len(window.transcript_viewer._segment_widgets), 1)

    def test_error_with_no_bound_worker_session_does_not_raise(self):
        window = self._make_window()
        window._current_session = self._session("recording_current")
        window._transcription_worker = None

        window._on_transcription_error("boom")  # must not raise


if __name__ == "__main__":
    unittest.main()
