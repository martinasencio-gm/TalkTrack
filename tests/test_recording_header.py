"""Tests for RecordingHeader widget."""
import unittest


class TestRecordingHeaderHelpers(unittest.TestCase):

    def test_display_name_from_metadata_with_name(self):
        from app.ui.recording_header import _display_name_from_metadata
        metadata = {"name": "Sprint Planning", "directory": "C:/recordings/rec_2024"}
        self.assertEqual(_display_name_from_metadata(metadata), "Sprint Planning")

    def test_display_name_falls_back_to_directory(self):
        from app.ui.recording_header import _display_name_from_metadata
        metadata = {"directory": "C:/recordings/recording_20240308_1430"}
        self.assertEqual(_display_name_from_metadata(metadata), "recording_20240308_1430")

    def test_display_name_empty_name_falls_back(self):
        from app.ui.recording_header import _display_name_from_metadata
        metadata = {"name": "", "directory": "C:/recordings/my_rec"}
        self.assertEqual(_display_name_from_metadata(metadata), "my_rec")

    def test_format_duration_zero(self):
        from app.ui.recording_header import _format_duration
        self.assertEqual(_format_duration(0), "0s")

    def test_format_duration_minutes(self):
        from app.ui.recording_header import _format_duration
        self.assertEqual(_format_duration(65), "1m 5s")

    def test_format_duration_hours(self):
        from app.ui.recording_header import _format_duration
        self.assertEqual(_format_duration(3661), "1h 1m 1s")

    def test_format_calendar_line_with_attendees(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "Q3 Roadmap Sync", "attendees": ["Jane", "John", "Priya"]}
        self.assertEqual(
            _format_calendar_line(event), "\U0001F4C5 Q3 Roadmap Sync · 3 attendees"
        )

    def test_format_calendar_line_singular_attendee(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "1:1", "attendees": ["Jane"]}
        self.assertEqual(_format_calendar_line(event), "\U0001F4C5 1:1 · 1 attendee")

    def test_format_calendar_line_no_attendees(self):
        from app.ui.recording_header import _format_calendar_line
        event = {"subject": "Focus Block", "attendees": []}
        self.assertEqual(_format_calendar_line(event), "\U0001F4C5 Focus Block")

    def test_format_transcribe_line_with_model(self):
        from app.ui.recording_header import _format_transcribe_line
        self.assertEqual(
            _format_transcribe_line("small", 72.0),
            "Transcribed in 1m 12s using small model",
        )

    def test_format_transcribe_line_no_model(self):
        from app.ui.recording_header import _format_transcribe_line
        self.assertEqual(
            _format_transcribe_line("", 5.0),
            "Transcribed in 5s",
        )

    def test_format_transcribe_line_no_time_yet(self):
        from app.ui.recording_header import _format_transcribe_line
        self.assertEqual(_format_transcribe_line("small", 0.0), "")


if __name__ == "__main__":
    unittest.main()


class TestMatchEventBySubject(unittest.TestCase):
    """Renaming a recording to a suggested meeting's subject should also tag
    it with that meeting — the user picked the meeting, not just its text."""

    def _events(self):
        return [
            {"subject": "Sprint Planning", "organizer": "ana@example.com"},
            {"subject": "TimJ <> Martin A - Monthly Catch Up", "organizer": "tim@example.com"},
        ]

    def test_matches_a_suggested_subject(self):
        from app.ui.recording_header import match_event_by_subject
        event = match_event_by_subject("Sprint Planning", self._events())
        self.assertEqual(event["organizer"], "ana@example.com")

    def test_ignores_surrounding_whitespace(self):
        from app.ui.recording_header import match_event_by_subject
        event = match_event_by_subject("  Sprint Planning  ", self._events())
        self.assertIsNotNone(event)

    def test_a_freely_typed_name_matches_nothing(self):
        # Typing your own name must rename without silently tagging the
        # recording to a meeting the user never chose.
        from app.ui.recording_header import match_event_by_subject
        self.assertIsNone(match_event_by_subject("Notes to self", self._events()))

    def test_partial_subject_does_not_match(self):
        from app.ui.recording_header import match_event_by_subject
        self.assertIsNone(match_event_by_subject("Sprint", self._events()))

    def test_no_candidates(self):
        from app.ui.recording_header import match_event_by_subject
        self.assertIsNone(match_event_by_subject("Sprint Planning", []))

    def test_empty_name_matches_nothing(self):
        from app.ui.recording_header import match_event_by_subject
        self.assertIsNone(match_event_by_subject("", [{"subject": ""}]))
