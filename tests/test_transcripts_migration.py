"""Tests for the one-time move of Markdown exports out of the old
repo-relative transcripts folder into the configured one.

Exports written before the Documents data-dir move (c49d8c6/d8e86fc) landed
in <repo>/transcripts while the app now reads and writes
Documents/talktrack/transcripts, leaving them invisible to the app.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from app.utils.transcripts_migration import import_legacy_exports


class TestImportLegacyExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = Path(self.tmp) / "legacy"
        self.legacy.mkdir()
        self.target = Path(self.tmp) / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_moves_markdown_files_into_the_target(self):
        (self.legacy / "a_20260815_1002.md").write_text("# a", encoding="utf-8")
        (self.legacy / "b_20260815_1013.md").write_text("# b", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(sorted(moved), ["a_20260815_1002.md", "b_20260815_1013.md"])
        self.assertTrue((self.target / "a_20260815_1002.md").exists())
        self.assertFalse((self.legacy / "a_20260815_1002.md").exists())

    def test_skips_names_already_present_in_the_target(self):
        """The target copy is the newer one — never overwrite it, and leave
        the legacy file alone so nothing is silently destroyed."""
        (self.legacy / "dup.md").write_text("old", encoding="utf-8")
        (self.target / "dup.md").write_text("new", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(moved, [])
        self.assertEqual((self.target / "dup.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.legacy / "dup.md").exists())

    def test_ignores_non_markdown_files(self):
        (self.legacy / "notes.txt").write_text("x", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(moved, [])
        self.assertTrue((self.legacy / "notes.txt").exists())

    def test_same_directory_is_a_noop(self):
        (self.legacy / "a.md").write_text("# a", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.legacy))

        self.assertEqual(moved, [])
        self.assertTrue((self.legacy / "a.md").exists())

    def test_missing_legacy_dir_is_a_noop(self):
        moved = import_legacy_exports(str(Path(self.tmp) / "gone"), str(self.target))
        self.assertEqual(moved, [])

    def test_missing_target_is_created(self):
        (self.legacy / "a.md").write_text("# a", encoding="utf-8")
        target = Path(self.tmp) / "made_here"

        moved = import_legacy_exports(str(self.legacy), str(target))

        self.assertEqual(moved, ["a.md"])
        self.assertTrue((target / "a.md").exists())

    def test_falsy_arguments_are_a_noop(self):
        self.assertEqual(import_legacy_exports("", str(self.target)), [])
        self.assertEqual(import_legacy_exports(str(self.legacy), None), [])


if __name__ == "__main__":
    unittest.main()
