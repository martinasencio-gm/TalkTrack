import unittest

from app.ui.meeting_banner import format_start_text, format_end_text


class TestStartText(unittest.TestCase):
    def test_with_name_and_elapsed(self):
        self.assertEqual(format_start_text("Sprint Planning", 120),
                         "Sprint Planning started 2 minutes ago - record it?")

    def test_without_name_falls_back(self):
        self.assertEqual(format_start_text(None, 60),
                         "A meeting started 1 minute ago - record it?")

    def test_under_a_minute(self):
        self.assertEqual(format_start_text("Standup", 30),
                         "Standup started just now - record it?")

    def test_zero_elapsed(self):
        self.assertEqual(format_start_text("Standup", 0),
                         "Standup started just now - record it?")


class TestEndText(unittest.TestCase):
    def test_states_captured_length(self):
        self.assertEqual(
            format_end_text("Sprint Planning", 1440),
            "Sprint Planning ended - stop recording? (24 minutes captured)")

    def test_without_name(self):
        self.assertEqual(format_end_text(None, 60),
                         "The meeting ended - stop recording? (1 minute captured)")

    def test_very_short_recording(self):
        self.assertEqual(
            format_end_text(None, 12),
            "The meeting ended - stop recording? (less than a minute captured)")


if __name__ == "__main__":
    unittest.main()
