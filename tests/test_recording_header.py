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


if __name__ == "__main__":
    unittest.main()
