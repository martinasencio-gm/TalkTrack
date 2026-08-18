"""Tests for the batch runner's --until wall-clock cutoff.

The cutoff is the latest time a *new* recording may be started. It is
checked between recordings only — a job already running is allowed to
finish.
"""
import unittest
from datetime import datetime


class TestParseCutoff(unittest.TestCase):
    def test_bare_time_later_today(self):
        from app.batch.cutoff import parse_cutoff
        now = datetime(2026, 8, 17, 20, 0)
        self.assertEqual(parse_cutoff("23:30", now=now), datetime(2026, 8, 17, 23, 30))

    def test_bare_time_already_passed_means_tomorrow(self):
        from app.batch.cutoff import parse_cutoff
        # The whole point of the feature: a task firing at 23:00 with
        # --until 07:00 gets eight hours, not a cutoff already in the past.
        now = datetime(2026, 8, 17, 23, 0)
        self.assertEqual(parse_cutoff("07:00", now=now), datetime(2026, 8, 18, 7, 0))

    def test_bare_time_equal_to_now_means_tomorrow(self):
        from app.batch.cutoff import parse_cutoff
        # "Don't start anything after 07:00", asked at exactly 07:00, can
        # only sensibly mean the next one — otherwise the run does nothing.
        now = datetime(2026, 8, 17, 7, 0)
        self.assertEqual(parse_cutoff("07:00", now=now), datetime(2026, 8, 18, 7, 0))

    def test_accepts_an_absolute_iso_datetime(self):
        from app.batch.cutoff import parse_cutoff
        now = datetime(2026, 8, 17, 23, 0)
        self.assertEqual(
            parse_cutoff("2026-08-19T06:30", now=now), datetime(2026, 8, 19, 6, 30),
        )

    def test_absolute_datetime_in_the_past_is_honoured_as_given(self):
        from app.batch.cutoff import parse_cutoff
        # An explicit date is unambiguous: the caller meant that instant,
        # so it is not rolled forward the way a bare HH:MM is.
        now = datetime(2026, 8, 17, 23, 0)
        self.assertEqual(
            parse_cutoff("2026-08-16T06:30", now=now), datetime(2026, 8, 16, 6, 30),
        )

    def test_rejects_nonsense(self):
        from app.batch.cutoff import parse_cutoff, CutoffError
        for bad in ("", "later", "25:00", "07:61", "7pm", None):
            with self.assertRaises(CutoffError):
                parse_cutoff(bad, now=datetime(2026, 8, 17, 20, 0))

    def test_accepts_single_digit_hour(self):
        from app.batch.cutoff import parse_cutoff
        now = datetime(2026, 8, 17, 20, 0)
        self.assertEqual(parse_cutoff("7:05", now=now), datetime(2026, 8, 18, 7, 5))


class TestMayStartAnother(unittest.TestCase):
    def test_before_the_cutoff(self):
        from app.batch.cutoff import may_start_another
        cutoff = datetime(2026, 8, 18, 7, 0)
        self.assertTrue(may_start_another(cutoff, now=datetime(2026, 8, 18, 6, 59)))

    def test_at_the_cutoff(self):
        from app.batch.cutoff import may_start_another
        cutoff = datetime(2026, 8, 18, 7, 0)
        self.assertFalse(may_start_another(cutoff, now=datetime(2026, 8, 18, 7, 0)))

    def test_after_the_cutoff(self):
        from app.batch.cutoff import may_start_another
        cutoff = datetime(2026, 8, 18, 7, 0)
        self.assertFalse(may_start_another(cutoff, now=datetime(2026, 8, 18, 7, 1)))

    def test_no_cutoff_never_stops(self):
        from app.batch.cutoff import may_start_another
        self.assertTrue(may_start_another(None, now=datetime(2099, 1, 1)))


if __name__ == "__main__":
    unittest.main()
