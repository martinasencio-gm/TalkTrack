"""Tests for the model store / manifest."""
import json
import unittest
from unittest.mock import patch

from app.ai import model_catalog, model_store


class ModelStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        from pathlib import Path
        self._dir = Path(self._tmp) / "models"
        patcher = patch.object(model_catalog, "models_dir", return_value=self._dir)
        self.enterContext(patcher)
        # model_store calls model_catalog.models_dir() indirectly; make sure
        # both module references resolve to the patched function.
        self.enterContext(patch.object(model_store, "models_dir", model_catalog.models_dir))
        self.key = model_catalog.CATALOG[0].key
        self.fname = model_catalog.CATALOG[0].hf_filename

    def _write_fake_gguf(self, nbytes):
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / self.fname).write_bytes(b"\0" * nbytes)

    def test_load_manifest_missing_returns_empty_dict(self):
        self.assertEqual(model_store.load_manifest(), {})

    def test_load_manifest_corrupt_returns_empty_dict(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        model_store.manifest_path().write_text("{not json")
        self.assertEqual(model_store.load_manifest(), {})

    def test_is_downloaded_false_when_file_present_but_no_manifest_entry(self):
        self._write_fake_gguf(500)
        self.assertFalse(model_store.is_downloaded(self.key))

    def test_is_downloaded_false_when_manifest_entry_present_but_file_missing(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        model_store.manifest_path().write_text(json.dumps(
            {self.key: {"filename": self.fname, "sha256": "x", "size": 500,
                        "downloaded_at": "2026-08-26T00:00:00+00:00"}}))
        self.assertFalse(model_store.is_downloaded(self.key))

    def test_record_download_then_is_downloaded_true(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "abc123")
        self.assertTrue(model_store.is_downloaded(self.key))
        entry = model_store.load_manifest()[self.key]
        self.assertEqual(entry["sha256"], "abc123")
        self.assertEqual(entry["size"], 500)
        self.assertEqual(entry["filename"], self.fname)

    def test_is_downloaded_false_when_file_outside_size_tolerance(self):
        self._write_fake_gguf(1000)
        model_store.record_download(self.key, "abc123")  # records size=1000
        # File grows/shrinks well past the 20% tolerance -> not a valid match.
        (self._dir / self.fname).write_bytes(b"\0" * 5000)
        self.assertFalse(model_store.is_downloaded(self.key))

    def test_record_download_twice_updates_entry_without_duplicating(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "first")
        self._write_fake_gguf(700)
        model_store.record_download(self.key, "second")
        manifest = model_store.load_manifest()
        self.assertEqual(list(manifest.keys()), [self.key])
        self.assertEqual(manifest[self.key]["sha256"], "second")
        self.assertEqual(manifest[self.key]["size"], 700)

    def test_record_download_persists_advertised_size(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "abc123", advertised_size=1234567)
        self.assertEqual(
            model_store.load_manifest()[self.key]["advertised_size"], 1234567
        )

    def test_remove_deletes_file_and_entry_and_is_idempotent(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "abc123")
        model_store.remove(self.key)
        self.assertFalse((self._dir / self.fname).exists())
        self.assertNotIn(self.key, model_store.load_manifest())
        model_store.remove(self.key)  # no raise

    def test_free_disk_bytes_is_positive(self):
        self.assertGreater(model_store.free_disk_bytes(), 0)

    def test_list_status_covers_catalog_in_order(self):
        statuses = model_store.list_status()
        self.assertEqual([s.model.key for s in statuses],
                         [m.key for m in model_catalog.CATALOG])
        self.assertTrue(all(s.downloaded is False for s in statuses))


if __name__ == "__main__":
    unittest.main()
