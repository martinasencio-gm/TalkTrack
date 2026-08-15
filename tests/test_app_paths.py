import importlib
import unittest
from pathlib import Path
from unittest import mock


def _reload_with_home_documents_fallback(home):
    """Reload app_paths with Path.home() patched and the registry lookup
    forced to fail, so resolution falls back to home/Documents - keeps
    these tests isolated from the real machine's actual Documents location.
    """
    with mock.patch("pathlib.Path.home", return_value=home), \
         mock.patch("winreg.OpenKey", side_effect=OSError("no key")):
        import app.utils.app_paths as app_paths
        return importlib.reload(app_paths)


class TestKnownDocumentsResolution(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_uses_registry_personal_value_when_available(self):
        # Simulates a redirected Documents folder (e.g. to OneDrive) - the
        # real-world case that Path.home()/"Documents" gets wrong.
        redirected = self.home / "OneDrive - Example" / "Documents"
        with mock.patch("pathlib.Path.home", return_value=self.home), \
             mock.patch("winreg.OpenKey"), \
             mock.patch("winreg.QueryValueEx", return_value=(str(redirected), 1)):
            import app.utils.app_paths as app_paths
            module = importlib.reload(app_paths)

        self.assertEqual(module.APP_DATA_DIR, redirected / "TalkTrack")

    def test_falls_back_to_home_documents_when_registry_unreadable(self):
        module = _reload_with_home_documents_fallback(self.home)
        self.assertEqual(module.APP_DATA_DIR, self.home / "Documents" / "TalkTrack")


class TestAppPaths(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_install_resolves_to_documents_target_without_moving(self):
        module = _reload_with_home_documents_fallback(self.home)
        self.assertEqual(module.APP_DATA_DIR, self.home / "Documents" / "TalkTrack")
        self.assertFalse((self.home / ".talktrack").exists())

    def test_legacy_dot_dir_is_migrated_to_documents(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"marker": true}')

        module = _reload_with_home_documents_fallback(self.home)

        target = self.home / "Documents" / "TalkTrack"
        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        self.assertFalse(legacy.exists())

    def test_wrong_unredirected_documents_dir_is_migrated_to_real_target(self):
        # Simulates a machine where Documents is redirected (e.g. OneDrive)
        # but an earlier build wrote data to the naive Path.home()/Documents
        # location instead. That data must be picked up and moved to the
        # real, registry-resolved Documents folder.
        stale = self.home / "Documents" / "TalkTrack"
        stale.mkdir(parents=True)
        (stale / "settings.json").write_text('{"marker": true}')

        redirected = self.home / "OneDrive - Example" / "Documents"
        with mock.patch("pathlib.Path.home", return_value=self.home), \
             mock.patch("winreg.OpenKey"), \
             mock.patch("winreg.QueryValueEx", return_value=(str(redirected), 1)):
            import app.utils.app_paths as app_paths
            module = importlib.reload(app_paths)

        target = redirected / "TalkTrack"
        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        self.assertFalse(stale.exists())

    def test_target_dir_with_unrelated_content_still_gets_settings_merged_in(self):
        # Reproduces the real bug found live: Documents/TalkTrack already
        # existed (recordings/transcripts subfolders from unrelated output
        # settings), so a directory-existence check wrongly concluded
        # migration was already done and left settings.json stranded.
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"marker": true}')
        (legacy / "talktrack.log").write_text("log line")

        target = self.home / "Documents" / "TalkTrack"
        (target / "recordings").mkdir(parents=True)

        module = _reload_with_home_documents_fallback(self.home)

        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        self.assertTrue((target / "talktrack.log").exists())
        self.assertTrue((target / "recordings").exists())  # untouched
        self.assertFalse(legacy.exists())

    def test_already_migrated_new_dir_is_used_and_legacy_left_alone(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "leftover.txt").write_text("untouched")

        target = self.home / "Documents" / "TalkTrack"
        target.mkdir(parents=True)
        (target / "settings.json").write_text('{"marker": true}')

        module = _reload_with_home_documents_fallback(self.home)

        self.assertEqual(module.APP_DATA_DIR, target)
        self.assertTrue((target / "settings.json").exists())
        # Legacy dir is left exactly as found - not merged, not deleted.
        self.assertTrue((legacy / "leftover.txt").exists())

    def test_migration_failure_falls_back_to_legacy_dir(self):
        legacy = self.home / ".talktrack"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"marker": true}')

        with mock.patch("pathlib.Path.home", return_value=self.home), \
             mock.patch("winreg.OpenKey", side_effect=OSError("no key")), \
             mock.patch("shutil.move", side_effect=OSError("permission denied")):
            import app.utils.app_paths as app_paths
            module = importlib.reload(app_paths)

        self.assertEqual(module.APP_DATA_DIR, legacy)
        self.assertTrue((legacy / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
