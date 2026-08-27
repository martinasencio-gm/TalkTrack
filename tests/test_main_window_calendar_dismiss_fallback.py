"""Declining the post-recording calendar-suggestion banner must still fall
back to tagging the recording with the ad-hoc meeting name detected from the
Teams/Zoom window title.

Bug: when the calendar lookup returned an *unrelated* overlapping meeting,
the banner was shown and `detected_name` was dropped. Dismissing the banner
then left the recording with no calendar tag at all, even though a usable
detected name was in hand — unlike the no-events path, which tags via
`_maybe_tag_detected_meeting`.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class _FakeWorker:
    """Stands in for the CalendarLookupWorker that `self.sender()` returns."""

    def __init__(self, session, detected_name, manual=False, for_rename=False):
        self.session = session
        self.detected_name = detected_name
        self.manual = manual
        self.for_rename = for_rename


class TestCalendarDismissFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            if hasattr(window, "_meeting_signals_timer"):
                window._meeting_signals_timer.stop()
            if hasattr(window, "_com_session_poller") and window._com_session_poller:
                window._com_session_poller.stop()
            window.close()
        self.addCleanup(_close)
        return window

    def _session(self):
        return {"directory": "/nonexistent/rec", "name": None,
                "started_at": "2026-08-21T11:00:00", "stopped_at": "2026-08-21T11:09:00"}

    def test_lookup_with_events_stashes_detected_name_for_the_banner(self):
        window = self._make_window()
        session = self._session()
        window._current_session = session
        worker = _FakeWorker(session, "Eugen Gitin")
        events = [{"subject": "Some Other Standup", "start": None, "end": None,
                   "organizer": "", "attendees": []}]
        with patch.object(window, "sender", return_value=worker), \
             patch.object(window.calendar_banner, "show_matches"):
            window._on_calendar_lookup_finished(events)
        self.assertEqual(window._calendar_banner_detected_name, "Eugen Gitin")

    def test_dismissing_post_recording_banner_tags_with_detected_name(self):
        window = self._make_window()
        session = self._session()
        window._current_session = session
        window._calendar_banner_session = session
        window._calendar_banner_is_post_recording = True
        window._calendar_banner_detected_name = "Eugen Gitin"
        with patch.object(window, "_maybe_tag_detected_meeting") as mock_tag, \
             patch.object(window, "_open_tag_dialog"):
            window._on_calendar_dismissed()
        mock_tag.assert_called_once_with(session, "Eugen Gitin")

    def test_dismissing_manual_change_lookup_does_not_tag(self):
        # The "Change" flow (manual=True) shows the banner with
        # _calendar_banner_is_post_recording False; dismissing it must not
        # invent a detected-name tag over a deliberate retag.
        window = self._make_window()
        session = self._session()
        window._current_session = session
        window._calendar_banner_session = session
        window._calendar_banner_is_post_recording = False
        window._calendar_banner_detected_name = "Eugen Gitin"
        with patch.object(window, "_maybe_tag_detected_meeting") as mock_tag, \
             patch.object(window, "_open_tag_dialog"):
            window._on_calendar_dismissed()
        mock_tag.assert_not_called()

    def test_dismiss_with_no_detected_name_is_a_noop_tag_call_safe(self):
        window = self._make_window()
        session = self._session()
        window._current_session = session
        window._calendar_banner_session = session
        window._calendar_banner_is_post_recording = True
        window._calendar_banner_detected_name = None
        with patch.object(window, "_maybe_tag_detected_meeting") as mock_tag, \
             patch.object(window, "_open_tag_dialog"):
            window._on_calendar_dismissed()
        # Still routed through the same guard-bearing helper (which no-ops on
        # a falsy name) rather than a separate untested branch.
        mock_tag.assert_called_once_with(session, None)

    def test_selecting_a_meeting_clears_the_stashed_detected_name(self):
        window = self._make_window()
        session = self._session()
        window._current_session = session
        window._calendar_banner_session = session
        window._calendar_banner_detected_name = "Eugen Gitin"
        event = {"subject": "Some Other Standup", "start": "2026-08-21T11:00:00",
                 "end": "2026-08-21T11:30:00", "organizer": "", "attendees": []}
        with patch.object(window, "_apply_calendar_event", return_value=event) as mock_apply, \
             patch.object(window, "_maybe_suggest_rename"), \
             patch.object(window, "_export_transcript"):
            window._on_calendar_tag_requested(event)
        mock_apply.assert_called_once()
        self.assertIsNone(window._calendar_banner_detected_name)


if __name__ == "__main__":
    unittest.main()
