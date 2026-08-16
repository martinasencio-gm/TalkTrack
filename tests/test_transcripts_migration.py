"""Tests for the one-time import of Markdown transcript exports, stranded
in the old separate transcripts/ folder, into their matching recording's
own session folder (see #74 — transcript.md now lives at
<session_dir>/transcript.md, not in a separately managed folder).
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from app.utils.transcripts_migration import import_exports_into_sessions


class TestImportExportsIntoSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_folder = Path(self.tmp) / "old_transcripts"
        self.old_folder.mkdir()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, name):
        d = self.recordings_dir / name
        d.mkdir()
        return d

    def test_moves_export_into_its_matching_session_folder(self):
        session = self._make_session("rec_20260813_140000")
        (self.old_folder / "rec_20260813_140000_20260813_1400.md").write_text(
            "# exported", encoding="utf-8"
        )

        moved = import_exports_into_sessions([str(self.old_folder)], str(self.recordings_dir))

        destination = session / "transcript.md"
        self.assertEqual(moved, [str(destination)])
        self.assertTrue(destination.exists())
        self.assertFalse((self.old_folder / "rec_20260813_140000_20260813_1400.md").exists())

    def test_orphaned_export_with_no_matching_session_is_left_untouched(self):
        """The export is the only surviving copy of a deleted recording —
        never delete or lose it, just leave it where it is."""
        orphan = self.old_folder / "rec_deleted_long_ago_20260101_0900.md"
        orphan.write_text("# exported", encoding="utf-8")

        moved = import_exports_into_sessions([str(self.old_folder)], str(self.recordings_dir))

        self.assertEqual(moved, [])
        self.assertTrue(orphan.exists())

    def test_does_not_overwrite_an_existing_transcript_md(self):
        session = self._make_session("rec_20260813_140000")
        (session / "transcript.md").write_text("# already here", encoding="utf-8")
        stranded = self.old_folder / "rec_20260813_140000_20260813_1400.md"
        stranded.write_text("# stale export", encoding="utf-8")

        moved = import_exports_into_sessions([str(self.old_folder)], str(self.recordings_dir))

        self.assertEqual(moved, [])
        self.assertEqual((session / "transcript.md").read_text(encoding="utf-8"), "# already here")
        self.assertTrue(stranded.exists())

    def test_scans_multiple_source_dirs(self):
        session_a = self._make_session("rec_a")
        session_b = self._make_session("rec_b")
        other_folder = Path(self.tmp) / "legacy_default"
        other_folder.mkdir()
        (self.old_folder / "rec_a_20260101_0000.md").write_text("a", encoding="utf-8")
        (other_folder / "rec_b_20260101_0000.md").write_text("b", encoding="utf-8")

        moved = import_exports_into_sessions(
            [str(self.old_folder), str(other_folder)], str(self.recordings_dir)
        )

        self.assertEqual(sorted(moved),
                          sorted([str(session_a / "transcript.md"), str(session_b / "transcript.md")]))

    def test_deduplicates_source_dirs_resolving_to_the_same_path(self):
        session = self._make_session("rec_a")
        (self.old_folder / "rec_a_20260101_0000.md").write_text("a", encoding="utf-8")

        moved = import_exports_into_sessions(
            [str(self.old_folder), str(self.old_folder)], str(self.recordings_dir)
        )

        self.assertEqual(moved, [str(session / "transcript.md")])

    def test_longest_matching_session_name_wins_over_a_prefix_sibling(self):
        """A session named "rec_a" must not steal an export that belongs to
        sibling session "rec_ab" just because "rec_a_" is also a prefix
        match — the longest (most specific) name wins."""
        self._make_session("rec_a")
        session_ab = self._make_session("rec_ab")
        (self.old_folder / "rec_ab_20260101_0000.md").write_text("ab", encoding="utf-8")

        moved = import_exports_into_sessions([str(self.old_folder)], str(self.recordings_dir))

        self.assertEqual(moved, [str(session_ab / "transcript.md")])

    def test_ignores_non_markdown_files(self):
        self._make_session("rec_a")
        (self.old_folder / "rec_a_notes.txt").write_text("x", encoding="utf-8")

        moved = import_exports_into_sessions([str(self.old_folder)], str(self.recordings_dir))

        self.assertEqual(moved, [])
        self.assertTrue((self.old_folder / "rec_a_notes.txt").exists())

    def test_missing_source_dir_is_a_noop(self):
        moved = import_exports_into_sessions(
            [str(Path(self.tmp) / "gone")], str(self.recordings_dir)
        )
        self.assertEqual(moved, [])

    def test_missing_recordings_dir_is_a_noop(self):
        (self.old_folder / "rec_a_20260101_0000.md").write_text("a", encoding="utf-8")
        moved = import_exports_into_sessions(
            [str(self.old_folder)], str(Path(self.tmp) / "no_such_recordings_dir")
        )
        self.assertEqual(moved, [])

    def test_falsy_and_empty_source_dirs_are_skipped(self):
        moved = import_exports_into_sessions([None, "", None], str(self.recordings_dir))
        self.assertEqual(moved, [])


if __name__ == "__main__":
    unittest.main()
