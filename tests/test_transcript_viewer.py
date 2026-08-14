"""Tests for TranscriptViewer progress-display helpers."""
import unittest


class TestFormatProgressText(unittest.TestCase):

    def test_no_percent_shows_message_and_elapsed(self):
        from app.ui.transcript_viewer import _format_progress_text
        self.assertEqual(
            _format_progress_text("Loading model...", elapsed_seconds=5, percent=None),
            "Loading model...  (00:05 elapsed)",
        )

    def test_no_elapsed_shows_message_only(self):
        from app.ui.transcript_viewer import _format_progress_text
        self.assertEqual(
            _format_progress_text("Loading model...", elapsed_seconds=None, percent=None),
            "Loading model...",
        )

    def test_percent_with_remaining_shows_eta(self):
        from app.ui.transcript_viewer import _format_progress_text
        # 30s elapsed = 25% of total => 120s total => 90s remaining
        self.assertEqual(
            _format_progress_text("Transcribing...", elapsed_seconds=30, percent=25),
            "Transcribing...  25%  (00:30 elapsed · ~01:30 remaining)",
        )

    def test_percent_zero_shows_no_eta(self):
        from app.ui.transcript_viewer import _format_progress_text
        self.assertEqual(
            _format_progress_text("Transcribing...", elapsed_seconds=2, percent=0),
            "Transcribing...  0%  (00:02 elapsed)",
        )

    def test_percent_complete_shows_no_eta(self):
        from app.ui.transcript_viewer import _format_progress_text
        self.assertEqual(
            _format_progress_text("Transcribing...", elapsed_seconds=90, percent=100),
            "Transcribing...  100%  (01:30 elapsed)",
        )


if __name__ == "__main__":
    unittest.main()
