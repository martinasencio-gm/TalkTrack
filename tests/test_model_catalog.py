"""Tests for the built-in model catalog."""
import unittest

from app.ai import model_catalog
from app.ai.model_catalog import CATALOG, CatalogModel, get, local_path_for, models_dir
from app.utils.app_paths import APP_DATA_DIR


class CatalogShapeTest(unittest.TestCase):
    def test_catalog_is_non_empty_list_of_catalog_models(self):
        self.assertIsInstance(CATALOG, list)
        self.assertGreaterEqual(len(CATALOG), 3)
        self.assertTrue(all(isinstance(m, CatalogModel) for m in CATALOG))

    def test_keys_are_unique_and_non_empty(self):
        keys = [m.key for m in CATALOG]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(m.key for m in CATALOG))

    def test_every_entry_has_repo_filename_and_positive_numbers(self):
        for m in CATALOG:
            self.assertTrue(m.hf_repo, m.key)
            self.assertTrue(m.hf_filename.endswith(".gguf"), m.key)
            self.assertGreater(m.size_bytes, 100 * 1024 * 1024, m.key)
            self.assertGreater(m.context_tokens, 0, m.key)
            self.assertGreater(m.ram_hint_gb, 0, m.key)
            self.assertTrue(m.license, m.key)

    def test_get_returns_model_or_none(self):
        self.assertIs(get("nonexistent-key"), None)
        first = CATALOG[0]
        self.assertEqual(get(first.key), first)

    def test_models_dir_is_under_app_data_dir_and_not_created(self):
        self.assertEqual(models_dir(), APP_DATA_DIR / "models")

    def test_local_path_for_joins_models_dir_and_filename(self):
        m = CATALOG[0]
        self.assertEqual(local_path_for(m.key), models_dir() / m.hf_filename)
        with self.assertRaises(KeyError):
            local_path_for("nonexistent-key")


if __name__ == "__main__":
    unittest.main()
