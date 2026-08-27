"""Download a catalog model from HuggingFace into the app's models dir.

Wraps huggingface_hub.hf_hub_download (HTTP resume + its own .incomplete
temp file). Progress is surfaced through a tqdm shim because hf_hub_download
does not expose a plain callback; if a future hub version stops honouring
tqdm_class the bar simply stays at 0 until completion — the download itself
is unaffected.
"""
import hashlib
import shutil

from huggingface_hub import hf_hub_download

from app.ai.model_catalog import CatalogModel, local_path_for, models_dir
from app.ai import model_store

_MIN_VALID_BYTES = 100 * 1024 * 1024  # smaller than this = an error page, not a model


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _make_tqdm_class(progress_cb, cancel_check):
    from tqdm.auto import tqdm as _base

    class _CallbackTqdm(_base):
        def update(self, n=1):
            super().update(n)
            if cancel_check is not None and cancel_check():
                raise DownloadCancelled()
            if progress_cb is not None and self.total:
                try:
                    progress_cb(int(self.n), int(self.total))
                except DownloadCancelled:
                    raise
                except Exception:
                    pass

    return _CallbackTqdm


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _clean_partial(model: CatalogModel) -> None:
    try:
        local_path_for(model.key).unlink()
    except FileNotFoundError:
        pass
    cache = models_dir() / ".cache"
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)


def download(model: CatalogModel, token: str = "", progress_cb=None,
             cancel_check=None):
    models_dir().mkdir(parents=True, exist_ok=True)
    tqdm_class = _make_tqdm_class(progress_cb, cancel_check)
    try:
        result = hf_hub_download(
            repo_id=model.hf_repo,
            filename=model.hf_filename,
            local_dir=str(models_dir()),
            token=token or None,
            tqdm_class=tqdm_class,
        )
    except DownloadCancelled:
        _clean_partial(model)
        raise
    except Exception as e:  # network, auth, disk full, hub API changes
        _clean_partial(model)
        raise DownloadError(str(e)) from e

    from pathlib import Path
    path = Path(result)
    if not path.exists() or path.stat().st_size < _MIN_VALID_BYTES:
        _clean_partial(model)
        raise DownloadError(
            f"Downloaded file is too small ({path.stat().st_size if path.exists() else 0} "
            f"bytes) — the download was truncated or the server returned an error page."
        )
    model_store.record_download(model.key, _sha256(path))
    return path
