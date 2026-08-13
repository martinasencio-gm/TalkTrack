"""Tests for Outlook calendar overlap matching."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestEventOverlapsWindow(unittest.TestCase):
    def test_exact_overlap(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        window_start = datetime(2026, 8, 13, 14, 0)
        window_end = datetime(2026, 8, 13, 14, 45)
        self.assertTrue(_event_overlaps_window(
            window_start, window_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_no_overlap_far_apart(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 9, 0)
        event_end = datetime(2026, 8, 13, 9, 30)
        window_start = datetime(2026, 8, 13, 14, 0)
        window_end = datetime(2026, 8, 13, 14, 45)
        self.assertFalse(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_late_join_within_tolerance(self):
        # Recording started 4 minutes after the event's scheduled start.
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 14, 0)
        event_end = datetime(2026, 8, 13, 14, 45)
        window_start = datetime(2026, 8, 13, 14, 4)
        window_end = datetime(2026, 8, 13, 14, 40)
        self.assertTrue(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))

    def test_just_outside_tolerance(self):
        from app.integrations.outlook_calendar import _event_overlaps_window
        event_start = datetime(2026, 8, 13, 14, 0)
        event_end = datetime(2026, 8, 13, 14, 45)
        window_start = datetime(2026, 8, 13, 15, 0)  # 15 min after event ends
        window_end = datetime(2026, 8, 13, 15, 30)
        self.assertFalse(_event_overlaps_window(
            event_start, event_end, window_start, window_end, tolerance_minutes=5
        ))


class _FakeAppointment:
    def __init__(self, subject, start, end, organizer, attendees):
        self.Subject = subject
        self.Start = start
        self.End = end
        self.Organizer = organizer
        self.RequiredAttendees = attendees  # semicolon-separated, as Outlook returns it


class TestFindOverlappingEvents(unittest.TestCase):
    def _mock_outlook(self, appointments):
        mock_items = MagicMock()
        mock_items.__iter__ = lambda self_: iter(appointments)
        mock_items.IncludeRecurrences = False
        mock_items.Sort = MagicMock()
        # find_overlapping_events calls items.Restrict(...) and iterates the
        # result (C2 fix) — the fake calendar has no real filtering, so
        # Restrict just hands back the same (already fully-populated) fake
        # Items collection.
        mock_items.Restrict = MagicMock(return_value=mock_items)
        mock_calendar_folder = MagicMock()
        mock_calendar_folder.Items = mock_items
        mock_namespace = MagicMock()
        mock_namespace.GetDefaultFolder.return_value = mock_calendar_folder
        mock_outlook_app = MagicMock()
        mock_outlook_app.GetNamespace.return_value = mock_namespace
        return mock_outlook_app

    def test_single_match_returns_event_dict(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Q3 Roadmap Sync",
            datetime(2026, 8, 13, 14, 0),
            datetime(2026, 8, 13, 14, 45),
            "Jane Smith",
            "Jane Smith; John Doe; Priya Patel",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Q3 Roadmap Sync")
        self.assertEqual(results[0]["organizer"], "Jane Smith")
        self.assertEqual(
            results[0]["attendees"], ["Jane Smith", "John Doe", "Priya Patel"]
        )

    def test_no_matches_returns_empty_list(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Unrelated Meeting",
            datetime(2026, 8, 13, 9, 0),
            datetime(2026, 8, 13, 9, 30),
            "Someone Else",
            "Someone Else",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results, [])

    def test_multiple_overlaps_returns_all(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt1 = _FakeAppointment(
            "Meeting A", datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 30),
            "Alice", "Alice",
        )
        appt2 = _FakeAppointment(
            "Meeting B", datetime(2026, 8, 13, 14, 15), datetime(2026, 8, 13, 14, 45),
            "Bob", "Bob",
        )
        mock_app = self._mock_outlook([appt1, appt2])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual({r["subject"] for r in results}, {"Meeting A", "Meeting B"})

    def test_outlook_unavailable_returns_empty_list(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    side_effect=Exception("Outlook not installed")):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results, [])

    def test_empty_attendees_string_returns_empty_list_not_list_with_blank(self):
        from app.integrations.outlook_calendar import find_overlapping_events
        appt = _FakeAppointment(
            "Solo Block", datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45),
            "Jane Smith", "",
        )
        mock_app = self._mock_outlook([appt])
        with patch("app.integrations.outlook_calendar.win32com.client.Dispatch",
                    return_value=mock_app):
            results = find_overlapping_events(
                datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 13, 14, 45)
            )
        self.assertEqual(results[0]["attendees"], [])


if __name__ == "__main__":
    unittest.main()
