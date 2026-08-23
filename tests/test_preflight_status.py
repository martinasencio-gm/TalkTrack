"""Tests for app.utils.preflight_status — the pure verdict/check computation
behind the capture bar's pre-flight block (app/ui/preflight.py). Extracted so
the truth table is testable without instantiating any Qt widget.
"""
import unittest

from app.utils.preflight_status import (
    READY, WARNING, BLOCKED,
    compute_mic_check, compute_call_check, compute_transcription_check,
    compute_verdict,
)


class TestComputeMicCheck(unittest.TestCase):
    def test_no_mic_is_blocked(self):
        status, _ = compute_mic_check(has_mic=False, mic_mismatch=None)
        self.assertEqual(status, BLOCKED)

    def test_mismatch_is_warning(self):
        status, text = compute_mic_check(
            has_mic=True, mic_mismatch={"app": "Microsoft Teams", "device": "Jabra"}
        )
        self.assertEqual(status, WARNING)
        self.assertIn("Microsoft Teams", text)

    def test_mic_selected_no_mismatch_is_ready(self):
        status, _ = compute_mic_check(has_mic=True, mic_mismatch=None)
        self.assertEqual(status, READY)


class TestComputeCallCheck(unittest.TestCase):
    def test_conferencing_blocked_wins_over_everything(self):
        status, _ = compute_call_check(
            has_source=True, conferencing_blocked=True, output_mismatch=None
        )
        self.assertEqual(status, BLOCKED)

    def test_no_source_selected_is_warning(self):
        status, _ = compute_call_check(
            has_source=False, conferencing_blocked=False, output_mismatch=None
        )
        self.assertEqual(status, WARNING)

    def test_output_mismatch_is_warning(self):
        status, text = compute_call_check(
            has_source=True, conferencing_blocked=False,
            output_mismatch={"app": "Zoom", "device": "Headphones"},
        )
        self.assertEqual(status, WARNING)
        self.assertIn("Zoom", text)

    def test_source_selected_no_issues_is_ready(self):
        status, _ = compute_call_check(
            has_source=True, conferencing_blocked=False, output_mismatch=None
        )
        self.assertEqual(status, READY)


class TestComputeTranscriptionCheck(unittest.TestCase):
    def test_diarization_enabled_without_token_is_warning(self):
        status, _ = compute_transcription_check(
            diarization_enabled=True, hf_token_present=False
        )
        self.assertEqual(status, WARNING)

    def test_diarization_enabled_with_token_is_ready(self):
        status, _ = compute_transcription_check(
            diarization_enabled=True, hf_token_present=True
        )
        self.assertEqual(status, READY)

    def test_diarization_disabled_is_ready_regardless_of_token(self):
        status, _ = compute_transcription_check(
            diarization_enabled=False, hf_token_present=False
        )
        self.assertEqual(status, READY)


class TestComputeVerdict(unittest.TestCase):
    def test_all_ready_is_ready(self):
        verdict, title, subtitle = compute_verdict(READY, READY, READY)
        self.assertEqual(verdict, READY)
        self.assertTrue(title)
        self.assertTrue(subtitle)

    def test_one_warning_is_warning_verdict(self):
        verdict, _, _ = compute_verdict(READY, WARNING, READY)
        self.assertEqual(verdict, WARNING)

    def test_one_blocked_beats_a_warning(self):
        verdict, _, _ = compute_verdict(WARNING, BLOCKED, READY)
        self.assertEqual(verdict, BLOCKED)


if __name__ == "__main__":
    unittest.main()
