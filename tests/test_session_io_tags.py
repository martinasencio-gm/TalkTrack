"""Unit tests for session_io.py tag loading and markdown export integration."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.utils import session_io, tag_manager


class TestSessionIOTags(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.session_dir = self.root / "rec1"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_load_tags_empty(self):
        session = {"directory": str(self.session_dir)}
        self.assertEqual(session_io.load_tags(session), [])

    def test_load_tags_from_dict(self):
        session = {"directory": str(self.session_dir), "tags": ["Meeting", "Action"]}
        self.assertEqual(session_io.load_tags(session), ["Meeting", "Action"])

    def test_load_tags_from_disk_metadata(self):
        meta = {"tags": ["Sprint", "Design"], "directory": str(self.session_dir)}
        (self.session_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        session = {"directory": str(self.session_dir)}
        self.assertEqual(session_io.load_tags(session), ["Sprint", "Design"])

    def test_export_session_markdown_includes_tags(self):
        meta = {
            "name": "Project Kickoff",
            "directory": str(self.session_dir),
            "started_at": "2026-08-21T10:00:00",
            "duration": 120,
            "tags": ["Kickoff", "Q3 Goals"],
        }
        (self.session_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        transcript_data = {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "Welcome everyone.", "speaker": "SPEAKER_00"}
            ],
            "language": "en",
        }
        (self.session_dir / "transcript.json").write_text(json.dumps(transcript_data), encoding="utf-8")

        session_io.export_session_markdown(meta)

        md_file = self.session_dir / "transcript.md"
        self.assertTrue(md_file.exists())
        content = md_file.read_text(encoding="utf-8")
        self.assertIn("tags:\n  - \"Kickoff\"\n  - \"Q3 Goals\"", content)


if __name__ == "__main__":
    unittest.main()
