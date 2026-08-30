"""Task 6: opening a recording always shows both outer columns for that
viewing session, without overwriting the user's saved collapse preference.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestExpandPanelsForRecordingView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_expands_a_collapsed_transcript_column(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter2.is_collapsed())

    def test_expands_a_collapsed_inspector_column(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.splitter1.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter1.is_collapsed())

    def test_does_not_persist_the_expand(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()  # persists transcript_collapsed=True
        window.splitter1.toggle_collapse()  # persists inspector_collapsed=True
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

        window._expand_panels_for_recording_view()

        # Visually open now, but the saved preference must be untouched —
        # a fresh launch should still come up collapsed.
        self.assertFalse(window.splitter2.is_collapsed())
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

    def test_is_a_noop_when_already_expanded(self):
        window = _make_window(self)
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()  # must not raise or toggle anything

        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

    def test_do_on_recording_selected_calls_the_expand_helper(self):
        from unittest.mock import patch
        window = _make_window(self)
        metadata = {
            "directory": window.config.get("output", "directory"),
            "audio_files": {},
        }
        with patch.object(window, "_expand_panels_for_recording_view") as mock_expand:
            window._do_on_recording_selected(metadata)
        mock_expand.assert_called_once()

    def test_do_on_recording_selected_can_opt_out_of_the_expand(self):
        # Finding #2: a caller that isn't a user-initiated open (the
        # background batch-refresh path) must be able to skip the
        # force-expand and leave a deliberately collapsed layout alone.
        from unittest.mock import patch
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        window.splitter1.toggle_collapse()
        self.assertTrue(window.splitter2.is_collapsed())
        self.assertTrue(window.splitter1.is_collapsed())
        metadata = {
            "directory": window.config.get("output", "directory"),
            "audio_files": {},
        }
        with patch.object(window, "_expand_panels_for_recording_view") as mock_expand:
            window._do_on_recording_selected(metadata, expand_panels=False)
        mock_expand.assert_not_called()
        self.assertTrue(window.splitter2.is_collapsed())
        self.assertTrue(window.splitter1.is_collapsed())

    def test_batch_job_finished_does_not_force_expand_collapsed_panels(self):
        # A finishing batch job refreshing the currently displayed recording
        # is not a user-initiated open and must not undo the user's
        # collapsed layout.
        from app.batch.pipeline import JobOutcome
        from app.batch.worklist import Job
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        window.splitter1.toggle_collapse()

        directory = window.config.get("output", "directory")
        metadata = {"directory": directory, "audio_files": {}}
        window._current_session = metadata
        job = Job(directory=directory, session=metadata, label="test", audio_path=None)
        outcome = JobOutcome(ok=True, message="ok")

        window._on_batch_job_finished(job, outcome)

        self.assertTrue(window.splitter2.is_collapsed())
        self.assertTrue(window.splitter1.is_collapsed())

    def test_batch_job_finished_refresh_passes_expand_panels_false(self):
        from unittest.mock import patch
        from app.batch.pipeline import JobOutcome
        from app.batch.worklist import Job
        window = _make_window(self)
        directory = window.config.get("output", "directory")
        metadata = {"directory": directory, "audio_files": {}}
        window._current_session = metadata
        job = Job(directory=directory, session=metadata, label="test", audio_path=None)
        outcome = JobOutcome(ok=True, message="ok")

        with patch.object(window, "_do_on_recording_selected") as mock_do:
            window._on_batch_job_finished(job, outcome)

        mock_do.assert_called_once_with(metadata, expand_panels=False)


if __name__ == "__main__":
    unittest.main()
