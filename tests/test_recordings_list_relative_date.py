"""Tests for format_relative_date, the pure helper behind the recordings
list's "Today, HH:MM" / "Yesterday, HH:MM" / "D Mon, HH:MM" row date label.
"""
import unittest
from datetime import datetime

from app.ui.recordings_list import format_relative_date


class TestFormatRelativeDate(unittest.TestCase):
    def test_same_day_is_today(self):
        now = datetime(2026, 8, 22, 15, 30)
        dt = datetime(2026, 8, 22, 9, 2)
        self.assertEqual(format_relative_date(dt, now=now), "Today, 09:02")

    def test_one_day_before_is_yesterday(self):
        now = datetime(2026, 8, 22, 15, 30)
        dt = datetime(2026, 8, 21, 14, 30)
        self.assertEqual(format_relative_date(dt, now=now), "Yesterday, 14:30")

    def test_older_uses_day_month_time(self):
        now = datetime(2026, 8, 22, 15, 30)
        dt = datetime(2026, 8, 19, 15, 20)
        self.assertEqual(format_relative_date(dt, now=now), "19 Aug, 15:20")

    def test_yesterday_across_month_boundary(self):
        now = datetime(2026, 9, 1, 8, 0)
        dt = datetime(2026, 8, 31, 23, 45)
        self.assertEqual(format_relative_date(dt, now=now), "Yesterday, 23:45")

    def test_two_days_before_is_not_yesterday(self):
        now = datetime(2026, 8, 22, 15, 30)
        dt = datetime(2026, 8, 20, 15, 30)
        self.assertEqual(format_relative_date(dt, now=now), "20 Aug, 15:30")

    def test_defaults_now_when_not_given(self):
        # Smoke test: no `now` arg still returns a non-empty string using the
        # real clock, without raising.
        dt = datetime(2020, 1, 1, 0, 0)
        result = format_relative_date(dt)
        self.assertIsInstance(result, str)
        self.assertIn("Jan", result)


if __name__ == "__main__":
    unittest.main()
