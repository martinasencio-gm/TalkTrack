"""Unit tests for tag_manager.py."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.utils import tag_manager


class TestTagManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.tags_file = self.root / "tags.json"
        self.recordings_dir = self.root / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_session(self, name, tags=None):
        sess_dir = self.recordings_dir / name
        sess_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": name,
            "directory": str(sess_dir),
            "name": name,
            "tags": tags or [],
        }
        (sess_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        return sess_dir, meta

    def test_load_all_tags_default(self):
        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        self.assertTrue(len(tags) > 0)
        self.assertTrue(all("name" in t and "color" in t for t in tags))

    def test_create_tag(self):
        created = tag_manager.create_tag("Sprint Review", color="#89b4fa", tags_file=self.tags_file)
        self.assertEqual(created["name"], "Sprint Review")
        self.assertEqual(created["color"], "#89b4fa")

        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        self.assertTrue(any(t["name"] == "Sprint Review" for t in tags))

    def test_create_duplicate_tag(self):
        tag_manager.create_tag("Design", color="#89b4fa", tags_file=self.tags_file)
        # Creating duplicate returns existing tag
        second = tag_manager.create_tag("Design", color="#fab387", tags_file=self.tags_file)
        self.assertEqual(second["name"], "Design")

    def test_rename_tag_propagates_to_recordings(self):
        tag_manager.create_tag("OldTag", color="#89b4fa", tags_file=self.tags_file)
        sess1, _ = self._create_session("rec1", tags=["OldTag", "OtherTag"])
        sess2, _ = self._create_session("rec2", tags=["OldTag"])
        sess3, _ = self._create_session("rec3", tags=["Unrelated"])

        success = tag_manager.rename_tag("OldTag", "NewTag", recordings_dir=self.recordings_dir, tags_file=self.tags_file)
        self.assertTrue(success)

        # Check global tags
        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        tag_names = [t["name"] for t in tags]
        self.assertIn("NewTag", tag_names)
        self.assertNotIn("OldTag", tag_names)

        # Check session 1
        meta1 = json.loads((sess1 / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta1["tags"], ["NewTag", "OtherTag"])

        # Check session 2
        meta2 = json.loads((sess2 / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta2["tags"], ["NewTag"])

        # Check session 3 unchanged
        meta3 = json.loads((sess3 / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta3["tags"], ["Unrelated"])

    def test_delete_tag_removes_from_recordings(self):
        tag_manager.create_tag("ToDelete", color="#f38ba8", tags_file=self.tags_file)
        sess1, _ = self._create_session("rec1", tags=["ToDelete", "KeepMe"])
        sess2, _ = self._create_session("rec2", tags=["ToDelete"])

        success = tag_manager.delete_tag("ToDelete", recordings_dir=self.recordings_dir, tags_file=self.tags_file)
        self.assertTrue(success)

        # Check global tags
        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        self.assertFalse(any(t["name"] == "ToDelete" for t in tags))

        # Check sessions
        meta1 = json.loads((sess1 / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta1["tags"], ["KeepMe"])

        meta2 = json.loads((sess2 / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta2["tags"], [])

    def test_get_tag_counts(self):
        self._create_session("rec1", tags=["Planning", "Backend"])
        self._create_session("rec2", tags=["Planning", "Frontend"])
        self._create_session("rec3", tags=["Planning"])

        counts = tag_manager.get_tag_counts(self.recordings_dir)
        self.assertEqual(counts.get("Planning"), 3)
        self.assertEqual(counts.get("Backend"), 1)
        self.assertEqual(counts.get("Frontend"), 1)
        self.assertEqual(counts.get("Unknown", 0), 0)

    def test_assign_and_unassign_tags_on_recording(self):
        sess_dir, _ = self._create_session("rec1", tags=[])

        # Add tag
        updated = tag_manager.add_tag_to_recording(sess_dir, "Demo", tags_file=self.tags_file)
        self.assertEqual(updated, ["Demo"])

        # Metadata was saved
        meta = json.loads((sess_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["tags"], ["Demo"])

        # Auto-registered in tags.json
        tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        self.assertTrue(any(t["name"] == "Demo" for t in tags))

        # Add another tag
        updated = tag_manager.add_tag_to_recording(sess_dir, "Urgent", tags_file=self.tags_file)
        self.assertEqual(updated, ["Demo", "Urgent"])

        # Remove tag
        updated = tag_manager.remove_tag_from_recording(sess_dir, "Demo")
        self.assertEqual(updated, ["Urgent"])

        meta = json.loads((sess_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["tags"], ["Urgent"])

    def test_find_tags_for_recording_name_match(self):
        self._create_session("rec1", tags=["Weekly", "Team"])
        meta1_file = self.recordings_dir / "rec1" / "metadata.json"
        meta1 = json.loads(meta1_file.read_text(encoding="utf-8"))
        meta1["name"] = "Sprint Sync"
        meta1_file.write_text(json.dumps(meta1), encoding="utf-8")

        self._create_session("rec2", tags=["Other"])
        meta2_file = self.recordings_dir / "rec2" / "metadata.json"
        meta2 = json.loads(meta2_file.read_text(encoding="utf-8"))
        meta2["name"] = "Different Meeting"
        meta2_file.write_text(json.dumps(meta2), encoding="utf-8")

        # Query matching name
        found = tag_manager.find_tags_for_recording_name("Sprint Sync", self.recordings_dir)
        self.assertEqual(found, ["Weekly", "Team"])

        # Case-insensitive match
        found_lower = tag_manager.find_tags_for_recording_name("sprint sync", self.recordings_dir)
        self.assertEqual(found_lower, ["Weekly", "Team"])

        # Exclude self
        found_exclude = tag_manager.find_tags_for_recording_name(
            "Sprint Sync", self.recordings_dir, exclude_dir=self.recordings_dir / "rec1"
        )
        self.assertEqual(found_exclude, [])

        # Non-matching name
        not_found = tag_manager.find_tags_for_recording_name("Unknown Name", self.recordings_dir)
        self.assertEqual(not_found, [])


if __name__ == "__main__":
    unittest.main()
