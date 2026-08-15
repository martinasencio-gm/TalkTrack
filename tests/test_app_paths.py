import importlib
import shutil
import unittest
from pathlib import Path
from unittest import mock


def _reload_app_paths(home):
    with mock.patch("pathlib.Path.home", return_value=home):
        import app.utils.app_paths as app_paths
        return importlib.reload(app_paths)


class TestAppPaths(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_install_resolves_to_documents_target_without_moving(self):
        module = _reload_app_paths(self.home)
        self.assertEqual(module.APP_DATA_DIR, self.home / "Documents" / "TalkTrack")
        self.assertFalse((self.home / ".talktrack").exists())

    def test_legacy_dir_is_migrated_to_documents(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"marker": true}')

        module = _reload_app_paths(self.home)

        target = self.home / "Documents" / "TalkTrack"
        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        self.assertFalse(legacy.exists())

    def test_already_migrated_new_dir_is_used_and_legacy_left_alone(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "leftover.txt").write_text("untouched")

        target = self.home / "Documents" / "TalkTrack"
        target.mkdir(parents=True)
        (target / "settings.json").write_text('{"marker": true}')

        module = _reload_app_paths(self.home)

        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        # Legacy dir is left exactly as found - not merged, not deleted.
        self.assertTrue((legacy / "leftover.txt").exists())

    def test_migration_failure_falls_back_to_legacy_dir(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"marker": true}')

        with mock.patch("pathlib.Path.home", return_value=self.home), \
             mock.patch("shutil.move", side_effect=OSError("permission denied")):
            import app.utils.app_paths as app_paths
            module = importlib.reload(app_paths)

        self.assertEqual(module.APP_DATA_DIR, legacy)
        self.assertTrue((legacy / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
