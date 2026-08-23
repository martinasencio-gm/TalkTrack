import unittest

from app.utils.icons import icon_path


class TestIconPath(unittest.TestCase):
    def test_resolves_to_an_existing_vendored_file(self):
        path = icon_path("warning")
        self.assertTrue(path.is_file())

    def test_resolves_under_resources_icons_regardless_of_cwd(self):
        path = icon_path("check-circle-fill")
        self.assertEqual(path.parent.name, "icons")
        self.assertEqual(path.parent.parent.name, "resources")

    def test_unknown_name_still_builds_a_path_without_raising(self):
        path = icon_path("does-not-exist")
        self.assertFalse(path.is_file())


if __name__ == "__main__":
    unittest.main()
