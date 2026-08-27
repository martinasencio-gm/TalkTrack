"""Download a catalog model from HuggingFace into the app's models dir.

Wraps huggingface_hub.hf_hub_download (HTTP resume + its own .incomplete
temp file). Progress is surfaced through a tqdm shim because hf_hub_download
does not expose a plain callback; if a future hub version stops honouring
tqdm_class the bar simply stays at 0 until completion — the download itself
is unaffected.
"""
import hashlib
import io
import shutil

from huggingface_hub import hf_hub_download

from app.ai.model_catalog import CatalogModel, local_path_for, models_dir
from app.ai import model_store

_MIN_VALID_BYTES = 100 * 1024 * 1024  # smaller than this = an error page, not a model
_SIZE_MISMATCH_FRACTION = 0.25  # catalog sizes are approximate; only flag a wide miss


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _make_tqdm_class(progress_cb, cancel_check):
    from tqdm.auto import tqdm as _base

    class _CallbackTqdm(_base):
        # Under pythonw, stderr is redirected to talktrack.log; tqdm's own bar
        # rendering would spam progress frames into the log. Suppress all
        # rendering (display/refresh no-ops + a throwaway file sink so close()'s
        # trailing newline goes nowhere) — only the callback in update() matters.
        def __init__(self, *a, **k):
            k.setdefault("file", io.StringIO())
            super().__init__(*a, **k)

        def display(self, *a, **k):
            return

        def refresh(self, *a, **k):
            return

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


def _clean_partial(model: CatalogModel, wipe_cache: bool = True) -> None:
    """Remove a failed download's target ``.gguf``.

    ``wipe_cache=True`` (cancel / size-check failure) also clears HF's
    ``.cache`` dir so the next attempt starts fresh. ``wipe_cache=False``
    (transient network error) keeps ``.cache/huggingface/download/`` intact so
    ``hf_hub_download`` can resume from its ``.incomplete`` blob.
    """
    try:
        local_path_for(model.key).unlink()
    except OSError:
        # missing, or locked/partial (PermissionError) — must not mask the
        # original error that brought us here.
        pass
    if wipe_cache:
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
        _clean_partial(model, wipe_cache=True)
        raise
    except Exception as e:  # network, auth, disk full, hub API changes
        # Transient failure — keep HF's resume cache so the retry doesn't
        # re-download multiple GB from zero.
        _clean_partial(model, wipe_cache=False)
        raise DownloadError(str(e)) from e

    from pathlib import Path
    path = Path(result)
    if not path.exists() or path.stat().st_size < _MIN_VALID_BYTES:
        _clean_partial(model, wipe_cache=True)
        raise DownloadError(
            f"Downloaded file is too small ({path.stat().st_size if path.exists() else 0} "
            f"bytes) — the download was truncated or the server returned an error page."
        )
    actual = path.stat().st_size
    if model.size_bytes > 0 and \
            abs(actual - model.size_bytes) > _SIZE_MISMATCH_FRACTION * model.size_bytes:
        _clean_partial(model, wipe_cache=True)
        raise DownloadError(
            f"Downloaded size ({actual} bytes) is not within "
            f"{int(_SIZE_MISMATCH_FRACTION * 100)}% of the expected "
            f"{model.size_bytes} bytes — the download was truncated."
        )
    model_store.record_download(model.key, _sha256(path),
                                advertised_size=model.size_bytes)
    return path
