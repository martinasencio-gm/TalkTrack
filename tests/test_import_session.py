"""Tests for recording-import metadata construction."""
import unittest
from datetime import datetime


class TestBuildImportMetadata(unittest.TestCase):
    def test_computes_stopped_at_from_duration(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.wav",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=90.0,
            audio_filename="combined_audio.wav",
        )
        self.assertEqual(meta["started_at"], "2026-08-13T14:00:00")
        self.assertEqual(meta["stopped_at"], "2026-08-13T14:01:30")
        self.assertEqual(meta["duration"], 90.0)

    def test_marks_session_as_imported(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.m4a",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=60.0,
            audio_filename="combined_audio.wav",
        )
        self.assertTrue(meta["imported"])
        self.assertEqual(meta["capture_mode"], "imported")
        self.assertEqual(meta["source_filename"], "call.m4a")

    def test_audio_files_points_at_combined_track(self):
        from app.recording.import_session import build_import_metadata
        meta = build_import_metadata(
            source_path="C:/Downloads/call.wav",
            session_dir="C:/recordings/recording_20260813_140000",
            started_at=datetime(2026, 8, 13, 14, 0, 0),
            duration=60.0,
            audio_filename="combined_audio.wav",
        )
        self.assertEqual(
            meta["audio_files"],
            {"combined": "C:/recordings/recording_20260813_140000/combined_audio.wav"},
        )
        self.assertEqual(meta["directory"], "C:/recordings/recording_20260813_140000")


class TestNeedsConversion(unittest.TestCase):
    def test_m4a_needs_conversion(self):
        from app.recording.import_session import needs_conversion
        self.assertTrue(needs_conversion("C:/Downloads/call.m4a"))
        self.assertTrue(needs_conversion("C:/Downloads/CALL.M4A"))

    def test_wav_and_mp3_do_not(self):
        from app.recording.import_session import needs_conversion
        self.assertFalse(needs_conversion("C:/Downloads/call.wav"))
        self.assertFalse(needs_conversion("C:/Downloads/call.mp3"))


if __name__ == "__main__":
    unittest.main()
