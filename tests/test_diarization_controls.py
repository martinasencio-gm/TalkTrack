"""Tests for the per-run diarization checkbox and on-demand button."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _transcript():
    from app.transcription.transcriber import TranscriptResult, TranscriptSegment
    return TranscriptResult(
        segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
        duration=1.0,
    )


class TestDiarizationCheckbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        from app.ui.transcript_viewer import TranscriptViewer
        self.viewer = TranscriptViewer()
        self.addCleanup(self.viewer.deleteLater)

    def test_unchecked_by_default(self):
        self.assertFalse(self.viewer.diarization_enabled())

    def test_checked_with_a_token_reports_enabled(self):
        self.viewer.set_diarization_available(True)
        self.viewer.set_diarization_enabled(True)
        self.assertTrue(self.viewer.diarization_enabled())

    def test_without_a_token_a_checked_box_still_reports_disabled(self):
        # The saved preference can be True from a session that had a token.
        # Reporting it as enabled would send pyannote a job it cannot run.
        self.viewer.set_diarization_available(False)
        self.viewer.set_diarization_enabled(True)
        self.assertFalse(self.viewer.diarization_enabled())
        self.assertFalse(self.viewer.diarize_cb.isEnabled())

    def test_programmatic_set_does_not_emit(self):
        # Syncing from config must not look like a user change, or loading
        # the window would write the setting straight back.
        seen = []
        self.viewer.diarize_toggled.connect(seen.append)
        self.viewer.set_diarization_available(True)
        self.viewer.set_diarization_enabled(True)
        self.assertEqual(seen, [])

    def test_user_toggle_emits(self):
        seen = []
        self.viewer.set_diarization_available(True)
        self.viewer.diarize_toggled.connect(seen.append)
        self.viewer.diarize_cb.setChecked(True)
        self.assertEqual(seen, [True])


class TestOnDemandDiarizeButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        from app.ui.transcript_viewer import TranscriptViewer
        self.viewer = TranscriptViewer()
        self.viewer.show()  # isVisible() is False for an unshown parent
        self.addCleanup(self.viewer.deleteLater)
        _get_app().processEvents()

    def _ready(self):
        self.viewer.set_diarization_available(True)
        self.viewer.set_audio_path("/fake/combined.wav")
        self.viewer.display_transcript(_transcript())
        _get_app().processEvents()

    def test_hidden_without_a_token(self):
        self.viewer.set_audio_path("/fake/combined.wav")
        self.viewer.display_transcript(_transcript())
        _get_app().processEvents()
        self.assertFalse(self.viewer.diarize_btn.isVisible())

    def test_hidden_without_a_transcript_to_label(self):
        self.viewer.set_diarization_available(True)
        self.viewer.set_audio_path("/fake/combined.wav")
        _get_app().processEvents()
        self.assertFalse(self.viewer.diarize_btn.isVisible())

    def test_hidden_without_audio_to_read(self):
        self.viewer.set_diarization_available(True)
        self.viewer.display_transcript(_transcript())
        _get_app().processEvents()
        self.assertFalse(self.viewer.diarize_btn.isVisible())

    def test_visible_with_token_transcript_and_audio(self):
        self._ready()
        self.assertTrue(self.viewer.diarize_btn.isVisible())
        self.assertTrue(self.viewer.diarize_btn.isEnabled())

    def test_click_requests_diarization(self):
        self._ready()
        seen = []
        self.viewer.diarize_requested.connect(lambda: seen.append(True))
        self.viewer.diarize_btn.click()
        self.assertEqual(seen, [True])

    def test_hidden_again_after_clear(self):
        self._ready()
        self.viewer.clear()
        _get_app().processEvents()
        self.assertFalse(self.viewer.diarize_btn.isVisible())


class TestMainWindowWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        from app.main_window import MainWindow
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window._really_quit = True
        self.window.close()

    def test_toggling_the_checkbox_persists_the_preference(self):
        from unittest.mock import patch
        original = self.window.config.get("diarization", "enabled")
        # save() is stubbed so the running app's real settings.json is left
        # alone; the in-memory write is what this asserts on.
        with patch.object(self.window.config, "save"):
            self.window._on_diarize_toggled(not original)
            self.assertEqual(
                self.window.config.get("diarization", "enabled"), not original
            )
            self.window._on_diarize_toggled(original)

    def test_on_demand_does_nothing_without_a_loaded_recording(self):
        from unittest.mock import patch
        self.window._transcript = None
        self.window._current_session = None
        with patch.object(self.window, "_start_diarization") as start:
            self.window._on_diarize_requested()
        start.assert_not_called()

    def test_on_demand_waits_rather_than_stacking_on_a_running_job(self):
        # The pipeline runs one job at a time; a second one would race the
        # first for the same transcript.
        from unittest.mock import MagicMock, patch
        self.window._transcript = _transcript()
        self.window._current_session = {"directory": "/fake", "audio_files": {}}
        with patch.object(self.window, "_transcription_busy", return_value=True), \
             patch.object(self.window, "_start_diarization") as start:
            self.window._on_diarize_requested()
        start.assert_not_called()

    def test_on_demand_diarizes_the_loaded_transcript(self):
        from unittest.mock import patch
        transcript = _transcript()
        session = {"directory": "/fake", "audio_files": {"combined": "/fake/c.wav"}}
        self.window._transcript = transcript
        self.window._current_session = session
        with patch.object(self.window, "_transcription_busy", return_value=False), \
             patch.object(self.window, "_start_diarization") as start:
            self.window._on_diarize_requested()
        start.assert_called_once_with(transcript, session)

    def test_display_transcript_hides_progress_and_stops_timer(self):
        self.window.transcript_viewer.show_progress("Running speaker diarization...")
        self.assertFalse(self.window.transcript_viewer.status_label.isHidden())
        self.assertTrue(self.window.transcript_viewer._elapsed_timer.isActive())

        self.window.transcript_viewer.display_transcript(_transcript())
        self.assertTrue(self.window.transcript_viewer.status_label.isHidden())
        self.assertFalse(self.window.transcript_viewer._elapsed_timer.isActive())
        self.assertIsNone(self.window.transcript_viewer._progress_start_time)

    def test_clear_hides_progress_and_stops_timer(self):
        self.window.transcript_viewer.show_progress("Running speaker diarization...")
        self.assertTrue(self.window.transcript_viewer._elapsed_timer.isActive())

        self.window.transcript_viewer.clear()
        self.assertTrue(self.window.transcript_viewer.status_label.isHidden())
        self.assertFalse(self.window.transcript_viewer._elapsed_timer.isActive())
        self.assertIsNone(self.window.transcript_viewer._progress_start_time)

    def test_display_final_transcript_hides_progress_when_session_is_different(self):
        self.window.transcript_viewer.show_progress("Running speaker diarization...")
        self.assertTrue(self.window.transcript_viewer._elapsed_timer.isActive())

        # Final transcript arrives for a background session
        other_session = {"directory": "/fake/other", "name": "Other Meeting"}
        with patch.object(self.window, "_write_transcript_for_session"), \
             patch.object(self.window, "_process_pending_transcriptions"):
            self.window._display_final_transcript(_transcript(), session=other_session)

        self.assertTrue(self.window.transcript_viewer.status_label.isHidden())
        self.assertFalse(self.window.transcript_viewer._elapsed_timer.isActive())


class TestOneOnOneCallDiarizationDefault(unittest.TestCase):
    """A calendar-tagged 1:1 call (you + exactly one other attendee) is
    exactly what SimpleDiarizer's mic-vs-system energy split already
    handles well: there is only one possible "Remote" speaker, so
    pyannote's clustering buys nothing. 'Identify speakers' should default
    off for such a recording, without touching the persisted global
    preference (see test_toggling_the_checkbox_persists_the_preference
    above for why that distinction matters)."""

    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window._really_quit = True
        self.window.close()

    def _session(self, name, attendees=None):
        import json
        session_dir = self.tmp_dir / name
        session_dir.mkdir()
        if attendees is not None:
            (session_dir / "calendar_event.json").write_text(
                json.dumps({"subject": "Sync", "attendees": attendees}),
                encoding="utf-8",
            )
        return {"directory": str(session_dir), "audio_files": {}}

    def test_one_attendee_unchecks_identify_speakers(self):
        session = self._session("one_on_one", attendees=["alice@example.com"])
        self.window._on_recording_selected(session)
        self.assertFalse(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_two_attendees_keeps_the_default_checked(self):
        # Group call: SimpleDiarizer's binary You/Remote split can't tell
        # two remote attendees apart, so pyannote still earns its keep here.
        session = self._session("group_call", attendees=["alice@example.com", "bob@example.com"])
        self.window._on_recording_selected(session)
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_zero_attendees_keeps_the_default_checked(self):
        session = self._session("solo", attendees=[])
        self.window._on_recording_selected(session)
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_untagged_recording_keeps_the_default_checked(self):
        session = self._session("untagged", attendees=None)
        self.window._on_recording_selected(session)
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_switching_from_one_on_one_to_group_restores_the_default(self):
        # Regression guard: the auto-uncheck for a 1:1 call must not leak
        # into the next, unrelated recording selected afterwards.
        one_on_one = self._session("one_on_one2", attendees=["alice@example.com"])
        group = self._session("group_call2", attendees=["alice@example.com", "bob@example.com"])

        self.window._on_recording_selected(one_on_one)
        self.assertFalse(self.window.transcript_viewer.diarize_cb.isChecked())

        self.window._on_recording_selected(group)
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_manual_recheck_for_a_one_on_one_survives_settings_resync(self):
        # _sync_diarization_controls() also runs when Settings closes (it
        # pushes the token/enabled flag the dialog owns back into the
        # checkbox). That must not re-apply the 1:1 override on top of a
        # manual choice the user already made for the recording on screen.
        session = self._session("one_on_one3", attendees=["alice@example.com"])
        self.window._on_recording_selected(session)
        self.assertFalse(self.window.transcript_viewer.diarize_cb.isChecked())

        self.window.transcript_viewer.diarize_cb.setChecked(True)
        self.window._sync_diarization_controls()
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

    def test_tagging_an_already_open_recording_as_one_on_one_unchecks_it(self):
        # The other place attendees become known: tagging a recording
        # that's already on screen (calendar banner / manual tag), not just
        # selecting a pre-tagged one.
        session = self._session("to_be_tagged", attendees=None)
        self.window._on_recording_selected(session)
        self.assertTrue(self.window.transcript_viewer.diarize_cb.isChecked())

        self.window._apply_calendar_event({
            "subject": "1:1 sync", "attendees": ["alice@example.com"],
            "start": "2026-01-01T10:00:00", "end": "2026-01-01T10:30:00",
        })
        self.assertFalse(self.window.transcript_viewer.diarize_cb.isChecked())


if __name__ == "__main__":
    unittest.main()
