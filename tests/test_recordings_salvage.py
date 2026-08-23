# tests/test_recordings_salvage.py
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf


class TestSalvageOrphanedRecordings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_dir(self, name, wavs=(), metadata=None, age_seconds=None):
        d = self.root / name
        d.mkdir()
        for wav in wavs:
            data = np.zeros(16000, dtype=np.float32)  # 1s at 16 kHz
            sf.write(str(d / wav), data, 16000)
        if metadata is not None:
            with open(d / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(metadata, f)
        if age_seconds is not None:
            old = time.time() - age_seconds
            os.utime(d, (old, old))
        return d

    def test_orphan_with_audio_gets_metadata(self):
        from app.ui.recordings_list import salvage_orphaned_recordings
        d = self._make_dir("recording_20260101_120000",
                           wavs=["mic_audio.wav", "combined_audio.wav"],
                           age_seconds=3600)
        salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)
        self.assertEqual(salvaged, [str(d)])
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["directory"], str(d))
        self.assertIn("Recovered", meta["name"])
        self.assertAlmostEqual(meta["duration"], 1.0, places=1)
        self.assertIn("combined", meta["audio_files"])
        self.assertTrue(meta["recovered"])

    def test_dir_with_metadata_untouched(self):
        from app.ui.recordings_list import salvage_orphaned_recordings
        original = {"id": "x", "directory": "y", "name": "Keep me"}
        d = self._make_dir("recording_a", wavs=["mic_audio.wav"],
                           metadata=original, age_seconds=3600)
        salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)
        self.assertEqual(salvaged, [])
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "Keep me")

    def test_recent_orphan_skipped(self):
        from app.ui.recordings_list import salvage_orphaned_recordings
        d = self._make_dir("recording_b", wavs=["mic_audio.wav"])  # fresh mtime
        salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)
        self.assertEqual(salvaged, [])
        self.assertFalse((d / "metadata.json").exists())

    def test_cloud_placeholder_audio_skipped_not_opened(self):
        """A OneDrive Files-On-Demand placeholder must never be handed to
        soundfile: opening one blocks on a synchronous cloud fetch with no
        timeout, which stalled MainWindow construction for 8 minutes on one
        un-hydrated file in production. `system` is the un-hydrated file
        here and `combined` doesn't exist, so this also proves the "skip a
        cloud candidate, fall through to the next" path, not just "give up
        entirely"."""
        from app.ui.recordings_list import salvage_orphaned_recordings
        d = self._make_dir("recording_20260823_082835",
                            wavs=["mic_audio.wav", "system_audio.wav"],
                            age_seconds=3600)

        def fake_is_cloud_placeholder(path):
            return Path(path).name == "system_audio.wav"

        with patch("app.ui.recordings_list._is_cloud_placeholder",
                   side_effect=fake_is_cloud_placeholder), \
             patch("soundfile.info", wraps=sf.info) as mock_info:
            salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)

        self.assertEqual(salvaged, [str(d)])
        for call in mock_info.call_args_list:
            self.assertNotIn("system_audio.wav", str(call))
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(meta["duration"], 1.0, places=1)  # read mic_audio.wav instead

    def test_all_candidates_cloud_placeholder_duration_zero(self):
        """Every audio file un-hydrated: salvage must still complete (no
        blocking open attempted anywhere) and record the recording with a
        0.0 duration rather than hang."""
        from app.ui.recordings_list import salvage_orphaned_recordings
        d = self._make_dir("recording_d", wavs=["mic_audio.wav"], age_seconds=3600)

        with patch("app.ui.recordings_list._is_cloud_placeholder", return_value=True), \
             patch("soundfile.info", wraps=sf.info) as mock_info:
            salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)

        self.assertEqual(salvaged, [str(d)])
        mock_info.assert_not_called()
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["duration"], 0.0)

    def test_empty_orphan_left_alone(self):
        from app.ui.recordings_list import salvage_orphaned_recordings
        d = self._make_dir("recording_c", age_seconds=3600)
        salvaged = salvage_orphaned_recordings(self.root, min_age_seconds=600)
        self.assertEqual(salvaged, [])
        self.assertFalse((d / "metadata.json").exists())
        self.assertTrue(d.exists())


if __name__ == "__main__":
    unittest.main()
