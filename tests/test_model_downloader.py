"""Tests for the catalog model downloader (hf_hub_download mocked)."""
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai import model_catalog, model_downloader, model_store
from app.ai.model_downloader import DownloadCancelled, DownloadError, download


class DownloaderTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self._dir = Path(tmp) / "models"
        self._dir.mkdir(parents=True)
        self.enterContext(patch.object(model_catalog, "models_dir", return_value=self._dir))
        self.enterContext(patch.object(model_downloader, "models_dir", model_catalog.models_dir))
        self.enterContext(patch.object(model_store, "models_dir", model_catalog.models_dir))
        self.model = model_catalog.CATALOG[0]

    def _fake_hf_download(self, valid_bytes=model_downloader._MIN_VALID_BYTES + 1):
        def _impl(*, repo_id, filename, local_dir, token=None, tqdm_class=None, **kw):
            dest = Path(local_dir) / filename
            dest.write_bytes(b"\0" * valid_bytes)
            return str(dest)
        return _impl

    def test_success_records_manifest_and_returns_path(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=self._fake_hf_download()):
            path = download(self.model)
        self.assertTrue(path.exists())
        self.assertTrue(model_store.is_downloaded(self.model.key))
        expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(model_store.load_manifest()[self.model.key]["sha256"], expected_sha)

    def test_truncated_download_raises_download_error(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=self._fake_hf_download(valid_bytes=1024)):
            with self.assertRaises(DownloadError):
                download(self.model)
        self.assertFalse(model_store.is_downloaded(self.model.key))

    def test_network_failure_is_wrapped_in_download_error(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=OSError("connection reset")):
            with self.assertRaises(DownloadError):
                download(self.model)

    def test_cancel_raises_and_cleans_partial(self):
        def _cancelling_download(*, repo_id, filename, local_dir, token=None,
                                 tqdm_class=None, **kw):
            # Simulate hf streaming: drive the tqdm shim, which polls cancel.
            bar = tqdm_class(total=1000) if tqdm_class else None
            (Path(local_dir) / filename).write_bytes(b"\0" * 2048)  # partial
            if bar:
                bar.update(500)  # progress_cb -> cancel_check True -> raises
            return str(Path(local_dir) / filename)

        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=_cancelling_download):
            with self.assertRaises(DownloadCancelled):
                download(self.model, cancel_check=lambda: True)
        self.assertFalse((self._dir / self.model.hf_filename).exists())

    def test_progress_cb_receives_downloaded_and_total(self):
        seen = []

        def _driving_download(*, repo_id, filename, local_dir, token=None,
                              tqdm_class=None, **kw):
            bar = tqdm_class(total=2000)
            bar.update(1000)
            bar.update(1000)
            dest = Path(local_dir) / filename
            dest.write_bytes(b"\0" * (model_downloader._MIN_VALID_BYTES + 1))
            return str(dest)

        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=_driving_download):
            download(self.model, progress_cb=lambda d, t: seen.append((d, t)))
        self.assertIn((1000, 2000), seen)
        self.assertIn((2000, 2000), seen)


if __name__ == "__main__":
    unittest.main()
